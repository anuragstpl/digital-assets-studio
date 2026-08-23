"""Non-secret settings: provider choices, role routing, UI preferences.

Secrets never land in here - only the *name* of the credential to look up in the
key vault. That means settings.json is safe to sync, diff or paste into a
support thread.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from typing import Any

from ..config import ALL_ROLES, SETTINGS_FILE, WORKSPACE
from .events import BUS, TOPIC_SETTINGS

log = logging.getLogger(__name__)

UI_REVISION = 1


@dataclass
class ProviderConfig:
    """One configured LLM endpoint."""
    id: str                       # stable slug, e.g. "anthropic"
    kind: str                     # anthropic | openai | google | openai_compat | gemini_image | openai_image
    label: str = ""
    base_url: str = ""            # only used by openai_compat
    default_model: str = ""
    enabled: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def secret_name(self) -> str:
        return f"provider::{self.id}"


@dataclass
class RoleRoute:
    provider_id: str = ""
    model: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096


DEFAULT_PROVIDERS: list[ProviderConfig] = [
    ProviderConfig("anthropic", "anthropic", "Anthropic (Claude)", default_model="claude-sonnet-4-5"),
    ProviderConfig("openai", "openai", "OpenAI", default_model="gpt-4.1"),
    ProviderConfig("google", "google", "Google (Gemini)", default_model="gemini-2.5-pro"),
    ProviderConfig("deepseek", "openai_compat", "DeepSeek",
                   base_url="https://api.deepseek.com/v1", default_model="deepseek-chat"),
    ProviderConfig("local", "openai_compat", "Local / OpenAI-compatible",
                   base_url="http://localhost:11434/v1", default_model="llama3.1:8b", enabled=False),
    ProviderConfig("openrouter", "openai_compat", "OpenRouter",
                   base_url="https://openrouter.ai/api/v1", default_model="anthropic/claude-sonnet-4.5", enabled=False),
    ProviderConfig("gemini_image", "gemini_image", "Gemini image", default_model="imagen-4.0-generate-001"),
    ProviderConfig("openai_image", "openai_image", "OpenAI image", default_model="gpt-image-1", enabled=False),
    ProviderConfig("comfy", "sd_webui", "Local Stable Diffusion / ComfyUI",
                   base_url="http://127.0.0.1:7860", default_model="", enabled=False),
]

TEXT_KINDS = {"anthropic", "openai", "google", "openai_compat"}
IMAGE_KINDS = {"gemini_image", "openai_image", "sd_webui"}


@dataclass
class Settings:
    dark_mode: bool = False
    author_name: str = ""
    imprint: str = ""
    default_currency: str = "USD"
    providers: list[ProviderConfig] = field(default_factory=lambda: [ProviderConfig(**asdict(p)) for p in DEFAULT_PROVIDERS])
    routes: dict[str, RoleRoute] = field(default_factory=dict)
    onboarded: bool = False
    ui_revision: int = 0        # bumped when the default look changes

    # ------------------------------------------------------------- helpers --
    def provider(self, pid: str) -> ProviderConfig | None:
        for p in self.providers:
            if p.id == pid:
                return p
        return None

    def text_providers(self) -> list[ProviderConfig]:
        return [p for p in self.providers if p.kind in TEXT_KINDS]

    def image_providers(self) -> list[ProviderConfig]:
        return [p for p in self.providers if p.kind in IMAGE_KINDS]

    def route(self, role: str) -> RoleRoute:
        r = self.routes.get(role)
        if r is None:
            r = RoleRoute()
            self.routes[role] = r
        return r


_lock = threading.RLock()
_settings: Settings | None = None


def _from_dict(d: dict[str, Any]) -> Settings:
    provs = [ProviderConfig(**{**asdict(p), **_match(d.get("providers", []), p.id)}) for p in DEFAULT_PROVIDERS]
    known = {p.id for p in provs}
    for raw in d.get("providers", []):
        if raw.get("id") and raw["id"] not in known:
            try:
                provs.append(ProviderConfig(**raw))
            except TypeError:
                log.warning("ignoring malformed provider entry: %r", raw)
    routes = {k: RoleRoute(**v) for k, v in (d.get("routes") or {}).items()}
    return Settings(
        dark_mode=d.get("dark_mode", False),
        author_name=d.get("author_name", ""),
        imprint=d.get("imprint", ""),
        default_currency=d.get("default_currency", "USD"),
        providers=provs,
        routes=routes,
        onboarded=d.get("onboarded", False),
        ui_revision=d.get("ui_revision", 0),
    )


def _match(raw_list: list[dict], pid: str) -> dict:
    for raw in raw_list:
        if raw.get("id") == pid:
            return {k: v for k, v in raw.items() if k in ProviderConfig.__dataclass_fields__}
    return {}


def load() -> Settings:
    global _settings
    with _lock:
        if _settings is not None:
            return _settings
        if SETTINGS_FILE.exists():
            try:
                _settings = _from_dict(json.loads(SETTINGS_FILE.read_text("utf-8")))
            except Exception:  # noqa: BLE001
                log.exception("settings.json unreadable; starting from defaults")
                _settings = Settings()
        else:
            _settings = Settings()
        _seed_routes(_settings)
        if _settings.ui_revision < UI_REVISION:
            # the suite moved to a light default; adopt it once, then never touch
            # the user's choice again
            _settings.dark_mode = False
            _settings.ui_revision = UI_REVISION
        return _settings


def usable(p: ProviderConfig) -> bool:
    """A provider we could actually call right now: enabled, and either keyless
    by nature or holding a saved key."""
    from .keyvault import has_secret

    if not p.enabled:
        return False
    if p.kind == "sd_webui":
        return True
    if p.kind == "openai_compat" and ("localhost" in p.base_url or "127.0.0.1" in p.base_url):
        return True
    return has_secret(p.secret_name)


def retarget_unkeyed_roles(s: "Settings") -> int:
    """Point any role sitting on a provider with no key at one that has one.

    Called after a key is saved, so adding your first key wires up the whole
    suite instead of leaving every role on a provider you never configured.
    """
    from ..config import IMAGE_ROLES

    image_role_ids = {r[0] for r in IMAGE_ROLES}
    text_ok = [p for p in s.text_providers() if usable(p)]
    image_ok = [p for p in s.image_providers() if usable(p)]
    changed = 0
    for role_id, _, _ in ALL_ROLES:
        r = s.route(role_id)
        current = s.provider(r.provider_id) if r.provider_id else None
        if current is not None and usable(current):
            continue
        pool = image_ok if role_id in image_role_ids else text_ok
        if not pool:
            continue
        r.provider_id = pool[0].id
        r.model = pool[0].default_model
        changed += 1
    return changed


def set_all_roles(s: "Settings", provider_id: str) -> int:
    """Point every text role at one provider. The 'just use this one' button."""
    from ..config import IMAGE_ROLES

    prov = s.provider(provider_id)
    if prov is None:
        return 0
    image_role_ids = {r[0] for r in IMAGE_ROLES}
    is_image = prov.kind in IMAGE_KINDS
    changed = 0
    for role_id, _, _ in ALL_ROLES:
        if (role_id in image_role_ids) != is_image:
            continue
        r = s.route(role_id)
        r.provider_id = provider_id
        r.model = prov.default_model
        changed += 1
    return changed


def _seed_routes(s: Settings) -> None:
    """Give every role a sane default the first time we see it."""
    from ..config import IMAGE_ROLES

    image_role_ids = {r[0] for r in IMAGE_ROLES}
    text_default = (next((p for p in s.text_providers() if usable(p)), None)
                    or next((p for p in s.text_providers() if p.enabled), None))
    image_default = (next((p for p in s.image_providers() if usable(p)), None)
                     or next((p for p in s.image_providers() if p.enabled), None))
    for role_id, _, _ in ALL_ROLES:
        r = s.route(role_id)
        if r.provider_id:
            continue
        src = image_default if role_id in image_role_ids else text_default
        if src is not None:
            r.provider_id = src.id
            r.model = src.default_model
    # cheap roles get lower ceilings by default
    s.route("metadata").max_tokens = 1500
    s.route("image_prompt").max_tokens = 1200
    s.route("longform").max_tokens = 8000
    s.route("longform").temperature = 0.8
    s.route("research").temperature = 0.3
    s.route("editing").temperature = 0.3


def save(s: Settings | None = None) -> None:
    global _settings
    with _lock:
        if s is not None:
            _settings = s
        if _settings is None:
            return
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        payload = {
            "dark_mode": _settings.dark_mode,
            "author_name": _settings.author_name,
            "imprint": _settings.imprint,
            "default_currency": _settings.default_currency,
            "onboarded": _settings.onboarded,
            "ui_revision": _settings.ui_revision,
            "providers": [asdict(p) for p in _settings.providers],
            "routes": {k: asdict(v) for k, v in _settings.routes.items()},
        }
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), "utf-8")
        tmp.replace(SETTINGS_FILE)
    BUS.publish(TOPIC_SETTINGS, None)
