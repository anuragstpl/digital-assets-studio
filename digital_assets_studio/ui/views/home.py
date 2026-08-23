"""Home: pick what you are shipping, or reopen something in flight."""
from __future__ import annotations

import flet as ft

from ...config import APP_NAME, TAGLINE
from ...core import projects as pj
from ...pipelines import PIPELINES, get as get_pipeline
from ...theme import LG, MD, RADIUS, RADIUS_LG, SM, brand_gradient, shadow
from ..components import (body, card, empty_state, ghost_button, h1, h2, label, pill,
                          primary_button)


def _asset_card(studio, pipeline) -> ft.Container:
    p = studio.palette
    accent = getattr(p, pipeline.accent)
    auto = sum(1 for s in pipeline.steps if s.kind == "auto")
    return ft.Container(
        content=ft.Column([
            ft.Container(content=ft.Icon(pipeline.icon, size=26, color=accent),
                         width=54, height=54, border_radius=16,
                         bgcolor=ft.Colors.with_opacity(0.14, accent),
                         alignment=ft.alignment.center),
            ft.Container(height=4),
            ft.Text(pipeline.title, size=17, weight=ft.FontWeight.W_600, color=p.text),
            ft.Text(pipeline.subtitle, size=12, color=accent, weight=ft.FontWeight.W_500),
            ft.Container(height=2),
            ft.Text(pipeline.description, size=13, color=p.text_muted),
            ft.Container(expand=True),
            ft.Row([
                pill(p, f"{auto} automated", p.ok),
                pill(p, f"{len(pipeline.steps) - auto} you", p.text_faint),
            ], spacing=6),
        ], spacing=6, tight=False),
        padding=22, width=310, height=330,
        bgcolor=p.surface, border_radius=RADIUS_LG,
        border=ft.border.all(1, p.line),
        shadow=None if p.dark else shadow(p),
        on_click=lambda e, pid=pipeline.id: studio.start_new(pid),
        ink=True,
    )


def _project_row(studio, project) -> ft.Container:
    p = studio.palette
    pipeline = get_pipeline(project.kind)
    if pipeline is None:
        return ft.Container()
    done, total = pipeline.progress(project)
    accent = getattr(p, pipeline.accent)
    frac = done / total if total else 0
    return ft.Container(
        content=ft.Row([
            ft.Container(content=ft.Icon(pipeline.icon, size=18, color=accent),
                         width=40, height=40, border_radius=12,
                         bgcolor=ft.Colors.with_opacity(0.13, accent),
                         alignment=ft.alignment.center),
            ft.Column([
                ft.Text(project.name, size=14, weight=ft.FontWeight.W_600, color=p.text),
                ft.Text(f"{pipeline.title} · updated {project.updated_at[:10]}",
                        size=11, color=p.text_faint),
            ], spacing=2, tight=True, expand=True),
            ft.Column([
                ft.Text(f"{done}/{total}", size=12, color=p.text_muted),
                ft.Container(
                    content=ft.Container(width=max(4, int(110 * frac)), height=5,
                                         bgcolor=accent, border_radius=999),
                    width=110, height=5, bgcolor=p.surface_alt, border_radius=999,
                    alignment=ft.alignment.center_left),
            ], spacing=5, horizontal_alignment=ft.CrossAxisAlignment.END, tight=True),
            ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=18, color=p.text_faint),
        ], spacing=14, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=ft.padding.symmetric(12, 16),
        bgcolor=p.surface, border_radius=RADIUS,
        border=ft.border.all(1, p.line),
        shadow=None if p.dark else shadow(p),
        on_click=lambda e, pid=project.id: studio.open_project(pid),
        ink=True,
    )


def build(studio) -> ft.Control:
    p = studio.palette
    recent = pj.all_projects()[:6]

    hero = ft.Container(
        content=ft.Column([
            ft.Text(APP_NAME, size=32, weight=ft.FontWeight.W_700, color="#FFFFFF"),
            ft.Text(TAGLINE, size=15, color=ft.Colors.with_opacity(0.85, "#FFFFFF")),
            ft.Container(height=6),
            ft.Text("Pick what you are shipping. The suite does every step it can and stops "
                    "only where a person is genuinely required.",
                    size=13, color=ft.Colors.with_opacity(0.75, "#FFFFFF")),
        ], spacing=6, tight=True),
        padding=ft.padding.symmetric(28, 32),
        border_radius=RADIUS_LG,
        gradient=brand_gradient(p),
    )

    cards = ft.Row([_asset_card(studio, pl) for pl in PIPELINES],
                   spacing=16, wrap=True, run_spacing=16)

    if recent:
        recent_block = ft.Column([
            ft.Row([h2(p, "In flight"),
                    ft.Container(expand=True),
                    ghost_button(p, "All projects", lambda e: studio.navigate("projects"),
                                 ft.Icons.FOLDER_OPEN_ROUNDED)],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Column([_project_row(studio, r) for r in recent], spacing=8),
        ], spacing=12)
    else:
        recent_block = ft.Container(
            content=body(p, "Nothing in flight yet — start with one of the three above.", muted=True),
            padding=ft.padding.only(top=4))

    return ft.Column([
        hero,
        ft.Container(height=8),
        h2(p, "Start something"),
        cards,
        ft.Container(height=14),
        recent_block,
        ft.Container(height=30),
    ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)
