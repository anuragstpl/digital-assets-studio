"""The editing intelligence: what to cut, what to hold, what to caption.

Two halves, deliberately kept apart.

The *mechanical* half - assembling a first edit from what a project already has
on disk, cutting dead air out of a take, cropping a Short out of a long video -
is arithmetic. It runs with no model, no key and no network, and it is the half
that has to be right.

The *judgement* half asks a model for an opinion and gets back a list of
operations, never a rewritten document. That is the whole safety property here:
a model can only ask for edits this file already knows how to make, every one is
validated against the timeline before it is applied, and anything it invents is
rejected with a reason rather than quietly corrupting the edit.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from ...config import ROLE_EDITING, ROLE_SCRIPT
from ..llm import router
from . import analyze
from . import timeline as tl

log = logging.getLogger(__name__)


class EditorError(RuntimeError):
    pass


# The vocabulary a model is allowed to speak. Kept as one string so the prompt
# and the validator can never drift apart.
OPS_HELP = """Each operation is an object with an "op" key. Nothing else is understood.

  {"op":"trim","clip":<id or index>,"start":<seconds into the source>,"end":<seconds>}
  {"op":"hold","clip":<ref>,"seconds":<how long a still stays on screen>}
  {"op":"remove","clip":<ref>}
  {"op":"reorder","clip":<ref>,"to":<new index>}
  {"op":"split","clip":<ref>,"at":<seconds on the timeline>}
  {"op":"speed","clip":<ref>,"factor":0.25 to 4}
  {"op":"volume","clip":<ref>,"level":0 to 4}
  {"op":"transition","clip":<ref>,"style":"cut|fade|dissolve|slide","seconds":0.1 to 3}
  {"op":"grade","clip":<ref>,"brightness":-1 to 1,"contrast":0 to 3,"saturation":0 to 3}
  {"op":"title","text":"...","start":<seconds>,"seconds":<how long>,"position":"top|middle|lower_third|bottom"}
  {"op":"remove_title","index":<n>}
  {"op":"captions","source":"<path to an .srt, relative to the project>"}
  {"op":"music","source":"<path to an audio file>","gain_db":-40 to 0}
  {"op":"fade_out","seconds":<fade to black at the end>}
  {"op":"note","text":"one line on what you changed and why"}"""

SYSTEM = """You are a video editor cutting for YouTube retention. Hold a shot only as
long as it earns; cut on the beat of the narration, never mid-word. Titles are for
the two or three moments a viewer would otherwise scrub past - a title on every
scene reads as noise. Never invent a file that is not listed. Never write a title
that states something the narration does not say."""


# --------------------------------------------------------------- discovery --

def sources(project, slug: str = "") -> dict:
    """Everything in a project that could go into an edit.

    Deliberately tolerant: a project part-way through its pipeline has some of
    these and not others, and the editor has to open on whatever is there.
    """
    slug = slug or str(project.answer("episode_slug", "") or "")
    build = project.dir / "build"
    if not slug:
        newest = sorted(build.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
        slug = newest[0].stem if newest else ""

    voice_dir = build / "voice" / slug
    out = {
        "slug": slug,
        "voice": sorted(voice_dir.glob("scene_*.mp3")) if voice_dir.exists() else [],
        "images": sorted((build / "scenes" / slug).glob("*.jpg"))
        if (build / "scenes" / slug).exists() else [],
        "footage": [],
        "rendered": build / f"{slug}.mp4" if (build / f"{slug}.mp4").exists() else None,
        "srt": build / "voice" / f"{slug}.srt"
        if (build / "voice" / f"{slug}.srt").exists() else None,
        "timings": build / "voice" / f"{slug}.timings.json"
        if (build / "voice" / f"{slug}.timings.json").exists() else None,
    }
    for folder in (build / "stock" / slug, build / "aiclips" / slug):
        if folder.exists():
            out["footage"] += sorted(f for f in folder.iterdir()
                                     if f.suffix.lower() in tl.VIDEO_SUFFIXES)
    return out


def _seconds(path: Path, fallback: float = 4.0) -> float:
    measured = analyze.duration(path)
    return round(measured, 3) if measured > 0 else fallback


def _timings(project, slug: str) -> list[float]:
    """Narration lengths as the voiceover step recorded them.

    Reading these beats probing every mp3 again, and it is the only way to lay
    out a timeline correctly on a machine with no ffprobe."""
    raw = project.read_text(f"build/voice/{slug}.timings.json", "")
    if not raw:
        return []
    try:
        return [float(x.get("seconds") or 0) for x in json.loads(raw)]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- assembly --

def assemble(project, slug: str = "", portrait: bool | None = None,
             note=None) -> tl.Timeline:
    """A first edit, built from whatever the project has produced so far.

    Per-scene narration with per-scene visuals becomes a scene-per-clip cut with
    the voice laid on top. A project that only has a finished render becomes a
    single clip - which is still worth having, because everything the editor can
    do to it (trim the slow open, add a title, cut the dead air, lay music) is
    exactly what that render needs.
    """
    found = sources(project, slug)
    slug = found["slug"] or project.id
    if portrait is None:
        portrait = str(project.answer("orientation", "Landscape 16:9")) == "Portrait 9:16"
    width, height = tl.PORTRAIT if portrait else tl.LANDSCAPE
    doc = tl.Timeline(name=slug, width=width, height=height)

    voice, images, footage = found["voice"], found["images"], found["footage"]
    if voice and (images or footage):
        lengths = _timings(project, slug)
        at = 0.0
        for i, audio in enumerate(voice):
            seconds = lengths[i] if i < len(lengths) and lengths[i] > 0 else _seconds(audio)
            if images:
                visual = images[min(i, len(images) - 1)]
            else:
                visual = footage[i % len(footage)]
            kind = tl.kind_for(visual)
            clip = tl.Clip(
                source=project.rel(visual), kind=kind,
                source_in=0.0, source_out=seconds,
                # footage under narration is silent by design: two voices at once
                # is the fastest way to make a video unwatchable
                volume=0.0, ken_burns=kind == tl.IMAGE,
                label=f"Scene {i + 1}")
            if kind == tl.VIDEO:
                available = _seconds(visual, seconds)
                clip.source_out = min(seconds, available) if available > 0 else seconds
                if clip.source_length < seconds:
                    # the clip is shorter than its narration: slow it rather than
                    # cutting the sentence off
                    clip.speed = tl.clamp(clip.source_length / seconds, tl.MIN_SPEED, 1.0)
            doc.add(clip)
            doc.audio.append(tl.Audio(source=project.rel(audio), role=tl.VOICE,
                                      start=round(at, 3), gain_db=0.0,
                                      fade_in=0.0, fade_out=0.0, loop=False).sanitised())
            at += doc.clips[-1].length
    elif found["rendered"] is not None:
        rendered = found["rendered"]
        length = _seconds(rendered, 60.0)
        doc.add(tl.Clip(source=project.rel(rendered), kind=tl.VIDEO,
                        source_in=0.0, source_out=length, volume=1.0,
                        label="Rendered episode"))
        probed = analyze.probe(rendered)
        if probed.width and probed.height:
            doc.width, doc.height = probed.width, probed.height
    else:
        raise EditorError(
            "There is nothing to edit yet. Run the voiceover and the scene art (or the "
            "footage step), or render the video first — then the editor has something "
            "to cut.")

    if found["srt"] is not None:
        doc.captions = project.rel(found["srt"])
    music = str(project.answer("music_path", "") or "").strip()
    if music and Path(music).expanduser().exists():
        doc.set_music(music, gain_db=-22.0)
    doc.fade_out_seconds = 1.0

    if note:
        note(f"Assembled {len(doc.clips)} clips — {doc.summary()}")
    return doc


# --------------------------------------------------------------- describing --

def describe(doc: tl.Timeline, base: Path | None = None) -> str:
    """The timeline as a model should see it: short, numbered, in seconds."""
    lines = [f"Canvas {doc.width}x{doc.height} at {doc.fps}fps, "
             f"total {doc.duration:.1f}s, {len(doc.clips)} clips."]
    for i, (start, clip) in enumerate(zip(doc.starts(), doc.clips)):
        name = Path(clip.source).name or clip.kind
        extra = []
        if abs(clip.speed - 1.0) > 1e-6:
            extra.append(f"{clip.speed:g}x")
        if clip.volume <= 0:
            extra.append("muted")
        if clip.transition != tl.CUT:
            extra.append(f"{clip.transition} in")
        lines.append(
            f"[{i}] id={clip.id} {name} kind={clip.kind} "
            f"timeline {start:.1f}s→{start + clip.length:.1f}s "
            f"source {clip.source_in:.1f}s→{clip.source_out:.1f}s"
            + (f" ({', '.join(extra)})" if extra else "")
            + (f" — {clip.label}" if clip.label else ""))
    for i, o in enumerate(doc.overlays):
        lines.append(f"title[{i}] {o.start:.1f}s for {o.seconds:.1f}s "
                     f"{o.position}: {o.text[:70]}")
    if doc.music is not None:
        lines.append(f"music {doc.music.source} at {doc.music.gain_db:.0f} dB")
    if doc.captions:
        lines.append(f"burned subtitles from {doc.captions}")
    return "\n".join(lines)


# ------------------------------------------------------------- applying ops --

def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def apply_ops(doc: tl.Timeline, ops, base: Path | None = None) -> tuple[list[str], list[str]]:
    """Apply a list of operations, reporting what took and what did not.

    Nothing raises. A model that asks for something impossible - a clip that does
    not exist, a negative speed, a file that is not there - should cost you one
    rejected line, not the edit you had.
    """
    applied: list[str] = []
    rejected: list[str] = []
    if isinstance(ops, dict):
        ops = ops.get("operations") or ops.get("ops") or []
    if not isinstance(ops, list):
        return applied, ["The edit plan was not a list of operations."]

    for raw in ops:
        if not isinstance(raw, dict):
            rejected.append(f"Not an operation: {str(raw)[:60]}")
            continue
        op = str(raw.get("op", "")).strip().lower()
        ref = raw.get("clip", raw.get("index"))
        clip = doc.clip(ref) if ref is not None else None
        needs_clip = op in ("trim", "hold", "remove", "reorder", "split", "speed",
                            "volume", "transition", "grade")
        if needs_clip and clip is None:
            rejected.append(f"{op}: no clip {ref!r} on this timeline")
            continue

        try:
            if op == "trim":
                start, end = _num(raw.get("start")), _num(raw.get("end"))
                doc.trim(clip.id, start, end)
                applied.append(f"Trimmed clip {doc.index(clip.id)} to "
                               f"{clip.source_in:.1f}s→{clip.source_out:.1f}s")
            elif op == "hold":
                seconds = _num(raw.get("seconds"))
                if seconds is None or seconds <= 0:
                    rejected.append("hold: needs a positive number of seconds")
                    continue
                doc.trim(clip.id, clip.source_in, clip.source_in + seconds)
                applied.append(f"Clip {doc.index(clip.id)} now holds {seconds:.1f}s")
            elif op == "remove":
                i = doc.index(clip.id)
                doc.remove(clip.id)
                applied.append(f"Removed clip {i}")
            elif op == "reorder":
                to = raw.get("to")
                if to is None or _num(to) is None:
                    rejected.append("reorder: needs a 'to' index")
                    continue
                doc.move(clip.id, int(_num(to)))
                applied.append(f"Moved a clip to position {int(_num(to))}")
            elif op == "split":
                at = _num(raw.get("at"))
                if at is None or doc.split(clip.id, at) is None:
                    rejected.append(f"split: {at} is not inside that clip")
                    continue
                applied.append(f"Split a clip at {at:.1f}s")
            elif op == "speed":
                factor = _num(raw.get("factor"), 1.0)
                doc.set(clip.id, speed=factor)
                applied.append(f"Clip {doc.index(clip.id)} at {clip.speed:g}x")
            elif op == "volume":
                doc.set(clip.id, volume=_num(raw.get("level"), 1.0))
                applied.append(f"Clip {doc.index(clip.id)} volume {clip.volume:g}")
            elif op == "transition":
                style = str(raw.get("style", tl.DISSOLVE)).lower()
                if style not in tl.TRANSITIONS:
                    rejected.append(f"transition: {style!r} is not one of "
                                    f"{', '.join(tl.TRANSITIONS)}")
                    continue
                if doc.index(clip.id) == 0 and style != tl.CUT:
                    rejected.append("transition: the first clip has nothing to fade from")
                    continue
                doc.set(clip.id, transition=style,
                        transition_seconds=_num(raw.get("seconds"), 0.5))
                applied.append(f"Clip {doc.index(clip.id)} enters on a {style}")
            elif op == "grade":
                doc.set(clip.id,
                        brightness=_num(raw.get("brightness"), clip.brightness),
                        contrast=_num(raw.get("contrast"), clip.contrast),
                        saturation=_num(raw.get("saturation"), clip.saturation))
                applied.append(f"Graded clip {doc.index(clip.id)}")
            elif op == "title":
                text = str(raw.get("text", "")).strip()
                if not text:
                    rejected.append("title: no text")
                    continue
                added = doc.add_text(tl.Text(
                    text=text, start=_num(raw.get("start"), 0.0) or 0.0,
                    seconds=_num(raw.get("seconds"), 3.0) or 3.0,
                    position=str(raw.get("position", tl.LOWER_THIRD))))
                applied.append(f"Title “{added.text[:32]}” at {added.start:.1f}s")
            elif op == "remove_title":
                index = raw.get("index", raw.get("title"))
                if not doc.remove_text(index):
                    rejected.append(f"remove_title: no title {index!r}")
                    continue
                applied.append(f"Removed title {index}")
            elif op == "captions":
                source = str(raw.get("source", "")).strip()
                if base is not None and source and not (base / source).exists() \
                        and not Path(source).expanduser().is_absolute():
                    rejected.append(f"captions: no subtitle file at {source}")
                    continue
                doc.captions = source
                applied.append(f"Subtitles from {source}" if source else "Subtitles off")
            elif op == "music":
                source = str(raw.get("source", "")).strip()
                path = Path(source).expanduser()
                if base is not None and source and not path.is_absolute():
                    path = base / source
                if not source or not path.exists():
                    rejected.append(f"music: no audio file at {source}")
                    continue
                doc.set_music(source, gain_db=_num(raw.get("gain_db"), -22.0))
                applied.append(f"Music bed {Path(source).name} at "
                               f"{doc.music.gain_db:.0f} dB")
            elif op == "fade_out":
                doc.fade_out_seconds = max(_num(raw.get("seconds"), 1.0) or 0.0, 0.0)
                applied.append(f"Fades out over {doc.fade_out_seconds:.1f}s")
            elif op == "note":
                text = str(raw.get("text", "")).strip()
                doc.notes = (doc.notes + "\n" + text).strip() if doc.notes else text
                applied.append(text[:90])
            else:
                rejected.append(f"{op or '(no op)'}: not an operation the editor knows")
        except Exception as exc:  # noqa: BLE001 - one bad op must not lose the rest
            log.exception("edit operation failed: %s", raw)
            rejected.append(f"{op}: {exc}")

    if doc.clips:
        doc.clips[0].transition = tl.CUT
    return applied, rejected


# ------------------------------------------------------------- asking a model --

def plan_edit(doc: tl.Timeline, brief: str, context: str = "",
              role: str = ROLE_EDITING) -> list[dict]:
    """Ask for an edit in operations, not prose."""
    prompt = f"""Edit this video timeline.

