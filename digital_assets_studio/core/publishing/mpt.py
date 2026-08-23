"""Bridge to a running MoneyPrinterTurbo instance.

MoneyPrinterTurbo (harry0703, MIT) generates stock-footage videos end to end. Its
dependency stack — MoviePy, Streamlit, faster-whisper, Azure Speech, litellm — is
heavier than everything else in this suite put together, so the suite does not
bundle it. If you already run it, point this at your instance and use it as the
video engine; if you do not, the native engine in stockvideo.py does the same job
on ffmpeg alone.

Start its API with `python main.py` from the MoneyPrinterTurbo folder (not the
WebUI, which is a separate Streamlit app on port 8501), then give the base URL
below.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

import httpx

from .. import keyvault

log = logging.getLogger(__name__)

BASE_KEY = "mpt::base_url"
DEFAULT_BASE = "http://127.0.0.1:8080"

ASPECTS = {"Landscape 16:9": "16:9", "Portrait 9:16": "9:16", "Square 1:1": "1:1"}
CONCAT_MODES = ["random", "sequential"]
TRANSITIONS = ["None", "Shuffle", "FadeIn", "FadeOut", "SlideIn", "SlideOut"]
SUBTITLE_POSITIONS = ["bottom", "top", "center"]
SOURCES = ["pexels", "pixabay"]


class MPTError(RuntimeError):
    pass


def base_url() -> str:
    return (keyvault.get_secret(BASE_KEY) or DEFAULT_BASE).rstrip("/")


def save_base_url(url: str) -> None:
    keyvault.set_secret(BASE_KEY, (url or DEFAULT_BASE).strip().rstrip("/"))


def configured() -> bool:
    return bool(keyvault.get_secret(BASE_KEY))


def _api(path: str) -> str:
    return f"{base_url()}/api/v1{path}"


def _check(r: httpx.Response, what: str) -> dict:
    if r.status_code >= 400:
        raise MPTError(f"MoneyPrinterTurbo {what} returned {r.status_code}: {r.text[:300]}")
    try:
        body = r.json()
    except Exception as exc:  # noqa: BLE001
        raise MPTError(f"MoneyPrinterTurbo {what} returned something that is not JSON") from exc
    if isinstance(body, dict) and body.get("status", 200) >= 400:
        raise MPTError(f"MoneyPrinterTurbo {what}: {body.get('message', 'unknown error')}")
    return body.get("data", body) if isinstance(body, dict) else {}


def ping() -> str:
    try:
        r = httpx.get(_api("/musics"), timeout=15)
    except Exception as exc:  # noqa: BLE001
        raise MPTError(
            f"Could not reach MoneyPrinterTurbo at {base_url()} ({exc}).\n\n"
            f"Start its API server from the MoneyPrinterTurbo folder:\n"
            f"    python main.py\n\n"
            f"That is the API on port 8080 — not webui.bat, which is the separate\n"
            f"Streamlit interface on port 8501 and does not answer these calls.\n"
            f"Check it is up by opening {base_url()}/docs in a browser.\n\n"
            f"You do not need any of this: the built-in stock footage engine does the\n"
            f"same job with only a free Pexels key."
        ) from exc
    _check(r, "ping")
    return f"OK — reachable at {base_url()}"


def build_params(subject: str, script: str = "", terms: list[str] | None = None,
                 aspect: str = "9:16", voice: str = "", language: str = "",
                 source: str = "pexels", clip_seconds: int = 5,
                 concat_mode: str = "random", transition: str | None = None,
                 subtitles: bool = True, subtitle_position: str = "bottom",
                 font_size: int = 60, bgm_volume: float = 0.2,
                 voice_rate: float = 1.0, count: int = 1) -> dict:
    params: dict = {
        "video_subject": subject,
        "video_script": script or "",
        "video_aspect": aspect,
        "video_concat_mode": concat_mode,
        "video_clip_duration": int(clip_seconds),
        "video_count": int(count),
        "video_source": source,
        "subtitle_enabled": bool(subtitles),
        "subtitle_position": subtitle_position,
        "font_size": int(font_size),
        "bgm_type": "random",
        "bgm_volume": float(bgm_volume),
        "voice_rate": float(voice_rate),
    }
    if terms:
        params["video_terms"] = terms
    if voice:
        params["voice_name"] = voice
    if language:
        params["video_language"] = language
    if transition and transition.lower() != "none":
        params["video_transition_mode"] = transition
    return params


def create(params: dict) -> str:
    r = httpx.post(_api("/videos"), json=params, timeout=120)
    data = _check(r, "video request")
    task_id = data.get("task_id") or data.get("taskId")
    if not task_id:
        raise MPTError(f"MoneyPrinterTurbo accepted the request but returned no task id: "
                       f"{str(data)[:200]}")
    return str(task_id)


def status(task_id: str) -> dict:
    r = httpx.get(_api(f"/tasks/{task_id}"), timeout=60)
    return _check(r, "task query")


def wait(task_id: str, timeout: float = 3600.0, poll: float = 6.0,
         progress: Callable[[float, str], None] | None = None) -> dict:
    """Block until the task produces a video, or give up with a useful message."""
    deadline = time.time() + timeout
    last = -1.0
    while time.time() < deadline:
        info = status(task_id)
        pct = float(info.get("progress", 0) or 0)
        state = info.get("state", "")
        if progress and pct != last:
            progress(min(pct / 100.0, 0.99), f"MoneyPrinterTurbo {pct:.0f}%")
            last = pct
        videos = info.get("videos") or info.get("combined_videos") or []
        if videos:
            return info
        if isinstance(state, str) and state.lower() in ("failed", "error"):
            raise MPTError(f"MoneyPrinterTurbo reported the task failed: "
                           f"{info.get('message') or 'no reason given'}")
        if isinstance(state, int) and state < 0:
            raise MPTError("MoneyPrinterTurbo reported the task failed. Check its own log — "
                           "the usual causes are a missing stock-footage key or an LLM key it "
                           "has not been given.")
        time.sleep(poll)
    raise MPTError(f"MoneyPrinterTurbo did not finish task {task_id} within "
                   f"{timeout / 60:.0f} minutes.")


def download(url_or_path: str, dest: Path) -> Path:
    """Task results come back as absolute URLs on some builds and bare paths on
    others - accept either."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = url_or_path
    if not url.startswith("http"):
        url = _api(f"/download/{url_or_path.lstrip('/')}")
    with httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0), follow_redirects=True) as c:
        with c.stream("GET", url) as r:
            if r.status_code >= 400:
                raise MPTError(f"Could not download the finished video ({r.status_code}) from {url}")
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
    if dest.stat().st_size < 10_000:
        raise MPTError("The downloaded video is suspiciously small; check the instance's output.")
    return dest


def generate(subject: str, dest: Path, script: str = "", terms: list[str] | None = None,
             aspect: str = "9:16", progress: Callable[[float, str], None] | None = None,
             **kwargs) -> Path:
    params = build_params(subject, script, terms, aspect, **kwargs)
    if progress:
        progress(0.02, f"Handing the job to MoneyPrinterTurbo at {base_url()}")
    task_id = create(params)
    if progress:
        progress(0.05, f"Task {task_id} queued")
    info = wait(task_id, progress=progress)
    videos = info.get("combined_videos") or info.get("videos") or []
    if progress:
        progress(0.97, "Downloading the finished video")
    return download(videos[0], dest)
