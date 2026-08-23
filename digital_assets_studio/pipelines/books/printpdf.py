"""Print-ready interior PDF.

Defaults follow KDP paperback rules: a 6x9in trim, mirrored inside/outside
margins with a gutter that grows with page count, chapters opening on a
right-hand page, and running heads that stay off chapter-opening and blank
pages. Change TRIMS or the margin table if you print elsewhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, NextPageTemplate,
                                PageBreak, PageTemplate, Paragraph, Spacer)

TRIMS: dict[str, tuple[float, float]] = {
    "5 x 8 in": (5.0 * inch, 8.0 * inch),
    "5.25 x 8 in": (5.25 * inch, 8.0 * inch),
    "5.5 x 8.5 in": (5.5 * inch, 8.5 * inch),
    "6 x 9 in": (6.0 * inch, 9.0 * inch),
    "7 x 10 in": (7.0 * inch, 10.0 * inch),
    "8.5 x 11 in": (8.5 * inch, 11.0 * inch),
}


def gutter_for(page_count: int) -> float:
    """KDP's minimum inside margin, by page count."""
    if page_count <= 150:
        return 0.375 * inch
    if page_count <= 300:
        return 0.5 * inch
    if page_count <= 500:
        return 0.625 * inch
    if page_count <= 700:
        return 0.75 * inch
    return 0.875 * inch


@dataclass
class PrintSpec:
    title: str
    author: str
    trim: str = "6 x 9 in"
    body_font: str = "Times-Roman"
    body_size: float = 11.0
    leading: float = 15.5
    outside_margin: float = 0.6 * inch
    top_margin: float = 0.75 * inch
    bottom_margin: float = 0.75 * inch
    estimated_pages: int = 200
    justify: bool = True


class _Doc(BaseDocTemplate):
    def __init__(self, path: str, spec: PrintSpec, **kw):
        self.spec = spec
        w, h = TRIMS.get(spec.trim, TRIMS["6 x 9 in"])
        super().__init__(path, pagesize=(w, h),
                         title=spec.title, author=spec.author, **kw)
        gutter = gutter_for(spec.estimated_pages)
        fw = w - gutter - spec.outside_margin
        fh = h - spec.top_margin - spec.bottom_margin
        recto = Frame(gutter, spec.bottom_margin, fw, fh, id="recto",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        verso = Frame(spec.outside_margin, spec.bottom_margin, fw, fh, id="verso",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates([
            PageTemplate(id="front", frames=[recto], onPage=self._blank_furniture),
            PageTemplate(id="body_recto", frames=[recto], onPage=self._furniture),
            PageTemplate(id="body_verso", frames=[verso], onPage=self._furniture),
        ])
        self._chapter_open_pages: set[int] = set()

    # -- page furniture -----------------------------------------------------
    def _blank_furniture(self, canvas, doc) -> None:
        canvas.saveState()
        canvas.restoreState()

    def _furniture(self, canvas, doc) -> None:
        canvas.saveState()
        w, h = self.pagesize
        s = self.spec
        page = doc.page
        if page not in self._chapter_open_pages:
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#555555"))
            head = s.author if page % 2 == 0 else s.title
            x = w / 2
            canvas.drawCentredString(x, h - s.top_margin + 20, head.upper()[:60])
        canvas.setFont("Times-Roman", 9.5)
        canvas.setFillColor(colors.black)
        canvas.drawCentredString(w / 2, s.bottom_margin - 24, str(page))
        canvas.restoreState()

    def afterFlowable(self, flowable) -> None:
        if getattr(flowable, "_chapter_open", False):
            self._chapter_open_pages.add(self.page)


def _styles(spec: PrintSpec) -> dict[str, ParagraphStyle]:
    align = TA_JUSTIFY if spec.justify else TA_LEFT
    body = ParagraphStyle("body", fontName=spec.body_font, fontSize=spec.body_size,
                          leading=spec.leading, alignment=align, firstLineIndent=spec.body_size * 1.4,
                          spaceAfter=0, hyphenationLang="en_US")
    return {
        "body": body,
        "body_first": ParagraphStyle("body_first", parent=body, firstLineIndent=0),
        "h1": ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=spec.body_size * 1.9,
                             leading=spec.body_size * 2.2, alignment=TA_CENTER,
                             spaceBefore=spec.body_size * 5, spaceAfter=spec.body_size * 2.4),
        "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=spec.body_size * 1.25,
                             leading=spec.body_size * 1.5, alignment=TA_LEFT,
                             spaceBefore=spec.body_size * 1.6, spaceAfter=spec.body_size * 0.6),
        "quote": ParagraphStyle("quote", parent=body, leftIndent=22, rightIndent=22,
                                firstLineIndent=0, fontName="Times-Italic",
                                spaceBefore=8, spaceAfter=8),
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=20, firstLineIndent=0,
                                 bulletIndent=8, spaceAfter=3),
        "center": ParagraphStyle("center", parent=body, alignment=TA_CENTER, firstLineIndent=0),
    }


