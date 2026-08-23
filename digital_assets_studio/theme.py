"""Design tokens for the suite.

One palette, two modes. Every colour used anywhere in the UI comes from here so
the whole app can be re-skinned by editing this file alone.
"""
from __future__ import annotations

from dataclasses import dataclass

import flet as ft


@dataclass(frozen=True)
class Palette:
    dark: bool
    # surfaces, back to front
    canvas: str          # window background
    sidebar: str         # navigation column
    surface: str         # cards, panels
    surface_alt: str     # inputs, raised rows, hover
    line: str            # hairline borders
    # type
    text: str
    text_muted: str
    text_faint: str
    # brand
    accent: str
    accent_soft: str
    accent_grad_a: str
    accent_grad_b: str
    # semantic
    ok: str
    warn: str
    danger: str
    info: str
    # asset-type accents
    books: str
    video: str
    apps: str


LIGHT = Palette(
    dark=False,
    canvas="#FFFFFF",
    sidebar="#FBFCFE",
    surface="#FFFFFF",
    surface_alt="#F5F7FA",
    line="#E6EAF0",
    text="#101828",
    text_muted="#586274",
    text_faint="#6B7687",
    accent="#5B5BD6",
    accent_soft="#EEEEFC",
    accent_grad_a="#5B5BD6",
    accent_grad_b="#9333EA",
    ok="#15803D",
    warn="#B45309",
    danger="#DC2626",
    info="#0284C7",
    books="#C2410C",
    video="#E11D48",
    apps="#0F766E",
)

DARK = Palette(
    dark=True,
    canvas="#0E1117",
    sidebar="#12161F",
    surface="#161B24",
    surface_alt="#1E2531",
    line="#262E3D",
    text="#E8ECF3",
    text_muted="#9AA6B8",
    text_faint="#6B7789",
    accent="#7C7CF9",
    accent_soft="#2A2A56",
    accent_grad_a="#6366F1",
    accent_grad_b="#A855F7",
    ok="#34D399",
    warn="#FBBF24",
    danger="#F87171",
    info="#38BDF8",
    books="#F0A868",
    video="#F1616F",
    apps="#4ECDC4",
)

# Spacing scale
XS, SM, MD, LG, XL, XXL = 4, 8, 16, 24, 32, 48

RADIUS_SM = 8
RADIUS = 14
RADIUS_LG = 20

FONT = "Poppins"
FONT_MONO = "Poppins"


def palette(dark: bool) -> Palette:
    return DARK if dark else LIGHT


def build_theme(p: Palette) -> ft.Theme:
    return ft.Theme(
        font_family=FONT,
        use_material3=True,
        color_scheme=ft.ColorScheme(
            primary=p.accent,
            on_primary="#FFFFFF",
            surface=p.surface,
            on_surface=p.text,
            background=p.canvas,
            on_background=p.text,
            error=p.danger,
            outline=p.line,
        ),
        scrollbar_theme=ft.ScrollbarTheme(
            thumb_color=p.line,
            thickness=8,
            radius=4,
        ),
        divider_color=p.line,
    )


def shadow(p: Palette, strength: int = 1) -> ft.BoxShadow:
    """Dark UIs can take a heavy shadow; on white it reads as dirt."""
    alpha = (0.28 if strength > 1 else 0.18) if p.dark else (0.10 if strength > 1 else 0.06)
    return ft.BoxShadow(
        spread_radius=0,
        blur_radius=(12 if p.dark else 18) * strength,
        color=ft.Colors.with_opacity(alpha, "#0B1220"),
        offset=ft.Offset(0, 3 * strength),
    )


def brand_gradient(p: Palette, begin=ft.alignment.top_left, end=ft.alignment.bottom_right) -> ft.LinearGradient:
    return ft.LinearGradient(begin=begin, end=end, colors=[p.accent_grad_a, p.accent_grad_b])
