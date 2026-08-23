"""Background job runner.

Flet's UI thread must never block on a network call, so every long operation
runs here. Jobs are cancellable co-operatively: the callable receives a
JobContext and is expected to check ``ctx.cancelled`` between units of work.
"""
from __future__ import annotations

import logging
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from .events import BUS, TOPIC_JOB, TOPIC_LOG

log = logging.getLogger(__name__)

PENDING, RUNNING, DONE, FAILED, CANCELLED = "pending", "running", "done", "failed", "cancelled"


@dataclass
class JobUpdate:
    job_id: str
    name: str
    status: str
    progress: float | None = None
    message: str = ""
    result: Any = None
    error: str = ""


@dataclass
class JobContext:
    job_id: str
    name: str
    _cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def check(self) -> None:
        if self.cancelled:
            raise JobCancelled(self.name)

    def progress(self, fraction: float | None, message: str = "") -> None:
        BUS.publish(TOPIC_JOB, JobUpdate(self.job_id, self.name, RUNNING, fraction, message))
        if message:
            self.log(message)

    def log(self, message: str, level: str = "info") -> None:
        BUS.publish(TOPIC_LOG, {"job": self.name, "level": level, "message": message})
        getattr(log, level, log.info)("[%s] %s", self.name, message)


class JobCancelled(RuntimeError):
    pass


class JobRunner:
    def __init__(self, max_workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._active: dict[str, JobContext] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        name: str,
        fn: Callable[[JobContext], Any],
        on_done: Callable[[JobUpdate], None] | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        ctx = JobContext(job_id, name)
        with self._lock:
            self._active[job_id] = ctx

        def runner() -> None:
            BUS.publish(TOPIC_JOB, JobUpdate(job_id, name, RUNNING, 0.0, f"Started: {name}"))
            update: JobUpdate
            try:
                result = fn(ctx)
                update = JobUpdate(job_id, name, DONE, 1.0, f"Finished: {name}", result=result)
            except JobCancelled:
                update = JobUpdate(job_id, name, CANCELLED, None, f"Cancelled: {name}")
            except Exception as exc:  # noqa: BLE001
                detail = traceback.format_exc(limit=6)
                log.error("job %s failed: %s", name, detail)
                BUS.publish(TOPIC_LOG, {"job": name, "level": "error", "message": str(exc)})
                update = JobUpdate(job_id, name, FAILED, None, str(exc), error=detail)
            finally:
                with self._lock:
                    self._active.pop(job_id, None)
            BUS.publish(TOPIC_JOB, update)
            if on_done is not None:
                try:
                    on_done(update)
                except Exception:  # noqa: BLE001
                    log.exception("on_done callback failed for job %s", name)

        self._pool.submit(runner)
        return job_id

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            ctx = self._active.get(job_id)
        if ctx is None:
            return False
        ctx.cancel()
        return True

    def cancel_all(self) -> None:
        with self._lock:
            contexts = list(self._active.values())
        for c in contexts:
            c.cancel()

    @property
    def busy(self) -> bool:
        with self._lock:
            return bool(self._active)

    def shutdown(self) -> None:
        self.cancel_all()
        self._pool.shutdown(wait=False)


RUNNER = JobRunner()
