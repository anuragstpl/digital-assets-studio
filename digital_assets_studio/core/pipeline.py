"""The pipeline engine.

A pipeline is an ordered list of steps grouped into phases. Two kinds of step:

  AUTO    - the suite does it. A python callable gets the project and a job
            context, calls models, writes files, returns a StepResult.
  MANUAL  - only a human can do it (creating a Google account, uploading an AAB,
            clicking Publish). The suite renders the instructions, the exact
            values to paste, deep links, and a checklist, then waits.

Every step declares what it needs and what it produces, so the UI can grey out
what is not ready yet and tell you exactly why.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from . import telemetry
from .events import BUS, TOPIC_STEP
from .jobs import JobContext
from .projects import DONE, FAILED, PENDING, RUNNING, SKIPPED, Project, _now

log = logging.getLogger(__name__)


def _announce(project: Project, step_id: str, status: str) -> None:
    """Tell whoever is watching that one step changed state.

    Steps run on a worker thread, so without this the screen has nothing to
    repaint from between them: a twenty-minute run would sit looking frozen and
    then jump straight to its finished state."""
    BUS.publish(TOPIC_STEP, {"project": project.id, "step": step_id, "status": status})


AUTO, MANUAL = "auto", "manual"

# Why a manual step is manual. Autopilot can clear a REVIEW gate on your behalf;
# it can never clear an EXTERNAL one, because those need a person on a website,
# a keyboard, or an identity document.
REVIEW, EXTERNAL = "review", "external"


@dataclass
class Field_:
    """One input on a step's form."""
    key: str
    label: str
    # text | multiline | number | select | switch | slug | file | folder
    type: str = "text"
    help: str = ""
    options: list[str] = field(default_factory=list)
    default: Any = ""
    required: bool = False
    placeholder: str = ""
    # A select whose options are only knowable at the time you look at it - the
    # YouTube channels you have connected, say. Static options stay the fallback,
    # because a form must still draw when whatever the callable reads is missing.
    options_fn: Callable[[], list[str]] | None = None
    extensions: list[str] = field(default_factory=list)   # file pickers only

    def choices(self) -> list[str]:
        if self.options_fn is None:
            return list(self.options)
        try:
            live = list(self.options_fn() or [])
        except Exception:  # noqa: BLE001
            log.exception("options_fn failed for field %s", self.key)
            return list(self.options)
        return live or list(self.options)


@dataclass
class Link:
    label: str
    url: str


@dataclass
class StepResult:
    message: str = ""
    artifacts: list[str] = field(default_factory=list)
    answers: dict[str, Any] = field(default_factory=dict)


RunFn = Callable[[Project, JobContext], StepResult]


@dataclass
class Step:
    id: str
    title: str
    phase: str
    kind: str = AUTO
    summary: str = ""
    instructions: str = ""                       # markdown, for manual steps
    fields: list[Field_] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    checklist: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    optional: bool = False
    run: RunFn | None = None
    cost_hint: str = ""
    run_label: str = ""
    applies_when: Callable[[Project], bool] | None = None
    gate: str = EXTERNAL              # manual steps only
    autofill: Callable[[Project], dict] | None = None   # what autopilot answers for you
    needs_attention: bool = False     # opens a window / needs you at the keyboard
    # a screen this step hands you off to - the video editor is the only one so
    # far, and a step whose output you finish by hand needs a way in
    opens: str = ""

    def applies(self, project: Project) -> bool:
        """Some steps only exist on one branch of a pipeline - creating a YouTube
        channel matters only if you do not already have one."""
        if self.applies_when is None:
            return True
        try:
            return bool(self.applies_when(project))
        except Exception:  # noqa: BLE001
            log.exception("applies_when failed for step %s", self.id)
            return True

    def blocked_by(self, project: Project) -> list[str]:
        return [r for r in self.requires if not project.is_done(r)]


