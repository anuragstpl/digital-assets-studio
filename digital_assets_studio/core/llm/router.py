"""Role-based routing: every call site asks for a *role*, never a model name.

    text(ROLE_LONGFORM, system="...", user="...")

Which provider and model actually serves that role is a settings decision the
user makes once, in one screen, and can change without touching any pipeline.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from ...config import ROLE_IMAGE
from .. import keyvault
from ..settings import ProviderConfig, load as load_settings
from .base import Completion, ImageResult, LLMError, Message
from .images import build_image_provider
from .providers import build_text_provider

log = logging.getLogger(__name__)


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    images: int = 0
    seconds: float = 0.0


USAGE = Usage()


def _resolve(role: str) -> tuple[ProviderConfig, str, float, int]:
    s = load_settings()
    route = s.route(role)
    provider = s.provider(route.provider_id) if route.provider_id else None
    if provider is None:
        raise LLMError(
            f"No provider is assigned to the '{role}' role yet. "
            f"Open Settings › Model routing and pick one."
        )
    if not provider.enabled:
        raise LLMError(f"Provider '{provider.label}' is turned off but the '{role}' role still points at it.")
    model = route.model or provider.default_model
    if not model:
        raise LLMError(f"No model set for the '{role}' role.")
    return provider, model, route.temperature, route.max_tokens


def _key_for(p: ProviderConfig) -> str:
    key = keyvault.get_secret(p.secret_name)
    local = p.kind == "openai_compat" and ("localhost" in p.base_url or "127.0.0.1" in p.base_url)
    needs_key = p.kind != "sd_webui" and not local
    if needs_key and not key:
        s = load_settings()
        others = [x.label for x in s.providers
                  if x.enabled and x.id != p.id and keyvault.get_secret(x.secret_name)]
        hint = (f" You do have a key for {', '.join(others)} — open Settings › Providers, "
                f"press 'Use for every role' on the one you want, and this will route there."
                if others else " Add it in Settings › Providers.")
        raise LLMError(f"No API key saved for {p.label}, which is what this job is routed to.{hint}")
    return key


def text(
    role: str,
    user: str,
    system: str = "",
    history: list[Message] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: float = 300.0,
) -> Completion:
    provider, model, temp, cap = _resolve(role)
    client = build_text_provider(provider.kind, _key_for(provider), provider.base_url)
    messages: list[Message] = []
    if system:
        messages.append(Message("system", system))
    if history:
        messages.extend(history)
    messages.append(Message("user", user))

    started = time.time()
    last: Exception | None = None
    for attempt in range(3):
        try:
            out = client.complete(
                messages, model,
                temperature=temp if temperature is None else temperature,
                max_tokens=cap if max_tokens is None else max_tokens,
                timeout=timeout,
            )
            USAGE.calls += 1
            USAGE.input_tokens += out.input_tokens
            USAGE.output_tokens += out.output_tokens
            USAGE.seconds += time.time() - started
            return out
        except LLMError as exc:
            last = exc
            if exc.status in (429, 500, 502, 503, 504) and attempt < 2:
                wait = 3 * (attempt + 1) ** 2
                log.warning("%s - retrying in %ss", exc, wait)
                time.sleep(wait)
                continue
            raise
        except Exception as exc:  # noqa: BLE001 - network hiccups
            last = exc
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            raise LLMError(f"{provider.label}: {exc}") from exc
    raise LLMError(str(last))


def text_json(role: str, user: str, system: str = "", **kw) -> dict | list:
    """Ask for JSON and actually get JSON back, even when the model wraps it in
    prose or a code fence."""
    guard = ("Respond with valid JSON only. No prose before or after, no markdown "
             "code fences, no trailing commas.")
    system = f"{system}\n\n{guard}".strip()
    out = text(role, user, system, **kw)
    return parse_json(out.text)


def parse_json(raw: str) -> dict | list:
    raw = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", raw, re.S)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # last resort: grab the outermost brace/bracket pair
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Model did not return usable JSON. First 200 chars: {raw[:200]!r}")


def image(prompt: str, count: int = 1, size: str = "1024x1024", timeout: float = 300.0) -> ImageResult:
    provider, model, _, _ = _resolve(ROLE_IMAGE)
    client = build_image_provider(provider.kind, _key_for(provider), provider.base_url)
    out = client.generate(prompt, model, count=count, size=size, timeout=timeout)
    USAGE.images += len(out.images)
    return out


def test_provider(p: ProviderConfig) -> str:
    from ..settings import IMAGE_KINDS

    key = keyvault.get_secret(p.secret_name)
    if p.kind in IMAGE_KINDS:
        return build_image_provider(p.kind, key, p.base_url).test()
    return build_text_provider(p.kind, key, p.base_url).test()


def list_models(p: ProviderConfig) -> list[str]:
    from ..settings import IMAGE_KINDS

    if p.kind in IMAGE_KINDS:
        return []
    key = keyvault.get_secret(p.secret_name)
    return build_text_provider(p.kind, key, p.base_url).list_models()
