"""Printable page generation.

Everything is drawn as vector PDF at the exact trim, so it prints crisply at any
size and stays a small file. Two page sizes ship because they cover the market:
US Letter for North America and A4 for everywhere else — sell both in one pack
and you stop getting refund requests about margins.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.units import inch, mm
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from ...config import ASSETS_DIR

SIZES = {"US Letter (8.5 × 11 in)": LETTER, "A4 (210 × 297 mm)": A4}
FONT_DIR = ASSETS_DIR / "fonts"
_REGISTERED = False


def _register_fonts() -> tuple[str, str]:
    global _REGISTERED
    if not _REGISTERED:
        try:
            pdfmetrics.registerFont(TTFont("Poppins", str(FONT_DIR / "Poppins-Regular.ttf")))
            pdfmetrics.registerFont(TTFont("Poppins-Bold", str(FONT_DIR / "Poppins-Bold.ttf")))
            _REGISTERED = True
        except Exception:  # noqa: BLE001
            return "Helvetica", "Helvetica-Bold"
    return "Poppins", "Poppins-Bold"


@dataclass
class Style:
    ink: str = "#1B2430"
    rule: str = "#C9D0DA"
    faint: str = "#E7EBF1"
    accent: str = "#3B5BDB"
    page: str = "US Letter (8.5 × 11 in)"
    margin: float = 0.6 * inch
    show_footer: bool = True
    brand: str = ""

    @property
    def size(self):
        return SIZES.get(self.page, LETTER)


def _c(hex_str: str):
    return colors.HexColor(hex_str)


class Page:
    """Shared furniture: title, rules, footer."""

    def __init__(self, c: rl_canvas.Canvas, st: Style):
        self.c, self.st = c, st
        self.body, self.bold = _register_fonts()
        self.w, self.h = st.size

    def header(self, title: str, subtitle: str = "") -> float:
        c, st = self.c, self.st
        y = self.h - st.margin - 26
        c.setFillColor(_c(st.ink))
        c.setFont(self.bold, 22)
        c.drawString(st.margin, y, title)
        if subtitle:
            y -= 20
            c.setFillColor(_c(st.ink))
            c.setFont(self.body, 10.5)
            c.drawString(st.margin, y, subtitle)
        y -= 14
        c.setStrokeColor(_c(st.accent))
        c.setLineWidth(2)
        c.line(st.margin, y, st.margin + 54, y)
        return y - 26

    def footer(self) -> None:
        if not self.st.show_footer or not self.st.brand:
            return
        c, st = self.c, self.st
        c.setFillColor(_c(st.rule))
        c.setFont(self.body, 7.5)
        c.drawCentredString(self.w / 2, st.margin - 16, self.st.brand)

    def lines(self, top: float, count: int, gap: float = 26, label_width: float = 0) -> float:
        c, st = self.c, self.st
        c.setStrokeColor(_c(st.rule))
        c.setLineWidth(0.6)
        y = top
        for _ in range(count):
            c.line(st.margin + label_width, y, self.w - st.margin, y)
            y -= gap
        return y

    def checkboxes(self, top: float, count: int, gap: float = 26, label: str = "") -> float:
        c, st = self.c, self.st
        y = top
        for _ in range(count):
            c.setStrokeColor(_c(st.ink))
            c.setLineWidth(0.9)
            c.roundRect(st.margin, y - 3, 11, 11, 2, stroke=1, fill=0)
            c.setStrokeColor(_c(st.rule))
            c.setLineWidth(0.6)
            c.line(st.margin + 20, y, self.w - st.margin, y)
            y -= gap
        return y

    def box(self, x: float, y: float, w: float, h: float, title: str = "") -> None:
        c, st = self.c, self.st
        c.setStrokeColor(_c(st.faint))
        c.setLineWidth(1)
        c.roundRect(x, y, w, h, 6, stroke=1, fill=0)
        if title:
            c.setFillColor(_c(st.ink))
            c.setFont(self.bold, 8.5)
            c.drawString(x + 9, y + h - 15, title.upper())


# ------------------------------------------------------------------- pages ---

def daily_planner(pg: Page, heading: str = "Daily Plan") -> None:
    st = pg.c, pg.st
    c, s = st
    y = pg.header(heading, "Date  ______________")
    col = (pg.w - 2 * s.margin - 18) / 2

    pg.box(s.margin, y - 250, col, 250, "Schedule")
    hour_y = y - 26
    c.setFont(pg.body, 8)
    for hour in range(6, 22):
        c.setFillColor(_c(s.rule))
        c.drawString(s.margin + 9, hour_y, f"{hour:02d}:00")
        c.setStrokeColor(_c(s.faint))
        c.setLineWidth(0.5)
        c.line(s.margin + 42, hour_y - 3, s.margin + col - 9, hour_y - 3)
        hour_y -= 14

    right = s.margin + col + 18
    pg.box(right, y - 118, col, 118, "Top three")
    ty = y - 26
    for _ in range(3):
        c.setStrokeColor(_c(s.ink))
        c.setLineWidth(0.9)
        c.roundRect(right + 9, ty - 4, 11, 11, 2, stroke=1, fill=0)
        c.setStrokeColor(_c(s.rule))
        c.setLineWidth(0.6)
        c.line(right + 28, ty - 4, right + col - 9, ty - 4)
        ty -= 32

    pg.box(right, y - 250, col, 118, "Notes")
    ny = y - 158
    c.setStrokeColor(_c(s.faint))
    for _ in range(5):
        c.line(right + 9, ny, right + col - 9, ny)
        ny -= 18

    pg.box(s.margin, y - 400, pg.w - 2 * s.margin, 130, "Tomorrow, one line")
    pg.footer()


def weekly_planner(pg: Page, heading: str = "Week At A Glance") -> None:
    c, s = pg.c, pg.st
    y = pg.header(heading, "Week of  ______________")
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    usable = y - s.margin - 40
    row = usable / 7
    for day in days:
        pg.box(s.margin, y - row + 4, pg.w - 2 * s.margin, row - 8, day)
        c.setStrokeColor(_c(s.faint))
        c.setLineWidth(0.5)
        for k in range(1, 3):
            ly = y - row + 4 + (row - 8) * (1 - k / 3)
            c.line(s.margin + 80, ly, pg.w - s.margin - 12, ly)
        y -= row
    pg.footer()


def monthly_calendar(pg: Page, heading: str = "Month") -> None:
    c, s = pg.c, pg.st
    y = pg.header(heading, "Month  ______________     Year  __________")
    cols, rows = 7, 6
    cw = (pg.w - 2 * s.margin) / cols
    ch = (y - s.margin - 30) / rows
    c.setFont(pg.bold, 8.5)
    for i, name in enumerate(["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]):
        c.setFillColor(_c(s.accent))
        c.drawCentredString(s.margin + cw * i + cw / 2, y + 8, name)
    for r in range(rows):
        for col in range(cols):
            x = s.margin + col * cw
            top = y - r * ch
            c.setStrokeColor(_c(s.faint))
            c.setLineWidth(0.7)
            c.rect(x, top - ch, cw, ch, stroke=1, fill=0)
    pg.footer()


def habit_tracker(pg: Page, heading: str = "Habit Tracker") -> None:
    c, s = pg.c, pg.st
    y = pg.header(heading, "Month  ______________")
    label_w = 150
    days = 31
    grid_w = pg.w - 2 * s.margin - label_w
    cw = grid_w / days
    rows = 12
    rh = min(24, (y - s.margin - 30) / rows)
    c.setFont(pg.body, 5.6)
    for d in range(days):
        c.setFillColor(_c(s.rule))
        c.drawCentredString(s.margin + label_w + cw * d + cw / 2, y + 7, str(d + 1))
    for r in range(rows):
        top = y - r * rh
        c.setStrokeColor(_c(s.rule))
        c.setLineWidth(0.6)
        c.line(s.margin, top - rh + 4, s.margin + label_w - 8, top - rh + 4)
        for d in range(days):
            c.setStrokeColor(_c(s.faint))
            c.setLineWidth(0.4)
            c.rect(s.margin + label_w + cw * d, top - rh, cw, rh, stroke=1, fill=0)
    pg.footer()


def checklist(pg: Page, heading: str, items: list[str] | None = None,
              subtitle: str = "") -> None:
    c, s = pg.c, pg.st
    y = pg.header(heading, subtitle)
    if items:
        for item in items[:26]:
            c.setStrokeColor(_c(s.ink))
            c.setLineWidth(0.9)
            c.roundRect(s.margin, y - 3, 11, 11, 2, stroke=1, fill=0)
            c.setFillColor(_c(s.ink))
            c.setFont(pg.body, 10.5)
            c.drawString(s.margin + 22, y, item[:88])
            c.setStrokeColor(_c(s.faint))
            c.setLineWidth(0.5)
            c.line(s.margin + 22, y - 6, pg.w - s.margin, y - 6)
            y -= 26
    else:
        pg.checkboxes(y, 24)
    pg.footer()


def notes_page(pg: Page, heading: str = "Notes", ruling: str = "lined") -> None:
    c, s = pg.c, pg.st
    y = pg.header(heading)
    if ruling == "dotted":
        c.setFillColor(_c(s.rule))
        step = 5 * mm
        yy = y
        while yy > s.margin + 10:
            xx = s.margin
            while xx < pg.w - s.margin:
                c.circle(xx, yy, 0.5, stroke=0, fill=1)
                xx += step
            yy -= step
    elif ruling == "grid":
        c.setStrokeColor(_c(s.faint))
        c.setLineWidth(0.4)
        step = 5 * mm
        yy = y
        while yy > s.margin + 10:
            c.line(s.margin, yy, pg.w - s.margin, yy)
            yy -= step
        xx = s.margin
        while xx < pg.w - s.margin:
            c.line(xx, y, xx, s.margin + 10)
            xx += step
    else:
        pg.lines(y, int((y - s.margin) / 24), 24)
    pg.footer()


def worksheet(pg: Page, heading: str, prompts: list[str], subtitle: str = "") -> None:
    c, s = pg.c, pg.st
    y = pg.header(heading, subtitle)
    per = max(2, int((y - s.margin) / max(len(prompts), 1) / 24))
    for prompt in prompts[:10]:
        c.setFillColor(_c(s.ink))
        c.setFont(pg.bold, 10.5)
        for line in _wrap_text(prompt, pg.w - 2 * s.margin, 10.5, pg.bold):
            c.drawString(s.margin, y, line)
            y -= 15
        y -= 6
        y = pg.lines(y, per, 22)
        y -= 12
        if y < s.margin + 60:
            break
    pg.footer()


def cover_page(pg: Page, title: str, subtitle: str, brand: str) -> None:
    c, s = pg.c, pg.st
    c.setFillColor(_c(s.accent))
    c.rect(0, pg.h - 8, pg.w, 8, stroke=0, fill=1)
    c.setFillColor(_c(s.ink))
    c.setFont(pg.bold, 34)
    y = pg.h * 0.58
    for line in _wrap_text(title, pg.w - 2.4 * s.margin, 34, pg.bold):
        c.drawCentredString(pg.w / 2, y, line)
        y -= 40
    if subtitle:
        c.setFont(pg.body, 13)
        c.setFillColor(_c(s.rule))
        for line in _wrap_text(subtitle, pg.w - 3 * s.margin, 13, pg.body):
            c.drawCentredString(pg.w / 2, y - 6, line)
            y -= 18
    c.setFillColor(_c(s.accent))
    c.setFont(pg.bold, 10)
    c.drawCentredString(pg.w / 2, s.margin + 24, brand.upper())


def _wrap_text(text: str, width: float, size: float, font: str) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if pdfmetrics.stringWidth(trial, font, size) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


BUILDERS = {
    "cover": None,
    "daily": daily_planner,
    "weekly": weekly_planner,
    "monthly": monthly_calendar,
    "habits": habit_tracker,
    "checklist": checklist,
    "notes": notes_page,
    "worksheet": worksheet,
}


def build_pdf(out: Path, pages: list[dict], style: Style, title: str = "",
              subtitle: str = "") -> Path:
    """pages: [{type, heading, subtitle?, items?, prompts?, ruling?}]"""
    out.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(out), pagesize=style.size)
    c.setTitle(title or "Printable")
    for spec in pages:
        pg = Page(c, style)
        kind = spec.get("type", "notes")
        if kind == "cover":
            cover_page(pg, spec.get("heading", title), spec.get("subtitle", subtitle), style.brand)
        elif kind == "checklist":
            checklist(pg, spec.get("heading", "Checklist"), spec.get("items"), spec.get("subtitle", ""))
        elif kind == "worksheet":
            worksheet(pg, spec.get("heading", "Worksheet"), spec.get("prompts", []),
                      spec.get("subtitle", ""))
        elif kind == "notes":
            notes_page(pg, spec.get("heading", "Notes"), spec.get("ruling", "lined"))
        else:
            fn = BUILDERS.get(kind)
            if fn is None:
                notes_page(pg, spec.get("heading", "Notes"))
            else:
                fn(pg, spec.get("heading", kind.title()))
        c.showPage()
    c.save()
    return out
