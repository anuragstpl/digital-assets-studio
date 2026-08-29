"""AI-generated footage through OpenRouter.

OpenRouter aggregates the video models (Veo, Sora, Kling, Seedance, Wan, Hailuo,
Grok Imagine...) behind one key and one API shape, which is the only reason this
is practical: the suite needs a single credential and a single request format
rather than an account and an SDK per vendor.

Generation is asynchronous: POST /videos returns a job id and a polling URL, and
the finished mp4 is fetched from the job once it reports completed. Nothing here
streams, so a clip that takes four minutes to render blocks for four minutes.

The key is the same one Settings > Providers already stores for the OpenRouter
text provider, so configuring it once covers scripts and footage both.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

import httpx

from .. import keyvault

log = logging.getLogger(__name__)

# module-level so the integration tests can point them at a mock server
BASE = "https://openrouter.ai/api/v1"

# the OpenRouter provider in settings.py owns this secret; sharing it means one
# key configured in one place serves both the text roles and the video engine
KEY_NAME = "provider::openrouter"

DEFAULT_VIDEO_MODEL = "google/veo-3.1-fast"

RESOLUTIONS = ["720p", "1080p"]
DONE_STATES = {"completed", "succeeded", "success", "done"}
DEAD_STATES = {"failed", "error", "cancelled", "canceled", "expired"}


class AIVideoError(RuntimeError):
    pass


def api_key() -> str:
    return keyvault.get_secret(KEY_NAME)


def has_key() -> bool:
    return bool(api_key())


def _headers() -> dict[str, str]:
    key = api_key()
    if not key:
        raise AIVideoError(
            "No OpenRouter key saved. The AI video engine calls OpenRouter, and the key "
            "lives in Settings > Providers > OpenRouter - the same one the text roles use. "
            "Create one at openrouter.ai/keys, paste it there, press Save.")
    return {"Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/anuragstpl/digital-assets-studio",
            "X-Title": "Artalo Digi Suit"}


def _explain(r: httpx.Response, what: str) -> AIVideoError:
    detail = ""
    try:
        body = r.json()
        err = body.get("error")
        detail = (err.get("message") if isinstance(err, dict) else err) or str(body)[:400]
    except Exception:  # noqa: BLE001
        detail = r.text[:400]
    hints = {
        401: "the OpenRouter key was rejected",
        402: "OpenRouter is out of credit - video models are pay as you go and are not "
             "covered by any free tier",
        403: "the key is not allowed to use that model",
        404: "no such model, or OpenRouter no longer serves it - press 'List video models' "
             "in Settings > Publishing to see what it does",
        429: "rate limited",
    }
    hint = hints.get(r.status_code, "")
    msg = f"OpenRouter {what} returned {r.status_code}"
    if hint:
        msg += f" ({hint})"
    if detail:
        msg += f": {detail}"
    return AIVideoError(msg)


# ----------------------------------------------------------------- catalogue --

_catalogue: dict[str, list[dict]] = {}


def list_models(output: str = "video", refresh: bool = False) -> list[dict]:
    """Models OpenRouter currently serves that emit `output`.

    Cached for the life of the process: the catalogue moves week to week, not
    second to second, and every caller that asks would otherwise pay for a round
    trip it does not need."""
    if not refresh and output in _catalogue:
        return _catalogue[output]
    try:
        r = httpx.get(f"{BASE}/models", params={"output_modalities": output},
                      headers={"Accept": "application/json"}, timeout=60)
    except Exception as exc:  # noqa: BLE001
        raise AIVideoError(f"Could not reach OpenRouter to list models: {exc}") from exc
    if r.status_code >= 400:
        raise _explain(r, "model list")
    out = []
    for m in r.json().get("data", []):
        mods = (m.get("architecture") or {}).get("output_modalities") or []
        if output not in mods:
            continue
        out.append({"id": m.get("id", ""), "name": m.get("name", ""),
                    "modality": (m.get("architecture") or {}).get("modality", ""),
                    "pricing": m.get("pricing") or {}})
    out.sort(key=lambda m: m["id"])
    _catalogue[output] = out
    return out


def video_models(refresh: bool = False) -> list[str]:
    return [m["id"] for m in list_models("video", refresh)]


def test() -> str:
    if not has_key():
        raise AIVideoError(
            "No OpenRouter key saved yet. Add it in Settings > Providers > OpenRouter.")
    vids = list_models("video", refresh=True)
    return f"OK - key saved, {len(vids)} video models reachable through OpenRouter"


# --------------------------------------------------------------------- video --

def _aspect(portrait: bool) -> str:
    return "9:16" if portrait else "16:9"


def create_video(prompt: str, model: str = "", seconds: int = 8, portrait: bool = False,
                 resolution: str = "720p", generate_audio: bool = False,
                 seed: int | None = None) -> dict:
    """Queue one generation. Returns the job payload, including its polling URL."""
    payload: dict = {
        "model": model or DEFAULT_VIDEO_MODEL,
        "prompt": prompt,
        "aspect_ratio": _aspect(portrait),
        "resolution": resolution or "720p",
        "generate_audio": bool(generate_audio),
    }
    if seconds:
        payload["duration"] = int(seconds)
    if seed is not None:
        payload["seed"] = int(seed)
    headers = _headers()
    try:
        r = httpx.post(f"{BASE}/videos", json=payload, headers=headers, timeout=180)
    except Exception as exc:  # noqa: BLE001
        raise AIVideoError(f"Could not reach OpenRouter: {exc}") from exc
    if r.status_code >= 400:
        raise _explain(r, "video request")
    job = r.json()
    if not job.get("id"):
        raise AIVideoError("OpenRouter accepted the request but returned no job id: "
                           f"{str(job)[:200]}")
    return job


def poll_video(job: dict, timeout: float = 1800.0, poll: float = 5.0,
               progress: Callable[[float, str], None] | None = None) -> dict:
    """Wait for one job and return its completed payload."""
    url = job.get("polling_url") or f"{BASE}/videos/{job.get('id', '')}"
    deadline = time.time() + timeout
    waited = 0.0
    while time.time() < deadline:
        r = httpx.get(url, headers=_headers(), timeout=60)
        if r.status_code >= 400:
            raise _explain(r, "job status")
        info = r.json()
        state = str(info.get("status") or "").lower()
        if state in DONE_STATES:
            return info
        if state in DEAD_STATES:
            raise AIVideoError(
                "OpenRouter reported the generation failed: "
                f"{info.get('error') or info.get('message') or state}")
        if progress:
            # no percentage comes back, so report elapsed time rather than invent one
            progress(min(waited / max(timeout, 1.0), 0.95),
                     f"Generating - {waited:.0f}s elapsed ({state or 'queued'})")
        time.sleep(poll)
        waited += poll
    raise AIVideoError(f"OpenRouter did not finish job {job.get('id')} within "
                       f"{timeout / 60:.0f} minutes.")


def _video_urls(info: dict) -> list[str]:
    for key in ("unsigned_urls", "urls", "output_urls", "videos"):
        vals = info.get(key)
        if isinstance(vals, list) and vals:
            return [v if isinstance(v, str) else (v.get("url") or "") for v in vals]
    job_id = info.get("id")
    return [f"{BASE}/videos/{job_id}/content?index=0"] if job_id else []


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0), follow_redirects=True) as c:
        with c.stream("GET", url, headers=_headers()) as r:
            if r.status_code >= 400:
                raise AIVideoError(f"Could not download the finished clip ({r.status_code}).")
            with dest.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
    if dest.stat().st_size < 10_000:
        dest.unlink()
        raise AIVideoError("The clip OpenRouter returned was empty.")
    return dest


def generate_video(prompt: str, dest: Path, model: str = "", seconds: int = 8,
                   portrait: bool = False, resolution: str = "720p",
                   generate_audio: bool = False, seed: int | None = None,
                   timeout: float = 1800.0, poll_seconds: float = 5.0,
                   progress: Callable[[float, str], None] | None = None) -> Path:
    """Queue, wait, download. One clip."""
    job = create_video(prompt, model, seconds, portrait, resolution, generate_audio, seed)
    if progress:
        progress(0.05, f"Queued with {model or DEFAULT_VIDEO_MODEL}")
    info = poll_video(job, timeout=timeout, poll=poll_seconds, progress=progress)
    urls = [u for u in _video_urls(info) if u]
    if not urls:
        raise AIVideoError("OpenRouter reported the job finished but returned no video URL.")
    if progress:
        progress(0.97, "Downloading the clip")
    return download(urls[0], dest)


# ------------------------------------------------------------------- helpers --

def gather(prompts: list[str], count: int, dest: Path, model: str = "",
           seconds: int = 8, portrait: bool = False, resolution: str = "720p",
           style: str = "", poll_seconds: float = 5.0,
           progress: Callable[[float, str], None] | None = None,
           note: Callable[[str, str], None] | None = None) -> list[Path]:
    """Generate `count` clips, cycling through the visual briefs.

    Every clip is a real charge, so the caller decides how many to buy and the
    renderer reuses them across the scenes rather than generating one per scene.
    A clip that fails does not sink the batch - the ones that worked still come
    back, and the caller decides whether that is enough.
    """
    dest.mkdir(parents=True, exist_ok=True)
    briefs = [b.strip() for b in (prompts or []) if b and b.strip()] or \
        ["abstract flowing background"]
    made: list[Path] = []
    errors: list[str] = []
    total = max(1, count)
    for i in range(total):
        prompt = f"{briefs[i % len(briefs)]}. {style}".strip()
        target = dest / f"{i:03d}.mp4"
        if target.exists() and target.stat().st_size > 10_000:
            made.append(target)          # already bought on an earlier run
            continue
        if progress:
            progress(i / total, f"Generating clip {i + 1} of {total}")
        try:
            generate_video(
                prompt, target, model=model, seconds=seconds, portrait=portrait,
                resolution=resolution, poll_seconds=poll_seconds,
                progress=(lambda f, m, base=i: progress((base + f) / total, m))
                if progress else None)
            made.append(target)
        except AIVideoError as exc:
            errors.append(f"clip {i + 1}: {exc}")
            if note:
                note(f"Clip {i + 1} failed: {exc}", "warning")
            # a rejected key or an empty wallet will reject every other clip too
            if any(s in str(exc) for s in ("401", "402", "403", "No OpenRouter key")):
                break
    if not made:
        raise AIVideoError("No clips were generated.\n\n" + "\n".join(errors[:4]))
    return made
