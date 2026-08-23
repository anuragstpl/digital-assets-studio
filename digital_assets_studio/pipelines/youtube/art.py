"""Channel art: avatar, banner and thumbnails, drawn in code.

The banner is the one people get wrong. YouTube shows 2560x1440 on a TV but
crops to 1546x423 in the centre on a phone, so every word has to live inside
that box while the illustration flows outside it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ...config import ASSETS_DIR

FONTS = ASSETS_DIR / "fonts"

BANNER = (2560, 1440)
SAFE = (1546, 423)
AVATAR = (800, 800)
THUMB = (1280, 720)


@dataclass
class BrandSpec:
    name: str
    tagline: str = ""
    handle: str = ""
    bg: str = "#0E1117"
    bg2: str = "#1B2A4A"
    accent: str = "#0E7C7B"
    ink: str = "#F7F4EF"
    muted: str = "#B9C2D0"
    light_theme: bool = False
    display_font: str = "Poppins-Bold.ttf"
    body_font: str = "Poppins-Medium.ttf"
    initials: str = ""
    palette: list[str] = field(default_factory=list)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    p = FONTS / name
    if p.exists():
        return ImageFont.truetype(str(p), size)
    return ImageFont.load_default(size=size)


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _gradient(size: tuple[int, int], a: str, b: str, diagonal: bool = True) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    ca, cb = _hex(a), _hex(b)
    steps = h if not diagonal else w + h
    for i in range(steps):
        t = i / max(steps - 1, 1)
        col = tuple(int(ca[k] + (cb[k] - ca[k]) * t) for k in range(3))
        if diagonal:
            d.line([(i, 0), (0, i)], fill=col)
        else:
            d.line([(0, i), (w, i)], fill=col)
    return img


def _wrap(d, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if d.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_avatar(spec: BrandSpec) -> Image.Image:
    w, h = AVATAR
    img = _gradient(AVATAR, spec.bg2, spec.accent)
    mask = Image.new("L", AVATAR, 0)
    ImageDraw.Draw(mask).ellipse([0, 0, w, h], fill=255)
    out = Image.new("RGB", AVATAR, _hex(spec.bg))
    out.paste(img, (0, 0), mask)
    d = ImageDraw.Draw(out)
    initials = (spec.initials or "".join(p[0] for p in spec.name.split()[:2])).upper()[:2]
    f = _font(spec.display_font, int(h * 0.42))
    tw = d.textlength(initials, font=f)
    bbox = f.getbbox(initials)
    d.text(((w - tw) / 2, (h - (bbox[3] - bbox[1])) / 2 - bbox[1]), initials, font=f, fill=_hex(spec.ink))
    d.ellipse([10, 10, w - 10, h - 10], outline=_hex(spec.ink), width=6)
    return out


def render_banner(spec: BrandSpec, show_safe_area: bool = False) -> Image.Image:
    W, H = BANNER
    img = _gradient(BANNER, spec.bg, spec.bg2).convert("RGBA")

    deco = Image.new("RGBA", BANNER, (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    acc = _hex(spec.accent)
    for i, cx in enumerate((int(W * 0.13), int(W * 0.87))):
        for r in (520, 380, 240):
            dd.ellipse([cx - r, H // 2 - r, cx + r, H // 2 + r], outline=(*acc, 60 + i * 10), width=6)
    deco = deco.filter(ImageFilter.GaussianBlur(2))
    img = Image.alpha_composite(img, deco)

    d = ImageDraw.Draw(img)
    sw, sh = SAFE
    sx, sy = (W - sw) // 2, (H - sh) // 2
    inner = int(sw * 0.86)

    f_name = _font(spec.display_font, 150)
    while d.textlength(spec.name.upper(), font=f_name) > inner and f_name.size > 60:
        f_name = _font(spec.display_font, f_name.size - 6)
    name = spec.name.upper()
    y = sy + int(sh * 0.14)
    d.text(((W - d.textlength(name, font=f_name)) / 2, y), name, font=f_name, fill=_hex(spec.ink))
    y += int(f_name.size * 1.18)

    if spec.tagline:
        f_tag = _font(spec.body_font, 54)
        for line in _wrap(d, spec.tagline, f_tag, inner)[:2]:
            d.text(((W - d.textlength(line, font=f_tag)) / 2, y), line, font=f_tag, fill=_hex(spec.muted))
            y += int(f_tag.size * 1.3)

    if spec.handle:
        f_h = _font(spec.body_font, 42)
        t = spec.handle if spec.handle.startswith("@") else f"@{spec.handle}"
        tw = d.textlength(t, font=f_h)
        pad = 22
        bx = (W - tw) / 2
        d.rounded_rectangle([bx - pad, y - 8, bx + tw + pad, y + f_h.size + 14],
                            radius=(f_h.size + 22) // 2, fill=(*acc, 210))
        d.text((bx, y), t, font=f_h, fill=_hex(spec.ink))

    if show_safe_area:
        d.rectangle([sx, sy, sx + sw, sy + sh], outline=(255, 0, 255, 200), width=4)
        d.rectangle([(W - 2560) // 2, (H - 423) // 2, W, H], outline=(0, 255, 255, 60), width=2)
    return img.convert("RGB")


def render_thumbnail(spec: BrandSpec, headline: str, kicker: str = "",
                     badge: str = "", art: bytes | None = None) -> Image.Image:
    import io

    W, H = THUMB
    if art:
        bg = Image.open(io.BytesIO(art)).convert("RGB")
        scale = max(W / bg.width, H / bg.height)
        bg = bg.resize((math.ceil(bg.width * scale), math.ceil(bg.height * scale)), Image.LANCZOS)
        bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2,
                      (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
    else:
        bg = _gradient(THUMB, spec.bg, spec.bg2)

    img = bg.convert("RGBA")
    veil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(veil).rectangle([0, 0, int(W * 0.72), H], fill=(0, 0, 0, 150))
    img = Image.alpha_composite(img, veil.filter(ImageFilter.GaussianBlur(40)))
    d = ImageDraw.Draw(img)

    pad = 64
    y = pad + 10
    if kicker:
        fk = _font(spec.body_font, 38)
        t = kicker.upper()
        tw = d.textlength(t, font=fk)
        d.rounded_rectangle([pad, y, pad + tw + 40, y + 62], radius=31, fill=_hex(spec.accent))
        d.text((pad + 20, y + 10), t, font=fk, fill=_hex(spec.ink))
        y += 92

    size = 118
    max_w = int(W * 0.66)
    while size > 54:
        f = _font(spec.display_font, size)
        lines = _wrap(d, headline.upper(), f, max_w)
        if len(lines) * size * 1.1 <= H - y - pad - 40 and len(lines) <= 4:
            break
        size -= 6
    f = _font(spec.display_font, size)
    for line in _wrap(d, headline.upper(), f, max_w):
        d.text((pad + 3, y + 4), line, font=f, fill=(0, 0, 0, 190))
        d.text((pad, y), line, font=f, fill=_hex(spec.ink))
        y += int(size * 1.1)

    if badge:
        fb = _font(spec.display_font, 62)
        tw = d.textlength(badge.upper(), font=fb)
        bx, by = W - tw - 90, H - 150
        d.rounded_rectangle([bx - 28, by - 18, bx + tw + 28, by + 92], radius=24,
                            fill=(*_hex(spec.accent), 235))
        d.text((bx, by), badge.upper(), font=fb, fill=_hex(spec.ink))
    return img.convert("RGB")


def save(img: Image.Image, path: Path, quality: int = 92) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        img.convert("RGB").save(path, "JPEG", quality=quality, optimize=True)
    else:
        img.save(path)
    return path


SLIDE = (1920, 1080)
SLIDE_PORTRAIT = (1080, 1920)


def render_scene(spec: BrandSpec, heading: str, on_screen: str = "", index: int = 0,
                 art: bytes | None = None, size: tuple[int, int] = SLIDE) -> Image.Image:
    """One video scene: heading, supporting line, optional illustration behind."""
    import io

    W, H = size
    if art:
        bg = Image.open(io.BytesIO(art)).convert("RGB")
        scale = max(W / bg.width, H / bg.height)
        bg = bg.resize((math.ceil(bg.width * scale), math.ceil(bg.height * scale)), Image.LANCZOS)
        bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2,
                      (bg.width - W) // 2 + W, (bg.height - H) // 2 + H))
        img = bg.convert("RGBA")
        veil = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(veil).rectangle([0, 0, W, H], fill=(0, 0, 0, 110))
        img = Image.alpha_composite(img, veil)
    else:
        img = _gradient(size, spec.bg, spec.bg2).convert("RGBA")
        deco = Image.new("RGBA", size, (0, 0, 0, 0))
        dd = ImageDraw.Draw(deco)
        acc = _hex(spec.accent)
        cx, cy = int(W * (0.82 if index % 2 == 0 else 0.18)), int(H * 0.5)
        for r in (int(H * 0.46), int(H * 0.34), int(H * 0.22)):
            dd.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(*acc, 70), width=5)
        img = Image.alpha_composite(img, deco.filter(ImageFilter.GaussianBlur(1)))

    d = ImageDraw.Draw(img)
    pad = int(W * 0.075)
    max_w = int(W * 0.72)

    bar_h = int(H * 0.012)
    d.rounded_rectangle([pad, int(H * 0.20), pad + int(W * 0.09), int(H * 0.20) + bar_h],
                        radius=bar_h // 2, fill=_hex(spec.accent))

    size_h = int(H * 0.085)
    while size_h > int(H * 0.04):
        f = _font(spec.display_font, size_h)
        lines = _wrap(d, heading, f, max_w)
        if len(lines) <= 3:
            break
        size_h -= 4
    f = _font(spec.display_font, size_h)
    y = int(H * 0.26)
    for line in _wrap(d, heading, f, max_w)[:3]:
        d.text((pad + 2, y + 3), line, font=f, fill=(0, 0, 0, 150))
        d.text((pad, y), line, font=f, fill=_hex(spec.ink))
        y += int(size_h * 1.15)

    if on_screen:
        fs = _font(spec.body_font, int(H * 0.038))
        y += int(H * 0.03)
        for line in _wrap(d, on_screen, fs, max_w)[:3]:
            d.text((pad, y), line, font=fs, fill=(*_hex(spec.muted), 235))
            y += int(fs.size * 1.4)

    if spec.name:
        fn = _font(spec.body_font, int(H * 0.026))
        d.text((pad, H - int(H * 0.075)), spec.name.upper(), font=fn, fill=(*_hex(spec.muted), 190))
    return img.convert("RGB")
