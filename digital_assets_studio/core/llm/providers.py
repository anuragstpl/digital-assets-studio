"""Concrete text providers. One small class per API shape."""
from __future__ import annotations

import json
import logging

import httpx

from .base import Completion, LLMError, Message, TextProvider, join_system

log = logging.getLogger(__name__)

_UA = {"User-Agent": "ArtaloDigiSuit/0.1"}


def _client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=20.0), headers=_UA, follow_redirects=True)


def _explain(resp: httpx.Response, provider: str) -> LLMError:
    detail = ""
    try:
        body = resp.json()
        detail = (
            body.get("error", {}).get("message")
            if isinstance(body.get("error"), dict)
            else body.get("error") or body.get("message") or json.dumps(body)[:400]
        )
    except Exception:  # noqa: BLE001
        detail = resp.text[:400]
    hints = {
        401: "the API key was rejected",
        403: "the key is valid but not allowed to use this model",
        404: "that model name does not exist for this key",
        413: "the request was too large - lower max tokens",
        429: "rate limited or out of credit",
    }
    hint = hints.get(resp.status_code, "")
    msg = f"{provider} returned {resp.status_code}"
    if hint:
        msg += f" ({hint})"
    if detail:
        msg += f": {detail}"
    return LLMError(msg, resp.status_code, provider)


# --------------------------------------------------------------- Anthropic ---

class AnthropicProvider:
    kind = "anthropic"
    API = "https://api.anthropic.com/v1"

    def __init__(self, api_key: str, base_url: str = "") -> None:
        self.api_key = api_key
        self.base = (base_url or self.API).rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def complete(self, messages, model, temperature=0.7, max_tokens=4096, timeout=180.0) -> Completion:
        system, rest = join_system(messages)
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": m.role, "content": m.content} for m in rest] or
                        [{"role": "user", "content": system or "Hello"}],
        }
        if system:
            payload["system"] = system
        with _client(timeout) as c:
            r = c.post(f"{self.base}/messages", json=payload, headers=self._headers())
        if r.status_code >= 400:
            raise _explain(r, "Anthropic")
        data = r.json()
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        return Completion(text.strip(), data.get("model", model),
                          usage.get("input_tokens", 0), usage.get("output_tokens", 0), data)

    def list_models(self) -> list[str]:
        with _client(30) as c:
            r = c.get(f"{self.base}/models?limit=100", headers=self._headers())
        if r.status_code >= 400:
            raise _explain(r, "Anthropic")
        return [m["id"] for m in r.json().get("data", [])]

    def test(self) -> str:
        out = self.complete([Message("user", "Reply with the single word: ready")],
                            model="claude-3-5-haiku-latest", max_tokens=16, timeout=45)
        return f"OK - {out.model} replied {out.text[:40]!r}"


# ------------------------------------------------------ OpenAI & compatible ---

class OpenAIProvider:
    """Works for api.openai.com and anything speaking the same dialect
    (Ollama, LM Studio, OpenRouter, vLLM, Together, Groq...)."""
    kind = "openai"

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1") -> None:
        self.api_key = api_key
        self.base = (base_url or "https://api.openai.com/v1").rstrip("/")

    def _headers(self) -> dict[str, str]:
        h = {"content-type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def complete(self, messages, model, temperature=0.7, max_tokens=4096, timeout=180.0) -> Completion:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        with _client(timeout) as c:
            r = c.post(f"{self.base}/chat/completions", json=payload, headers=self._headers())
            if r.status_code == 400 and "max_tokens" in r.text:
                # newer reasoning models renamed the field
                payload.pop("max_tokens")
                payload["max_completion_tokens"] = max_tokens
                payload.pop("temperature", None)
                r = c.post(f"{self.base}/chat/completions", json=payload, headers=self._headers())
        if r.status_code >= 400:
            raise _explain(r, "OpenAI-compatible endpoint")
        data = r.json()
        choices = data.get("choices") or []
        text = (choices[0].get("message", {}).get("content") if choices else "") or ""
        usage = data.get("usage", {})
        return Completion(text.strip(), data.get("model", model),
                          usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), data)

    def list_models(self) -> list[str]:
        with _client(30) as c:
            r = c.get(f"{self.base}/models", headers=self._headers())
        if r.status_code >= 400:
            raise _explain(r, "OpenAI-compatible endpoint")
        return sorted(m.get("id", "") for m in r.json().get("data", []) if m.get("id"))

    def test(self) -> str:
        models = self.list_models()
        return f"OK - endpoint reachable, {len(models)} models available"


# ------------------------------------------------------------------ Google ---

class GoogleProvider:
    kind = "google"
    API = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, api_key: str, base_url: str = "") -> None:
        self.api_key = api_key
        self.base = (base_url or self.API).rstrip("/")

    def complete(self, messages, model, temperature=0.7, max_tokens=4096, timeout=180.0) -> Completion:
        system, rest = join_system(messages)
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in rest
        ] or [{"role": "user", "parts": [{"text": system or "Hello"}]}]
        payload = {
            "contents": contents,
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        url = f"{self.base}/models/{model}:generateContent"
        with _client(timeout) as c:
            r = c.post(url, json=payload, headers={"x-goog-api-key": self.api_key,
                                                   "content-type": "application/json"})
        if r.status_code >= 400:
            raise _explain(r, "Google")
        data = r.json()
        cands = data.get("candidates") or []
        parts = cands[0].get("content", {}).get("parts", []) if cands else []
        text = "".join(p.get("text", "") for p in parts)
        usage = data.get("usageMetadata", {})
        return Completion(text.strip(), model,
                          usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0), data)

    def list_models(self) -> list[str]:
        with _client(30) as c:
            r = c.get(f"{self.base}/models", headers={"x-goog-api-key": self.api_key})
        if r.status_code >= 400:
            raise _explain(r, "Google")
        out = []
        for m in r.json().get("models", []):
            name = m.get("name", "").removeprefix("models/")
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                out.append(name)
        return sorted(out)

    def test(self) -> str:
        models = self.list_models()
        return f"OK - key valid, {len(models)} models available"


def build_text_provider(kind: str, api_key: str, base_url: str = "") -> TextProvider:
    if kind == "anthropic":
        return AnthropicProvider(api_key, base_url)
    if kind == "google":
        return GoogleProvider(api_key, base_url)
    if kind in ("openai", "openai_compat"):
        return OpenAIProvider(api_key, base_url or "https://api.openai.com/v1")
    raise LLMError(f"Unknown text provider kind: {kind}")
