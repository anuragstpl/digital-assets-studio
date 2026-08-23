"""Cover generation: e-book front cover and full paperback wrap.

The art can come from an image model, from a file you drop in, or from the
built-in procedural background - which is deliberately simple and typographic,
because a clean type-led cover outsells a muddy AI illustration in most
categories.

Spine width follows KDP's paper-stock table, so the wrap lines up on the press.
"""
from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ...config import ASSETS_DIR

FONTS = ASSETS_DIR / "fonts"

# inches of thickness per page, by KDP paper stock
PAPER = {
    "white (b&w interior)": 0.002252,
    "cream (b&w interior)": 0.0025,
    "colour interior": 0.002347,
}

EBOOK_W, EBOOK_H = 1600, 2560     # 1:1.6, comfortably above every store minimum
DPI = 300


@dataclass
class CoverSpec:
    title: str
    author: str
    subtitle: str = ""
    series: str = ""
    imprint: str = ""
    palette: list[str] = field(default_factory=lambda: ["#101322", "#25305C", "#C9A227", "#F2EFE6"])
    title_color: str = "#F7F3EA"
    subtitle_color: str = "#D9CFAF"
    author_color: str = "#F7F3EA"
    title_case: str = "upper"
    display_font: str = "Poppins-Bold.ttf"
    body_font: str = "Poppins-Medium.ttf"
    blurb: str = ""              # back-cover copy for the wrap
    trim: str = "6 x 9 in"
    pages: int = 200
    paper: str = "white (b&w interior)"


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    for fallback in ("Poppins-Bold.ttf", "Poppins-Regular.ttf"):
        fp = FONTS / fallback
        if fp.exists():
            return ImageFont.truetype(str(fp), size)
    return ImageFont.load_default(size=size)


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _luma(c: str) -> float:
    r, g, b = _hex(c)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


# --------------------------------------------------------------- background --