WHAT THE EDITOR ASKED FOR:
{brief.strip() or "Tighten it. Cut anything that does not earn its place."}

{("CONTEXT:" + chr(10) + context.strip() + chr(10)) if context.strip() else ""}
THE TIMELINE:
{describe(doc)}

Return JSON: {{"operations": [ ... ]}}

{OPS_HELP}

Refer to clips by the id shown in brackets, or by index. Seconds are seconds, not
frames or timecode. Return only operations you are confident improve the video;
an empty list is a valid answer."""
    data = router.text_json(role, prompt, SYSTEM, max_tokens=3000)
    ops = data.get("operations") or data.get("ops") or []
    return ops if isinstance(ops, list) else []


def suggest_titles(doc: tl.Timeline, script: str, context: str = "") -> list[dict]:
    """Two or three on-screen titles for the moments that need one."""
    prompt = f"""Choose the on-screen titles for this video.

THE NARRATION:
{script[:6000]}

THE TIMELINE:
{describe(doc)}

Return JSON: {{"operations": [ ... ]}} using only "title" and "fade_out" and "note"
operations. At most three titles across the whole video, each under nine words,
each timed to a moment the narration is actually on. {context.strip()}"""
    data = router.text_json(ROLE_SCRIPT, prompt, SYSTEM, max_tokens=1500)
    ops = data.get("operations") or data.get("ops") or []
    return [o for o in ops if isinstance(o, dict)
            and str(o.get("op", "")).lower() in ("title", "fade_out", "note")]


def auto_edit(doc: tl.Timeline, brief: str, base: Path | None = None,
              context: str = "") -> tuple[list[str], list[str]]:
    """Ask, validate, apply. The one call the editor's AI box makes."""
    try:
        ops = plan_edit(doc, brief, context)
    except Exception as exc:  # noqa: BLE001
        raise EditorError(f"The model could not plan that edit: {exc}") from exc
    return apply_ops(doc, ops, base)


