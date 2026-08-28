"""The intake form for a new project."""
from __future__ import annotations

import flet as ft

from ...theme import RADIUS, RADIUS_LG
from ..components import (body, build_field, card, ghost_button, h1, label, primary_button,
                          text_field)


def build(studio) -> ft.Control:
    p = studio.palette
    pl = studio.pipeline
    accent = getattr(p, pl.accent)
    draft = studio.draft

    name = text_field(p, "Project name", draft.get("__name", ""),
                      hint="What you will call this in the projects list",
                      on_change=lambda e: draft.update({"__name": e.control.value}))

    fields = [build_field(p, f, draft.get(f.key, f.default), lambda k, v: draft.update({k: v}),
                          browse=studio.browse_for)
              for f in pl.intake]

    def create(e):
        missing = [f.label for f in pl.intake
                   if f.required and not str(draft.get(f.key, f.default) or "").strip()]
        if not (name.value or "").strip():
            missing.insert(0, "Project name")
        if missing:
            studio.toast(f"Still needed: {', '.join(missing)}", "warn")
            return
        answers = {f.key: draft.get(f.key, f.default) for f in pl.intake}
        studio.create_project(name.value.strip(), pl.id, answers)

    return ft.Column([
        ft.Container(
            content=ft.Row([
                ft.Container(content=ft.Icon(pl.icon, size=22, color="#FFFFFF"),
                             width=48, height=48, border_radius=14,
                             bgcolor=ft.Colors.with_opacity(0.22, "#FFFFFF"),
                             alignment=ft.alignment.center),
                ft.Column([
                    ft.Text(f"New {pl.title.lower()}", size=22, weight=ft.FontWeight.W_600,
                            color="#FFFFFF"),
                    ft.Text(pl.description, size=12,
                            color=ft.Colors.with_opacity(0.82, "#FFFFFF")),
                ], spacing=3, tight=True, expand=True),
            ], spacing=14),
            padding=24, border_radius=RADIUS_LG,
            gradient=ft.LinearGradient(begin=ft.alignment.top_left, end=ft.alignment.bottom_right,
                                       colors=[accent, p.accent_grad_b])),
        ft.Container(height=6),
        card(p, label(p, "The brief"),
             body(p, "Answer what you know. Anything you leave blank, the suite proposes and you "
                     "approve at the first checkpoint.", muted=True, size=12),
             name, *fields),
        ft.Row([primary_button(p, "Create project", create, ft.Icons.ARROW_FORWARD_ROUNDED),
                ghost_button(p, "Cancel", lambda e: studio.navigate("home"))], spacing=10),
        ft.Container(height=30),
    ], spacing=14, scroll=ft.ScrollMode.AUTO, expand=True)
