"""Anonymous usage analytics, sent to Aptabase.

What this answers: how many people run the app, on which OS, which version, and
roughly where in the world. Nothing else. It exists so the project can tell a
dead release from a popular one, and so a step that breaks on every Windows
machine in the wild does not stay invisible.

**It must never affect the app.** A user on a plane, behind a corporate proxy, or
on a machine that cannot resolve DNS has to see exactly the same app as everyone
else. So:

  * ``track`` never blocks and never raises - it drops the event into a bounded
    queue and returns. The queue being full is not an error, it is the design.
  * One daemon thread does the sending. Nothing on the UI thread ever waits on a
    socket, and the process never delays its exit for an event.
  * A batch that fails is dropped, not retried. After a few consecutive failures
    the sender gives up for the rest of the session, so an offline machine stops
    paying for the attempt entirely.
  * Every exception in here is swallowed. Analytics is not allowed to be the
    reason anything breaks.

**What is never sent:** API keys, project names, file contents, file paths, email
addresses, prompts, or anything a model wrote. Only the fields assembled in
``_system_props`` and the short identifiers passed by the two or three call
sites. Location is not collected at all - Aptabase derives a country from the
request IP at ingest and discards the address.

Turn it off with the switch in Settings > General, ``DAS_TELEMETRY=0``, or the
conventional ``DO_NOT_TRACK=1``.
"""
from __future__ import annotations

import json
import locale
import logging
import os
import platform
import queue
import sys
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import APP_VERSION, APTABASE_APP_KEY

log = logging.getLogger(__name__)

# region prefix in the app key decides where events go; A-SH- is a self-hosted
# instance and needs DAS_APTABASE_URL pointing at it
HOSTS = {"EU": "https://eu.aptabase.com", "US": "https://us.aptabase.com", "SH": ""}

BATCH = 25                 # Aptabase rejects larger batches
QUEUE_LIMIT = 64           # a long offline run must not grow memory
GIVE_UP_AFTER = 3          # consecutive failures before this session stops trying
TIMEOUT = httpx.Timeout(8.0, connect=4.0)
SDK = "digital-assets-studio"

_queue: queue.Queue = queue.Queue(maxsize=QUEUE_LIMIT)
_worker: threading.Thread | None = None
_lock = threading.RLock()
_session_id = uuid.uuid4().hex
_failures = 0
_stopped = False
_STOP = object()


# ------------------------------------------------------------------ config --

def app_key() -> str:
    return (os.environ.get("DAS_APTABASE_KEY") or APTABASE_APP_KEY or "").strip()


def base_url() -> str:
    override = (os.environ.get("DAS_APTABASE_URL") or "").strip().rstrip("/")
    if override:
        return override
    parts = app_key().split("-")
    return HOSTS.get(parts[1], "") if len(parts) == 3 else ""


def opted_out() -> bool:
    """Every way a person or a machine can say no."""
    if os.environ.get("DO_NOT_TRACK", "").strip() in ("1", "true", "yes"):
        return True
    if os.environ.get("DAS_TELEMETRY", "").strip() in ("0", "false", "no", "off"):
        return True
    try:
        from .settings import load as load_settings
        return not load_settings().analytics
    except Exception:  # noqa: BLE001 - settings unreadable is not a reason to send
        return True


def enabled() -> bool:
    return bool(app_key()) and bool(base_url()) and not _stopped and not opted_out()


def status() -> str:
    """One line for the Settings screen, so nothing about this is a surprise."""
    if not app_key():
        return "No analytics key is built into this copy, so nothing is sent."
    if opted_out():
        return "Analytics is off. Nothing is sent."
    if _stopped:
        return "Analytics is on, but this machine could not reach the server, so it stopped trying."
    return f"Analytics is on. Anonymous events go to {base_url()}."


# ------------------------------------------------------------------ payload --

