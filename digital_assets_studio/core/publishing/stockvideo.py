"""Free stock media from Pexels and Pixabay — clips for video, stills for covers.

The approach is the one MoneyPrinterTurbo popularised (MIT, harry0703): turn a
script into visual search terms, pull free stock clips, lay the narration over
them and burn captions. This is a native implementation on ffmpeg and httpx —
the two things the suite already needs — so it adds no dependencies and no
second Python environment to keep alive.

If you would rather run the real MoneyPrinterTurbo, the bridge in mpt.py drives
your own instance instead.
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

from .. import keyvault
from .video import LANDSCAPE, PORTRAIT, VideoError, caption_filter, ffmpeg

log = logging.getLogger(__name__)

PEXELS_KEY = "stock::pexels"
PIXABAY_KEY = "stock::pixabay"

SOURCES = ["pexels", "pixabay"]

# module-level so tests can point them at a local server
PEXELS_VIDEO_URL = "https://api.pexels.com/videos/search"
PEXELS_PHOTO_URL = "https://api.pexels.com/v1/search"
PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"
PIXABAY_PHOTO_URL = "https://pixabay.com/api/"


class StockError(RuntimeError):
    pass


@dataclass
class Clip:
    url: str
    width: int
    height: int
    seconds: float
    source: str
    preview: str = ""

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width


def has_key(source: str) -> bool:
    return bool(keyvault.get_secret(PEXELS_KEY if source == "pexels" else PIXABAY_KEY))


def save_key(source: str, value: str) -> None:
    keyvault.set_secret(PEXELS_KEY if source == "pexels" else PIXABAY_KEY, value)


def test_source(source: str) -> str:
    clips = search("city at night", source=source, portrait=False, limit=3)
    return f"OK — {source} returned {len(clips)} clips for a test query"


# ------------------------------------------------------------------ search ---

def _pexels(query: str, portrait: bool, limit: int) -> list[Clip]:
    key = keyvault.get_secret(PEXELS_KEY)
    if not key:
        raise StockError("No Pexels API key saved. Add one in Settings › Publishing › Stock footage. "
                         "Pexels keys are free.")
    r = httpx.get(PEXELS_VIDEO_URL,
                  params={"query": query, "per_page": min(max(limit, 1), 40),
                          "orientation": "portrait" if portrait else "landscape",
                          "size": "medium"},
                  headers={"Authorization": key}, timeout=60)
    if r.status_code >= 400:
        raise StockError(f"Pexels returned {r.status_code}: {r.text[:200]}")
    out: list[Clip] = []
    for v in r.json().get("videos", []):
        best = None
        for f in v.get("video_files", []):
            if f.get("file_type") != "video/mp4":
                continue
            w, h = f.get("width") or 0, f.get("height") or 0
            if w < 720 and h < 720:
                continue
            if best is None or (w * h) < (best.get("width", 0) * best.get("height", 0)):
                # smallest file that still clears 720p keeps downloads quick
                best = f
        if best:
            out.append(Clip(best["link"], best.get("width", 0), best.get("height", 0),
                            float(v.get("duration") or 0), "pexels",
                            v.get("image", "")))
    return out


def _pixabay(query: str, portrait: bool, limit: int) -> list[Clip]:
    key = keyvault.get_secret(PIXABAY_KEY)
    if not key:
        raise StockError("No Pixabay API key saved. Add one in Settings › Publishing › Stock footage.")
    r = httpx.get(PIXABAY_VIDEO_URL,
                  params={"key": key, "q": query, "per_page": min(max(limit, 3), 50),
                          "video_type": "film", "safesearch": "true"}, timeout=60)
    if r.status_code >= 400:
        raise StockError(f"Pixabay returned {r.status_code}: {r.text[:200]}")
    out: list[Clip] = []
    for hit in r.json().get("hits", []):
        videos = hit.get("videos", {})
        pick = videos.get("large") or videos.get("medium") or videos.get("small")
        if not pick or not pick.get("url"):
            continue
        w, h = pick.get("width") or 0, pick.get("height") or 0
        if portrait and w > h:
            continue
        if not portrait and h > w:
            continue
        out.append(Clip(pick["url"], w, h, float(hit.get("duration") or 0), "pixabay"))
    return out


def search(query: str, source: str = "pexels", portrait: bool = False,
           limit: int = 12) -> list[Clip]:
    fn = _pexels if source == "pexels" else _pixabay
    try:
        return fn(query, portrait, limit)
    except StockError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StockError(f"{source} search failed: {exc}") from exc


def gather(terms: list[str], seconds_needed: float, dest: Path, portrait: bool = False,
           source: str = "pexels", clip_seconds: float = 5.0,
           progress: Callable[[float, str], None] | None = None) -> list[Path]:
    """Download enough distinct clips to cover the narration."""
    dest.mkdir(parents=True, exist_ok=True)
    needed = max(2, math.ceil(seconds_needed / max(clip_seconds, 2.0)))
    found: list[Clip] = []
    seen: set[str] = set()
    for i, term in enumerate(terms or ["abstract background"]):
        if len(found) >= needed:
            break
        if progress:
            progress(i / max(len(terms), 1), f"Searching stock footage for “{term}”")
        try:
            results = search(term, source, portrait, limit=10)
        except StockError as exc:
            log.warning("%s", exc)
            if i == 0 and "API key" in str(exc):
                raise
            continue
        for clip in results:
            if clip.url in seen or clip.seconds < 2:
                continue
            seen.add(clip.url)
            found.append(clip)
            if len(found) >= needed:
                break
    if not found:
        raise StockError(
            "No stock footage came back for any of the search terms. Try broader terms, "
            "or switch the video engine to designed slides.")

    files: list[Path] = []
    with httpx.Client(timeout=httpx.Timeout(300.0, connect=30.0), follow_redirects=True) as client:
        for i, clip in enumerate(found):
            if progress:
                progress(i / len(found), f"Downloading clip {i + 1} of {len(found)}")
            name = hashlib.sha1(clip.url.encode()).hexdigest()[:16] + ".mp4"
            target = dest / name
            if not target.exists():
                try:
                    with client.stream("GET", clip.url) as resp:
                        if resp.status_code >= 400:
                            continue
                        with target.open("wb") as fh:
                            for chunk in resp.iter_bytes(1 << 20):
                                fh.write(chunk)
                except Exception as exc:  # noqa: BLE001
                    log.warning("clip download failed: %s", exc)
                    continue
            if target.exists() and target.stat().st_size > 10_000:
                files.append(target)
    if not files:
        raise StockError("Stock clips were found but none downloaded successfully.")
    return files


# ------------------------------------------------------------------ photos ---

@dataclass
class Photo:
    url: str
    width: int
    height: int
    source: str
    credit: str = ""
    alt: str = ""

    @property
    def ratio(self) -> float:
        return self.width / self.height if self.height else 1.0


def _pexels_photos(query: str, orientation: str, limit: int) -> list[Photo]:
    key = keyvault.get_secret(PEXELS_KEY)
    if not key:
        raise StockError("No Pexels API key saved. Add one in Settings › Publishing › Stock footage. "
                         "Pexels keys are free.")
    r = httpx.get(PEXELS_PHOTO_URL,
                  params={"query": query, "per_page": min(max(limit, 1), 40),
                          "orientation": orientation},
                  headers={"Authorization": key}, timeout=60)
    if r.status_code >= 400:
        raise StockError(f"Pexels returned {r.status_code}: {r.text[:200]}")
    out = []
    for ph in r.json().get("photos", []):
        src = ph.get("src", {})
        url = src.get("original") or src.get("large2x") or src.get("large")
        if not url:
            continue
        out.append(Photo(url, ph.get("width", 0), ph.get("height", 0), "pexels",
                         ph.get("photographer", ""), ph.get("alt", "")))
    return out


def _pixabay_photos(query: str, orientation: str, limit: int) -> list[Photo]:
    key = keyvault.get_secret(PIXABAY_KEY)
    if not key:
        raise StockError("No Pixabay API key saved. Add one in Settings › Publishing › Stock footage.")
    mapped = {"portrait": "vertical", "landscape": "horizontal"}.get(orientation, "all")
    r = httpx.get(PIXABAY_PHOTO_URL,
                  params={"key": key, "q": query, "image_type": "photo",
                          "orientation": mapped, "per_page": min(max(limit, 3), 50),
                          "safesearch": "true"}, timeout=60)
    if r.status_code >= 400:
        raise StockError(f"Pixabay returned {r.status_code}: {r.text[:200]}")
    out = []
    for hit in r.json().get("hits", []):
        url = hit.get("largeImageURL") or hit.get("webformatURL")
        if not url:
            continue
        out.append(Photo(url, hit.get("imageWidth", 0), hit.get("imageHeight", 0), "pixabay",
                         hit.get("user", ""), hit.get("tags", "")))
    return out


def search_photos(query: str, source: str = "pexels", orientation: str = "portrait",
                  limit: int = 12) -> list[Photo]:
    fn = _pexels_photos if source == "pexels" else _pixabay_photos
    try:
        return fn(query, orientation, limit)
    except StockError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise StockError(f"{source} photo search failed: {exc}") from exc


def best_photo(terms: list[str], source: str = "pexels", orientation: str = "portrait",
               min_width: int = 1400, target_ratio: float | None = None,
               progress: Callable[[float, str], None] | None = None) -> tuple[bytes, Photo]:
    """Search each term in turn and return the image closest to the shape you need.

    A book cover is 1:1.6, so a landscape photo cropped to it loses most of the
    frame; preferring the closest aspect ratio is what stops covers looking like a
    detail from someone else's photograph.
    """
    candidates: list[Photo] = []
    for i, term in enumerate(terms or ["abstract texture"]):
        if progress:
            progress(i / max(len(terms), 1), f"Searching {source} for “{term}”")
        try:
            found = search_photos(term, source, orientation, limit=10)
        except StockError as exc:
            if i == 0 and "API key" in str(exc):
                raise
            log.warning("%s", exc)
            continue
        candidates += [p for p in found if p.width >= min_width]
        if len(candidates) >= 12:
            break
    if not candidates:
        raise StockError(
            f"No {source} photo matched any of those terms at {min_width}px or wider. "
            f"Try broader terms, or use the typographic cover.")
    if target_ratio:
        candidates.sort(key=lambda p: abs(p.ratio - target_ratio))
    pick = candidates[0]
    if progress:
        progress(0.85, f"Downloading {pick.width}×{pick.height} from {pick.source}")
    with httpx.Client(timeout=httpx.Timeout(180.0, connect=30.0), follow_redirects=True) as c:
        r = c.get(pick.url)
        if r.status_code >= 400:
            raise StockError(f"Could not download the chosen photo ({r.status_code}).")
        return r.content, pick


def credit_line(photo: Photo) -> str:
    where = {"pexels": "Pexels", "pixabay": "Pixabay"}.get(photo.source, photo.source)
    who = photo.credit or "an uncredited contributor"
    return f"Cover photograph by {who} on {where}."


# ---------------------------------------------------------------- assembly ---

def _segment(clips: list[Path], audio: Path, seconds: float, out: Path,
             size: tuple[int, int], caption: str, font: Path | None,
             index: int, tmpdir: Path | None = None) -> Path:
    """One narration block over as many stock clips as it takes to cover it."""
    w, h = size
    per = max(2.0, seconds / max(len(clips), 1))
    inputs: list[str] = []
    filters: list[str] = []
    for i, clip in enumerate(clips):
        inputs += ["-t", f"{per:.3f}", "-i", str(clip)]
        filters.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},setsar=1,fps=30,format=yuv420p[v{i}]")
    concat_in = "".join(f"[v{i}]" for i in range(len(clips)))
    filters.append(f"{concat_in}concat=n={len(clips)}:v=1:a=0[vcat]")

    chain = "[vcat]"
    cap = caption_filter(caption, w, h, font, tmpdir or out.parent, index)
    if cap:
        filters.append(f"[vcat]{cap}[vout]")
        chain = "[vout]"

    args = [ffmpeg(), "-y", *inputs, "-i", str(audio),
            "-filter_complex", ";".join(filters),
            "-map", chain, "-map", f"{len(clips)}:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-t", f"{seconds:.3f}", str(out)]
    res = subprocess.run(args, capture_output=True, text=True, timeout=2400)
    if res.returncode != 0:
        raise VideoError(f"ffmpeg failed on stock segment {index + 1}: {res.stderr[-500:]}")
    return out


@dataclass
class Scene:
    audio: Path
    seconds: float
    caption: str = ""
    clips: list[Path] = field(default_factory=list)


def compose(scenes: list[Scene], out_path: Path, size: tuple[int, int] = LANDSCAPE,
            font: Path | None = None, music: Path | None = None, music_db: float = -22.0,
            progress: Callable[[float, str], None] | None = None) -> Path:
    if not scenes:
        raise VideoError("No scenes to compose.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        segments: list[Path] = []
        for i, sc in enumerate(scenes):
            if progress:
                progress(i / len(scenes), f"Building scene {i + 1} of {len(scenes)}")
            if not sc.clips:
                raise VideoError(f"Scene {i + 1} has no footage.")
            segments.append(_segment(sc.clips, sc.audio, sc.seconds,
                                     tmpdir / f"seg_{i:03d}.mp4", size, sc.caption, font, i,
                                     tmpdir))
        listing = tmpdir / "segments.txt"
        listing.write_text("\n".join(f"file '{p.as_posix()}'" for p in segments), encoding="utf-8")
        if progress:
            progress(0.9, "Joining scenes")
        joined = tmpdir / "joined.mp4"
        res = subprocess.run([ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                              "-c", "copy", str(joined)],
                             capture_output=True, text=True, timeout=2400)
        if res.returncode != 0:
            raise VideoError(f"Joining stock scenes failed: {res.stderr[-500:]}")

        if music and Path(music).exists():
            if progress:
                progress(0.95, "Mixing music underneath")
            res = subprocess.run(
                [ffmpeg(), "-y", "-i", str(joined), "-stream_loop", "-1", "-i", str(music),
                 "-filter_complex",
                 f"[1:a]volume={music_db}dB[bg];[0:a][bg]amix=inputs=2:duration=first:"
                 f"dropout_transition=3[a]",
                 "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                 str(out_path)], capture_output=True, text=True, timeout=2400)
            if res.returncode != 0:
                raise VideoError(f"Mixing music failed: {res.stderr[-500:]}")
        else:
            shutil.copy(joined, out_path)
    if progress:
        progress(1.0, "Stock render complete")
    return out_path


def assign_clips(files: list[Path], scenes: list[Scene], clip_seconds: float = 5.0,
                 shuffle: bool = True, seed: int = 7) -> None:
    """Spread the downloaded clips across the scenes without repeating a clip
    back to back."""
    pool = list(files)
    if shuffle:
        random.Random(seed).shuffle(pool)
    if not pool:
        return
    cursor = 0
    for sc in scenes:
        need = max(1, math.ceil(sc.seconds / max(clip_seconds, 2.0)))
        chosen: list[Path] = []
        for _ in range(need):
            chosen.append(pool[cursor % len(pool)])
            cursor += 1
        sc.clips = chosen
