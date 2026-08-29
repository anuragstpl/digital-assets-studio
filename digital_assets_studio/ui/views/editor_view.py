"""The video editor: clips on the left, the frame you are on in the middle, and
the AI and the output settings on the right.

The screen owns no state of its own. Everything lives on one Editor object that
holds the timeline, so a redraw - and there is one after every edit - rebuilds
from the document rather than from whatever the widgets happened to contain.
"""
from __future__ import annotations

import logging
from pathlib import Path

import flet as ft

from ...config import ASSETS_DIR
from ...core import projects as pj
from ...core.editor import ai as edit_ai
from ...core.editor import analyze, publish as edit_publish, render as edit_render
from ...core.editor import timeline as tl
from ...core.publishing import youtube as yt
from ...theme import RADIUS, RADIUS_SM, shadow
from ..components import (body, card, divider, dropdown, empty_state, ghost_button, h2,
                          label, pill, primary_button, text_field)

log = logging.getLogger(__name__)

FONT_FILE = ASSETS_DIR / "fonts" / "Poppins-Medium.ttf"

CANVASES = {"Landscape 1920×1080": tl.LANDSCAPE, "Portrait 1080×1920": tl.PORTRAIT}
PRIVACY = ["private", "unlisted", "public"]

QUICK_EDITS = [
    ("Tighten the open", "Cut anything before the first real sentence, and lose any shot "
                         "that repeats the one before it."),
    ("Punch up the pacing", "Trim every clip that outstays its narration, and put a short "
                            "dissolve where the subject changes."),
    ("Add the titles", "Add at most three on-screen titles at the moments a viewer would "
                       "otherwise scrub past."),
]


def clock(seconds: float) -> str:
    seconds = max(float(seconds or 0), 0)
    mins, secs = divmod(seconds, 60)
    return f"{int(mins)}:{secs:04.1f}"