# ------------------------------------------------------------- dead air --

def silence_cuts(doc: tl.Timeline, clip: tl.Clip,
                 silences: list[tuple[float, float]]) -> float:
    """Replace one clip with the parts of it where somebody is speaking.

    Takes the silences as an argument rather than measuring them, so the
    arithmetic - windowing, padding, dropping the stutters - is testable without
    ffmpeg anywhere near it. Returns the seconds removed.
    """
    if clip.kind != tl.VIDEO or clip not in doc.clips:
        return 0.0
    window = clip.source_length
    if window <= 0:
        return 0.0
    local = [(max(a - clip.source_in, 0.0), min(b - clip.source_in, window))
             for a, b in silences
             if b > clip.source_in and a < clip.source_out]
    local = [(a, b) for a, b in local if b > a]
    if not local:
        return 0.0

    keeps = analyze.keep_spans(window, local)
    index = doc.clips.index(clip)
    speed = tl.clamp(clip.speed or 1.0, tl.MIN_SPEED, tl.MAX_SPEED)
    before = clip.length
    if not keeps:
        doc.clips.remove(clip)
        return round(before, 3)

    made: list[tl.Clip] = []
    for n, (start, end) in enumerate(keeps):
        piece = tl.Clip(**{k: v for k, v in asdict(clip).items() if k != "id"})
        piece.source_in = round(clip.source_in + start, 3)
        piece.source_out = round(clip.source_in + end, 3)
        piece.transition = clip.transition if n == 0 else tl.CUT
        piece.label = clip.label or Path(clip.source).stem
        made.append(piece)
    doc.clips[index:index + 1] = made
    kept = sum(m.source_length for m in made) / speed
    if doc.clips:
        doc.clips[0].transition = tl.CUT
    return round(max(before - kept, 0.0), 3)


