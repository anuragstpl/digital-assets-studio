"""Shared UI pieces. Everything visual is built from these so the app stays consistent."""
from __future__ import annotations

from typing import Any, Callable

import flet as ft

from ..core.pipeline import Field_
from ..theme import (LG, MD, RADIUS, RADIUS_LG, RADIUS_SM, SM, XS, Palette, brand_gradient,
                     shadow)


def card(p: Palette, *content: ft.Control, padding: int = 20, gap: int = 12,
         expand: bool | int = False, on_click: Callable | None = None,
         bgcolor: str | None = None, border_color: str | None = None) -> ft.Container:
    return ft.Container(
        content=ft.Column(list(content), spacing=gap, tight=True),
        padding=padding,
        bgcolor=bgcolor or p.surface,
        border_radius=RADIUS,
        border=ft.border.all(1, border_color or p.line),
        on_click=on_click,
        ink=on_click is not None,
        animate=ft.Animation(160, ft.AnimationCurve.EASE_OUT),
        shadow=None if p.dark else shadow(p),
        expand=expand,
    )


def h1(p: Palette, text: str) -> ft.Text:
    return ft.Text(text, size=26, weight=ft.FontWeight.W_600, color=p.text)


def h2(p: Palette, text: str) -> ft.Text:
    return ft.Text(text, size=18, weight=ft.FontWeight.W_600, color=p.text)


def label(p: Palette, text: str) -> ft.Text:
    return ft.Text(text.upper(), size=11, weight=ft.FontWeight.W_600,
                   color=p.text_faint, spans=None)


def body(p: Palette, text: str, muted: bool = False, size: int = 14,
         selectable: bool = False) -> ft.Text:
    return ft.Text(text, size=size, color=p.text_muted if muted else p.text,
                   selectable=selectable)


def pill(p: Palette, text: str, color: str, filled: bool = False) -> ft.Container:
    return ft.Container(
        content=ft.Text(text, size=11, weight=ft.FontWeight.W_600,
                        color="#FFFFFF" if filled else color),
        padding=ft.padding.symmetric(4, 10),
        bgcolor=color if filled else ft.Colors.with_opacity(0.14, color),
        border_radius=999,
    )


def primary_button(p: Palette, text: str, on_click: Callable, icon: str | None = None,
                   disabled: bool = False, expand: bool = False) -> ft.Container:
    inner: list[ft.Control] = []
    if icon:
        inner.append(ft.Icon(icon, size=17, color="#FFFFFF"))
    inner.append(ft.Text(text, size=14, weight=ft.FontWeight.W_600, color="#FFFFFF"))
    return ft.Container(
        content=ft.Row(inner, spacing=8, alignment=ft.MainAxisAlignment.CENTER, tight=True),
        padding=ft.padding.symmetric(12, 20),
        border_radius=RADIUS_SM,
        gradient=None if disabled else brand_gradient(p),
        bgcolor=p.surface_alt if disabled else None,
        on_click=None if disabled else on_click,
        ink=not disabled,
        opacity=0.5 if disabled else 1,
        animate_opacity=150,
        expand=expand,
        alignment=ft.alignment.center,
    )


def ghost_button(p: Palette, text: str, on_click: Callable, icon: str | None = None,
                 danger: bool = False, disabled: bool = False) -> ft.Container:
    col = p.danger if danger else p.text_muted
    inner: list[ft.Control] = []
    if icon:
        inner.append(ft.Icon(icon, size=16, color=col))
    inner.append(ft.Text(text, size=13, weight=ft.FontWeight.W_500, color=col))
    return ft.Container(
        content=ft.Row(inner, spacing=7, tight=True, alignment=ft.MainAxisAlignment.CENTER),
        padding=ft.padding.symmetric(10, 16),
        border_radius=RADIUS_SM,
        border=ft.border.all(1, p.line),
        on_click=None if disabled else on_click,
        ink=not disabled,
        opacity=0.45 if disabled else 1,
    )


def text_field(p: Palette, label_text: str, value: str = "", hint: str = "",
               multiline: bool = False, password: bool = False,
               on_change: Callable | None = None, helper: str = "",
               width: int | None = None, suffix: ft.Control | None = None) -> ft.TextField:
    return ft.TextField(
        label=label_text, value=value, hint_text=hint, helper_text=helper or None,
        multiline=multiline, min_lines=3 if multiline else 1, max_lines=8 if multiline else 1,
        password=password, can_reveal_password=password, on_change=on_change,
        border_radius=RADIUS_SM, filled=True, fill_color=p.surface_alt,
        border_color=p.line, focused_border_color=p.accent,
        color=p.text, label_style=ft.TextStyle(color=p.text_muted, size=13),
        helper_style=ft.TextStyle(color=p.text_faint, size=11),
        text_size=14, width=width, suffix=suffix, dense=False,
        content_padding=ft.padding.symmetric(14, 14),
    )


def dropdown(p: Palette, label_text: str, options: list[str], value: str = "",
             on_change: Callable | None = None, width: int | None = None) -> ft.Dropdown:
    return ft.Dropdown(
        label=label_text,
        options=[ft.dropdown.Option(o) for o in options],
        value=value if value in options else (options[0] if options else None),
        on_change=on_change, border_radius=RADIUS_SM, filled=True, fill_color=p.surface_alt,
        border_color=p.line, focused_border_color=p.accent, color=p.text,
        label_style=ft.TextStyle(color=p.text_muted, size=13), text_size=14, width=width,
        content_padding=ft.padding.symmetric(12, 14),
    )


