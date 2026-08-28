"""A tiny synchronous pub/sub bus.

Background jobs publish; the UI subscribes. Handlers must be cheap and must not
raise - a raising handler is logged and skipped so one bad listener can never
take down a running pipeline.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

log = logging.getLogger(__name__)

Handler = Callable[[Any], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        with self._lock:
            self._subs[topic].append(handler)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._subs[topic].remove(handler)
                except ValueError:
                    pass

        return unsubscribe

    def publish(self, topic: str, payload: Any = None) -> None:
        with self._lock:
            handlers = list(self._subs.get(topic, ()))
        for h in handlers:
            try:
                h(payload)
            except Exception:  # noqa: BLE001 - a listener must never kill a job
                log.exception("event handler failed for topic %s", topic)

    def clear(self, topic: str | None = None) -> None:
        with self._lock:
            if topic is None:
                self._subs.clear()
            else:
                self._subs.pop(topic, None)


BUS = EventBus()

# Topic names used across the app
TOPIC_LOG = "log"                  # payload: LogRecordLite
TOPIC_JOB = "job"                  # payload: JobUpdate
TOPIC_PROJECTS = "projects"        # payload: None (list changed)
TOPIC_STEP = "step"                # payload: {"project", "step", "status"}
TOPIC_SETTINGS = "settings"        # payload: None