def cut_dead_air(doc: tl.Timeline, base: Path, threshold_db: float = -34.0,
                 minimum: float = 0.6, progress=None) -> float:
    """Measure the silences in every clip's source and cut them out.

    Sources are measured once each, not once per clip: a timeline that reuses one
    take eight times should not run silence detection eight times.
    """
    if not analyze.available():
        raise EditorError(analyze.ffmpeg() or
                          "ffmpeg is needed to find the silences and is not installed.")
    measured: dict[str, list[tuple[float, float]]] = {}
    removed = 0.0
    for i, clip in enumerate(list(doc.clips)):
        if clip.kind != tl.VIDEO or not clip.source or clip.volume <= 0:
            continue
        key = str(clip.path(base))
        if progress:
            progress(i / max(len(doc.clips), 1), f"Listening to {Path(clip.source).name}")
        if key not in measured:
            measured[key] = analyze.silences(clip.path(base), threshold_db, minimum)
        removed += silence_cuts(doc, clip, measured[key])
    return round(removed, 3)


# ------------------------------------------------------------------- shorts --

def highlight_start(doc: tl.Timeline, seconds: float = 45.0,
                    hint: float | None = None) -> float:
    """Where a Short should begin.

    A hint from the metadata step wins. Otherwise it starts on a clip boundary
    about a fifth of the way in - past the throat-clearing, before the payoff -
    pulled back far enough that the whole window fits.
    """
    total = doc.duration
    if total <= seconds:
        return 0.0
    if hint is not None and 0 <= hint <= total - seconds:
        return round(float(hint), 3)
    target = total * 0.2
    starts = [s for s in doc.starts() if s <= total - seconds]
    if not starts:
        return 0.0
    return round(min(starts, key=lambda s: abs(s - target)), 3)