@dataclass
class Pipeline:
    id: str
    title: str
    subtitle: str
    description: str
    icon: str
    accent: str                                   # palette attribute name
    steps: list[Step] = field(default_factory=list)
    intake: list[Field_] = field(default_factory=list)

    def step(self, step_id: str) -> Step | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def active_steps(self, project: Project) -> list[Step]:
        return [s for s in self.steps if s.applies(project)]

    def blocked(self, project: Project, step: Step) -> list[str]:
        """Prerequisites that are still outstanding, ignoring any that belong to a
        branch this project is not on."""
        out = []
        for req in step.requires:
            other = self.step(req)
            if other is not None and not other.applies(project):
                continue
            if not project.is_done(req):
                out.append(req)
        return out

    def phases(self, project: Project | None = None) -> list[tuple[str, list[Step]]]:
        out: list[tuple[str, list[Step]]] = []
        for s in (self.steps if project is None else self.active_steps(project)):
            if not out or out[-1][0] != s.phase:
                out.append((s.phase, []))
            out[-1][1].append(s)
        return out

    def progress(self, project: Project) -> tuple[int, int]:
        required = [s for s in self.active_steps(project) if not s.optional]
        done = sum(1 for s in required if project.is_done(s.id))
        return done, len(required)

    def next_step(self, project: Project) -> Step | None:
        for s in self.active_steps(project):
            if project.is_done(s.id):
                continue
            if self.blocked(project, s):
                continue
            return s
        return None


# ----------------------------------------------------------------- execution --

def execute(pipeline: Pipeline, project: Project, step: Step, ctx: JobContext) -> StepResult:
    """Run one auto step, recording state on the project as it goes."""
    if step.kind != AUTO or step.run is None:
        raise RuntimeError(f"Step '{step.id}' is manual - it cannot be run by the suite.")

    missing = pipeline.blocked(project, step)
    if missing:
        titles = ", ".join((pipeline.step(m).title if pipeline.step(m) else m) for m in missing)
        raise RuntimeError(f"'{step.title}' needs these finished first: {titles}")

    state = project.state(step.id)
    state.status = RUNNING
    state.started_at = _now()
    state.message = ""
    project.save()
    _announce(project, step.id, RUNNING)

    try:
        result = step.run(project, ctx) or StepResult()
    except Exception as exc:  # noqa: BLE001
        state.status = FAILED
        state.finished_at = _now()
        state.message = str(exc)
        project.save()
        _announce(project, step.id, FAILED)
        # the step id only - never the message, which can carry paths and keys
        telemetry.track("step_failed", {"pipeline": pipeline.id, "step": step.id})
        raise

    state.status = DONE
    state.finished_at = _now()
    state.message = result.message
    state.artifacts = result.artifacts
    if result.answers:
        project.answers.update(result.answers)
    project.save()
    _announce(project, step.id, DONE)
    return result


def mark_manual_done(project: Project, step: Step, note: str = "") -> None:
    st = project.state(step.id)
    st.status = DONE
    st.finished_at = _now()
    st.message = note or "Marked done"
    project.save()
    _announce(project, step.id, DONE)


def reset_step(project: Project, step: Step) -> None:
    project.steps[step.id] = type(project.state(step.id))()
    project.save()


def skip_step(project: Project, step: Step) -> None:
    st = project.state(step.id)
    st.status = SKIPPED
    st.finished_at = _now()
    st.message = "Skipped"
    project.save()
    _announce(project, step.id, SKIPPED)


__all__ = [
    "AUTO", "MANUAL", "DONE", "FAILED", "PENDING", "RUNNING", "SKIPPED",
    "Field_", "Link", "Pipeline", "Step", "StepResult",
    "execute", "mark_manual_done", "reset_step", "skip_step", "run_all",
    "REVIEW", "EXTERNAL", "can_autopilot",
]


# ------------------------------------------------------------- auto runner --

def can_autopilot(step: Step) -> bool:
    return step.kind == AUTO or step.gate == REVIEW


def runnable_now(pipeline: Pipeline, project: Project) -> list[Step]:
    """Auto steps whose prerequisites are satisfied and which have not run."""
    out: list[Step] = []
    for s in pipeline.active_steps(project):
        if s.kind != AUTO or project.is_done(s.id):
            continue
        if pipeline.blocked(project, s):
            continue
        out.append(s)
    return out


