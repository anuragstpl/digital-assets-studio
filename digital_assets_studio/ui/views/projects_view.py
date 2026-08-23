"""Every project, newest first."""
from __future__ import annotations

import flet as ft

from ...core import projects as pj
from ...pipelines import get as get_pipeline
from ...theme import RADIUS
from ..components import body, empty_state, ghost_button, h1, pill, primary_button, snack


def build(studio) -> ft.Control:
    p = studio.palette
    items = pj.all_projects(include_archived=True)

    if not items:
        return empty_state(
            p, ft.Icons.ROCKET_LAUNCH_ROUNDED, "No projects yet",
            "Every book, channel and app you ship lives here.",
            primary_button(p, "Start one", lambda e: studio.navigate("home"), ft.Icons.ADD_ROUNDED))

    rows = []
    for proj in items:
        pipeline = get_pipeline(proj.kind)
        if pipeline is None:
            continue
        done, total = pipeline.progress(proj)
        accent = getattr(p, pipeline.accent)
        rows.append(ft.Container(
            content=ft.Row([
                ft.Container(content=ft.Icon(pipeline.icon, size=18, color=accent),
                             width=40, height=40, border_radius=12,
                             bgcolor=ft.Colors.with_opacity(0.13, accent),
                             alignment=ft.alignment.center),
                ft.Column([
                    ft.Text(proj.name, size=14, weight=ft.FontWeight.W_600, color=p.text),
                    ft.Text(f"{pipeline.title} · {done} of {total} steps · {proj.updated_at[:16].replace('T', ' ')}",
                            size=11, color=p.text_faint),
                ], spacing=2, tight=True, expand=True),
                pill(p, "archived" if proj.archived else f"{int(100 * done / total) if total else 0}%",
                     p.text_faint if proj.archived else accent),
                ft.IconButton(ft.Icons.FOLDER_OPEN_ROUNDED, icon_size=17, icon_color=p.text_faint,
                              tooltip="Open the project folder",
                              on_click=lambda e, d=str(proj.dir): studio.reveal(d)),
                ft.IconButton(ft.Icons.DELETE_OUTLINE_ROUNDED, icon_size=17, icon_color=p.text_faint,
                              tooltip="Delete",
                              on_click=lambda e, i=proj.id, n=proj.name: studio.confirm_delete(i, n)),
                ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=18, color=p.text_faint),
            ], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(10, 16),
            bgcolor=p.surface, border_radius=RADIUS, border=ft.border.all(1, p.line),
            on_click=lambda e, i=proj.id: studio.open_project(i), ink=True))

    return ft.Column([
        ft.Row([h1(p, "Projects"), ft.Container(expand=True),
                primary_button(p, "New", lambda e: studio.navigate("home"), ft.Icons.ADD_ROUNDED)],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        body(p, f"{len(items)} project(s) in {studio.workspace}", muted=True, size=12),
        ft.Container(height=6),
        ft.Column(rows, spacing=8),
        ft.Container(height=30),
    ], spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)
