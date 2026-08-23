"""Image providers: Gemini/Imagen, OpenAI images, local Stable Diffusion."""
from __future__ import annotations

import base64
import logging

import httpx

from .base import ImageProvider, ImageResult, LLMError

log = logging.getLogger(__name__)


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=20.0),
                        headers={"User-Agent": "DigitalAssetsStudio/0.1"}, follow_redirects=True)


def _fail(r: httpx.Response, who: str) -> LLMError:
    try:
        body = r.json()
        detail = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else str(body)[:400]
    except Exception:  # noqa: BLE001
        detail = r.text[:400]
    return LLMError(f"{who} returned {r.status_code}: {detail}", r.status_code, who)


class GeminiImageProvider:
    """Imagen via the Generative Language API predict endpoint."""
    kind = "gemini_image"
    API = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, base_url: str = "") -> None:
        self.api_key = api_key
        self.base = (base_url or self.API).rstrip("/")

    def generate(self, prompt, model, count=1, size="1024x1024", timeout=300.0) -> ImageResult:
        ratio = _aspect_from_size(size)
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {"sampleCount": max(1, min(4, count)), "aspectRatio": ratio},
        }
        with _client(timeout) as c:
            r = c.post(f"{self.base}/models/{model}:predict", json=payload,
                       headers={"x-goog-api-key": self.api_key, "content-type": "application/json"})
        if r.status_code >= 400:
            raise _fail(r, "Gemini image")
        images = []
        for pred in r.json().get("predictions", []):
            b64 = pred.get("bytesBase64Encoded") or pred.get("image", {}).get("imageBytes")
            if b64:
                images.append(base64.b64decode(b64))
        if not images:
            raise LLMError("Gemini image returned no image data (often a safety block on the prompt)")
        return ImageResult(images, model)

    def test(self) -> str:
        with _client(30) as c:
            r = c.get(f"{self.base}/models", headers={"x-goog-api-key": self.api_key})
        if r.status_code >= 400:
            raise _fail(r, "Gemini image")
        return "OK - Google key accepted"


class OpenAIImageProvider:
    kind = "openai_image"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        self.api_key = api_key
        self.base = (base_url or "https://api.openai.com/v1").rstrip("/")

    def generate(self, prompt, model, count=1, size="1024x1024", timeout=300.0) -> ImageResult:
        payload = {"model": model, "prompt": prompt, "n": max(1, min(4, count)), "size": size}
        with _client(timeout) as c:
            r = c.post(f"{self.base}/images/generations", json=payload,
                       headers={"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"})
        if r.status_code >= 400:
            raise _fail(r, "OpenAI image")
        images = []
        for item in r.json().get("data", []):
            if item.get("b64_json"):
                images.append(base64.b64decode(item["b64_json"]))
            elif item.get("url"):
                with _client(timeout) as c2:
                    got = c2.get(item["url"])
                if got.status_code < 400:
                    images.append(got.content)
        if not images:
            raise LLMError("OpenAI image returned no image data")
        return ImageResult(images, model)

    def test(self) -> str:
        with _client(30) as c:
            r = c.get(f"{self.base}/models", headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code >= 400:
            raise _fail(r, "OpenAI image")
        return "OK - OpenAI key accepted"


class SDWebUIProvider:
    """Automatic1111 / Forge / ComfyUI-with-A1111-bridge on localhost. No key needed."""
    kind = "sd_webui"

    def __init__(self, api_key: str = "", base_url: str = "http://127.0.0.1:7860") -> None:
        self.base = (base_url or "http://127.0.0.1:7860").rstrip("/")

    def generate(self, prompt, model, count=1, size="1024x1024", timeout=300.0) -> ImageResult:
        w, h = _wh(size)
        payload = {"prompt": prompt, "width": w, "height": h,
                   "batch_size": max(1, min(4, count)), "steps": 30, "cfg_scale": 6.5}
        if model:
            payload["override_settings"] = {"sd_model_checkpoint": model}
        with _client(timeout) as c:
            r = c.post(f"{self.base}/sdapi/v1/txt2img", json=payload)
        if r.status_code >= 400:
            raise _fail(r, "Stable Diffusion")
        imgs = [base64.b64decode(b) for b in r.json().get("images", [])]
        if not imgs:
            raise LLMError("Stable Diffusion returned no images")
        return ImageResult(imgs, model or "local")

    def test(self) -> str:
        with _client(15) as c:
            r = c.get(f"{self.base}/sdapi/v1/sd-models")
        if r.status_code >= 400:
            raise _fail(r, "Stable Diffusion")
        return f"OK - {len(r.json())} checkpoints loaded"


def _wh(size: str) -> tuple[int, int]:
    try:
        w, h = size.lower().split("x")
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 1024, 1024


def _aspect_from_size(size: str) -> str:
    w, h = _wh(size)
    ratio = w / h
    table = {1.0: "1:1", 0.75: "3:4", 1.3333: "4:3", 0.5625: "9:16", 1.7778: "16:9"}
    best = min(table, key=lambda k: abs(k - ratio))
    return table[best]


def build_image_provider(kind: str, api_key: str, base_url: str = "") -> ImageProvider:
    if kind == "gemini_image":
        return GeminiImageProvider(api_key, base_url)
    if kind == "openai_image":
        return OpenAIImageProvider(api_key, base_url)
    if kind == "sd_webui":
        return SDWebUIProvider(api_key, base_url)
    raise LLMError(f"Unknown image provider kind: {kind}")
