"""Turning a timeline into a file, with ffmpeg.

The renderer is written as a *plan*: a list of commands, built without running
anything. That is the only way this part of the suite can be tested honestly -
the interesting bugs here are wrong filter strings, wrong crossfade offsets and
unescaped Windows paths, and every one of them is visible in the command before
ffmpeg is ever launched.

Three passes, and the cheap ones are skipped when they have nothing to do:

  1. every clip becomes a normalised segment - one size, one frame rate, one
     audio layout, so the join cannot fail on a mismatch
  2. the segments are joined; a run of straight cuts is copied rather than
     re-encoded, and only real transitions pay for an xfade
  3. one finishing pass burns in titles and subtitles, lays the music under the
     narration and fades out
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

from . import timeline as tl
from .analyze import probe

log = logging.getLogger(__name__)


class RenderError(RuntimeError):
    pass


FFMPEG_HINT = (
    "ffmpeg is not installed or not on PATH.\n"
    "  Windows:  winget install Gyan.FFmpeg\n"
    "  macOS:    brew install ffmpeg\n"
    "Then restart Digital Assets Studio.")

XFADE = {tl.FADE: "fade", tl.DISSOLVE: "dissolve", tl.SLIDE: "slideleft"}

SAMPLE_RATE = 48000
SILENCE = f"anullsrc=channel_layout=stereo:sample_rate={SAMPLE_RATE}"
AFORMAT = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RenderError(FFMPEG_HINT)
    return exe


def available() -> bool:
    return shutil.which("ffmpeg") is not None


@dataclass
class Job:
    """One ffmpeg invocation, and what to tell the user while it runs."""
    label: str
    argv: list[str] = field(default_factory=list)
    output: Path | None = None


# ------------------------------------------------------------------ escaping --

def ffpath(path: Path | str) -> str:
    """A path ffmpeg's *filter* parser accepts, on Windows too.

    Filters treat ':' as an argument separator, so C:/Users/... has to become
    C\\:/Users/... or the whole filtergraph fails to parse."""
    return Path(path).as_posix().replace(":", r"\:")


def ffcolour(value: str, fallback: str = "white") -> str:
    """#RRGGBB is a CSS colour, not an ffmpeg one. 0xRRGGBB is both."""
    text = str(value or "").strip()
    if text.startswith("#") and len(text) in (7, 9):
        return "0x" + text[1:]
    return text or fallback


def atempo_chain(speed: float) -> list[float]:
    """atempo only stretches between 0.5x and 2x per pass, so anything outside
    that has to be split across several - otherwise fast clips come out silent."""
    speed = tl.clamp(float(speed or 1.0), tl.MIN_SPEED, tl.MAX_SPEED)
    factors: list[float] = []
    remaining = speed
    while remaining > 2.0 + 1e-6:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5 - 1e-6:
        factors.append(0.5)
        remaining /= 0.5
    if abs(remaining - 1.0) > 1e-6 or not factors:
        factors.append(round(remaining, 6))
    return factors


# ------------------------------------------------------------------ segments --

def _video_chain(clip: tl.Clip, size: tuple[int, int], fps: int) -> str:
    w, h = size
    parts: list[str] = []
    if clip.kind == tl.IMAGE and clip.ken_burns:
        # oversample first: zoompan works on the frame it is handed, and zooming
        # a 1080p still straight to 1080p is what makes Ken Burns look like mush
        frames = max(int(clip.length * fps), 2)
        parts.append(f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
                     f"crop={w * 2}:{h * 2}")
        parts.append(f"zoompan=z='1.0+0.0012*on':d={frames}:x='iw/2-(iw/zoom/2)'"
                     f":y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}")
    else:
        parts.append(f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}")
        if clip.kind == tl.VIDEO and abs(clip.speed - 1.0) > 1e-6:
            parts.append(f"setpts=PTS/{clip.speed:.4f}")
        parts.append(f"fps={fps}")
    if (abs(clip.brightness) > 1e-6 or abs(clip.contrast - 1.0) > 1e-6
            or abs(clip.saturation - 1.0) > 1e-6):
        parts.append(f"eq=brightness={clip.brightness:.3f}:contrast={clip.contrast:.3f}"
                     f":saturation={clip.saturation:.3f}")
    parts.append("setsar=1")
    parts.append("format=yuv420p")
    return ",".join(parts)