def _locale() -> str:
    """A BCP-47 tag like ``en-SG``.

    ``locale.getlocale()`` is no use here: on Windows it answers with a display
    name - ``English_Singapore`` - which is not a tag any dashboard can group by.
    Ask Windows for the real one, and read the environment everywhere else."""
    try:
        if sys.platform == "win32":
            import ctypes

            buf = ctypes.create_unicode_buffer(85)
            if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85):
                return buf.value[:35]
        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            raw = os.environ.get(var, "")
            if raw and raw not in ("C", "POSIX"):
                return raw.split(".")[0].split("@")[0].replace("_", "-")[:35]
        tag = locale.getlocale()[0] or ""
        if "_" in tag and len(tag.split("_")[0]) == 2:   # already a real tag
            return tag.replace("_", "-")[:35]
    except Exception:  # noqa: BLE001
        pass
    return "en-US"


def _system_props() -> dict[str, Any]:
    return {
        "locale": _locale(),
        "osName": platform.system(),
        "osVersion": platform.release(),
        "deviceModel": platform.machine(),
        # a run from source is the developer's own; flagging it keeps the
        # dashboard honest about how many real installs there are
        "isDebug": not getattr(sys, "frozen", False),
        "appVersion": APP_VERSION,
        "sdkVersion": SDK,
    }


def _clean(props: dict[str, Any] | None) -> dict[str, Any]:
    """Only short, primitive values leave this machine.

    A whitelist by shape rather than by name: anything that is not a plain
    scalar is dropped, and strings are truncated hard, so no call site can
    accidentally post a path, a prompt or a stack trace."""
    out: dict[str, Any] = {}
    for key, value in (props or {}).items():
        if not isinstance(key, str) or len(key) > 40:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            out[key] = value
        elif isinstance(value, str):
            out[key] = value[:64]
        if len(out) >= 10:
            break
    return out


def _event(name: str, props: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds")
                             .replace("+00:00", "Z"),
        "sessionId": _session_id,
        "eventName": name[:64],
        "systemProps": _system_props(),
        "props": _clean(props),
    }


# ------------------------------------------------------------------ sending --

def _post(batch: list[dict]) -> None:
    with httpx.Client(timeout=TIMEOUT) as client:
        r = client.post(f"{base_url()}/api/v0/events",
                        headers={"App-Key": app_key(),
                                 "Content-Type": "application/json"},
                        content=json.dumps(batch))
        if r.status_code >= 400:
            raise RuntimeError(f"{r.status_code}: {r.text[:120]}")


def _drain() -> list[dict] | None:
    """Block for one event, then take whatever else is already waiting."""
    first = _queue.get()
    if first is _STOP:
        return None
    batch = [first]
    while len(batch) < BATCH:
        try:
            nxt = _queue.get_nowait()
        except queue.Empty:
            break
        if nxt is _STOP:
            _queue.put(_STOP)          # let the next drain see it
            break
        batch.append(nxt)
    return batch


def _run() -> None:
    global _failures, _stopped
    while True:
        try:
            batch = _drain()
        except Exception:  # noqa: BLE001
            return
        if batch is None:
            return
        try:
            _post(batch)
            _failures = 0
        except Exception as exc:  # noqa: BLE001 - offline is normal, not an error
            _failures += 1
            log.debug("analytics batch dropped (%s of %s): %s",
                      _failures, GIVE_UP_AFTER, exc)
            if _failures >= GIVE_UP_AFTER:
                # no network here. Stop spending anything on it this session.
                _stopped = True
                log.debug("analytics disabled for this session")
                return


def _ensure_worker() -> None:
    global _worker
    with _lock:
        if _worker is not None and _worker.is_alive():
            return
        # daemon: the app must be able to exit with events still queued
        _worker = threading.Thread(target=_run, name="telemetry", daemon=True)
        _worker.start()


# ------------------------------------------------------------------- public --

def track(name: str, props: dict[str, Any] | None = None) -> None:
    """Record one event. Never blocks, never raises, safe to call from anywhere."""
    try:
        if not enabled():
            return
        _ensure_worker()
        _queue.put_nowait(_event(name, props))
    except queue.Full:
        pass          # nothing is owed to a queue that is already behind
    except Exception:  # noqa: BLE001 - analytics may never break the caller
        log.debug("analytics event dropped", exc_info=True)


def shutdown(timeout: float = 1.0) -> None:
    """Ask the sender to finish. Waits briefly, then gives up and lets the app go."""
    try:
        if _worker is None or not _worker.is_alive():
            return
        _queue.put_nowait(_STOP)
        _worker.join(timeout=timeout)
    except Exception:  # noqa: BLE001
        pass