_INLINE = [
    (re.compile(r"\*\*\*(.+?)\*\*\*", re.S), r"<b><i>\1</i></b>"),
    (re.compile(r"\*\*(.+?)\*\*", re.S), r"<b>\1</b>"),
    (re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", re.S), r"<i>\1</i>"),
    (re.compile(r"`(.+?)`", re.S), r"<font face='Courier'>\1</font>"),
    (re.compile(r"\[(.+?)\]\((.+?)\)"), r"\1"),
]


def _inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for pattern, repl in _INLINE:
        text = pattern.sub(repl, text)
    text = text.replace("---", "—").replace("--", "–")
    return text


def markdown_to_flowables(text: str, st: dict[str, ParagraphStyle], chapter_title: str | None = None) -> list:
    flow: list = []
    first_para = True
    if chapter_title:
        p = Paragraph(_inline(chapter_title), st["h1"])
        p._chapter_open = True
        flow.append(p)
    lines = text.replace("\r\n", "\n").split("\n")
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer, first_para
        if not buffer:
            return
        joined = " ".join(x.strip() for x in buffer).strip()
        buffer = []
        if not joined:
            return
        style = st["body_first"] if first_para else st["body"]
        flow.append(Paragraph(_inline(joined), style))
        first_para = False

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("#"):
            flush()
            level = len(line) - len(line.lstrip("#"))
            heading = line.lstrip("#").strip()
            if level <= 1 and chapter_title:
                pass  # already rendered as the chapter opener
            elif level <= 2 and chapter_title and heading.lower() == chapter_title.lower():
                pass
            else:
                flow.append(Paragraph(_inline(heading), st["h2"] if level >= 2 else st["h1"]))
                first_para = True
        elif line.startswith(">"):
            flush()
            flow.append(Paragraph(_inline(line.lstrip("> ").strip()), st["quote"]))
            first_para = True
        elif re.match(r"^([-*+]|\d+[.)])\s+", line):
            flush()
            items = []
            while i < len(lines) and re.match(r"^([-*+]|\d+[.)])\s+", lines[i].strip()):
                items.append(re.sub(r"^([-*+]|\d+[.)])\s+", "", lines[i].strip()))
                i += 1
            for item in items:
                flow.append(Paragraph(_inline(item), st["bullet"], bulletText="•"))
            first_para = True
            continue
        elif set(line) <= {"-", "*", "_"} and len(line) >= 3:
            flush()
            flow.append(Spacer(1, 10))
            flow.append(Paragraph("* * *", st["center"]))
            flow.append(Spacer(1, 10))
            first_para = True
        else:
            buffer.append(line)
        i += 1
    flush()
    return flow


def build_pdf(spec: PrintSpec, sections: list[tuple[str, str]], out_path: Path) -> Path:
    """sections: list of (chapter_title_or_empty, markdown_body)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    st = _styles(spec)
    doc = _Doc(str(out_path), spec)
    story: list = [NextPageTemplate(["body_verso", "body_recto"])]
    for idx, (title, body) in enumerate(sections):
        if idx:
            story.append(PageBreak())
        story.extend(markdown_to_flowables(body, st, chapter_title=title or None))
    doc.multiBuild(story)
    return out_path


def page_estimate(word_count: int, spec: PrintSpec, chapters: int = 0,
                  front_back_pages: int = 10) -> int:
    """Rough but useful.

    Words-per-page scales with trim area and inversely with type size. Then add
    the pages chapter openers waste: a chapter start burns roughly half a page
    of white space, plus the blank verso when it is forced onto a recto.
    """
    w, h = TRIMS.get(spec.trim, TRIMS["6 x 9 in"])
    area = (w / inch) * (h / inch)
    per_page = area * 4.6 * (11.0 / max(spec.body_size, 7.0))
    text_pages = word_count / max(per_page, 110)
    return max(24, int(round(text_pages + chapters * 1.4 + front_back_pages)))