def divider(p: Palette, height: int = 1) -> ft.Container:
    return ft.Container(height=height, bgcolor=p.line)


def empty_state(p: Palette, icon: str, title: str, subtitle: str,
                action: ft.Control | None = None) -> ft.Container:
    items: list[ft.Control] = [
        ft.Container(content=ft.Icon(icon, size=34, color=p.text_faint),
                     width=76, height=76, border_radius=999, bgcolor=p.surface_alt,
                     alignment=ft.alignment.center),
        ft.Text(title, size=17, weight=ft.FontWeight.W_600, color=p.text),
        ft.Text(subtitle, size=13, color=p.text_muted, text_align=ft.TextAlign.CENTER),
    ]
    if action:
        items.append(ft.Container(content=action, margin=ft.margin.only(top=8)))
    return ft.Container(
        content=ft.Column(items, spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        alignment=ft.alignment.center, padding=48, expand=True,
    )


def markdown(p: Palette, text: str) -> ft.Markdown:
    return ft.Markdown(
        text or "_Nothing here yet._",
        selectable=True,
        extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
        code_theme=(ft.MarkdownCodeTheme.ATOM_ONE_DARK if p.dark
                    else ft.MarkdownCodeTheme.ATOM_ONE_LIGHT),
        md_style_sheet=ft.MarkdownStyleSheet(
            p_text_style=ft.TextStyle(color=p.text, size=14, height=1.55),
            h1_text_style=ft.TextStyle(color=p.text, size=22, weight=ft.FontWeight.W_600),
            h2_text_style=ft.TextStyle(color=p.text, size=18, weight=ft.FontWeight.W_600),
            h3_text_style=ft.TextStyle(color=p.text, size=15, weight=ft.FontWeight.W_600),
            code_text_style=ft.TextStyle(color=p.text, size=12, font_family="monospace"),
            blockquote_text_style=ft.TextStyle(color=p.text_muted, size=14, italic=True),
            list_bullet_text_style=ft.TextStyle(color=p.accent),
            a_text_style=ft.TextStyle(color=p.accent),
        ),
        auto_follow_links=True,
    )


def build_field(p: Palette, f: Field_, value: Any, on_change: Callable[[str, Any], None],
                browse: Callable[[Field_, Callable[[str], None]], None] | None = None) -> ft.Control:
    """One control for one field.

    `browse` opens the OS file dialog for file/folder fields. It is passed in
    rather than imported because the picker belongs to the page, not to a widget,
    and a form without one still has to draw - the box stays typeable.
    """
    def handler(e):
        v = e.control.value
        if f.type == "number":
            try:
                v = float(v) if "." in str(v) else int(v)
            except (TypeError, ValueError):
                v = 0
        on_change(f.key, v)

    if f.type == "select":
        return dropdown(p, f.label, f.choices(), str(value or f.default or ""), handler)
    if f.type in ("file", "folder"):
        box = text_field(
            p, f.label, str(value or f.default or ""), hint=f.placeholder,
            helper=f.help, on_change=handler)

        def picked(path: str) -> None:
            box.value = path
            on_change(f.key, path)
            try:
                box.update()
            except Exception:  # noqa: BLE001 - not mounted yet; the value still stuck
                pass

        return ft.Row([
            ft.Container(content=box, expand=True),
            ghost_button(p, "Browse", lambda e: browse(f, picked) if browse else None,
                         ft.Icons.FOLDER_OPEN_ROUNDED if f.type == "folder"
                         else ft.Icons.ATTACH_FILE_ROUNDED,
                         disabled=browse is None),
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START)
    if f.type == "switch":
        return ft.Container(
            content=ft.Row([
                ft.Switch(value=bool(value if value not in ("", None) else f.default),
                          active_color=p.accent,
                          on_change=lambda e: on_change(f.key, e.control.value)),
                ft.Column([ft.Text(f.label, size=14, color=p.text),
                           ft.Text(f.help, size=11, color=p.text_faint)] if f.help
                          else [ft.Text(f.label, size=14, color=p.text)], spacing=1, tight=True),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.padding.symmetric(6, 2))
    return text_field(
        p, f.label + (" *" if f.required else ""),
        str(value if value not in (None, "") else (f.default if f.default != 0 or f.type == "number" else "")),
        hint=f.placeholder, multiline=f.type == "multiline", helper=f.help, on_change=handler)


def status_dot(p: Palette, status: str) -> ft.Container:
    colors = {"done": p.ok, "running": p.info, "failed": p.danger,
              "skipped": p.text_faint, "pending": p.line}
    return ft.Container(width=9, height=9, border_radius=999,
                        bgcolor=colors.get(status, p.line))


def snack(page: ft.Page, p: Palette, message: str, kind: str = "info") -> None:
    color = {"ok": p.ok, "error": p.danger, "warn": p.warn}.get(kind, p.surface_alt)
    page.open(ft.SnackBar(
        content=ft.Text(message, color="#FFFFFF" if kind != "info" else p.text, size=13),
        bgcolor=color, duration=5000 if kind == "error" else 3000,
        behavior=ft.SnackBarBehavior.FLOATING,
        shape=ft.RoundedRectangleBorder(radius=RADIUS_SM),
    ))
