"""Video assembly with ffmpeg.

One segment per scene: a still with a slow Ken Burns move, the scene's own
voiceover, and an optional caption bar. Segments are concatenated, then music is
ducked underneath if you supplied any. Because each scene owns its audio, the
picture always lands on the word - no manual timing.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import textwrap
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


class VideoError(RuntimeError):
    pass


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise VideoError(
            "ffmpeg is not installed or not on PATH.\n"
            "  Windows:  winget install Gyan.FFmpeg\n"
            "  macOS:    brew install ffmpeg\n"
            "Then restart Digital Assets Studio.")
    return exe


def available() -> bool:
    return shutil.which("ffmpeg") is not None


@dataclass
class Scene:
    image: Path
    audio: Path
    seconds: float
    caption: str = ""


LANDSCAPE = (1920, 1080)
PORTRAIT = (1080, 1920)


def ffpath(path: Path) -> str:
    """A path ffmpeg's filter parser accepts, on Windows too.

    Filters treat ':' as an argument separator, so C:/Users/... has to become
    C\:/Users/... or the whole filtergraph fails to parse."""
    text = Path(path).as_posix()
    return text.replace(":", r"\:")


def caption_filter(text: str, w: int, h: int, font: Path | None, tmpdir: Path,
                   index: int) -> str:
    """A wrapped, boxed caption. Long lines used to run off the frame - in
    portrait especially - because the text was truncated, never wrapped.

    The text goes through a file rather than the filter string: it removes every
    escaping problem with quotes, colons and commas in real narration.
    """
    text = " ".join((text or "").split())
    if not text or font is None or not Path(font).exists():
        return ""
    size = int(h * 0.042)
    # Poppins averages a little over half the point size per glyph
    per_line = max(16, int(w / (size * 0.52)))
    lines = textwrap.wrap(text, per_line)[:3]
    if not lines:
        return ""
    caption_file = tmpdir / f"caption_{index:03d}.txt"
    caption_file.write_text("\n".join(lines), encoding="utf-8")
    line_h = int(size * 1.32)
    box_h = line_h * len(lines) + int(size * 0.9)
    return (f"drawbox=y=ih-{box_h + int(h * 0.045)}:color=black@0.55:width=iw:"
            f"height={box_h}:t=fill,"
            f"drawtext=fontfile='{ffpath(font)}':textfile='{ffpath(caption_file)}'"
            f":fontcolor=white:fontsize={size}:line_spacing=8"
            f":x=(w-text_w)/2:y=h-{box_h + int(h * 0.045)}+{int(size * 0.45)}:box=0")


def _segment(scene: Scene, out: Path, size: tuple[int, int], font: Path | None,
             zoom: bool = True, index: int = 0, tmpdir: Path | None = None) -> Path:
    w, h = size
    dur = max(scene.seconds, 0.6)
    fps = 30
    frames = max(int(dur * fps), 2)

    scale = f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2}"
    if zoom:
        direction = 0.0012 if index % 2 == 0 else -0.0012
        base = 1.0 if direction > 0 else 1.08
        move = (f"zoompan=z='{base}+{direction}*on':d={frames}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps}")
    else:
        move = f"scale={w}:{h},fps={fps}"
    chain = f"{scale},{move},format=yuv420p"

    caption = caption_filter(scene.caption, w, h, font, tmpdir or out.parent, index)
    if caption:
        chain += "," + caption

    cmd = [ffmpeg(), "-y", "-loop", "1", "-i", str(scene.image), "-i", str(scene.audio),
           "-filter_complex", f"[0:v]{chain}[v]", "-map", "[v]", "-map", "1:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-shortest", "-t", f"{dur:.3f}",
           str(out)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        raise VideoError(f"ffmpeg failed on scene {index + 1}: {res.stderr[-500:]}")
    return out


def render(scenes: list[Scene], out_path: Path, size: tuple[int, int] = LANDSCAPE,
           font: Path | None = None, music: Path | None = None, music_db: float = -22.0,
           zoom: bool = True, progress=None) -> Path:
    if not scenes:
        raise VideoError("No scenes to render.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        segments: list[Path] = []
        for i, sc in enumerate(scenes):
            if progress:
                progress(i / len(scenes), f"Rendering scene {i + 1} of {len(scenes)}")
            segments.append(_segment(sc, tmpdir / f"seg_{i:03d}.mp4", size, font, zoom, i,
                                     tmpdir))

        listing = tmpdir / "segments.txt"
        listing.write_text("\n".join(f"file '{p.as_posix()}'" for p in segments), encoding="utf-8")

        if progress:
            progress(0.9, "Joining scenes")
        joined = tmpdir / "joined.mp4"
        res = subprocess.run(
            [ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)],
            capture_output=True, text=True, timeout=1800)
        if res.returncode != 0:
            raise VideoError(f"Joining the scenes failed: {res.stderr[-500:]}")

        if music and Path(music).exists():
            if progress:
                progress(0.95, "Mixing music underneath")
            res = subprocess.run(
                [ffmpeg(), "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(music),
                 "-filter_complex",
                 f"[1:a]volume={music_db}dB[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=3[a]",
                 "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                 str(out_path)],
                capture_output=True, text=True, timeout=1800)
            if res.returncode != 0:
                raise VideoError(f"Mixing the music failed: {res.stderr[-500:]}")
        else:
            shutil.copy(joined, out_path)

    if progress:
        progress(1.0, "Render complete")
    return out_path


def cut_short(source: Path, out_path: Path, start: float, duration: float = 45.0,
              size: tuple[int, int] = PORTRAIT, subtitles: Path | None = None,
              font: Path | None = None) -> Path:
    w, h = size
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}")
    if subtitles and Path(subtitles).exists():
        style = "FontSize=16,Outline=2,Shadow=0,Alignment=2,MarginV=90"
        if font and font.exists():
            style += f",FontName={font.stem}"
        vf += f",subtitles='{Path(subtitles).as_posix()}':force_style='{style}'"
    cmd = [ffmpeg(), "-y", "-ss", f"{start:.2f}", "-t", f"{duration:.2f}", "-i", str(source),
           "-vf", vf, "-c:v", "libx264", "-crf", "20", "-preset", "medium",
           "-c:a", "aac", "-b:a", "192k", str(out_path)]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        raise VideoError(f"Cutting the Short failed: {res.stderr[-500:]}")
    return out_path


def probe(path: Path) -> dict:
    exe = shutil.which("ffprobe")
    if not exe:
        return {}
    res = subprocess.run([exe, "-v", "quiet", "-print_format", "json", "-show_format",
                          "-show_streams", str(path)], capture_output=True, text=True, timeout=120)
    try:
        return json.loads(res.stdout)
    except Exception:  # noqa: BLE001
        return {}
