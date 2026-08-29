"""The workbench: the step list on the left, the step you are on to the right."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import flet as ft

from ...core import pipeline as pipe
from ...core.jobs import RUNNER
from ...core.projects import DONE, FAILED, PENDING, RUNNING, SKIPPED
from ...theme import RADIUS, RADIUS_SM, shadow
from ..components import (body, build_field, card, divider, ghost_button, h1, h2, label,
                          markdown, pill, primary_button, snack, status_dot, text_field)

STATUS_LABEL = {DONE: "done", RUNNING: "running", FAILED: "failed",
                SKIPPED: "skipped", PENDING: "not started"}


def header(studio) -> ft.Control:
    """Title row, progress bar and the two run buttons.

    Kept separate so progress can be redrawn without rebuilding the screen."""
    p = studio.palette
    project, pl = studio.project, studio.pipeline
    done, total = pl.progress(project)
    accent = getattr(p, pl.accent)
    frac = done / total if total else 0
    bar = ft.Container(
        content=ft.Row([
            ft.Container(bgcolor=accent, border_radius=999, expand=max(int(frac * 1000), 1)),
            ft.Container(bgcolor=p.surface_alt, border_radius=999,
                         expand=max(1000 - int(frac * 1000), 1)),
        ], spacing=0),
        height=6, border_radius=999)
    return ft.Column([
        ft.Row([
            ft.Container(content=ft.Icon(pl.icon, size=20, color=accent),
                         width=44, height=44, border_radius=13,
                         bgcolor=ft.Colors.with_opacity(0.14, accent),
                         alignment=ft.alignment.center),
            ft.Column([
                ft.Text(project.name, size=20, weight=ft.FontWeight.W_600, color=p.text),
                ft.Text(f"{pl.title} · {done} of {total} required steps done",
                        size=12, color=p.text_muted),
            ], spacing=2, tight=True, expand=True),
            ghost_button(p, "Folder", lambda e: studio.reveal(str(project.dir)),
                         ft.Icons.FOLDER_OPEN_ROUNDED),
            ghost_button(p, "Run what can run", lambda e: studio.run_all(),
                         ft.Icons.PLAY_ARROW_ROUNDED, disabled=studio.busy),
            primary_button(p, "Autopilot", lambda e: studio.autopilot(),
                           ft.Icons.ROCKET_LAUNCH_ROUNDED, disabled=studio.busy),
        ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bar,
    ], spacing=14, tight=True)


def build(studio) -> ft.Control:
    project, pl = studio.project, studio.pipeline
    if project is None or pl is None:
        return body(studio.palette, "No project open.")
    nxt = pl.next_step(project)
    selected = studio.selected_step or (nxt.id if nxt else pl.steps[0].id)
    studio.selected_step = selected

    studio.pane_header = ft.Container(content=header(studio))
    studio.pane_list = ft.Container(content=_step_list(studio, selected), width=320)
    studio.pane_detail = ft.Container(content=_step_detail(studio, selected), expand=True)

    return ft.Column([
        studio.pane_header,
        ft.Container(height=6),
        ft.Row([studio.pane_list, studio.pane_detail], spacing=18,
               vertical_alignment=ft.CrossAxisAlignment.START, expand=True),
    ], spacing=6, expand=True)


def step_list(studio) -> ft.Control:
    return _step_list(studio, studio.selected_step)


def step_detail(studio) -> ft.Control:
    return _step_detail(studio, studio.selected_step)



def _row_look(studio, step) -> tuple[str, str, bool, bool]:
    """(icon, colour, selected, blocked) for one row, from current state."""
    p, project = studio.palette, studio.project
    st = project.state(step.id)
    blocked = bool(studio.pipeline.blocked(project, step))
    colour = {DONE: p.ok, RUNNING: p.info, FAILED: p.danger,
              SKIPPED: p.text_faint}.get(st.status, p.text_faint)
    icon = (ft.Icons.CHECK_ROUNDED if st.status == DONE else
            ft.Icons.ERROR_OUTLINE_ROUNDED if st.status == FAILED else
            ft.Icons.REMOVE_ROUNDED if st.status == SKIPPED else
            ft.Icons.LOCK_OUTLINE_ROUNDED if blocked else
            ft.Icons.BOLT_ROUNDED if step.kind == pipe.AUTO else ft.Icons.PAN_TOOL_ALT_ROUNDED)
    return icon, colour, step.id == studio.selected_step, blocked


def _style_row(studio, step, parts: dict) -> None:
    """Restyle a row in place. Rebuilding the list instead would reset its scroll
    position, which is what makes a long pipeline jump to the top on every click."""
    p, project = studio.palette, studio.project
    st = project.state(step.id)
    icon, colour, is_sel, blocked = _row_look(studio, step)

    parts["icon"].name = icon
    parts["icon"].color = colour
    parts["icon_box"].bgcolor = ft.Colors.with_opacity(0.13, colour)
    parts["title"].weight = ft.FontWeight.W_600 if is_sel else ft.FontWeight.W_500
    parts["title"].color = p.text_faint if blocked else p.text
    parts["status"].value = ("optional · " if step.optional else "") + STATUS_LABEL.get(st.status, "")
    parts["container"].bgcolor = p.surface_alt if is_sel else None
    parts["container"].border = ft.border.all(1, p.accent if is_sel else "#00000000")


def refresh_rows(studio) -> None:
    pl = studio.pipeline
    for step_id, parts in (studio.step_rows or {}).items():
        step = pl.step(step_id)
        if step is not None:
            _style_row(studio, step, parts)


def _step_list(studio, selected: str) -> ft.Control:
    p = studio.palette
    project, pl = studio.project, studio.pipeline
    studio.step_rows = {}
    blocks: list[ft.Control] = []
    for phase, steps in pl.phases(project):
        blocks.append(ft.Container(content=label(p, phase),
                                   padding=ft.padding.only(left=6, top=14, bottom=4)))
        for s in steps:
            icon_widget = ft.Icon(ft.Icons.BOLT_ROUNDED, size=13)
            icon_box = ft.Container(content=icon_widget, width=24, height=24,
                                    border_radius=8, alignment=ft.alignment.center)
            title = ft.Text(s.title, size=13)
            status = ft.Text("", size=10, color=p.text_faint)
            container = ft.Container(
                content=ft.Row([
                    icon_box,
                    ft.Column([title, status], spacing=1, tight=True, expand=True),
                ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                padding=ft.padding.symmetric(9, 10),
                border_radius=RADIUS_SM,
                on_click=lambda e, sid=s.id: studio.select_step(sid),
                ink=True)
            parts = {"container": container, "icon": icon_widget, "icon_box": icon_box,
                     "title": title, "status": status}
            studio.step_rows[s.id] = parts
            _style_row(studio, s, parts)
            blocks.append(container)
    return ft.Container(
        content=ft.Column(blocks, spacing=3, scroll=ft.ScrollMode.AUTO, expand=True),
        bgcolor=p.surface, border_radius=RADIUS, border=ft.border.all(1, p.line),
        shadow=None if p.dark else shadow(p),
        padding=ft.padding.symmetric(8, 10), expand=True)


def _artifact_row(studio, rel: str) -> ft.Control:
    p = studio.palette
    path = studio.project.dir / rel
    exists = path.exists()
    is_image = path.suffix.lower() in (".png", ".jpg", ".jpeg")
    leading = ft.Icon(
        ft.Icons.IMAGE_ROUNDED if is_image else
        ft.Icons.MOVIE_ROUNDED if path.suffix.lower() == ".mp4" else
        ft.Icons.DESCRIPTION_ROUNDED, size=15,
        color=p.text_muted if exists else p.text_faint)
    return ft.Container(
        content=ft.Row([
            leading,
            ft.Text(rel, size=12, color=p.text if exists else p.text_faint, expand=True),
            ft.Text("missing" if not exists else f"{path.stat().st_size / 1024:.0f} KB",
                    size=11, color=p.text_faint),
        ], spacing=9),
        padding=ft.padding.symmetric(7, 10), border_radius=RADIUS_SM, bgcolor=p.surface_alt,
        on_click=(lambda e, f=str(path): studio.open_path(f)) if exists else None, ink=exists)


def _step_detail(studio, step_id: str) -> ft.Control:
    p = studio.palette
    project, pl = studio.project, studio.pipeline
    step = pl.step(step_id)
    if step is None:
        return body(p, "Unknown step.")
    st = project.state(step.id)
    blocked = pl.blocked(project, step)
    accent = getattr(p, pl.accent)

    head = ft.Row([
        ft.Column([
            ft.Row([
                ft.Text(step.title, size=20, weight=ft.FontWeight.W_600, color=p.text),
                pill(p, "automated" if step.kind == pipe.AUTO else "yours", 
                     p.ok if step.kind == pipe.AUTO else p.warn),
                pill(p, "optional", p.text_faint) if step.optional else ft.Container(width=0),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Text(step.summary, size=13, color=p.text_muted),
        ], spacing=5, tight=True, expand=True),
        ft.Row([status_dot(p, st.status),
                ft.Text(STATUS_LABEL.get(st.status, ""), size=12, color=p.text_muted)], spacing=7),
    ], vertical_alignment=ft.CrossAxisAlignment.START)

    blocks: list[ft.Control] = [head]

    if blocked:
        names = ", ".join((pl.step(b).title if pl.step(b) else b) for b in blocked)
        blocks.append(ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE_ROUNDED, size=15, color=p.warn),
                            ft.Text(f"Waiting on: {names}", size=12, color=p.warn, expand=True)],
                           spacing=8),
            padding=12, bgcolor=ft.Colors.with_opacity(0.10, p.warn), border_radius=RADIUS_SM))

    if step.instructions:
        blocks.append(card(p, label(p, "What to do"), markdown(p, step.instructions)))

    if step.links:
        blocks.append(ft.Row([
            ghost_button(p, l.label, lambda e, u=l.url: studio.open_url(u),
                         ft.Icons.OPEN_IN_NEW_ROUNDED) for l in step.links],
            spacing=8, wrap=True, run_spacing=8))

    if step.fields:
        controls = [build_field(p, f, project.answers.get(f.key, ""), studio.set_answer,
                                browse=studio.browse_for)
                    for f in step.fields]
        blocks.append(card(p, label(p, "Options"), *controls))

    if step.checklist:
        boxes = []
        for item in step.checklist:
            boxes.append(ft.Checkbox(
                label=item, value=item in st.checked, active_color=accent,
                label_style=ft.TextStyle(size=13, color=p.text),
                on_change=lambda e, i=item: studio.toggle_check(step.id, i, e.control.value)))
        blocks.append(card(p, label(p, "Checklist"), *boxes, gap=2))

    # action row
    actions: list[ft.Control] = []
    if step.kind == pipe.AUTO:
        actions.append(primary_button(
            p, step.run_label or ("Re-run" if st.status == DONE else "Run this step"),
            lambda e: studio.run_step(step.id), ft.Icons.PLAY_ARROW_ROUNDED,
            disabled=bool(blocked) or studio.busy))
    else:
        actions.append(primary_button(
            p, "Mark done" if st.status != DONE else "Done",
            lambda e: studio.mark_done(step.id), ft.Icons.CHECK_ROUNDED,
            disabled=bool(blocked) or st.status == DONE))
    if step.opens == "editor":
        actions.append(ghost_button(p, "Open the editor", lambda e: studio.open_editor(),
                                    ft.Icons.VIDEO_SETTINGS_ROUNDED))
    if st.status in (DONE, FAILED, SKIPPED):
        actions.append(ghost_button(p, "Reset", lambda e: studio.reset_step(step.id),
                                    ft.Icons.RESTART_ALT_ROUNDED))
    if step.optional and st.status == PENDING:
        actions.append(ghost_button(p, "Skip", lambda e: studio.skip_step(step.id),
                                    ft.Icons.SKIP_NEXT_ROUNDED))
    if step.cost_hint:
        actions.append(ft.Container(
            content=ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=13, color=p.text_faint),
                            ft.Text(step.cost_hint, size=11, color=p.text_faint)], spacing=6),
            padding=ft.padding.only(left=6)))
    blocks.append(ft.Row(actions, spacing=10, wrap=True, run_spacing=8,
                         vertical_alignment=ft.CrossAxisAlignment.CENTER))

    if st.message:
        tone = p.danger if st.status == FAILED else p.ok
        blocks.append(ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED if st.status == FAILED
                        else ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED, size=16, color=tone),
                ft.Text(st.message, size=12, color=p.text, expand=True, selectable=True)], spacing=9),
            padding=12, bgcolor=ft.Colors.with_opacity(0.09, tone), border_radius=RADIUS_SM))

    arts = st.artifacts or step.produces
    if arts:
        blocks.append(card(p, label(p, "Files"),
                           *[_artifact_row(studio, a) for a in arts], gap=6))

    studio.log_column = ft.Column(
        [ft.Text(line, size=11, color=p.text_muted, selectable=True)
         for line in studio.step_log[-40:]],
        spacing=3, tight=True, scroll=ft.ScrollMode.AUTO, auto_scroll=True)
    blocks.append(ft.Container(
        content=card(p, label(p, "Activity"), studio.log_column),
        visible=bool(studio.step_log), key="activity"))

    return ft.Container(
        content=ft.Column(blocks, spacing=16, scroll=ft.ScrollMode.AUTO, expand=True),
        padding=ft.padding.only(right=6), expand=True)