def crop(doc: tl.Timeline, start: float, seconds: float,
         portrait: bool = True) -> tl.Timeline:
    """A new timeline holding only the window between start and start+seconds.

    Clips are re-pointed at the right part of their own source rather than
    re-rendered, so a Short costs one render and no quality.
    """
    end = start + seconds
    width, height = tl.PORTRAIT if portrait else (doc.width, doc.height)
    out = tl.Timeline(name=f"{doc.name}_short", width=width, height=height, fps=doc.fps)

    for clip_start, clip in zip(doc.starts(), doc.clips):
        clip_end = clip_start + clip.length
        if clip_end <= start or clip_start >= end:
            continue
        piece = tl.Clip(**{k: v for k, v in asdict(clip).items() if k != "id"})
        head = max(start - clip_start, 0.0)
        tail = max(clip_end - end, 0.0)
        speed = 1.0 if clip.kind in (tl.IMAGE, tl.COLOUR) else tl.clamp(
            clip.speed or 1.0, tl.MIN_SPEED, tl.MAX_SPEED)
        piece.source_in = round(clip.source_in + head * speed, 3)
        piece.source_out = round(clip.source_out - tail * speed, 3)
        if piece.source_length < tl.MIN_CLIP:
            continue
        out.add(piece)

    for overlay in doc.overlays:
        if overlay.end <= start or overlay.start >= end:
            continue
        moved = tl.Text(**{k: v for k, v in asdict(overlay).items() if k != "id"})
        moved.start = round(max(overlay.start - start, 0.0), 3)
        moved.seconds = round(min(overlay.end, end) - max(overlay.start, start), 3)
        out.add_text(moved)

    for track in doc.audio:
        moved = tl.Audio(**{k: v for k, v in asdict(track).items() if k != "id"})
        if track.role == tl.MUSIC:
            out.audio.append(moved.sanitised())
            continue
        if moved.start >= end:
            continue                      # this block of narration is past the window
        # a narration block that began before the window is entered part-way
        head = max(start - moved.start, 0.0)
        moved.source_in = round(moved.source_in + head, 3)
        moved.start = round(max(moved.start - start, 0.0), 3)
        moved.seconds = round(max(seconds - moved.start, 0.0), 3)
        if moved.seconds < 0.2:
            continue
        out.audio.append(moved.sanitised())

    # a burned subtitle file is timed to the long video; carrying it into a
    # window that starts anywhere else would caption the wrong words
    out.captions = doc.captions if start <= 0.001 else ""
    out.fade_out_seconds = min(doc.fade_out_seconds, 0.5)
    out.notes = f"Cut from {doc.name} at {start:.1f}s for {seconds:.0f}s"
    return out


__all__ = ["EditorError", "OPS_HELP", "apply_ops", "assemble", "auto_edit", "crop",
           "cut_dead_air", "describe", "highlight_start", "plan_edit", "silence_cuts",
           "sources", "suggest_titles"]
