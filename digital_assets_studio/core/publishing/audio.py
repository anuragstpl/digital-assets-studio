"""Audio mastering and packaging for audiobooks and podcasts.

ACX (Audible) rejects files on measurable grounds, not taste: RMS between
-23 dB and -18 dB, peaks no higher than -3 dB, a noise floor under -60 dB, and
room tone at the head and tail. All of that is measurable with ffmpeg, so the
suite measures it, fixes it, and measures again rather than hoping.
"""
from __future__ import annotations

import html
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

log = logging.getLogger(__name__)

ACX_RMS_TARGET = -20.0      # middle of ACX's -23..-18 window
ACX_PEAK_CEILING = -3.1     # a hair under the limit, so rounding never fails you
ACX_BITRATE = "192k"
ACX_RATE = "44100"


class AudioError(RuntimeError):
    pass


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise AudioError("ffmpeg is not installed or not on PATH.\n"
                         "  Windows:  winget install Gyan.FFmpeg\n"
                         "  macOS:    brew install ffmpeg")
    return exe


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    res = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if res.returncode != 0:
        raise AudioError(res.stderr[-600:] or "ffmpeg failed with no message")
    return res


@dataclass
class Levels:
    mean_db: float
    peak_db: float
    seconds: float

    @property
    def acx_ok(self) -> bool:
        return -23.0 <= self.mean_db <= -18.0 and self.peak_db <= -3.0

    def verdict(self) -> str:
        if self.acx_ok:
            return f"passes ACX — RMS {self.mean_db:.1f} dB, peak {self.peak_db:.1f} dB"
        problems = []
        if self.mean_db < -23.0:
            problems.append(f"too quiet ({self.mean_db:.1f} dB RMS)")
        if self.mean_db > -18.0:
            problems.append(f"too loud ({self.mean_db:.1f} dB RMS)")
        if self.peak_db > -3.0:
            problems.append(f"peaks at {self.peak_db:.1f} dB")
        return "fails ACX — " + ", ".join(problems)


def duration(path: Path) -> float:
    exe = shutil.which("ffprobe")
    if not exe:
        return 0.0
    try:
        out = subprocess.run([exe, "-v", "quiet", "-print_format", "json", "-show_format",
                              str(path)], capture_output=True, text=True, timeout=120, check=True)
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0


def measure(path: Path) -> Levels:
    res = subprocess.run([ffmpeg(), "-i", str(path), "-af", "volumedetect",
                          "-f", "null", "-"], capture_output=True, text=True, timeout=900)
    text = res.stderr
    mean = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?) dB", text)
    peak = re.search(r"max_volume:\s*(-?\d+(?:\.\d+)?) dB", text)
    if not mean or not peak:
        raise AudioError(f"Could not measure {path.name}. ffmpeg said: {text[-300:]}")
    return Levels(float(mean.group(1)), float(peak.group(1)), duration(path))


def _apply(src: Path, dst: Path, gain_db: float, head: float, tail: float,
           mono: bool) -> None:
    chain = [f"volume={gain_db:.2f}dB",
             f"alimiter=limit={10 ** (ACX_PEAK_CEILING / 20):.4f}:level=disabled",
             "highpass=f=70"]
    if head > 0:
        chain.append(f"adelay={int(head * 1000)}|{int(head * 1000)}")
    if tail > 0:
        chain.append(f"apad=pad_dur={tail}")
    args = [ffmpeg(), "-y", "-i", str(src), "-af", ",".join(chain),
            "-ar", ACX_RATE, "-b:a", ACX_BITRATE, "-codec:a", "libmp3lame"]
    if mono:
        args += ["-ac", "1"]
    args.append(str(dst))
    _run(args)


def master(src: Path, dst: Path, target_rms: float = ACX_RMS_TARGET,
           head_silence: float = 0.6, tail_silence: float = 2.0,
           mono: bool = True, max_passes: int = 3) -> tuple[Levels, Levels]:
    """Normalise into the ACX window, ceiling the peaks, and add room tone.

    The correction has to happen *after* the room tone is added, not before.
    ffmpeg measures RMS across the whole file, so padding a chapter with silence
    lowers its measured level — compute the gain from the unpadded audio and the
    finished file lands under target, which is exactly the kind of thing ACX
    rejects. So: apply, re-measure the real output, and correct until it is
    inside the window.

    Returns (before, after) so the UI can show what changed.
    """
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    before = measure(src)

    gain = target_rms - before.mean_db
    _apply(src, dst, gain, head_silence, tail_silence, mono)
    after = measure(dst)

    passes = 1
    while not after.acx_ok and passes < max_passes:
        drift = target_rms - after.mean_db
        if abs(drift) < 0.3 and after.peak_db <= -3.0:
            break
        gain += drift
        # re-render from the source each time, so the padding is applied once and
        # the audio is never re-encoded on top of a previous encode
        _apply(src, dst, gain, head_silence, tail_silence, mono)
        after = measure(dst)
        passes += 1
    return before, after