def first_gate(pipeline: Pipeline, project: Project) -> Step | None:
    """The next manual step standing between here and the end."""
    for s in pipeline.active_steps(project):
        if project.is_done(s.id) or s.optional:
            continue
        if pipeline.blocked(project, s):
            # You are not waiting on this - you are waiting on whatever blocks it.
            continue
        if s.kind == MANUAL:
            return s
    return None


def run_all(pipeline: Pipeline, project: Project, ctx, skip_optional: bool = True,
            continue_on_optional_failure: bool = True, autopilot: bool = False,
            include_optional: bool = False) -> dict:
    """Run the pipeline until it needs a person.

    Without autopilot, a manual step of any kind stops the run.
    With autopilot, REVIEW gates are answered from what the models already wrote
    and the run continues; EXTERNAL gates still stop it, because no software can
    click Publish on Amazon for you or pass a tax interview on your behalf.
    """
    ran: list[str] = []
    approved: list[str] = []
    failed: list[tuple[str, str]] = []
    blocked_on: list[Step] = []
    attempted: set[str] = set()
    skip_optional = skip_optional and not include_optional
    made_progress = True

    while made_progress:
        ctx.check()
        made_progress = False
        for step in pipeline.active_steps(project):
            if project.is_done(step.id) or pipeline.blocked(project, step):
                continue
            if step.id in attempted:
                continue

            if step.kind == MANUAL:
                if step.optional and not include_optional:
                    continue
                if not (autopilot and step.gate == REVIEW):
                    # A gate we cannot clear does not end the run - work further
                    # down the pipeline may not depend on it at all.
                    if step not in blocked_on:
                        blocked_on.append(step)
                        ctx.log(f"⏸ {step.title} — needs you; carrying on with the rest")
                    continue
                answers = {}
                if step.autofill is not None:
                    try:
                        answers = step.autofill(project) or {}
                    except Exception as exc:  # noqa: BLE001
                        ctx.log(f"{step.title}: could not autofill ({exc})", "warning")
                missing = [f.label for f in step.fields
                           if f.required and not str(answers.get(f.key) or
                                                     project.answers.get(f.key) or "").strip()]
                if missing:
                    if step not in blocked_on:
                        blocked_on.append(step)
                        ctx.log(f"⏸ {step.title} — needs {', '.join(missing)}")
                    continue
                project.answers.update(answers)
                st = project.state(step.id)
                st.checked = list(step.checklist)
                mark_manual_done(project, step, "Approved by autopilot")
                approved.append(step.title)
                ctx.log(f"✓ {step.title} — approved automatically")
                made_progress = True
                break

            if step.optional and skip_optional:
                continue
            if autopilot and step.needs_attention:
                # It would open a browser and wait for you. Not while you are away.
                if step not in blocked_on:
                    blocked_on.append(step)
                    ctx.log(f"⏸ {step.title} — needs you at the keyboard, skipped by autopilot")
                continue
            ctx.progress(None, f"→ {step.title}")
            attempted.add(step.id)
            try:
                result = execute(pipeline, project, step, ctx)
                ran.append(step.title)
                ctx.log(f"{step.title}: {result.message}")
                made_progress = True
            except Exception as exc:  # noqa: BLE001
                failed.append((step.title, str(exc)))
                ctx.log(f"{step.title} failed: {exc}", "error")
                if step.optional and continue_on_optional_failure:
                    skip_step(project, step)
                    made_progress = True
                    continue
                if autopilot:
                    # One broken step should not strand the work that does not
                    # depend on it - ffmpeg missing must not cost you the metadata.
                    made_progress = True
                    continue
                return {"ran": ran, "approved": approved, "failed": failed,
                        "waiting_on": step, "blocked_on": blocked_on,
                        "stopped_because": "a step failed"}
            break  # rescan so newly unblocked steps run in pipeline order

    gate = blocked_on[0] if blocked_on else first_gate(pipeline, project)
    if gate is not None:
        why = "waiting for you"
    elif failed:
        why = f"{len(failed)} step(s) failed"
    else:
        why = "everything possible is done"
    return {"ran": ran, "approved": approved, "failed": failed, "waiting_on": gate,
            "blocked_on": blocked_on, "stopped_because": why}
