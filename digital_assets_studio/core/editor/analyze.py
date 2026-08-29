"""Reading what is actually in a media file.

Everything an edit decision needs to be made *from* rather than guessed at: how
long a file is, whether it carries audio at all, where the silences are, and
where the picture changes enough to be a natural cut.

The parsers are deliberately separate from the subprocess calls. ffmpeg reports
these things on stderr in a format that has never been stable, and a parser you
can hand a captured log to is a parser you can test without ffmpeg installed.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SILENCE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
SILENCE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")
SCENE_TIME = re.compile(r"pts_time:\s*([\d.]+)")


def ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe() -> str | None:
    return shutil.which("ffprobe")


def available() -> bool:
    return bool(ffmpeg())


@dataclass
class Media:
    """What the editor needs to know about one file before it can cut it."""
    path: Path
    seconds: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    has_video: bool = False

    @property
    def portrait(self) -> bool:
        return self.height > self.width > 0


def probe(path: Path) -> Media:
    """Measure a file. An unreadable one comes back zeroed rather than raising:
    the editor has to be able to show a clip whose source has gone missing."""
    path = Path(path)
    info = Media(path=path)
    exe = ffprobe()
    if not exe or not path.exists():
        return info
    try:
        res = subprocess.run(
            [exe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
             str(path)], capture_output=True, encoding="utf-8", errors="replace", timeout=120)
        data = json.loads(res.stdout or "{}")
    except Exception:  # noqa: BLE001
        log.exception("could not probe %s", path)
        return info

    try:
        info.seconds = float(data.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        info.seconds = 0.0
    for stream in data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and not info.has_video:
            info.has_video = True
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.fps = _rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate") or "")
            if not info.seconds:
                try:
                    info.seconds = float(stream.get("duration") or 0)
                except (TypeError, ValueError):
                    pass
        elif kind == "audio":
            info.has_audio = True
    return info


def _rate(text: str) -> float:
    try:
        if "/" in str(text):
            num, den = str(text).split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def duration(path: Path) -> float:
    return probe(path).seconds


# ------------------------------------------------------------------ silence --

def parse_silences(stderr: str) -> list[tuple[float, float]]:
    """Turn ffmpeg's silencedetect log into (start, end) pairs.

    A silence that is still open when the file ends has no ``silence_end`` line,
    so it is dropped: the caller has no length to trim to and guessing one is how
    you lose the last word of a sentence.
    """
    starts = [float(m) for m in SILENCE_START.findall(stderr or "")]
    ends = [float(m) for m in SILENCE_END.findall(stderr or "")]
    out: list[tuple[float, float]] = []
    for i, start in enumerate(starts):
        if i < len(ends) and ends[i] > start:
            out.append((round(max(start, 0.0), 3), round(ends[i], 3)))
    return out


def silences(path: Path, threshold_db: float = -34.0,
             minimum: float = 0.6) -> list[tuple[float, float]]:
    """Where nobody is speaking. Used by the 'cut the dead air' edit."""
    exe = ffmpeg()
    if not exe or not Path(path).exists():
        return []
    try:
        res = subprocess.run(
            [exe, "-hide_banner", "-nostats", "-i", str(path), "-af",
             f"silencedetect=noise={threshold_db}dB:d={minimum}", "-f", "null", "-"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=900)
    except Exception:  # noqa: BLE001
        log.exception("silence detection failed on %s", path)
        return []
    return parse_silences(res.stderr or "")


# -------------------------------------------------------------------- scenes --

def parse_scene_cuts(stderr: str) -> list[float]:
    """Timestamps where the picture changed enough to be a natural cut."""
    seen: list[float] = []
    for value in SCENE_TIME.findall(stderr or ""):
        try:
            t = round(float(value), 3)
        except (TypeError, ValueError):
            continue
        if not seen or abs(t - seen[-1]) > 0.25:
            seen.append(t)
    return seen


def scene_cuts(path: Path, sensitivity: float = 0.35) -> list[float]:
    exe = ffmpeg()
    if not exe or not Path(path).exists():
        return []
    try:
        res = subprocess.run(
            [exe, "-hide_banner", "-nostats", "-i", str(path), "-filter_complex",
             f"select='gt(scene,{sensitivity})',metadata=print:file=-", "-an", "-f", "null", "-"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=900)
    except Exception:  # noqa: BLE001
        log.exception("scene detection failed on %s", path)
        return []
    return parse_scene_cuts((res.stdout or "") + (res.stderr or ""))


# ---------------------------------------------------------------- keep spans --

def keep_spans(total: float, cuts: list[tuple[float, float]],
               pad: float = 0.12, minimum: float = 0.35) -> list[tuple[float, float]]:
    """Invert a list of spans to remove into the spans worth keeping.

    Each keeper is padded outward by ``pad`` so a cut never clips the breath at
    the start of a word, and anything left shorter than ``minimum`` is dropped
    rather than kept as a stutter.
    """
    if total <= 0:
        return []
    ordered = sorted((max(a, 0.0), min(b, total)) for a, b in cuts if b > a)
    merged: list[list[float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    spans: list[tuple[float, float]] = []
    at = 0.0
    for start, end in merged:
        keep_end = min(start + pad, total)
        if keep_end - at >= minimum:
            spans.append((round(at, 3), round(keep_end, 3)))
        at = max(end - pad, at)
    if total - at >= minimum:
        spans.append((round(at, 3), round(total, 3)))
    return spans


__all__ = ["Media", "available", "duration", "ffmpeg", "ffprobe", "keep_spans",
           "parse_scene_cuts", "parse_silences", "probe", "scene_cuts", "silences"]
