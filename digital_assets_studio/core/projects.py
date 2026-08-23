"""Projects: one folder per thing you are shipping.

    <workspace>/projects/<slug>/
        project.json        state, answers, step statuses
        drafts/             generated markdown
        build/              epub, pdf, images, zips
        notes/              anything the user drops in

The folder is the source of truth; project.json is just an index of it. Delete
the folder and the project is gone - no hidden database.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import PROJECTS_DIR
from .events import BUS, TOPIC_PROJECTS

log = logging.getLogger(__name__)

PENDING, RUNNING, DONE, FAILED, SKIPPED = "pending", "running", "done", "failed", "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str, fallback: str = "project") -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "")).strip("-").lower()
    s = re.sub(r"-{2,}", "-", s)
    return s[:60] or fallback


@dataclass
class StepState:
    status: str = PENDING
    started_at: str = ""
    finished_at: str = ""
    message: str = ""
    artifacts: list[str] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)


@dataclass
class Project:
    id: str
    name: str
    kind: str                       # pipeline id: "book" | "youtube" | "mobile"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    answers: dict[str, Any] = field(default_factory=dict)
    steps: dict[str, StepState] = field(default_factory=dict)
    archived: bool = False

    # ------------------------------------------------------------- layout --
    @property
    def dir(self) -> Path:
        return PROJECTS_DIR / self.id

    @property
    def drafts(self) -> Path:
        return self.dir / "drafts"

    @property
    def build(self) -> Path:
        return self.dir / "build"

    @property
    def notes(self) -> Path:
        return self.dir / "notes"

    def ensure_dirs(self) -> None:
        for p in (self.dir, self.drafts, self.build, self.notes):
            p.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------- state --
    def state(self, step_id: str) -> StepState:
        st = self.steps.get(step_id)
        if st is None:
            st = StepState()
            self.steps[step_id] = st
        return st

    def status(self, step_id: str) -> str:
        return self.state(step_id).status

    def is_done(self, step_id: str) -> bool:
        return self.state(step_id).status in (DONE, SKIPPED)

    def answer(self, key: str, default: Any = "") -> Any:
        v = self.answers.get(key)
        return default if v in (None, "") else v

    def set_answer(self, key: str, value: Any) -> None:
        self.answers[key] = value

    # ---------------------------------------------------------- artifacts --
    def write_text(self, relpath: str, content: str) -> Path:
        p = self.dir / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def read_text(self, relpath: str, default: str = "") -> str:
        p = self.dir / relpath
        return p.read_text(encoding="utf-8") if p.exists() else default

    def write_bytes(self, relpath: str, data: bytes) -> Path:
        p = self.dir / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def rel(self, path: Path | str) -> str:
        try:
            return str(Path(path).relative_to(self.dir)).replace("\\", "/")
        except ValueError:
            return str(path)

    def exists(self, relpath: str) -> bool:
        return (self.dir / relpath).exists()

    # ------------------------------------------------------- persistence --
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "answers": self.answers,
            "archived": self.archived,
            "steps": {k: asdict(v) for k, v in self.steps.items()},
        }

    def save(self) -> None:
        self.ensure_dirs()
        self.updated_at = _now()
        tmp = self.dir / "project.tmp"
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self.dir / "project.json")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Project":
        steps = {k: StepState(**v) for k, v in (d.get("steps") or {}).items()}
        return cls(
            id=d["id"], name=d.get("name", d["id"]), kind=d.get("kind", "book"),
            created_at=d.get("created_at", _now()), updated_at=d.get("updated_at", _now()),
            answers=d.get("answers", {}), steps=steps, archived=d.get("archived", False),
        )


_lock = threading.RLock()


def create(name: str, kind: str, answers: dict[str, Any] | None = None) -> Project:
    with _lock:
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        base = slugify(name, kind)
        pid, n = base, 2
        while (PROJECTS_DIR / pid).exists():
            pid = f"{base}-{n}"
            n += 1
        p = Project(id=pid, name=name.strip() or pid, kind=kind, answers=answers or {})
        p.save()
    BUS.publish(TOPIC_PROJECTS, None)
    return p


def load(project_id: str) -> Project | None:
    f = PROJECTS_DIR / project_id / "project.json"
    if not f.exists():
        return None
    try:
        return Project.from_dict(json.loads(f.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        log.exception("could not read project %s", project_id)
        return None


def all_projects(include_archived: bool = False) -> list[Project]:
    if not PROJECTS_DIR.exists():
        return []
    out = []
    for d in PROJECTS_DIR.iterdir():
        if not d.is_dir():
            continue
        p = load(d.name)
        if p and (include_archived or not p.archived):
            out.append(p)
    return sorted(out, key=lambda p: p.updated_at, reverse=True)


def delete(project_id: str) -> None:
    with _lock:
        shutil.rmtree(PROJECTS_DIR / project_id, ignore_errors=True)
    BUS.publish(TOPIC_PROJECTS, None)