class Editor:
    """The editor's state and every action its buttons trigger."""

    def __init__(self, studio, project) -> None:
        self.studio = studio
        self.project = project
        # which document is open. A Short is a second file, not a replacement:
        # switching to one must never save over the long edit it was cut from
        self.path = project.dir / "edit" / "timeline.json"
        self.doc = tl.load(self.path)
        self.selected: str | None = self.doc.clips[0].id if self.doc.clips else None
        self.playhead = 0.0
        self.brief = ""
        self.log: list[str] = []
        self._preview = 0
        self._preview_key: tuple | None = None
        self._preview_path: Path | None = None
        self.publish_fields = edit_publish.default_metadata(project)
        self.publish_fields.setdefault("privacy", "private")
        self.publish_fields.setdefault("channel", "")

    # ------------------------------------------------------------- storage --
    @property
    def is_short(self) -> bool:
        return self.path.name == "short.json"

    @property
    def out_path(self) -> Path:
        return self.project.build / f"{self.doc.name or self.project.id}.edit.mp4"

    def save(self, quiet: bool = False) -> None:
        self.doc.save(self.path)
        self.project.set_answer("edit_timeline", self.project.rel(self.path))
        self.project.save()
        if not quiet:
            self.note(f"Saved — {self.doc.summary()}")

    def note(self, message: str) -> None:
        self.log.append(message)
        del self.log[:-40]

    # ------------------------------------------------------------ selection --
    @property
    def clip(self) -> tl.Clip | None:
        return self.doc.clip(self.selected) if self.selected else None

    def select(self, clip_id: str) -> None:
        self.selected = clip_id
        i = self.doc.index(clip_id)
        if i >= 0:
            self.playhead = self.doc.starts()[i]
        self.refresh()

    def refresh(self) -> None:
        self.studio.refresh()

    # --------------------------------------------------------------- edits --
    def mutate(self, fn, message: str = "") -> None:
        """Run one editing operation, save, and redraw. Every button goes
        through here so no edit can be lost by forgetting to save."""
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log.exception("edit failed")
            self.studio.toast(f"That edit did not work: {exc}", "error")
            return
        if self.selected and self.doc.clip(self.selected) is None:
            self.selected = self.doc.clips[0].id if self.doc.clips else None
        self.save(quiet=True)
        if message:
            self.note(message)
        self.refresh()

    def add_media(self, path: str) -> None:
        source = Path(path)
        if not source.exists():
            self.studio.toast("No file there.", "error")
            return
        suffix = source.suffix.lower()
        if suffix in tl.AUDIO_SUFFIXES:
            self.mutate(lambda: self.doc.set_music(str(source)),
                        f"Music bed: {source.name}")
            return
        if suffix not in tl.VIDEO_SUFFIXES + tl.IMAGE_SUFFIXES:
            self.studio.toast(f"{suffix or 'That file'} is not a video, image or audio file.",
                              "warn")
            return
        kind = tl.kind_for(source)
        seconds = analyze.duration(source) if kind == tl.VIDEO else 0.0
        clip = tl.Clip(source=str(source), kind=kind, source_in=0.0,
                       source_out=seconds if seconds > 0 else 4.0, label=source.stem[:28])
        self.mutate(lambda: self.doc.add(clip), f"Added {source.name}")

    def split_here(self) -> None:
        clip = self.clip
        if clip is None:
            return
        if self.doc.split(clip.id, self.playhead) is None:
            self.studio.toast("The playhead has to be inside the clip, and at least "
                              f"{tl.MIN_CLIP}s from either end.", "warn")
            return
        self.save(quiet=True)
        self.note(f"Split at {clock(self.playhead)}")
        self.refresh()

    def nudge(self, delta: int) -> None:
        clip = self.clip
        if clip is None:
            return
        self.mutate(lambda: self.doc.move(clip.id, self.doc.index(clip.id) + delta))

    def set_prop(self, key: str, value) -> None:
        clip = self.clip
        if clip is None:
            return
        self.mutate(lambda: self.doc.set(clip.id, **{key: value}))

    def set_canvas(self, name: str) -> None:
        size = CANVASES.get(name)
        if not size:
            return
        self.doc.width, self.doc.height = size
        self.save(quiet=True)
        self.refresh()

    # ------------------------------------------------------------ preview --
    def preview(self) -> Path | None:
        """A still of the frame under the playhead.

        Grabbing a frame costs an ffmpeg launch, and the screen redraws after
        every edit, so the last frame is reused until the playhead or the clip
        under it actually changes. When it does change the file gets a new name:
        image widgets cache by path, and reusing one means the preview never
        appears to move.
        """
        if not edit_render.available() or not self.doc.clips:
            return None
        clip = self.doc.at(self.playhead)
        key = (round(self.playhead, 2), clip.id if clip else "",
               clip.source_in if clip else 0, clip.source_out if clip else 0,
               clip.source if clip else "", self.doc.width, self.doc.height)
        if key == self._preview_key and self._preview_path \
                and self._preview_path.exists():
            return self._preview_path
        folder = self.project.dir / "edit" / "preview"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for old in folder.glob("*.jpg"):
                old.unlink(missing_ok=True)
            self._preview += 1
            frame = edit_render.preview_frame(
                self.doc, self.project.dir, self.playhead,
                folder / f"f{self._preview:04d}.jpg", width=720)
        except Exception:  # noqa: BLE001 - a preview is never worth an error screen
            log.exception("preview failed")
            return None
        self._preview_key, self._preview_path = key, frame
        return frame

    # ---------------------------------------------------- long-running work --
    def assemble(self) -> None:
        project = self.project

        def work(ctx):
            doc = edit_ai.assemble(project, note=lambda m: ctx.log(m))
            try:
                slug = edit_ai.sources(project)["slug"]
                script = project.read_text(f"drafts/episodes/{slug}.md", "")
                if script:
                    ctx.progress(0.7, "Choosing the on-screen titles")
                    applied, _ = edit_ai.apply_ops(
                        doc, edit_ai.suggest_titles(doc, script), project.dir)
                    for line in applied:
                        ctx.log(line)
            except Exception as exc:  # noqa: BLE001 - titles are a bonus, not the job
                ctx.log(f"No AI titles this time: {exc}", "warning")
            doc.save(self.path)
            return doc

        def done(update):
            if update.status == "done" and update.result is not None:
                self.doc = update.result
                self.selected = self.doc.clips[0].id if self.doc.clips else None
                self.playhead = 0.0
                self.note(f"Assembled — {self.doc.summary()}")

        self.studio.submit("Assemble the edit", work, done)

    def ai_edit(self, brief: str = "") -> None:
        brief = (brief or self.brief).strip()
        if not brief:
            self.studio.toast("Tell the editor what to change first.", "warn")
            return
        doc, base = self.doc, self.project.dir
        context = f"The project is “{self.project.name}”. " \
                  f"Topic: {self.project.answer('topic', '')}"

        def work(ctx):
            ctx.progress(0.2, "Asking for an edit plan")
            applied, rejected = edit_ai.auto_edit(doc, brief, base, context)
            for line in applied:
                ctx.log(f"✓ {line}")
            for line in rejected:
                ctx.log(f"× {line}", "warning")
            doc.save(self.path)
            return applied, rejected

        def done(update):
            if update.status != "done" or not update.result:
                return
            applied, rejected = update.result
            self.note(f"AI edit: {len(applied)} change(s) applied, {len(rejected)} rejected")
            for line in applied + [f"rejected — {r}" for r in rejected]:
                self.note(line)

        self.studio.submit("AI edit", work, done)

    def cut_dead_air(self) -> None:
        doc, base = self.doc, self.project.dir

        def work(ctx):
            removed = edit_ai.cut_dead_air(doc, base,
                                           progress=lambda f, m: ctx.progress(f, m))
            doc.save(self.path)
            return removed

        def done(update):
            if update.status == "done":
                self.note(f"Cut {update.result:.1f}s of dead air — now {clock(self.doc.duration)}")
                self.selected = self.doc.clips[0].id if self.doc.clips else None

        self.studio.submit("Cut the dead air", work, done)

    def make_short(self, seconds: float = 45.0) -> None:
        if not self.doc.clips:
            self.studio.toast("Nothing on the timeline to cut a Short from.", "warn")
            return
        hint = self.project.answer("short_start", None)
        start = edit_ai.highlight_start(self.doc, seconds,
                                        float(hint) if hint not in (None, "") else None)
        if self.is_short:
            self.studio.toast("This is already the Short. Switch back to the long edit "
                              "to cut a different window.", "warn")
            return
        short = edit_ai.crop(self.doc, start, min(seconds, self.doc.duration), portrait=True)
        self.save(quiet=True)                     # the long edit, before we leave it
        self.path = self.project.dir / "edit" / "short.json"
        self.doc = short
        self.selected = short.clips[0].id if short.clips else None
        self.playhead = 0.0
        self.save(quiet=True)
        self.note(f"Vertical cut from {clock(start)} — {short.summary()}. "
                  f"Render it, then publish it as a Short.")
        self.refresh()

    def open_document(self, short: bool) -> None:
        """Switch between the long edit and the Short cut from it."""
        self.save(quiet=True)
        self.path = self.project.dir / "edit" / ("short.json" if short else "timeline.json")
        self.doc = tl.load(self.path)
        self.selected = self.doc.clips[0].id if self.doc.clips else None
        self.playhead = 0.0
        self.refresh()

    def render(self) -> None:
        problems = self.doc.problems(self.project.dir)
        blocking = [p for p in problems if "missing" in p or "no file at" in p or "empty" in p]
        if blocking:
            self.studio.toast(blocking[0], "error")
            return
        doc, base, out = self.doc, self.project.dir, self.out_path

        def work(ctx):
            return edit_render.render(doc, out, base, font=FONT_FILE,
                                      progress=lambda f, m: ctx.progress(f, m))

        def done(update):
            if update.status != "done":
                return
            size = out.stat().st_size / 1_048_576 if out.exists() else 0
            self.note(f"Rendered {out.name} — {clock(doc.duration)}, {size:.0f} MB")
            if not self.is_short:
                # the pipeline's upload step publishes whatever this points at, so
                # a rendered edit is picked up there without re-selecting a file.
                # A Short is not that file - it has its own step.
                self.project.set_answer("video_file", self.project.rel(out))
                self.project.save()
            self.studio.toast(f"Rendered to {out.name}", "ok")

        self.studio.submit("Render the edit", work, done)

    def publish(self) -> None:
        out = self.out_path
        if not out.exists():
            self.studio.toast("Render the edit before publishing it.", "warn")
            return
        fields = dict(self.publish_fields)
        project = self.project
        slug = self.doc.name or project.id
        thumbs = sorted((project.dir / "build" / "thumbnails").glob(f"{slug}_v*.jpg"))
        captions = project.dir / self.doc.captions if self.doc.captions else None

        def work(ctx):
            return edit_publish.publish(
                project, out, title=fields.get("title", ""),
                description=fields.get("description", ""), tags=fields.get("tags", []),
                privacy=fields.get("privacy", "private"),
                category=project.answer("yt_category", "Education"),
                channel=fields.get("channel", ""),
                thumbnail=thumbs[0] if thumbs else None,
                captions=captions if captions and captions.exists() else None,
                playlist=str(project.answer("playlist_name", "") or ""),
                made_for_kids=bool(project.answer("made_for_kids", False)),
                language=str(project.answer("yt_language_code", "en") or "en"),
                progress=lambda f, m: ctx.progress(f, m), note=lambda m: ctx.log(m))

        def done(update):
            if update.status == "done" and update.result:
                self.note(f"Published to {update.result['channel']} — {update.result['url']}")
                self.studio.toast(f"Published as {fields.get('privacy')} — "
                                  f"{update.result['url']}", "ok")

        self.studio.submit("Publish to YouTube", work, done)

    def confirm_publish(self) -> None:
        """Public is the one setting that cannot be taken back quietly."""
        if self.publish_fields.get("privacy") != "public":
            self.publish()
            return
        p = self.studio.palette
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Publish this publicly?", color=p.text),
            content=ft.Text(f"“{self.publish_fields.get('title', '')}” goes live on "
                            f"your channel as soon as YouTube finishes processing it.",
                            color=p.text_muted),
            bgcolor=p.surface,
            actions=[ft.TextButton("Cancel", on_click=lambda e: self.studio.page.close(dlg)),
                     ft.TextButton("Publish", on_click=lambda e: (
                         self.studio.page.close(dlg), self.publish()))])
        self.studio.page.open(dlg)


