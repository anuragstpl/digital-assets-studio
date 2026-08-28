"""The application shell: navigation, state, and every action the views trigger."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import webbrowser
from pathlib import Path

import flet as ft

from ..config import APP_NAME, APP_VERSION, WORKSPACE
from ..core import pipeline as pipe
from ..core import projects as pj
from ..core import telemetry
from ..core.events import BUS, TOPIC_LOG, TOPIC_STEP
from ..core.jobs import RUNNER
from ..core.settings import load as load_settings, save as save_settings
from ..pipelines import PIPELINES, get as get_pipeline
from ..theme import RADIUS, RADIUS_SM, build_theme, palette
from .components import primary_button, snack
from .views import activity, home, new_project, pipeline_view, projects_view, settings_view

log = logging.getLogger(__name__)

# tests set this so a broken screen fails loudly instead of showing a fallback
STRICT_RENDER = bool(os.environ.get("DAS_STRICT_RENDER"))

NAV = [
    ("home", ft.Icons.GRID_VIEW_ROUNDED, "Home"),
    ("projects", ft.Icons.FOLDER_ROUNDED, "Projects"),
    ("activity", ft.Icons.RECEIPT_LONG_ROUNDED, "Activity"),
    ("settings", ft.Icons.TUNE_ROUNDED, "Settings"),
]


class Studio:
    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.settings = load_settings()
        self.route = "home"
        self.project: pj.Project | None = None
        self.pipeline = None
        self.selected_step: str | None = None
        self.draft: dict = {}
        self.step_log: list[str] = []
        self.log_lines: list[str] = []
        self.busy = False
        self.settings_tab = 0
        self.workspace = WORKSPACE
        # live handles into the project screen, so one action redraws one pane
        self.pane_header: ft.Container | None = None
        self.pane_list: ft.Container | None = None
        self.pane_detail: ft.Container | None = None
        self.log_column: ft.Column | None = None
        self.step_rows: dict[str, dict] = {}
        self._body = ft.Container(expand=True)
        # one picker for the whole app; the callback is swapped per use, because
        # Flet delivers the result to whichever handler the control carries
        self._picker: ft.FilePicker | None = None
        self._picker_cb = None
        # true only while a multi-step run is driving the screen, so following
        # the run never fights a step you picked yourself
        self._following = False
        BUS.subscribe(TOPIC_LOG, self._on_log)
        BUS.subscribe(TOPIC_STEP, self._on_step)

    # -------------------------------------------------------------- palette --
    @property
    def palette(self):
        return palette(self.settings.dark_mode)

    # ------------------------------------------------------------ log sink --
    def _on_log(self, payload) -> None:
        if not isinstance(payload, dict):
            return
        line = f"[{payload.get('job','')}] {payload.get('message','')}"
        self.log_lines.append(line)
        message = payload.get("message", "")
        self.step_log.append(message)
        col = self.log_column
        if col is not None:
            try:
                col.controls.append(ft.Text(
                    message, size=11, selectable=True,
                    color=self.palette.danger if payload.get("level") == "error"
                    else self.palette.text_muted))
                del col.controls[:-40]
                if col.parent is not None:
                    col.parent.visible = True
            except Exception:  # noqa: BLE001
                pass
        self._safe_update()

    # -------------------------------------------------------- step progress --
    def _on_step(self, payload) -> None:
        """Repaint as each step starts and finishes.

        Steps run on a worker thread and the screen has no other way to know one
        moved: without this the list sits on its old statuses for the whole run
        and only catches up when the job ends, which reads as nothing happening.
        A run that is under way also drags the selection along with it, so the
        step you are looking at is the one being worked on.
        """
        if not isinstance(payload, dict) or self.route != "project":
            return
        if self.project is None or payload.get("project") != self.project.id:
            return
        step_id = payload.get("step") or ""
        if self._following and payload.get("status") == pj.RUNNING and step_id:
            self.selected_step = step_id
            self.log_column = None      # the detail pane is about to be rebuilt
        try:
            self.update_panes(header=True)
        except Exception:  # noqa: BLE001 - a repaint must never kill the run
            log.exception("live step repaint failed")

    # ----------------------------------------------------------- navigation --
    def navigate(self, route: str) -> None:
        self.route = route
        self.step_log = []
        self.refresh()

    def start_new(self, pipeline_id: str) -> None:
        self.pipeline = get_pipeline(pipeline_id)
        self.draft = {}
        self.navigate("new")

    def create_project(self, name: str, pipeline_id: str, answers: dict) -> None:
        # which kinds of asset people actually start; no name, no answers
        telemetry.track("project_created", {"kind": pipeline_id})
        proj = pj.create(name, pipeline_id, answers)
        proj.ensure_dirs()
        self.open_project(proj.id)
        self.toast(f"Created “{proj.name}”. Press Run everything to get going.", "ok")

    def open_project(self, project_id: str) -> None:
        self.step_rows = {}
        proj = pj.load(project_id)
        if proj is None:
            self.toast("That project could not be read.", "error")
            return
        self.project = proj
        self.pipeline = get_pipeline(proj.kind)
        nxt = self.pipeline.next_step(proj) if self.pipeline else None
        self.selected_step = nxt.id if nxt else (self.pipeline.steps[0].id if self.pipeline else None)
        self.step_log = []
        self.navigate("project")

    def select_step(self, step_id: str) -> None:
        self.selected_step = step_id
        self.step_log = []
        self.log_column = None
        self.update_panes()

    def update_panes(self, header: bool = False) -> None:
        """Redraw only the project screen's panes.

        A full refresh() rebuilds the page and throws your scroll position back
        to the top, which is maddening on a twenty-step pipeline."""
        if self.route != "project" or self.pane_detail is None:
            self.refresh()
            return
        try:
            self.pane_detail.content = pipeline_view.step_detail(self)
            # rows are restyled, never rebuilt - rebuilding resets the list's scroll.
            # The exception is a run that changed which steps apply at all: those
            # rows do not exist yet, so there is nothing to restyle.
            if self.pipeline is not None and self.project is not None and set(
                    self.step_rows or {}) != {s.id for s in
                                              self.pipeline.active_steps(self.project)}:
                if self.pane_list is not None:
                    self.pane_list.content = pipeline_view.step_list(self)
            else:
                pipeline_view.refresh_rows(self)
            if header and self.pane_header is not None:
                self.pane_header.content = pipeline_view.header(self)
            self._safe_update()
        except Exception:  # noqa: BLE001
            log.exception("pane refresh failed")
            self.refresh()

    def set_settings_tab(self, index: int) -> None:
        self.settings_tab = index

    # -------------------------------------------------------------- actions --
    def set_answer(self, key: str, value) -> None:
        if self.project is None:
            return
        self.project.set_answer(key, value)
        self.project.save()

    # ------------------------------------------------------- file browsing --
    def _ensure_picker(self) -> "ft.FilePicker | None":
        """Mount the OS file dialog once, lazily.

        It lives in page.overlay rather than in the form, so it survives every
        pane redraw - a picker rebuilt underneath an open dialog never returns.
        Headless test pages have no overlay; those get a typeable box instead.
        """
        if self._picker is not None:
            return self._picker
        try:
            picker = ft.FilePicker(on_result=self._on_picked)
            self.page.overlay.append(picker)
            self.page.update()
        except Exception:  # noqa: BLE001
            log.exception("no file picker available on this page")
            return None
        self._picker = picker
        return picker

    def _on_picked(self, e) -> None:
        cb, self._picker_cb = self._picker_cb, None
        if cb is None:
            return
        path = ""
        files = getattr(e, "files", None)
        if files:
            path = getattr(files[0], "path", "") or ""
        else:
            path = getattr(e, "path", "") or ""
        if not path:
            return          # cancelled
        try:
            cb(path)
        except Exception:  # noqa: BLE001
            log.exception("file picker callback failed")
        self._safe_update()

    def browse_for(self, field, on_pick) -> None:
        """Open the dialog for one file/folder field and hand the path back."""
        picker = self._ensure_picker()
        if picker is None:
            self.toast("This build cannot open a file dialog — type or paste the path instead.",
                       "warn")
            return
        self._picker_cb = on_pick
        try:
            if field.type == "folder":
                picker.get_directory_path(dialog_title=f"Choose a folder for {field.label}")
            else:
                picker.pick_files(
                    dialog_title=f"Choose {field.label}", allow_multiple=False,
                    allowed_extensions=list(field.extensions) or None)
        except Exception as exc:  # noqa: BLE001
            self._picker_cb = None
            self.toast(f"Could not open the file dialog: {exc}", "error")

    def toggle_check(self, step_id: str, item: str, on: bool) -> None:
        if self.project is None:
            return
        st = self.project.state(step_id)
        if on and item not in st.checked:
            st.checked.append(item)
        elif not on and item in st.checked:
            st.checked.remove(item)
        self.project.save()

    def submit(self, name: str, work, on_done=None, rerender: bool = True) -> None:
        """Run something off the UI thread.

        rerender=False keeps the current screen exactly where it is - no rebuild,
        so a long form does not jump back to the top while a background check runs.
        Callbacks that need to redraw can call refresh() themselves.
        """
        self.busy = True
        if not rerender:
            self._safe_update()
        elif self.route == "project" and self.pane_detail is not None:
            self.update_panes(header=True)
        else:
            self.refresh()

        def finish(update):
            self.busy = False
            if on_done:
                try:
                    on_done(update)
                except Exception:  # noqa: BLE001
                    log.exception("callback failed")
            if self.project:
                reloaded = pj.load(self.project.id)
                if reloaded:
                    self.project = reloaded
            if not rerender:
                self._safe_update()
            elif self.route == "project" and self.pane_detail is not None:
                self.update_panes(header=True)
            else:
                self.refresh()

        RUNNER.submit(name, work, finish)

    def _safe_update(self) -> None:
        try:
            self.page.update()
        except Exception:  # noqa: BLE001
            pass

    def run_step(self, step_id: str) -> None:
        if self.project is None or self.pipeline is None:
            return
        step = self.pipeline.step(step_id)
        if step is None:
            return
        self.step_log = []
        self.log_column = None
        pl, proj = self.pipeline, self.project

        def work(ctx):
            return pipe.execute(pl, proj, step, ctx)

        def done(update):
            if update.status == "done":
                self.toast(update.result.message or f"{step.title} finished", "ok")
            elif update.status == "failed":
                self.toast(update.message, "error")

        self.submit(step.title, work, done)

    def run_all(self) -> None:
        self._run_pipeline(autopilot=False, label="Run what can run")

    def autopilot(self) -> None:
        """Go as far as one press possibly can: every automated step, plus the
        review gates answered from what the models wrote."""
        self._run_pipeline(autopilot=True, label="Autopilot")

    def _run_pipeline(self, autopilot: bool, label: str) -> None:
        if self.project is None or self.pipeline is None:
            return
        self.step_log = []
        self.log_column = None
        self._following = True
        pl, proj = self.pipeline, self.project

        def work(ctx):
            ctx.log(f"{label} started on “{proj.name}”")
            return pipe.run_all(pl, proj, ctx, autopilot=autopilot,
                                include_optional=autopilot)

        def done(update):
            self._following = False
            if update.status != "done":
                self.toast(update.message, "error")
                return
            summary = update.result or {}
            gate = summary.get("waiting_on")
            ran, approved = len(summary.get("ran", [])), len(summary.get("approved", []))
            failed = summary.get("failed", [])
            parts = [f"{ran} step(s) run"]
            if approved:
                parts.append(f"{approved} gate(s) approved")
            if failed:
                parts.append(f"{len(failed)} failed")
            if gate is not None:
                self.selected_step = gate.id
                tail = f" Stopped at: {gate.title}"
                if summary.get("stopped_because", "").startswith("needs"):
                    tail += f" — {summary['stopped_because']}"
            else:
                tail = " Nothing left that software can do."
            self.notify(f"{label}: {', '.join(parts)}.{tail}",
                        "error" if failed else "ok")

        self.submit(label, work, done)

    def notify(self, message: str, kind: str = "info") -> None:
        """Toast, plus a line in Activity, so a run that finished while you were
        away is still there when you get back."""
        self.log_lines.append(f"[done] {message}")
        self.toast(message, kind)

    def mark_done(self, step_id: str) -> None:
        if self.project is None or self.pipeline is None:
            return
        step = self.pipeline.step(step_id)
        if step is None:
            return
        pipe.mark_manual_done(self.project, step)
        nxt = self.pipeline.next_step(self.project)
        if nxt:
            self.selected_step = nxt.id
        self.update_panes(header=True)

    def reset_step(self, step_id: str) -> None:
        if self.project is None or self.pipeline is None:
            return
        step = self.pipeline.step(step_id)
        if step:
            pipe.reset_step(self.project, step)
        self.update_panes(header=True)

    def skip_step(self, step_id: str) -> None:
        if self.project is None or self.pipeline is None:
            return
        step = self.pipeline.step(step_id)
        if step:
            pipe.skip_step(self.project, step)
        self.update_panes(header=True)

    def confirm_delete(self, project_id: str, name: str) -> None:
        p = self.palette

        def do(e):
            pj.delete(project_id)
            if self.project and self.project.id == project_id:
                self.project = None
                self.route = "projects"
            self.page.close(dlg)
            self.toast(f"Deleted “{name}”", "ok")
            self.refresh()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Delete “{name}”?", color=p.text),
            content=ft.Text("The whole project folder goes with it — drafts, builds, everything. "
                            "This cannot be undone.", color=p.text_muted),
            bgcolor=p.surface,
            actions=[ft.TextButton("Cancel", on_click=lambda e: self.page.close(dlg)),
                     ft.TextButton("Delete", on_click=do,
                                   style=ft.ButtonStyle(color=p.danger))],
        )
        self.page.open(dlg)

    def after_connect(self, update, what: str) -> None:
        if update.status == "done":
            self.toast(f"{what} connected", "ok")
        else:
            self.toast(update.message, "error")

    def set_dark(self, on: bool) -> None:
        self.settings.dark_mode = on
        save_settings(self.settings)
        self.apply_theme()
        self.refresh()

    # ------------------------------------------------------------- helpers --
    def toast(self, message: str, kind: str = "info") -> None:
        snack(self.page, self.palette, message, kind)

    def open_url(self, url: str) -> None:
        try:
            self.page.launch_url(url)
        except Exception:  # noqa: BLE001
            webbrowser.open(url)

    def open_path(self, path: str) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Could not open it: {exc}", "error")

    def reveal(self, folder: str) -> None:
        Path(folder).mkdir(parents=True, exist_ok=True)
        self.open_path(folder)

    # ----------------------------------------------------------- rendering --
    def apply_theme(self) -> None:
        p = self.palette
        self.page.theme = build_theme(p)
        self.page.dark_theme = build_theme(p)
        self.page.theme_mode = ft.ThemeMode.DARK if self.settings.dark_mode else ft.ThemeMode.LIGHT
        self.page.bgcolor = p.canvas

    def _sidebar(self) -> ft.Control:
        p = self.palette
        items: list[ft.Control] = [
            ft.Container(
                content=ft.Row([
                    ft.Container(content=ft.Text("D", size=17, weight=ft.FontWeight.W_700,
                                                 color="#FFFFFF"),
                                 width=34, height=34, border_radius=11,
                                 gradient=ft.LinearGradient(
                                     begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
                                     colors=[p.accent_grad_a, p.accent_grad_b]),
                                 alignment=ft.alignment.center),
                    ft.Column([
                        ft.Text("Digital Assets", size=14, weight=ft.FontWeight.W_700,
                                color=p.text),
                        ft.Text("Studio", size=11, color=p.text_faint),
                    ], spacing=0, tight=True),
                ], spacing=11),
                padding=ft.padding.only(left=6, top=6, bottom=18)),
        ]
        for route, icon, title in NAV:
            active = self.route == route or (route == "projects" and self.route == "project") \
                or (route == "home" and self.route == "new")
            items.append(ft.Container(
                content=ft.Row([
                    ft.Icon(icon, size=17, color=p.accent if active else p.text_faint),
                    ft.Text(title, size=13, weight=ft.FontWeight.W_600 if active else ft.FontWeight.W_500,
                            color=p.text if active else p.text_muted),
                ], spacing=11),
                padding=ft.padding.symmetric(10, 12), border_radius=RADIUS_SM,
                bgcolor=p.surface_alt if active else None,
                on_click=lambda e, r=route: self.navigate(r), ink=True))

        items.append(ft.Container(expand=True))
        if self.busy:
            items.append(ft.Container(
                content=ft.Row([ft.ProgressRing(width=14, height=14, stroke_width=2, color=p.accent),
                                ft.Text("Working…", size=12, color=p.text_muted)], spacing=9),
                padding=ft.padding.symmetric(8, 12)))
        items.append(ft.Container(
            content=ft.Text(f"v{APP_VERSION}", size=10, color=p.text_faint),
            padding=ft.padding.only(left=12, bottom=4)))

        return ft.Container(
            content=ft.Column(items, spacing=3, expand=True),
            width=204, padding=ft.padding.symmetric(14, 12),
            bgcolor=p.sidebar, border=ft.border.only(right=ft.BorderSide(1, p.line)))

    def _view(self) -> ft.Control:
        if self.route == "home":
            return home.build(self)
        if self.route == "projects":
            return projects_view.build(self)
        if self.route == "new":
            return new_project.build(self)
        if self.route == "project":
            return pipeline_view.build(self)
        if self.route == "settings":
            return settings_view.build(self)
        if self.route == "activity":
            return activity.build(self)
        return home.build(self)

    def refresh(self) -> None:
        p = self.palette
        try:
            self._body.content = ft.Container(content=self._view(),
                                              padding=ft.padding.symmetric(24, 30), expand=True)
            self._body.bgcolor = p.canvas
            self.page.controls.clear()
            self.page.add(ft.Row([self._sidebar(), self._body], spacing=0, expand=True))
            self.page.update()
        except Exception as exc:  # noqa: BLE001
            log.exception("render failed")
            if STRICT_RENDER:
                raise
            self.page.controls.clear()
            self.page.add(ft.Container(
                content=ft.Column([
                    ft.Text("This screen failed to draw", size=18,
                            weight=ft.FontWeight.W_600, color=p.danger),
                    ft.Text(str(exc), size=12, color=p.text_muted, selectable=True),
                    ft.Text("The full traceback is in studio.log. Everything else still works.",
                            size=12, color=p.text_faint),
                    ft.TextButton("Back to home", on_click=lambda e: self.navigate("home")),
                ], spacing=10),
                padding=40))
            self.page.update()

    def mount(self) -> None:
        self.apply_theme()
        self.refresh()