def _audio_chain(clip: tl.Clip, has_audio: bool) -> tuple[str, str]:
    """(filter, source label). Silence comes from a generated input rather than
    from nothing, so every segment has a stream to join and the concat never
    trips over a clip whose file happens to be mute."""
    if not has_audio or clip.volume <= 0 or clip.kind != tl.VIDEO:
        return f"[1:a]{AFORMAT}[a]", "silence"
    parts = [f"atempo={f:.6f}" for f in atempo_chain(clip.speed)
             if abs(f - 1.0) > 1e-6]
    if abs(clip.volume - 1.0) > 1e-6:
        parts.append(f"volume={clip.volume:.3f}")
    parts.append(AFORMAT)
    parts.append("apad")            # a short tail must not shorten the segment
    return "[0:a]" + ",".join(parts) + "[a]", "clip"


def segment_job(clip: tl.Clip, index: int, out: Path, size: tuple[int, int], fps: int,
                base: Path, has_audio: bool) -> Job:
    dur = max(clip.length, tl.MIN_CLIP)
    argv = [ffmpeg() if available() else "ffmpeg", "-y", "-hide_banner", "-nostdin"]

    if clip.kind == tl.COLOUR:
        argv += ["-f", "lavfi", "-t", f"{dur:.3f}",
                 "-i", f"color=c={ffcolour(clip.colour, 'black')}:s={size[0]}x{size[1]}:r={fps}"]
    elif clip.kind == tl.IMAGE:
        argv += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(clip.path(base))]
    else:
        source_len = max(clip.source_length, tl.MIN_CLIP)
        argv += ["-ss", f"{clip.source_in:.3f}", "-t", f"{source_len:.3f}",
                 "-i", str(clip.path(base))]
    argv += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", SILENCE]

    vchain = _video_chain(clip, size, fps)
    achain, _ = _audio_chain(clip, has_audio)
    argv += ["-filter_complex", f"[0:v]{vchain}[v];{achain}",
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-r", str(fps),
             "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-ac", "2",
             "-t", f"{dur:.3f}", str(out)]
    return Job(f"Clip {index + 1}: {clip.label or Path(clip.source).name or 'colour'}",
               argv, out)


# ---------------------------------------------------------------------- join --