# ------------------------------------------------------------------ screen --

def build(studio) -> ft.Control:
    if studio.project is None:
        return _pick_a_project(studio)
    if studio.editor is None or studio.editor.project.id != studio.project.id:
        studio.editor = Editor(studio, studio.project)
    ed = studio.editor

    return ft.Column([
        _header(studio, ed),
        ft.Container(height=4),
        ft.Row([
            ft.Container(content=_clips(studio, ed), width=290),
            ft.Container(content=_stage(studio, ed), expand=True),
            ft.Container(content=_side(studio, ed), width=340),
        ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.START, expand=True),
    ], spacing=8, expand=True)


def _pick_a_project(studio) -> ft.Control:
    p = studio.palette
    projects = pj.all_projects()[:8]
    if not projects:
        return empty_state(p, ft.Icons.MOVIE_FILTER_ROUNDED, "Nothing to edit yet",
                           "Start a project first — the editor opens on whatever it has "
                           "produced: a render, or the narration and scenes it was built from.",
                           primary_button(p, "New project", lambda e: studio.navigate("home"),
                                          ft.Icons.ADD_ROUNDED))
    rows = [ft.Container(
        content=ft.Row([ft.Icon(ft.Icons.FOLDER_ROUNDED, size=15, color=p.text_muted),
                        ft.Text(x.name, size=13, color=p.text, expand=True),
                        ft.Text(x.kind, size=11, color=p.text_faint)], spacing=10),
        padding=ft.padding.symmetric(10, 12), border_radius=RADIUS_SM, bgcolor=p.surface_alt,
        on_click=lambda e, pid=x.id: studio.open_editor(pid), ink=True) for x in projects]
    return ft.Column([
        h2(p, "Open a project in the editor"),
        body(p, "The editor cuts what a project already has: a rendered episode, or the "
                "narration and scene art it was built from.", muted=True),
        card(p, *rows, gap=6),
    ], spacing=14)


def _header(studio, ed: Editor) -> ft.Control:
    p = studio.palette
    doc = ed.doc
    rendered = ed.out_path.exists()
    return ft.Row([
        ft.Container(content=ft.Icon(ft.Icons.VIDEO_SETTINGS_ROUNDED, size=20, color=p.video),
                     width=44, height=44, border_radius=13,
                     bgcolor=ft.Colors.with_opacity(0.14, p.video),
                     alignment=ft.alignment.center),
        ft.Column([
            ft.Text(f"{ed.project.name} — editor", size=20, weight=ft.FontWeight.W_600,
                    color=p.text),
            ft.Text(doc.summary() if doc.clips else "Empty timeline", size=12,
                    color=p.text_muted),
        ], spacing=2, tight=True, expand=True),
        pill(p, "rendered" if rendered else "not rendered",
             p.ok if rendered else p.text_faint),
        ghost_button(p, "The long edit" if ed.is_short else "The Short",
                     lambda e: ed.open_document(not ed.is_short),
                     ft.Icons.SWAP_HORIZ_ROUNDED,
                     disabled=not ed.is_short
                     and not (ed.project.dir / "edit" / "short.json").exists()),
        ghost_button(p, "Back to the pipeline", lambda e: studio.navigate("project"),
                     ft.Icons.ARROW_BACK_ROUNDED),
        ghost_button(p, "Save", lambda e: (ed.save(), studio.refresh()),
                     ft.Icons.SAVE_ROUNDED),
        primary_button(p, "Render", lambda e: ed.render(), ft.Icons.MOVIE_ROUNDED,
                       disabled=studio.busy or not doc.clips),
    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)


def _clips(studio, ed: Editor) -> ft.Control:
    p = studio.palette
    doc = ed.doc
    rows: list[ft.Control] = []
    for i, (start, clip) in enumerate(zip(doc.starts(), doc.clips)):
        selected = clip.id == ed.selected
        icon = (ft.Icons.IMAGE_ROUNDED if clip.kind == tl.IMAGE else
                ft.Icons.SQUARE_ROUNDED if clip.kind == tl.COLOUR else
                ft.Icons.MOVIE_ROUNDED)
        missing = clip.kind != tl.COLOUR and not clip.path(ed.project.dir).exists()
        marks = []
        if clip.transition != tl.CUT:
            marks.append(clip.transition)
        if abs(clip.speed - 1.0) > 1e-6:
            marks.append(f"{clip.speed:g}x")
        if clip.volume <= 0:
            marks.append("muted")
        rows.append(ft.Container(
            content=ft.Row([
                ft.Container(content=ft.Icon(icon, size=13,
                                             color=p.danger if missing else p.text_muted),
                             width=24, height=24, border_radius=8,
                             bgcolor=p.surface_alt, alignment=ft.alignment.center),
                ft.Column([
                    ft.Text(f"{i + 1}. {clip.label or Path(clip.source).name or clip.kind}",
                            size=12, color=p.danger if missing else p.text,
                            weight=ft.FontWeight.W_600 if selected else ft.FontWeight.W_500,
                            max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"{clock(start)} · {clip.length:.1f}s"
                            + (f" · {' · '.join(marks)}" if marks else ""),
                            size=10, color=p.text_faint),
                ], spacing=1, tight=True, expand=True),
            ], spacing=9),
            padding=ft.padding.symmetric(8, 10), border_radius=RADIUS_SM,
            bgcolor=p.surface_alt if selected else None,
            border=ft.border.all(1, p.accent if selected else "#00000000"),
            on_click=lambda e, cid=clip.id: ed.select(cid), ink=True))

    if not rows:
        rows = [body(p, "No clips yet. Assemble from the project, or add a file.",
                     muted=True, size=12)]

    return ft.Container(
        content=ft.Column([
            ft.Row([label(p, "Clips"), ft.Container(expand=True),
                    ft.Text(clock(ed.doc.duration), size=11, color=p.text_faint)]),
            ft.Row([
                ghost_button(p, "Assemble", lambda e: ed.assemble(),
                             ft.Icons.AUTO_AWESOME_ROUNDED, disabled=studio.busy),
                ghost_button(p, "Add file", lambda e: studio.browse_media(ed.add_media),
                             ft.Icons.ADD_ROUNDED),
            ], spacing=8),
            divider(p),
            ft.Column(rows, spacing=4, scroll=ft.ScrollMode.AUTO, expand=True),
        ], spacing=10, expand=True),
        bgcolor=p.surface, border_radius=RADIUS, border=ft.border.all(1, p.line),
        shadow=None if p.dark else shadow(p),
        padding=ft.padding.symmetric(12, 12), expand=True)


def _stage(studio, ed: Editor) -> ft.Control:
    p = studio.palette
    doc = ed.doc
    frame = ed.preview()
    picture: ft.Control
    if frame is not None and Path(frame).exists():
        picture = ft.Image(src=str(frame), fit=ft.ImageFit.CONTAIN,
                           border_radius=RADIUS_SM, expand=True)
    else:
        picture = ft.Container(
            content=ft.Column([
                ft.Icon(ft.Icons.MOVIE_FILTER_ROUNDED, size=30, color=p.text_faint),
                ft.Text("No preview" if edit_render.available()
                        else "ffmpeg is not installed, so there is no preview",
                        size=12, color=p.text_faint)],
                spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER),
            alignment=ft.alignment.center, expand=True)

    scrub = ft.Slider(
        min=0, max=max(doc.duration, 0.1), value=min(ed.playhead, max(doc.duration, 0.1)),
        divisions=max(int(doc.duration * 4), 1), active_color=p.accent,
        label="{value}s",
        on_change_end=lambda e: (setattr(ed, "playhead", float(e.control.value)),
                                 studio.refresh()))

    return ft.Column([
        ft.Container(content=picture, bgcolor="#000000", border_radius=RADIUS,
                     height=340, alignment=ft.alignment.center, padding=6),
        ft.Row([ft.Text(clock(ed.playhead), size=12, color=p.text_muted, width=54),
                ft.Container(content=scrub, expand=True),
                ft.Text(clock(doc.duration), size=12, color=p.text_faint, width=54)],
               spacing=6),
        _clip_panel(studio, ed),
    ], spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)


def _num_field(p, label_text: str, value: float, on_set, width: int = 108,
               helper: str = "") -> ft.Control:
    """A number you commit by leaving the box.

    Committing on every keystroke would re-render the timeline while you are
    still typing "1" of "12", so this waits for blur or Enter, and puts the old
    value back if what you typed is not a number.
    """
    def commit(e):
        try:
            on_set(float(e.control.value))
        except (TypeError, ValueError):
            e.control.value = f"{value:g}"
            try:
                e.control.update()
            except Exception:  # noqa: BLE001 - not mounted; the value is still right
                pass

    return ft.TextField(
        label=label_text, value=f"{value:g}", width=width, dense=True, text_size=13,
        border_radius=RADIUS_SM, filled=True, fill_color=p.surface_alt,
        border_color=p.line, focused_border_color=p.accent, color=p.text,
        label_style=ft.TextStyle(color=p.text_muted, size=12),
        helper_text=helper or None, helper_style=ft.TextStyle(color=p.text_faint, size=10),
        on_blur=commit, on_submit=commit,
        content_padding=ft.padding.symmetric(10, 12))


def _clip_panel(studio, ed: Editor) -> ft.Control:
    p = studio.palette
    clip = ed.clip
    if clip is None:
        return card(p, label(p, "Clip"),
                    body(p, "Select a clip to trim it.", muted=True, size=12))
    still = clip.kind in (tl.IMAGE, tl.COLOUR)

    trim_row = ft.Row([
        _num_field(p, "Holds for (s)" if still else "In point (s)",
                   clip.source_length if still else clip.source_in,
                   lambda v: ed.mutate(
                       (lambda: ed.doc.trim(clip.id, clip.source_in, clip.source_in + v))
                       if still else (lambda: ed.doc.trim(clip.id, v, None)))),
    ] + ([] if still else [
        _num_field(p, "Out point (s)", clip.source_out,
                   lambda v: ed.mutate(lambda: ed.doc.trim(clip.id, None, v))),
        _num_field(p, "Speed", clip.speed, lambda v: ed.set_prop("speed", v),
                   helper="0.25 – 4"),
        _num_field(p, "Volume", clip.volume, lambda v: ed.set_prop("volume", v),
                   helper="0 mutes it"),
    ]), spacing=10, wrap=True, run_spacing=10)

    transition = ft.Row([
        dropdown(p, "Enters on", list(tl.TRANSITIONS), clip.transition,
                 lambda e: ed.set_prop("transition", e.control.value), width=150),
        _num_field(p, "Over (s)", clip.transition_seconds,
                   lambda v: ed.set_prop("transition_seconds", v)),
        ft.Container(
            content=ft.Row([ft.Switch(value=clip.ken_burns, active_color=p.accent,
                                      on_change=lambda e: ed.set_prop("ken_burns",
                                                                      e.control.value)),
                            ft.Text("Slow push", size=12, color=p.text)], spacing=6),
            visible=clip.kind == tl.IMAGE, padding=ft.padding.only(top=6)),
    ], spacing=10, wrap=True, run_spacing=10)

    grade = ft.Row([
        _num_field(p, "Brightness", clip.brightness,
                   lambda v: ed.set_prop("brightness", v), helper="-1 – 1"),
        _num_field(p, "Contrast", clip.contrast, lambda v: ed.set_prop("contrast", v),
                   helper="0 – 3"),
        _num_field(p, "Saturation", clip.saturation,
                   lambda v: ed.set_prop("saturation", v), helper="0 – 3"),
    ], spacing=10, wrap=True, run_spacing=10)

    actions = ft.Row([
        ghost_button(p, "Split here", lambda e: ed.split_here(), ft.Icons.CONTENT_CUT_ROUNDED),
        ghost_button(p, "Duplicate", lambda e: ed.mutate(
            lambda: ed.doc.duplicate(clip.id), "Duplicated a clip"),
            ft.Icons.COPY_ROUNDED),
        ghost_button(p, "Up", lambda e: ed.nudge(-1), ft.Icons.ARROW_UPWARD_ROUNDED),
        ghost_button(p, "Down", lambda e: ed.nudge(1), ft.Icons.ARROW_DOWNWARD_ROUNDED),
        ghost_button(p, "Delete", lambda e: ed.mutate(
            lambda: ed.doc.remove(clip.id), "Removed a clip"),
            ft.Icons.DELETE_OUTLINE_ROUNDED, danger=True),
    ], spacing=8, wrap=True, run_spacing=8)

    return card(
        p,
        ft.Row([label(p, "Clip"), ft.Container(expand=True),
                ft.Text(Path(clip.source).name or clip.kind, size=11, color=p.text_faint)]),
        trim_row, transition, grade, divider(p), actions)


def _side(studio, ed: Editor) -> ft.Control:
    p = studio.palette
    doc = ed.doc

    brief_box = ft.TextField(
        label="Tell the editor what to change", value=ed.brief, multiline=True,
        min_lines=3, max_lines=5, text_size=13, border_radius=RADIUS_SM, filled=True,
        fill_color=p.surface_alt, border_color=p.line, focused_border_color=p.accent,
        color=p.text, label_style=ft.TextStyle(color=p.text_muted, size=12),
        hint_text="Lose the first eight seconds, dissolve between the scenes, "
                  "and put a title on the hook.",
        on_change=lambda e: setattr(ed, "brief", e.control.value))

    ai_card = card(
        p, label(p, "AI editor"), brief_box,
        primary_button(p, "Apply the edit", lambda e: ed.ai_edit(),
                       ft.Icons.AUTO_FIX_HIGH_ROUNDED, disabled=studio.busy, expand=True),
        ft.Row([ghost_button(p, name, lambda e, b=prompt: ed.ai_edit(b))
                for name, prompt in QUICK_EDITS], spacing=6, wrap=True, run_spacing=6),
        ft.Row([
            ghost_button(p, "Cut dead air", lambda e: ed.cut_dead_air(),
                         ft.Icons.CONTENT_CUT_ROUNDED, disabled=studio.busy),
            ghost_button(p, "Make a Short", lambda e: ed.make_short(),
                         ft.Icons.SMARTPHONE_ROUNDED),
        ], spacing=6, wrap=True, run_spacing=6))

    titles: list[ft.Control] = []
    for i, overlay in enumerate(doc.overlays):
        titles.append(ft.Row([
            ft.Column([ft.Text(overlay.text[:44] or "(empty)", size=12, color=p.text,
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                       ft.Text(f"{clock(overlay.start)} for {overlay.seconds:.1f}s · "
                               f"{overlay.position.replace('_', ' ')}",
                               size=10, color=p.text_faint)], spacing=1, tight=True, expand=True),
            ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_size=14, icon_color=p.text_faint,
                          on_click=lambda e, oid=overlay.id: ed.mutate(
                              lambda: ed.doc.remove_text(oid), "Removed a title")),
        ], spacing=6))
    new_title = ft.TextField(
        label="New title at the playhead", dense=True, text_size=13, border_radius=RADIUS_SM,
        filled=True, fill_color=p.surface_alt, border_color=p.line,
        focused_border_color=p.accent, color=p.text,
        label_style=ft.TextStyle(color=p.text_muted, size=12),
        on_submit=lambda e: (ed.mutate(
            lambda: ed.doc.add_text(tl.Text(text=e.control.value, start=ed.playhead,
                                            seconds=3.0)),
            f"Title at {clock(ed.playhead)}") if e.control.value.strip() else None))
    titles_card = card(p, label(p, "Titles"), *(titles or [
        body(p, "None yet. The AI adds them, or type one below.", muted=True, size=12)]),
        new_title, gap=8)

    music = doc.music
    output_card = card(
        p, label(p, "Output"),
        dropdown(p, "Canvas", list(CANVASES),
                 "Portrait 1080×1920" if doc.portrait else "Landscape 1920×1080",
                 lambda e: ed.set_canvas(e.control.value)),
        ft.Row([
            _num_field(p, "Frame rate", doc.fps, lambda v: (
                setattr(doc, "fps", max(int(v), 1)), ed.save(quiet=True), studio.refresh())),
            _num_field(p, "Fade out (s)", doc.fade_out_seconds, lambda v: (
                setattr(doc, "fade_out_seconds", max(v, 0.0)), ed.save(quiet=True),
                studio.refresh())),
        ], spacing=10),
        ft.Row([
            ft.Text(f"Music: {Path(music.source).name if music else 'none'}", size=12,
                    color=p.text_muted, expand=True),
            ghost_button(p, "Choose", lambda e: studio.browse_media(ed.add_media),
                         ft.Icons.MUSIC_NOTE_ROUNDED),
        ], spacing=8),
        ft.Row([
            ft.Text(f"Subtitles: {Path(doc.captions).name if doc.captions else 'none'}",
                    size=12, color=p.text_muted, expand=True),
            ghost_button(p, "Clear" if doc.captions else "None",
                         lambda e: ed.mutate(lambda: setattr(doc, "captions", ""),
                                             "Subtitles off"),
                         ft.Icons.SUBTITLES_OFF_ROUNDED, disabled=not doc.captions),
        ], spacing=8))

    problems = doc.problems(ed.project.dir)
    problem_card = ft.Container(
        content=card(p, label(p, "Before you render"),
                     *[ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=14, color=p.warn),
                               ft.Text(x, size=11, color=p.text_muted, expand=True)], spacing=7)
                       for x in problems], gap=6),
        visible=bool(problems))

    log_card = ft.Container(
        content=card(p, label(p, "Activity"),
                     ft.Column([ft.Text(line, size=11, color=p.text_muted, selectable=True)
                                for line in ed.log[-12:]], spacing=3, tight=True)),
        visible=bool(ed.log))

    return ft.Column([ai_card, titles_card, _publish_card(studio, ed), output_card,
                      problem_card, log_card],
                     spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)