def procedural_background(w: int, h: int, palette: list[str], seed: int = 7) -> Image.Image:
    """A clean, type-friendly background: vertical gradient, a soft glow, and a
    few large geometric shapes. No noise, no clutter behind the title."""
    rng = random.Random(seed)
    pal = [_hex(c) for c in (palette + ["#101322", "#25305C", "#C9A227", "#F2EFE6"])[:4]]
    top, bottom = pal[0], pal[1]
    img = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(h - 1, 1)
        e = t * t * (3 - 2 * t)
        d.line([(0, y), (w, y)],
               fill=tuple(int(top[i] + (bottom[i] - top[i]) * e) for i in range(3)))

    shapes = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shapes)
    accent = pal[2]
    for i in range(3):
        r = rng.randint(int(w * 0.30), int(w * 0.75))
        cx = rng.randint(-r // 3, w + r // 3)
        cy = rng.randint(int(h * 0.35), int(h * 1.05))
        alpha = 26 + i * 10
        sd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*accent, alpha))
    shapes = shapes.filter(ImageFilter.GaussianBlur(w // 30))
    img = Image.alpha_composite(img.convert("RGBA"), shapes).convert("RGB")

    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gr = int(w * 0.55)
    gd.ellipse([w // 2 - gr, int(h * 0.18) - gr, w // 2 + gr, int(h * 0.18) + gr],
               fill=(*accent, 34))
    glow = glow.filter(ImageFilter.GaussianBlur(w // 12))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")

    # a darkened band behind where the title will sit, so type always reads
    veil = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    vd = ImageDraw.Draw(veil)
    vd.rectangle([0, 0, w, int(h * 0.52)], fill=(0, 0, 0, 70))
    veil = veil.filter(ImageFilter.GaussianBlur(h // 22))
    return Image.alpha_composite(img.convert("RGBA"), veil).convert("RGB")


# ---------------------------------------------------------------- typesetting --

def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for wd in words:
        trial = f"{cur} {wd}".strip()
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def _fit_block(draw, text, font_name, max_w, max_h, start, min_size=28, tracking=0.0):
    size = start
    while size > min_size:
        f = _font(font_name, size)
        lines = _wrap(draw, text, f, max_w)
        lh = size * 1.12
        if len(lines) * lh <= max_h and all(draw.textlength(l, font=f) <= max_w for l in lines):
            return f, lines, lh
        size -= 4
    f = _font(font_name, min_size)
    return f, _wrap(draw, text, f, max_w), min_size * 1.12


def _draw_centered_block(draw, lines, font, line_h, cx, top, fill, shadow=True):
    y = top
    for line in lines:
        wpx = draw.textlength(line, font=font)
        x = cx - wpx / 2
        if shadow:
            draw.text((x + 2, y + 3), line, font=font, fill=(0, 0, 0, 130))
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h
    return y


def render_front(spec: CoverSpec, art: bytes | None = None,
                 width: int = EBOOK_W, height: int = EBOOK_H) -> Image.Image:
    if art:
        bg = Image.open(io.BytesIO(art)).convert("RGB")
        bg = _cover_fit(bg, width, height)
        # Scrims top and bottom. Without the lower one the author name sits straight
        # on the photograph and vanishes the moment the picture is bright there.
        veil = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        vd = ImageDraw.Draw(veil)
        vd.rectangle([0, 0, width, int(height * 0.55)], fill=(0, 0, 0, 95))
        vd.rectangle([0, int(height * 0.78), width, height], fill=(0, 0, 0, 110))
        bg = Image.alpha_composite(bg.convert("RGBA"),
                                   veil.filter(ImageFilter.GaussianBlur(height // 24))).convert("RGB")
    else:
        bg = procedural_background(width, height, spec.palette)

    img = bg.convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    margin = int(width * 0.09)
    inner = width - margin * 2
    cx = width // 2

    title = spec.title.split(":")[0].strip()
    sub = spec.subtitle or (spec.title.split(":", 1)[1].strip() if ":" in spec.title else "")
    if spec.title_case == "upper":
        title = title.upper()

    y = int(height * 0.12)
    if spec.series:
        f = _font(spec.body_font, int(width * 0.030))
        t = spec.series.upper()
        d.text((cx - d.textlength(t, font=f) / 2, y), t, font=f, fill=_hex(spec.subtitle_color))
        y += int(width * 0.062)

    tf, tlines, tlh = _fit_block(d, title, spec.display_font, inner,
                                 int(height * 0.30), int(width * 0.155), int(width * 0.055))
    y = _draw_centered_block(d, tlines, tf, tlh, cx, y, _hex(spec.title_color))

    if sub:
        y += int(height * 0.018)
        sf, slines, slh = _fit_block(d, sub, spec.body_font, int(inner * 0.92),
                                     int(height * 0.12), int(width * 0.050), int(width * 0.026))
        rule_w = int(inner * 0.28)
        d.line([(cx - rule_w // 2, y), (cx + rule_w // 2, y)], fill=_hex(spec.subtitle_color), width=3)
        y += int(height * 0.022)
        y = _draw_centered_block(d, slines, sf, slh, cx, y, _hex(spec.subtitle_color))

    if not art:
        # a plain background leaves a hole in the middle third; a restrained
        # ornament and frame carry it instead of empty gradient
        oy = int(height * 0.615)
        r = int(width * 0.085)
        accent = _hex(spec.palette[2] if len(spec.palette) > 2 else spec.subtitle_color)
        d.ellipse([cx - r, oy - r, cx + r, oy + r], outline=(*accent, 150), width=max(2, width // 500))
        d.ellipse([cx - r // 3, oy - r // 3, cx + r // 3, oy + r // 3], fill=(*accent, 90))
        d.line([(cx - int(width * 0.26), oy), (cx - int(r * 1.5), oy)], fill=(*accent, 110), width=2)
        d.line([(cx + int(r * 1.5), oy), (cx + int(width * 0.26), oy)], fill=(*accent, 110), width=2)
        fr = int(width * 0.045)
        d.rectangle([fr, fr, width - fr, height - fr], outline=(*accent, 70), width=max(2, width // 600))

    af = _font(spec.display_font, int(width * 0.052))
    author = spec.author.upper()
    ay = int(height * 0.87)
    d.text((cx - d.textlength(author, font=af) / 2, ay), author, font=af, fill=_hex(spec.author_color))

    if spec.imprint:
        imf = _font(spec.body_font, int(width * 0.024))
        t = spec.imprint.upper()
        d.text((cx - d.textlength(t, font=imf) / 2, ay + int(width * 0.075)),
               t, font=imf, fill=_hex(spec.subtitle_color))

    return Image.alpha_composite(img, layer).convert("RGB")


def _cover_fit(img: Image.Image, w: int, h: int) -> Image.Image:
    scale = max(w / img.width, h / img.height)
    img = img.resize((math.ceil(img.width * scale), math.ceil(img.height * scale)), Image.LANCZOS)
    left = (img.width - w) // 2
    top = (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


# --------------------------------------------------------------------- wrap --

def spine_width_in(pages: int, paper: str) -> float:
    return pages * PAPER.get(paper, PAPER["white (b&w interior)"])


def render_wrap(spec: CoverSpec, art: bytes | None = None, bleed_in: float = 0.125) -> Image.Image:
    from .printpdf import TRIMS
    from reportlab.lib.units import inch as _in

    tw, th = TRIMS.get(spec.trim, TRIMS["6 x 9 in"])
    trim_w_in, trim_h_in = tw / _in, th / _in
    spine_in = spine_width_in(spec.pages, spec.paper)

    total_w_in = trim_w_in * 2 + spine_in + bleed_in * 2
    total_h_in = trim_h_in + bleed_in * 2
    W, H = int(total_w_in * DPI), int(total_h_in * DPI)

    front_w = int(trim_w_in * DPI)
    front = render_front(spec, art, width=front_w, height=int(trim_h_in * DPI))

    canvas = Image.new("RGB", (W, H), _hex(spec.palette[0] if spec.palette else "#101322"))
    # bleed for the front is created by scaling the front art slightly past trim
    bleed_px = int(bleed_in * DPI)
    front_bleed = front.resize((front_w + bleed_px * 2, front.height + bleed_px * 2), Image.LANCZOS)
    canvas.paste(front_bleed, (W - front_w - bleed_px * 2, 0))

    back_x0 = 0
    back_w = front_w + bleed_px
    back_bg = procedural_background(back_w, H, spec.palette, seed=11)
    canvas.paste(back_bg, (back_x0, 0))

    d = ImageDraw.Draw(canvas)
    light_type = _luma(spec.palette[0] if spec.palette else "#101322") < 0.5
    ink = (247, 243, 234) if light_type else (18, 20, 28)

    # back-cover copy
    pad = int(0.7 * DPI)
    bf = _font(spec.body_font, 46)
    y = int(0.9 * DPI)
    for para in (spec.blurb or "").split("\n\n"):
        for line in _wrap(d, para.strip(), bf, back_w - pad * 2 - bleed_px):
            d.text((pad, y), line, font=bf, fill=ink)
            y += 62
        y += 26
        if y > H - 2.4 * DPI:
            break

    if spec.imprint:
        imf = _font(spec.display_font, 40)
        d.text((pad, H - int(1.05 * DPI)), spec.imprint.upper(), font=imf, fill=ink)

    # ISBN barcode reservation - KDP prints its own barcode into this area
    bw, bh = int(2.0 * DPI), int(1.2 * DPI)
    bx = back_w - bw - int(0.45 * DPI)
    by = H - bh - int(0.45 * DPI)
    d.rectangle([bx, by, bx + bw, by + bh], fill=(255, 255, 255))
    sf = _font(spec.body_font, 30)
    d.text((bx + 24, by + bh // 2 - 18), "ISBN barcode area", font=sf, fill=(120, 120, 120))

    # spine
    spine_px = int(spine_in * DPI)
    spine_x = back_w
    if spine_px >= int(0.0625 * DPI * 2):   # under ~1/8in, leave the spine bare
        spine_img = Image.new("RGB", (H, spine_px), _hex(spec.palette[1] if len(spec.palette) > 1 else "#25305C"))
        sd = ImageDraw.Draw(spine_img)
        size = max(24, min(int(spine_px * 0.42), 90))
        f = _font(spec.display_font, size)
        text = f"{spec.title.split(':')[0].strip().upper()}   ·   {spec.author.upper()}"
        tw_px = sd.textlength(text, font=f)
        sd.text(((H - tw_px) / 2, (spine_px - size * 1.25) / 2), text, font=f, fill=_hex(spec.title_color))
        canvas.paste(spine_img.rotate(90, expand=True), (spine_x, 0))

    return canvas


def save_jpeg(img: Image.Image, path: Path, quality: int = 92) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=quality, dpi=(DPI, DPI), optimize=True)
    return path


def save_pdf(img: Image.Image, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "PDF", resolution=DPI)
    return path
