"""Secret storage.

Keys go into the OS credential store (Windows Credential Manager, macOS
Keychain, Secret Service on Linux) via ``keyring``. If no backend is available -
a bare Linux container, a locked-down machine - we fall back to an obfuscated
file in the workspace and say so loudly in the UI, because that fallback is
*not* real security.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
import sys
from pathlib import Path

from ..config import WORKSPACE

log = logging.getLogger(__name__)

SERVICE = "ArtaloDigiSuit"
# every name this app has had, newest first; keys saved under any of them
# are still found and moved across on first read
LEGACY_SERVICES = ("DigitalAssetsStudio", "AIpathStudio")
_FALLBACK_FILE = WORKSPACE / "keys.fallback"


# A test suite must never touch the real credential store. Reading it makes
# tests pass or fail on whatever the developer happens to have configured, and
# writing it destroys real keys - which is exactly what happened: an integration
# run overwrote a live Pexels key with its fixture. DAS_KEYVAULT=memory keeps
# every secret inside this process and never reaches the OS keychain or disk.
_MEMORY_ONLY = os.environ.get("DAS_KEYVAULT", "").strip().lower() == "memory"
_memory: dict[str, str] = {}


def _load_keyring():
    try:
        import keyring
        from keyring.errors import NoKeyringError  # noqa: F401

        backend = keyring.get_keyring()
        name = backend.__class__.__name__
        if "fail" in name.lower() or "null" in name.lower():
            return None, name
        return keyring, name
    except Exception as exc:  # noqa: BLE001
        log.warning("keyring unavailable: %s", exc)
        return None, "unavailable"


_KEYRING, _BACKEND_NAME = (None, "in-memory (test mode)") if _MEMORY_ONLY else _load_keyring()


# ---------------------------------------------------------------- fallback ---

def _machine_secret() -> bytes:
    seed = "|".join([platform.node(), sys.platform, str(Path.home()), SERVICE])
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _fallback_read() -> dict[str, str]:
    if not _FALLBACK_FILE.exists():
        return {}
    try:
        raw = base64.b64decode(_FALLBACK_FILE.read_bytes())
        return json.loads(_xor(raw, _machine_secret()).decode("utf-8"))
    except Exception:  # noqa: BLE001
        log.warning("could not read fallback key file; starting empty")
        return {}


def _fallback_write(data: dict[str, str]) -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    blob = _xor(json.dumps(data).encode("utf-8"), _machine_secret())
    _FALLBACK_FILE.write_bytes(base64.b64encode(blob))
    try:
        os.chmod(_FALLBACK_FILE, 0o600)
    except OSError:
        pass


# ------------------------------------------------------------------- public --

def backend_name() -> str:
    if _KEYRING is not None:
        return _BACKEND_NAME
    return "encoded file (no OS keychain found)"


def is_secure() -> bool:
    return _KEYRING is not None and not _MEMORY_ONLY


_warned = False


def _warn_once(exc: Exception) -> None:
    global _warned
    if not _warned:
        _warned = True
        log.warning("OS keychain unavailable (%s); using the encoded fallback file", exc)


def set_secret(name: str, value: str) -> None:
    value = (value or "").strip()
    if not value:
        delete_secret(name)
        return
    if _MEMORY_ONLY:
        _memory[name] = value
        return
    if _KEYRING is not None:
        try:
            _KEYRING.set_password(SERVICE, name, value)
            return
        except Exception as exc:  # noqa: BLE001
            _warn_once(exc)
    data = _fallback_read()
    data[name] = value
    _fallback_write(data)


def get_secret(name: str) -> str:
    if _MEMORY_ONLY:
        return _memory.get(name, "")
    if _KEYRING is not None:
        try:
            got = _KEYRING.get_password(SERVICE, name)
            if got:
                return got
            # keys saved under any previous name of the app move across the
            # first time they are asked for, so a rename never costs anyone a key
            for legacy in LEGACY_SERVICES:
                old = _KEYRING.get_password(legacy, name)
                if old:
                    try:
                        _KEYRING.set_password(SERVICE, name, old)
                    except Exception:  # noqa: BLE001
                        pass
                    return old
        except Exception as exc:  # noqa: BLE001
            _warn_once(exc)
    # Environment variables win as a last resort so CI / scripts can run headless.
    return _fallback_read().get(name) or os.environ.get(name.upper(), "")


def delete_secret(name: str) -> None:
    if _MEMORY_ONLY:
        _memory.pop(name, None)
        return
    if _KEYRING is not None:
        try:
            _KEYRING.delete_password(SERVICE, name)
        except Exception:  # noqa: BLE001
            pass
    data = _fallback_read()
    if data.pop(name, None) is not None:
        _fallback_write(data)


def has_secret(name: str) -> bool:
    return bool(get_secret(name))


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 10:
        return "•" * len(value)
    return f"{value[:4]}{'•' * 10}{value[-4:]}"