def concat(files: list[Path], dst: Path, bitrate: str = ACX_BITRATE) -> Path:
    if not files:
        raise AudioError("Nothing to join.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "files.txt"
        listing.write_text("\n".join(f"file '{Path(f).as_posix()}'" for f in files), encoding="utf-8")
        _run([ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
              "-codec:a", "libmp3lame", "-b:a", bitrate, "-ar", ACX_RATE, str(dst)])
    return dst


def make_m4b(files: list[Path], titles: list[str], dst: Path, book_title: str,
             author: str, cover: Path | None = None) -> Path:
    """A single chaptered audiobook file — what Apple Books and most players want."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        listing = tmpdir / "files.txt"
        listing.write_text("\n".join(f"file '{Path(f).as_posix()}'" for f in files), encoding="utf-8")

        meta = [";FFMETADATA1", f"title={book_title}", f"artist={author}", f"album={book_title}"]
        start_ms = 0
        for path, title in zip(files, titles):
            end_ms = start_ms + int(duration(Path(path)) * 1000)
            meta += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}",
                     f"title={title}"]
            start_ms = end_ms
        meta_file = tmpdir / "chapters.txt"
        meta_file.write_text("\n".join(meta), encoding="utf-8")

        args = [ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                "-i", str(meta_file)]
        maps = ["-map", "0:a"]
        if cover and Path(cover).exists():
            args += ["-i", str(cover)]
            maps += ["-map", "2:v", "-disposition:v:0", "attached_pic", "-c:v", "mjpeg"]
        args += ["-map_metadata", "1", *maps, "-c:a", "aac", "-b:a", "128k", str(dst)]
        _run(args)
    return dst


def retail_sample(source: Path, dst: Path, start: float = 30.0, length: float = 180.0) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    _run([ffmpeg(), "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(source),
          "-codec:a", "libmp3lame", "-b:a", ACX_BITRATE, "-ar", ACX_RATE, str(dst)])
    return dst


def tag_mp3(path: Path, title: str, artist: str, album: str, track: int,
            year: int | None = None, cover: Path | None = None) -> Path:
    out = path.with_suffix(".tagged.mp3")
    args = [ffmpeg(), "-y", "-i", str(path)]
    maps = ["-map", "0:a"]
    if cover and Path(cover).exists():
        args += ["-i", str(cover)]
        maps += ["-map", "1:v", "-disposition:v:0", "attached_pic", "-c:v", "mjpeg"]
    args += [*maps, "-c:a", "copy",
             "-metadata", f"title={title}", "-metadata", f"artist={artist}",
             "-metadata", f"album={album}", "-metadata", f"track={track}"]
    if year:
        args += ["-metadata", f"date={year}"]
    args.append(str(out))
    _run(args)
    out.replace(path)
    return path


# --------------------------------------------------------------------- RSS ---

def rss_feed(title: str, description: str, author: str, email: str, site_url: str,
             cover_url: str, episodes: list[dict], language: str = "en",
             category: str = "Technology", explicit: bool = False) -> str:
    """A podcast RSS feed Apple and Spotify will both accept.

    episodes: [{title, description, audio_url, bytes, seconds, published (datetime),
                episode_number, guid}]
    """
    e = html.escape

    def stamp(dt: datetime) -> str:
        return format_datetime(dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc))

    def hhmmss(seconds: float) -> str:
        s = int(seconds)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    items = []
    for ep in episodes:
        items.append(f"""    <item>
      <title>{e(ep['title'])}</title>
      <description><![CDATA[{ep.get('description', '')}]]></description>
      <itunes:summary><![CDATA[{ep.get('description', '')}]]></itunes:summary>
      <enclosure url="{e(ep['audio_url'])}" length="{int(ep.get('bytes', 0))}" type="audio/mpeg"/>
      <guid isPermaLink="false">{e(str(ep.get('guid', ep['audio_url'])))}</guid>
      <pubDate>{stamp(ep.get('published') or datetime.now(timezone.utc))}</pubDate>
      <itunes:duration>{hhmmss(ep.get('seconds', 0))}</itunes:duration>
      <itunes:episode>{int(ep.get('episode_number', 1))}</itunes:episode>
      <itunes:explicit>{'true' if explicit else 'false'}</itunes:explicit>
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{e(title)}</title>
    <link>{e(site_url)}</link>
    <language>{e(language)}</language>
    <description><![CDATA[{description}]]></description>
    <itunes:author>{e(author)}</itunes:author>
    <itunes:summary><![CDATA[{description}]]></itunes:summary>
    <itunes:owner>
      <itunes:name>{e(author)}</itunes:name>
      <itunes:email>{e(email)}</itunes:email>
    </itunes:owner>
    <itunes:image href="{e(cover_url)}"/>
    <itunes:category text="{e(category)}"/>
    <itunes:explicit>{'true' if explicit else 'false'}</itunes:explicit>
    <itunes:type>episodic</itunes:type>
    <lastBuildDate>{stamp(datetime.now(timezone.utc))}</lastBuildDate>
{chr(10).join(items)}
  </channel>
</rss>
"""
