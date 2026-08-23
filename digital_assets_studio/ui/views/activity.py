"""A running log of everything the suite has done this session."""
from __future__ import annotations

import flet as ft

from ...core.llm.router import USAGE
from ...theme import RADIUS, RADIUS_SM
from ..components import body, card, empty_state, h1, h2, label


def _stat(p, value: str, caption: str) -> ft.Control:
    return ft.Container(
        content=ft.Column([
            ft.Text(value, size=22, weight=ft.FontWeight.W_700, color=p.text),
            ft.Text(caption, size=11, color=p.text_faint),
        ], spacing=2, tight=True),
        padding=16, bgcolor=p.surface, border_radius=RADIUS,
        border=ft.border.all(1, p.line), expand=True)


def build(studio) -> ft.Control:
    p = studio.palette
    lines = studio.log_lines
    stats = ft.Row([
        _stat(p, str(USAGE.calls), "model calls"),
        _stat(p, f"{USAGE.input_tokens:,}", "input tokens"),
        _stat(p, f"{USAGE.output_tokens:,}", "output tokens"),
        _stat(p, str(USAGE.images), "images"),
        _stat(p, f"{USAGE.seconds:.0f}s", "spent waiting"),
    ], spacing=12)

    if not lines:
        content = empty_state(p, ft.Icons.RECEIPT_LONG_ROUNDED, "Nothing yet",
                              "Run a step and everything it does shows up here.")
    else:
        content = ft.Container(
            content=ft.Column(
                [ft.Text(line, size=11, color=p.text_muted, selectable=True,
                         font_family="monospace") for line in reversed(lines[-400:])],
                spacing=3, scroll=ft.ScrollMode.AUTO, expand=True),
            padding=16, bgcolor=p.surface, border_radius=RADIUS,
            border=ft.border.all(1, p.line), expand=True)

    return ft.Column([h1(p, "Activity"), stats, ft.Container(height=4), content],
                     spacing=12, expand=True)