def join_job(doc: tl.Timeline, segments: list[Path], out: Path, listing: Path) -> Job:
    """Join the segments, crossfading only where a transition asks for it."""
    exe = ffmpeg() if available() else "ffmpeg"
    if all(c.transition == tl.CUT for c in doc.clips[1:]):
        listing.parent.mkdir(parents=True, exist_ok=True)
        listing.write_text("\n".join(f"file '{p.as_posix()}'" for p in segments),
                           encoding="utf-8")
        return Job("Joining the cuts",
                   [exe, "-y", "-hide_banner", "-nostdin", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c", "copy", str(out)], out)

    starts = doc.starts()
    filters: list[str] = []
    vlabel, alabel = "0:v", "0:a"
    for i in range(1, len(segments)):
        clip = doc.clips[i]
        overlap = doc.overlap(i)
        nv, na = f"v{i}", f"a{i}"
        if clip.transition == tl.CUT or overlap <= 0:
            filters.append(f"[{vlabel}][{alabel}][{i}:v][{i}:a]"
                           f"concat=n=2:v=1:a=1[{nv}][{na}]")
        else:
            style = XFADE.get(clip.transition, "fade")
            filters.append(f"[{vlabel}][{i}:v]xfade=transition={style}"
                           f":duration={overlap:.3f}:offset={starts[i]:.3f}[{nv}]")
            filters.append(f"[{alabel}][{i}:a]acrossfade=d={overlap:.3f}:c1=tri:c2=tri[{na}]")
        vlabel, alabel = nv, na

    argv = [exe, "-y", "-hide_banner", "-nostdin"]
    for seg in segments:
        argv += ["-i", str(seg)]
    argv += ["-filter_complex", ";".join(filters),
             "-map", f"[{vlabel}]", "-map", f"[{alabel}]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), str(out)]
    return Job("Crossfading the joins", argv, out)


# ------------------------------------------------------------------- finish --

def text_filters(doc: tl.Timeline, font: Path | None, tmpdir: Path) -> list[str]:
    """One drawtext per title, each alive only for its own span.

    The words go through a file rather than the filter string: it removes every
    escaping problem with quotes, colons and commas in real copy at once.

    ``expansion=none`` is not optional. drawtext otherwise treats the text as a
    template, and a single per-cent sign - "50% off", "up 12%" - makes it render
    the whole title as nothing at all, with no error and no warning. A title
    that silently disappears is the worst failure this filter has."""
    if font is None or not Path(font).exists():
        return []
    w, h = doc.size
    out: list[str] = []
    for i, overlay in enumerate(doc.overlays):
        words = " ".join((overlay.text or "").split())
        if not words:
            continue
        size = max(int(h * overlay.size), 12)
        per_line = max(12, int(w / (size * 0.52)))
        lines = textwrap.wrap(words, per_line)[:3]
        if not lines:
            continue
        f = tmpdir / f"title_{i:03d}.txt"
        f.write_text("\n".join(lines), encoding="utf-8")
        y = {tl.TOP: f"{int(h * 0.07)}",
             tl.MIDDLE: "(h-text_h)/2",
             tl.LOWER_THIRD: f"{int(h * 0.70)}",
             tl.BOTTOM: f"h-text_h-{int(h * 0.09)}"}.get(overlay.position, f"{int(h * 0.70)}")
        box = (f":box=1:boxcolor=black@0.55:boxborderw={max(int(size * 0.4), 6)}"
               if overlay.box else "")
        out.append(
            f"drawtext=fontfile='{ffpath(font)}':textfile='{ffpath(f)}'"
            f":fontcolor={ffcolour(overlay.colour)}:fontsize={size}:line_spacing=8"
            f":x=(w-text_w)/2:y={y}{box}:expansion=none"
            f":enable='between(t,{overlay.start:.3f},{overlay.end:.3f})'")
    return out


def caption_filter(doc: tl.Timeline, base: Path, font: Path | None) -> str:
    if not doc.captions:
        return ""
    path = Path(doc.captions).expanduser()
    if not path.is_absolute():
        path = base / doc.captions
    if not path.exists():
        return ""
    style = "FontSize=16,Outline=2,Shadow=0,Alignment=2,MarginV=90"
    if font is not None and Path(font).exists():
        style += f",FontName={Path(font).stem}"
    return f"subtitles='{ffpath(path)}':force_style='{style}'"


def voice_tracks(doc: tl.Timeline) -> list[tl.Audio]:
    """Narration laid over the picture, rather than carried inside a clip.

    A slide-based edit has no sound of its own: the words live in one mp3 per
    scene, dropped at the moment their scene starts."""
    return [a for a in doc.audio if a.role == tl.VOICE and a.source]


def needs_finish(doc: tl.Timeline, base: Path, font: Path | None) -> bool:
    return bool(doc.overlays and font and Path(font).exists()) \
        or bool(caption_filter(doc, base, font)) \
        or doc.music is not None \
        or bool(voice_tracks(doc)) \
        or doc.fade_out_seconds > 0


def finish_job(doc: tl.Timeline, joined: Path, out: Path, base: Path,
               font: Path | None, tmpdir: Path) -> Job:
    exe = ffmpeg() if available() else "ffmpeg"
    duration = doc.duration
    music = doc.music
    voices = voice_tracks(doc)

    argv = [exe, "-y", "-hide_banner", "-nostdin", "-i", str(joined)]
    index = 1
    music_index = None
    if music is not None:
        if music.loop:
            argv += ["-stream_loop", "-1"]
        argv += ["-i", str(music.path(base))]
        music_index, index = index, index + 1
    voice_indices: list[int] = []
    for track in voices:
        argv += ["-i", str(track.path(base))]
        voice_indices.append(index)
        index += 1

    # ---- picture
    vparts = text_filters(doc, font, tmpdir)
    captions = caption_filter(doc, base, font)
    if captions:
        vparts.insert(0, captions)
    if doc.fade_out_seconds > 0 and duration > doc.fade_out_seconds:
        vparts.append(f"fade=t=out:st={duration - doc.fade_out_seconds:.3f}"
                      f":d={doc.fade_out_seconds:.3f}")
    filters: list[str] = []
    if vparts:
        filters.append("[0:v]" + ",".join(vparts) + "[v]")

    # ---- sound
    mix: list[str] = ["0:a"]
    for n, (track, source_index) in enumerate(zip(voices, voice_indices)):
        chain = [AFORMAT]
        if track.source_in > 0 or track.seconds > 0:
            end = (track.source_in + track.seconds) if track.seconds > 0 else duration
            chain.append(f"atrim={track.source_in:.3f}:{end:.3f}")
            chain.append("asetpts=PTS-STARTPTS")
        if abs(track.gain_db) > 1e-6:
            chain.append(f"volume={track.gain_db:.1f}dB")
        if track.start > 0:
            ms = int(track.start * 1000)
            chain.append(f"adelay={ms}|{ms}")
        filters.append(f"[{source_index}:a]" + ",".join(chain) + f"[nar{n}]")
        mix.append(f"nar{n}")
    if music is not None and music_index is not None:
        bed = [AFORMAT, f"volume={music.gain_db:.1f}dB", f"atrim=0:{duration:.3f}",
               "asetpts=PTS-STARTPTS"]
        if music.fade_in > 0:
            bed.insert(1, f"afade=t=in:st=0:d={music.fade_in:.2f}")
        if music.fade_out > 0 and duration > music.fade_out:
            bed.append(f"afade=t=out:st={duration - music.fade_out:.3f}:d={music.fade_out:.2f}")
        filters.append(f"[{music_index}:a]" + ",".join(bed) + "[bed]")
        mix.append("bed")

    tail = "0:a"
    if len(mix) > 1:
        # duration=first keeps the edit's own length - a looped bed must never
        # decide how long the video is - and normalize=0 stops amix quietly
        # halving the narration for every extra track laid under it
        filters.append("".join(f"[{m}]" for m in mix)
                       + f"amix=inputs={len(mix)}:duration=first:dropout_transition=3"
                         ":normalize=0[mixed]")
        tail = "mixed"
    if doc.fade_out_seconds > 0 and duration > doc.fade_out_seconds:
        filters.append(f"[{tail}]afade=t=out:st={duration - doc.fade_out_seconds:.3f}"
                       f":d={doc.fade_out_seconds:.3f}[aout]")
        tail = "aout"

    if filters:
        argv += ["-filter_complex", ";".join(filters)]
    argv += ["-map", "[v]" if vparts else "0:v",
             "-map", "0:a" if tail == "0:a" else f"[{tail}]"]
    argv += ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-ar", str(SAMPLE_RATE), "-ac", "2",
             "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out)]
    return Job("Titles, narration and music", argv, out)


# --------------------------------------------------------------------- plan --

def plan(doc: tl.Timeline, out: Path, base: Path, tmpdir: Path,
         font: Path | None = None, audio: dict[str, bool] | None = None) -> list[Job]:
    """Every command this render will run, in order.

    ``audio`` says which sources actually carry sound; without it every video
    clip is assumed to, which is what a caller with no ffprobe has to assume.
    """
    if not doc.clips:
        raise RenderError("There is nothing on the timeline to render.")
    tmpdir = Path(tmpdir)
    tmpdir.mkdir(parents=True, exist_ok=True)
    size, fps = doc.size, doc.fps

    jobs: list[Job] = []
    segments: list[Path] = []
    for i, clip in enumerate(doc.clips):
        seg = tmpdir / f"seg_{i:03d}.mp4"
        has_audio = True if audio is None else bool(
            audio.get(str(clip.path(base)), False))
        jobs.append(segment_job(clip, i, seg, size, fps, base, has_audio))
        segments.append(seg)

    finishing = needs_finish(doc, base, font)
    joined = tmpdir / "joined.mp4" if finishing else Path(out)
    jobs.append(join_job(doc, segments, joined, tmpdir / "segments.txt"))
    if finishing:
        jobs.append(finish_job(doc, joined, Path(out), base, font, tmpdir))
    return jobs


# ------------------------------------------------------------------- render --

def _run(job: Job, timeout: int = 3600) -> None:
    """Run one job, and turn a failure into words a person can act on.

    The output is decoded as UTF-8 rather than left to the platform: ffmpeg
    speaks UTF-8 everywhere, Windows would decode it as cp1252, and a single
    accented character in a filename or a title would then raise a
    UnicodeDecodeError *instead of* the render error we were trying to report.
    """
    res = subprocess.run(job.argv, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
    if res.returncode != 0:
        raise RenderError(f"{job.label} failed:\n{(res.stderr or '')[-700:]}")
    if job.output is not None and not Path(job.output).exists():
        raise RenderError(f"{job.label} produced no file.")


def audio_map(doc: tl.Timeline, base: Path) -> dict[str, bool]:
    """Which of the sources on this timeline actually carry sound.

    Probed once per file rather than once per clip: a timeline that reuses one
    b-roll clip eight times should not pay for eight ffprobe launches."""
    out: dict[str, bool] = {}
    for clip in doc.clips:
        if clip.kind != tl.VIDEO or not clip.source:
            continue
        key = str(clip.path(base))
        if key not in out:
            out[key] = probe(clip.path(base)).has_audio
    return out


def render(doc: tl.Timeline, out: Path, base: Path, font: Path | None = None,
           progress=None) -> Path:
    """Render the timeline to ``out``. Raises RenderError with ffmpeg's own words."""
    ffmpeg()                                   # fail early, with the install hint
    problems = [p for p in doc.problems(base) if "no file at" in p or "missing" in p]
    if problems:
        raise RenderError("This edit cannot be rendered yet:\n- " + "\n- ".join(problems))

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="das-edit-") as tmp:
        tmpdir = Path(tmp)
        jobs = plan(doc, out, base, tmpdir, font=font, audio=audio_map(doc, base))
        for i, job in enumerate(jobs):
            if progress:
                progress(i / max(len(jobs), 1), job.label)
            _run(job)
    if progress:
        progress(1.0, "Render complete")
    return out


# ------------------------------------------------------------------ preview --

def frame(source: Path, at: float, out: Path, width: int = 640) -> Path | None:
    """One still out of a file, for the editor's preview pane.

    Returns None rather than raising: a preview that cannot be made is a blank
    box, never a broken screen.
    """
    exe = shutil.which("ffmpeg")
    source = Path(source)
    if not exe or not source.exists():
        return None
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [exe, "-y", "-hide_banner", "-nostdin", "-ss", f"{max(at, 0):.3f}",
            "-i", str(source), "-frames:v", "1",
            "-vf", f"scale={width}:-2", str(out)]
    try:
        res = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    except Exception:  # noqa: BLE001
        log.exception("could not grab a frame from %s", source)
        return None
    return out if res.returncode == 0 and out.exists() else None


def preview_frame(doc: tl.Timeline, base: Path, at: float, out: Path,
                  width: int = 640) -> Path | None:
    """The frame the playhead is sitting on, whichever clip that lands in."""
    clip = doc.at(at)
    if clip is None:
        return None
    if clip.kind == tl.IMAGE:
        source = clip.path(base)
        return source if source.exists() else None
    if clip.kind == tl.COLOUR:
        return None
    return frame(clip.path(base), doc.source_seconds(clip.id, at), out, width)


__all__ = ["Job", "RenderError", "FFMPEG_HINT", "atempo_chain", "audio_map", "available",
           "caption_filter", "ffcolour", "ffpath", "finish_job", "frame", "join_job",
           "needs_finish", "plan", "preview_frame", "render", "segment_job", "text_filters",
           "voice_tracks"]