def _publish_card(studio, ed: Editor) -> ft.Control:
    p = studio.palette
    fields = ed.publish_fields
    rendered = ed.out_path.exists()
    channels = yt.channel_choices() or ["(no channel connected yet)"]

    def setter(key):
        return lambda e: fields.__setitem__(key, e.control.value)

    return card(
        p, label(p, "Publish to YouTube"),
        text_field(p, "Title", str(fields.get("title", "")), on_change=setter("title")),
        text_field(p, "Description", str(fields.get("description", "")), multiline=True,
                   on_change=setter("description")),
        ft.Row([
            dropdown(p, "Visibility", PRIVACY, str(fields.get("privacy", "private")),
                     setter("privacy"), width=140),
            ft.Container(content=dropdown(p, "Channel", channels,
                                          str(fields.get("channel", "")) or channels[0],
                                          setter("channel")), expand=True),
        ], spacing=10),
        primary_button(
            p, "Publish this edit", lambda e: ed.confirm_publish(),
            ft.Icons.CLOUD_UPLOAD_ROUNDED,
            disabled=studio.busy or not rendered, expand=True),
        body(p, "Render the edit first — this uploads the rendered file." if not rendered
             else "Uploads the rendered edit, sets the thumbnail and subtitles, and "
                  "reports the link.", muted=True, size=11))
