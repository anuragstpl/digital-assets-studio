"""Store assets: framed screenshots and the Play feature graphic."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ...config import ASSETS_DIR

FONTS = ASSETS_DIR / "fonts"

PLAY_PHONE = (1080, 1920)
PLAY_FEATURE = (1024, 500)
IOS_6_7 = (1290, 2796)
IOS_6_5 = (1242, 2688)
IPAD_12_9 = (2048, 2732)


@dataclass
class ShotSpec:
    caption: str
    subcaption: str = ""
    bg: str = "#0E1117"
    bg2: str = "#243B6B"
    ink: str = "#FFFFFF"
    accent: str = "#3B5BDB"
    display_font: str = "Poppins-Bold.ttf"
    body_font: str = "Poppins-Regular.ttf"
    device_frame: bool = True
    palette: list[str] = field(default_factory=list)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    p = FONTS / name
    return ImageFont.truetype(str(p), size) if p.exists() else ImageFont.load_default(size=size)


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _gradient(size, a, b):
    w, h = size
    img = Image.new("RGB", size)
    d = ImageDraw.Draw(img)
    ca, cb = _hex(a), _hex(b)
    for y in range(h):
        t = y / max(h - 1, 1)
        d.line([(0, y), (w, y)], fill=tuple(int(ca[i] + (cb[i] - ca[i]) * t) for i in range(3)))
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


def frame_screenshot(raw: Image.Image, spec: ShotSpec,
                     size: tuple[int, int] = PLAY_PHONE) -> Image.Image:
    """Put a raw app screenshot on a branded background with a caption above it."""
    W, H = size
    canvas = _gradient(size, spec.bg2, spec.bg).convert("RGBA")

    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    r = int(W * 0.9)
    gd.ellipse([W // 2 - r, int(H * 0.62) - r, W // 2 + r, int(H * 0.62) + r],
               fill=(*_hex(spec.accent), 60))
    canvas = Image.alpha_composite(canvas, glow.filter(ImageFilter.GaussianBlur(W // 8)))

    d = ImageDraw.Draw(canvas)
    pad = int(W * 0.08)
    y = int(H * 0.055)

    fc = _font(spec.display_font, int(W * 0.072))
    lines = _wrap(d, spec.caption, fc, W - pad * 2)[:3]
    for line in lines:
        d.text(((W - d.textlength(line, font=fc)) / 2, y), line, font=fc, fill=_hex(spec.ink))
        y += int(fc.size * 1.16)

    if spec.subcaption:
        fs = _font(spec.body_font, int(W * 0.040))
        y += int(H * 0.008)
        for line in _wrap(d, spec.subcaption, fs, int(W * 0.8))[:2]:
            d.text(((W - d.textlength(line, font=fs)) / 2, y), line, font=fs,
                   fill=(*_hex(spec.ink), 190))
            y += int(fs.size * 1.35)

    top = y + int(H * 0.035)
    avail_h = H - top - int(H * 0.05)
    avail_w = W - pad * 2
    scale = min(avail_w / raw.width, avail_h / raw.height)
    shot = raw.convert("RGB").resize((int(raw.width * scale), int(raw.height * scale)), Image.LANCZOS)

    radius = int(shot.width * 0.055)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, shot.width, shot.height], radius=radius, fill=255)

    x = (W - shot.width) // 2
    shadow = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [x + 8, top + 18, x + shot.width + 8, top + shot.height + 18], radius=radius, fill=(0, 0, 0, 150))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(28)))

    canvas.paste(shot, (x, top), mask)
    if spec.device_frame:
        dd = ImageDraw.Draw(canvas)
        dd.rounded_rectangle([x - 4, top - 4, x + shot.width + 4, top + shot.height + 4],
                             radius=radius + 4, outline=(255, 255, 255, 90), width=5)
    return canvas.convert("RGB")


def feature_graphic(app_name: str, tagline: str, spec: ShotSpec) -> Image.Image:
    W, H = PLAY_FEATURE
    img = _gradient(PLAY_FEATURE, spec.bg2, spec.bg).convert("RGBA")
    deco = Image.new("RGBA", PLAY_FEATURE, (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    for r in (300, 210, 130):
        dd.ellipse([W - 140 - r, H // 2 - r, W - 140 + r, H // 2 + r],
                   outline=(*_hex(spec.accent), 110), width=4)
    img = Image.alpha_composite(img, deco.filter(ImageFilter.GaussianBlur(1)))
    d = ImageDraw.Draw(img)
    pad = 56
    f = _font(spec.display_font, 76)
    while d.textlength(app_name, font=f) > W * 0.62 and f.size > 34:
        f = _font(spec.display_font, f.size - 4)
    d.text((pad, H // 2 - 74), app_name, font=f, fill=_hex(spec.ink))
    ft = _font(spec.body_font, 34)
    y = H // 2 + 14
    for line in _wrap(d, tagline, ft, int(W * 0.6))[:2]:
        d.text((pad, y), line, font=ft, fill=(*_hex(spec.ink), 205))
        y += 46
    return img.convert("RGB")


def save(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in (".jpg", ".jpeg"):
        img.convert("RGB").save(path, "JPEG", quality=92, optimize=True)
    else:
        img.save(path, "PNG")
    return path


def placeholder_screenshot(index: int, size=(1080, 2280)) -> Image.Image:
    """A neutral stand-in so the pipeline can be exercised before real screens exist."""
    img = Image.new("RGB", size, (24, 27, 36))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([40, 120, size[0] - 40, 320], radius=24, fill=(38, 44, 58))
    for i in range(6):
        top = 380 + i * 170
        d.rounded_rectangle([40, top, size[0] - 40, top + 140], radius=20, fill=(33, 38, 50))
        d.ellipse([70, top + 32, 146, top + 108], fill=(59, 91, 219))
    f = _font("Poppins-Medium.ttf", 44)
    d.text((60, 190), f"Screen {index}", font=f, fill=(226, 232, 240))
    return img
