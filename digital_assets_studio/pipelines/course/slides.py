"""Course slides and workbook.

Slides are rendered as images first, then bound into a PDF. That order matters:
the same images become the video frames, so the deck a student downloads and the
deck they watch are literally the same pixels — and nothing here needs poppler
or any other native tool installed.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ...config import ASSETS_DIR
from ..printables.pages import Style as PageStyle, _wrap_text, _register_fonts
from ..youtube.art import BrandSpec, _font, _gradient, _hex, _wrap, render_scene

SLIDE_16_9 = (1920, 1080)


def title_slide(spec: BrandSpec, title: str, subtitle: str = "", kicker: str = "") -> Image.Image:
    W, H = SLIDE_16_9
    img = _gradient(SLIDE_16_9, spec.bg, spec.bg2).convert("RGBA")
    d = ImageDraw.Draw(img)
    pad = int(W * 0.09)
    y = int(H * 0.30)
    if kicker:
        f = _font(spec.body_font, 40)
        d.text((pad, y - 70), kicker.upper(), font=f, fill=_hex(spec.accent))
    f = _font(spec.display_font, 104)
    for line in _wrap(d, title, f, int(W * 0.78))[:3]:
        d.text((pad, y), line, font=f, fill=_hex(spec.ink))
        y += 118
    if subtitle:
        fs = _font(spec.body_font, 42)
        y += 20
        for line in _wrap(d, subtitle, fs, int(W * 0.7))[:2]:
            d.text((pad, y), line, font=fs, fill=_hex(spec.muted))
            y += 56
    d.rectangle([pad, int(H * 0.245), pad + 120, int(H * 0.245) + 8], fill=_hex(spec.accent))
    return img.convert("RGB")


def bullet_slide(spec: BrandSpec, heading: str, bullets: list[str], number: str = "") -> Image.Image:
    W, H = SLIDE_16_9
    img = _gradient(SLIDE_16_9, spec.bg, spec.bg2).convert("RGBA")
    d = ImageDraw.Draw(img)
    pad = int(W * 0.075)
    f_h = _font(spec.display_font, 66)
    y = int(H * 0.13)
    for line in _wrap(d, heading, f_h, int(W * 0.8))[:2]:
        d.text((pad, y), line, font=f_h, fill=_hex(spec.ink))
        y += 78
    # spread the bullets over the space that is actually there, so a three-point
    # slide does not sit in the top third with a hole under it
    items = bullets[:6]
    f_b = _font(spec.body_font, 44)
    available = int(H * 0.86) - y
    gap = max(24, min(70, (available - len(items) * 56) // max(len(items), 1)))
    y += max(30, gap // 2)
    for b in items:
        d.ellipse([pad + 4, y + 18, pad + 22, y + 36], fill=_hex(spec.accent))
        for i, line in enumerate(_wrap(d, b, f_b, int(W * 0.72))[:2]):
            d.text((pad + 48, y), line, font=f_b, fill=_hex(spec.ink) if i == 0 else _hex(spec.muted))
            y += 54
        y += gap
    if number:
        f_n = _font(spec.body_font, 30)
        d.text((W - pad - d.textlength(number, font=f_n), H - pad), number,
               font=f_n, fill=_hex(spec.muted))
    return img.convert("RGB")


def render_lesson(spec: BrandSpec, lesson: dict, out_dir: Path, index: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    title = lesson.get("title", f"Lesson {index}")
    first = title_slide(spec, title, lesson.get("outcome", ""), f"Lesson {index}")
    p = out_dir / f"{index:02d}_00.png"
    first.save(p)
    paths.append(p)
    for i, slide in enumerate(lesson.get("slides", []), start=1):
        img = bullet_slide(spec, slide.get("heading", ""), slide.get("bullets", []),
                           f"{index}.{i}")
        p = out_dir / f"{index:02d}_{i:02d}.png"
        img.save(p)
        paths.append(p)
    return paths


def deck_pdf(images: list[Path], out: Path) -> Path:
    """Bind rendered slides into one PDF. No external tools involved."""
    if not images:
        raise ValueError("No slides to bind.")
    out.parent.mkdir(parents=True, exist_ok=True)
    first, *rest = [Image.open(p).convert("RGB") for p in images]
    first.save(out, "PDF", resolution=150, save_all=True, append_images=rest)
    return out


def workbook_pdf(out: Path, course_title: str, lessons: list[dict], brand: str = "") -> Path:
    """The printable companion: one exercise page per lesson."""
    from ..printables.pages import Style, build_pdf

    pages = [{"type": "cover", "heading": f"{course_title} — Workbook",
              "subtitle": "Print this. The course only works if you do the exercises."}]
    for i, lesson in enumerate(lessons, start=1):
        prompts = lesson.get("exercise_prompts") or [lesson.get("exercise", "What will you do first?")]
        pages.append({"type": "worksheet",
                      "heading": f"Lesson {i}: {lesson.get('title', '')}",
                      "subtitle": lesson.get("outcome", ""),
                      "prompts": [p for p in prompts if p][:4]})
    pages.append({"type": "notes", "heading": "Notes", "ruling": "lined"})
    style = Style(brand=brand)
    return build_pdf(out, pages, style, f"{course_title} Workbook")
