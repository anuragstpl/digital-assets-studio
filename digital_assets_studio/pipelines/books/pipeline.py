"""The book pipeline: idea to listed product."""
from __future__ import annotations

import json
from datetime import datetime

from ...core.jobs import JobContext
from ...core.llm import router
from ...core.pipeline import (AUTO, EXTERNAL, MANUAL, REVIEW, Field_, Link, Pipeline,
                              Step, StepResult)
from ...core.projects import Project
from ...core.publishing import browser, stockvideo
from ...core.settings import load as load_settings
from . import writing
from .cover import CoverSpec, render_front, render_wrap, save_jpeg, save_pdf, spine_width_in
from .epub import BookMeta, Chapter, build_epub
from .printpdf import TRIMS, PrintSpec, build_pdf, page_estimate
from .storepack import build_pack, markdown_table, pricing_table, recommend_price

CATEGORIES = [
    "Romance / romantasy", "Fantasy", "Cozy fantasy", "LitRPG / progression",
    "Thriller / mystery", "Science fiction", "Business how-to", "AI & technology guide",
    "Self-help", "Parenting", "Health & fitness", "Cookbook", "Children's picture book",
    "Planner / template kit", "Other",
]

FICTION_CATEGORIES = {"Romance / romantasy", "Fantasy", "Cozy fantasy",
                      "LitRPG / progression", "Thriller / mystery", "Science fiction"}


def _is_fiction(project: Project) -> bool:
    return project.answer("category") in FICTION_CATEGORIES


def _json(project: Project, name: str) -> dict:
    raw = project.read_text(f"drafts/{name}", "")
    return json.loads(raw) if raw else {}


# ============================================================== auto steps ==

def _step_concept(p: Project, ctx: JobContext) -> StepResult:
    ctx.progress(0.1, "Developing the concept")
    seed = {k: p.answer(k) for k in ("title", "category", "audience", "angle", "word_target", "tone")}
    concept = writing.develop_concept(seed)
    p.write_text("drafts/concept.json", json.dumps(concept, indent=2))
    lines = [f"# {concept.get('title', '')}", "", f"**Hook** — {concept.get('hook','')}", "",
             f"**Promise** — {concept.get('promise','')}", "",
             f"**Reader** — {concept.get('reader','')}", "",
             f"**Not for** — {concept.get('not_for','')}", "",
             f"**Differentiator** — {concept.get('differentiator','')}", "",
             f"**Voice** — {concept.get('voice','')}", "", "## Comparable titles", ""]
    lines += [f"- {c}" for c in concept.get("comparable_titles", [])]
    lines += ["", "## Risks", ""] + [f"- {r}" for r in concept.get("risks", [])]
    p.write_text("drafts/concept.md", "\n".join(lines))
    return StepResult(f"Concept locked in: “{concept.get('title','')}”",
                      ["drafts/concept.json", "drafts/concept.md"],
                      {"suggested_title": concept.get("title", "")})


def _step_market(p: Project, ctx: JobContext) -> StepResult:
    ctx.progress(0.2, "Running the market check")
    concept = _json(p, "concept.json")
    m = writing.market_check(concept, p.answer("category"))
    p.write_text("drafts/market.json", json.dumps(m, indent=2))
    md = [f"# Market check — {m.get('demand_verdict','?').upper()}", "", m.get("reasoning", ""), "",
          "## What the incumbents already do", "", m.get("competition", ""), "",
          "## The gap", "", m.get("gap") or "_No clear gap found. That is a reason to change the angle._", "",
          "## Price band", "", m.get("price_band", ""), "",
          "## Alternative titles", ""] + [f"- {t}" for t in m.get("title_alternates", [])]
    md += ["", "## Search phrases buyers actually type", ""] + [f"- {k}" for k in m.get("keywords_seed", [])]
    md += ["", "## Warnings", ""] + [f"- {w}" for w in m.get("warnings", [])]
    p.write_text("drafts/market.md", "\n".join(md))
    return StepResult(f"Verdict: {m.get('demand_verdict','?')}", ["drafts/market.md", "drafts/market.json"])


def _step_outline(p: Project, ctx: JobContext) -> StepResult:
    concept = _json(p, "concept.json")
    if p.answer("final_title"):
        concept["title"] = p.answer("final_title")
    target = int(p.answer("word_target", 30000) or 30000)
    chapters = int(p.answer("chapter_count", 0) or max(8, round(target / 2200)))
    ctx.progress(0.15, f"Planning {chapters} chapters for {target:,} words")
    outline = writing.build_outline(concept, target, chapters, _is_fiction(p))
    p.write_text("drafts/outline.json", json.dumps(outline, indent=2))
    md = [f"# Outline — {concept.get('title','')}", "", f"**Premise** — {outline.get('premise','')}", "",
          f"**Structure** — {outline.get('structure','')}", ""]
    for ch in outline.get("chapters", []):
        md += [f"## {ch.get('number')}. {ch.get('title')}  _( {ch.get('word_target', 0):,} words )_", "",
               f"*Purpose:* {ch.get('purpose','')}", ""]
        md += [f"- {b}" for b in ch.get("beats", [])]
        anchor = ch.get("turn") or ch.get("takeaway")
        if anchor:
            md += ["", f"*{'Turn' if ch.get('turn') else 'Takeaway'}:* {anchor}"]
        md.append("")
    p.write_text("drafts/outline.md", "\n".join(md))
    return StepResult(f"{len(outline.get('chapters', []))} chapters planned",
                      ["drafts/outline.md", "drafts/outline.json"],
                      {"chapter_count": len(outline.get("chapters", []))})


def _step_draft(p: Project, ctx: JobContext) -> StepResult:
    concept, outline = _json(p, "concept.json"), _json(p, "outline.json")
    chapters = outline.get("chapters", [])
    if not chapters:
        raise RuntimeError("The outline has no chapters. Re-run the outline step.")
    fiction = _is_fiction(p)
    written, total_words, continuity = [], 0, p.read_text("drafts/continuity.txt", "")
    for i, ch in enumerate(chapters):
        ctx.check()
        num = ch.get("number", i + 1)
        rel = f"drafts/chapters/{int(num):02d}.md"
        if p.exists(rel) and p.answer("resume_draft", True):
            body = p.read_text(rel)
            ctx.progress((i + 1) / len(chapters), f"Chapter {num} already written — keeping it")
        else:
            ctx.progress(i / len(chapters), f"Writing chapter {num}: {ch.get('title','')}")
            body = writing.draft_chapter(concept, outline, ch, continuity, fiction)
            p.write_text(rel, body)
        continuity = writing.summarise_for_continuity(body)
        p.write_text("drafts/continuity.txt", continuity)
        total_words += writing.word_count(body)
        written.append(rel)
    p.write_text("drafts/manuscript.md",
                 "\n\n".join(p.read_text(r) for r in written))
    return StepResult(f"{len(written)} chapters, {total_words:,} words",
                      written + ["drafts/manuscript.md"], {"word_count": total_words})


def _step_edit(p: Project, ctx: JobContext) -> StepResult:
    concept = _json(p, "concept.json")
    voice = str(concept.get("voice", "clear, warm, direct"))
    files = sorted((p.dir / "drafts" / "chapters").glob("*.md")) if (p.dir / "drafts" / "chapters").exists() else []
    if not files:
        raise RuntimeError("No chapters found to edit.")
    total = 0
    for i, f in enumerate(files):
        ctx.check()
        ctx.progress(i / len(files), f"Editing {f.name}")
        edited = writing.line_edit(f.read_text(encoding="utf-8"), voice)
        f.write_text(edited, encoding="utf-8")
        total += writing.word_count(edited)
    p.write_text("drafts/manuscript.md", "\n\n".join(f.read_text(encoding="utf-8") for f in files))
    return StepResult(f"Edited {len(files)} chapters — now {total:,} words",
                      ["drafts/manuscript.md"], {"word_count": total})


def _step_matter(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    concept = _json(p, "concept.json")
    author = p.answer("pen_name") or s.author_name or "Author"
    imprint = p.answer("imprint") or s.imprint or ""
    ctx.progress(0.4, "Writing front matter")
    front = writing.front_matter(concept, author, imprint, datetime.now().year, _is_fiction(p))
    p.write_text("drafts/front_matter.md", front)
    ctx.progress(0.8, "Writing back matter")
    back = writing.back_matter(concept, author, imprint)
    p.write_text("drafts/back_matter.md", back)
    return StepResult("Front and back matter written",
                      ["drafts/front_matter.md", "drafts/back_matter.md"])


def _back_matter_text(p: Project) -> str:
    """Back matter plus, if the cover used a stock photograph, its credit."""
    text = p.read_text("drafts/back_matter.md", "")
    credit = p.answer("cover_credit", "")
    if credit and credit not in text:
        text = f"{text}\n\n## Image credit\n\n{credit}\n".strip()
    return text


def _chapters_for_build(p: Project) -> list[tuple[str, str]]:
    outline = _json(p, "outline.json")
    titles = {int(c.get("number", i + 1)): c.get("title", f"Chapter {i+1}")
              for i, c in enumerate(outline.get("chapters", []))}
    folder = p.dir / "drafts" / "chapters"
    out: list[tuple[str, str]] = []
    for f in sorted(folder.glob("*.md")) if folder.exists() else []:
        try:
            num = int(f.stem)
        except ValueError:
            num = len(out) + 1
        out.append((titles.get(num, f"Chapter {num}"), f.read_text(encoding="utf-8")))
    return out


def _step_epub(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    concept = _json(p, "concept.json")
    market = _json(p, "market.json")
    title = p.answer("final_title") or concept.get("title", p.name)
    author = p.answer("pen_name") or s.author_name or "Author"
    ctx.progress(0.3, "Assembling the EPUB")

    chapters = [Chapter("Title Page",
                        f'<div class="titlepage"></div>\n\n# {title}\n\n**{author}**\n\n'
                        f'{p.answer("imprint") or s.imprint}', in_toc=False)]
    if p.exists("drafts/front_matter.md"):
        chapters.append(Chapter("Front Matter", p.read_text("drafts/front_matter.md"), in_toc=False))
    for t, body in _chapters_for_build(p):
        chapters.append(Chapter(t, body))
    if p.exists("drafts/back_matter.md"):
        chapters.append(Chapter("The End", _back_matter_text(p)))

    cover_bytes = None
    if p.exists("build/cover_front.jpg"):
        cover_bytes = (p.dir / "build" / "cover_front.jpg").read_bytes()

    meta = BookMeta(title=title, author=author, publisher=p.answer("imprint") or s.imprint,
                    description=(_json(p, "listing.json").get("blurb_plain", "") or concept.get("hook", ""))[:900],
                    subjects=market.get("keywords_seed", [])[:7])
    out = build_epub(meta, chapters, p.build / "book.epub", cover_bytes)
    mb = out.stat().st_size / 1_048_576
    return StepResult(f"EPUB built — {mb:.2f} MB, {len(chapters)} sections",
                      ["build/book.epub"], {"epub_mb": round(mb, 3)})


def _step_interior(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    concept = _json(p, "concept.json")
    title = p.answer("final_title") or concept.get("title", p.name)
    author = p.answer("pen_name") or s.author_name or "Author"
    trim = p.answer("trim", "6 x 9 in")
    words = int(p.answer("word_count", 0) or 0)
    sections = _chapters_for_build(p)
    if not sections:
        raise RuntimeError("No chapters found. Run the drafting step first.")
    spec = PrintSpec(title=title, author=author, trim=trim,
                     estimated_pages=page_estimate(words, PrintSpec(title, author, trim=trim), len(sections)))
    ordered: list[tuple[str, str]] = []
    if p.exists("drafts/front_matter.md"):
        ordered.append(("", p.read_text("drafts/front_matter.md")))
    ordered.extend(sections)
    if p.exists("drafts/back_matter.md"):
        ordered.append(("", _back_matter_text(p)))
    ctx.progress(0.5, f"Typesetting {len(ordered)} sections at {trim}")
    out = build_pdf(spec, ordered, p.build / "interior_print.pdf")
    pages = _count_pdf_pages(out)
    return StepResult(f"Interior PDF built — {pages} pages at {trim}",
                      ["build/interior_print.pdf"], {"page_count": pages})


def _count_pdf_pages(path) -> int:
    try:
        data = path.read_bytes()
        n = data.count(b"/Type /Page") - data.count(b"/Type /Pages")
        return max(n, data.count(b"/Type/Page") - data.count(b"/Type/Pages"), 1)
    except Exception:  # noqa: BLE001
        return 0


def _step_cover_brief(p: Project, ctx: JobContext) -> StepResult:
    concept = _json(p, "concept.json")
    ctx.progress(0.4, "Writing the cover brief")
    brief = writing.cover_brief(concept, p.answer("category"))
    p.write_text("drafts/cover_brief.json", json.dumps(brief, indent=2))
    p.write_text("drafts/cover_brief.md",
                 f"# Cover brief\n\n**Idea** — {brief.get('concept_line','')}\n\n"
                 f"**Mood** — {brief.get('mood','')}\n\n**Palette** — {', '.join(brief.get('palette', []))}\n\n"
                 f"## Image prompt\n\n```\n{brief.get('image_prompt','')}\n```\n\n"
                 f"## Negative prompt\n\n```\n{brief.get('negative_prompt','')}\n```\n")
    return StepResult("Cover brief and image prompt ready",
                      ["drafts/cover_brief.md", "drafts/cover_brief.json"])


AI_ART = "AI image model"
STOCK_ART = "Free stock photo (Pexels / Pixabay)"
NO_ART = "None — typographic cover only"
ART_SOURCES = [AI_ART, STOCK_ART, NO_ART]


def _step_cover_art(p: Project, ctx: JobContext) -> StepResult:
    brief = _json(p, "cover_brief.json")
    source = p.answer("art_source", AI_ART)

    if source == NO_ART:
        return StepResult("Skipped by choice — the built-in typographic cover will be used. "
                          "For a lot of categories that is the stronger cover anyway.")

    if source == STOCK_ART:
        terms = p.answer("stock_terms_override")
        terms = [t.strip() for t in str(terms).split(",") if t.strip()] if terms else \
            brief.get("stock_search_terms") or []
        if not terms:
            terms = [w for w in str(brief.get("concept_line", "")).split()[:3]] or ["texture"]
        library = p.answer("stock_source", "pexels")
        try:
            data, photo = stockvideo.best_photo(
                terms, source=library, orientation="portrait", min_width=1400,
                target_ratio=1 / 1.6, progress=lambda f, m: ctx.progress(f, m))
        except Exception as exc:  # noqa: BLE001
            return StepResult(f"No stock photo this time ({exc}). The built-in typographic "
                              f"background will be used instead.")
        rel = "build/cover_art_1.png"
        p.write_bytes(rel, data)
        credit = stockvideo.credit_line(photo)
        p.write_text("build/IMAGE_CREDITS.txt",
                     credit + "\n\nPexels and Pixabay both allow commercial use without "
                     "attribution, so this line is courtesy, not obligation. Neither licence "
                     "lets you sell the image itself — a cover with your title set over it is "
                     "fine, a poster of the bare photograph is not.\n")
        return StepResult(
            f"{photo.width}×{photo.height} photo from {photo.source} — {credit}",
            [rel, "build/IMAGE_CREDITS.txt"],
            {"cover_art": rel, "cover_credit": credit})

    prompt = p.answer("cover_prompt_override") or brief.get("image_prompt", "")
    if not prompt:
        raise RuntimeError("No image prompt available. Run the cover brief step first.")
    ctx.progress(0.3, "Asking the image model for cover art")
    try:
        result = router.image(prompt, count=1, size="1024x1536")
    except Exception as exc:  # noqa: BLE001
        return StepResult(f"No AI art this time ({exc}). The built-in typographic "
                          f"background will be used instead — that is a perfectly good cover.")
    paths = []
    for i, data in enumerate(result.images, start=1):
        rel = f"build/cover_art_{i}.png"
        p.write_bytes(rel, data)
        paths.append(rel)
    return StepResult(f"{len(paths)} background image(s) generated", paths,
                      {"cover_art": paths[0] if paths else ""})


def _step_cover_build(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    concept, brief, listing = _json(p, "concept.json"), _json(p, "cover_brief.json"), _json(p, "listing.json")
    title = p.answer("final_title") or concept.get("title", p.name)
    pages = int(p.answer("page_count", 0) or 120)
    spec = CoverSpec(
        title=title,
        subtitle=p.answer("subtitle") or "",
        author=p.answer("pen_name") or s.author_name or "Author",
        series=p.answer("series") or "",
        imprint=p.answer("imprint") or s.imprint or "",
        palette=brief.get("palette") or ["#101322", "#25305C", "#C9A227", "#F2EFE6"],
        title_color=brief.get("title_color") or "#F7F3EA",
        subtitle_color=brief.get("subtitle_color") or "#D9CFAF",
        author_color=brief.get("author_color") or "#F7F3EA",
        title_case=brief.get("title_case") or "upper",
        blurb=listing.get("blurb_plain", "") or concept.get("hook", ""),
        trim=p.answer("trim", "6 x 9 in"),
        pages=pages,
        paper=p.answer("paper", "white (b&w interior)"),
    )
    art = None
    art_rel = p.answer("cover_art")
    if art_rel and p.exists(art_rel):
        art = (p.dir / art_rel).read_bytes()

    ctx.progress(0.3, "Rendering the e-book cover")
    save_jpeg(render_front(spec, art), p.build / "cover_front.jpg")
    ctx.progress(0.7, "Rendering the paperback wrap")
    wrap = render_wrap(spec, art)
    save_jpeg(wrap, p.build / "cover_wrap.jpg")
    save_pdf(wrap, p.build / "cover_wrap.pdf")
    spine = spine_width_in(pages, spec.paper)
    return StepResult(
        f"Covers rendered. Spine {spine:.3f} in at {pages} pages — re-run this step if the page count changes.",
        ["build/cover_front.jpg", "build/cover_wrap.jpg", "build/cover_wrap.pdf"])


def _step_listing(p: Project, ctx: JobContext) -> StepResult:
    concept, outline = _json(p, "concept.json"), _json(p, "outline.json")
    ctx.progress(0.4, "Writing the store listing")
    listing = writing.store_copy(concept, outline, p.answer("category"))
    p.write_text("drafts/listing.json", json.dumps(listing, indent=2))
    kw = listing.get("seven_keywords", [])
    md = ["# Store listing", "", "## Description (HTML — paste into KDP)", "",
          "```html", listing.get("blurb_html", ""), "```", "",
          "## Description (plain text)", "", listing.get("blurb_plain", ""), "",
          "## Short pitch", "", listing.get("short_pitch", ""), "",
          f"**Hook line** — {listing.get('hook_line','')}", "",
          "## The seven KDP keyword slots", ""]
    md += [f"{i}. {k}" for i, k in enumerate(kw, start=1)]
    md += ["", "## Categories", ""] + [f"- {c}" for c in listing.get("categories", [])]
    md += ["", "## A+ content ideas", ""] + [f"- {a}" for a in listing.get("aplus_ideas", [])]
    p.write_text("drafts/listing.md", "\n".join(md))
    warn = "" if len(kw) == 7 else f"  (model returned {len(kw)} keywords, not 7 — check them)"
    return StepResult("Listing copy, keywords and categories written" + warn,
                      ["drafts/listing.md", "drafts/listing.json"])


def _step_pricing(p: Project, ctx: JobContext) -> StepResult:
    mb = float(p.answer("epub_mb", 1.0) or 1.0)
    pages = int(p.answer("page_count", 120) or 120)
    fiction = _is_fiction(p)
    ebook = float(p.answer("ebook_price", 0) or 0)
    print_price = float(p.answer("print_price", 0) or 0)
    if not ebook or not print_price:
        ebook, print_price, why = recommend_price(mb, fiction, pages)
    else:
        why = "Using the prices you set."
    rows = pricing_table(ebook, print_price, mb, pages)
    md = [f"# Pricing — e-book ${ebook:.2f}, paperback ${print_price:.2f}", "", why, "",
          markdown_table(rows), "",
          "> Rates are the ones published as of July 2026 and they do change. "
          "The per-megabyte delivery fee is what makes big illustrated files earn less at 70% "
          "than at 35% — that comparison is in the table above."]
    p.write_text("drafts/pricing.md", "\n".join(md))
    best = max(rows, key=lambda r: r.net)
    return StepResult(f"Best margin: {best.channel} at ${best.net:.2f} per sale",
                      ["drafts/pricing.md"],
                      {"ebook_price": ebook, "print_price": print_price})


def _step_pack(p: Project, ctx: JobContext) -> StepResult:
    listing = _json(p, "listing.json")
    concept = _json(p, "concept.json")
    title = p.answer("final_title") or concept.get("title", p.name)
    files = [p.build / n for n in ("book.epub", "interior_print.pdf", "cover_front.jpg",
                                   "cover_wrap.pdf", "cover_wrap.jpg")]
    files += [p.dir / "drafts" / n for n in ("listing.md", "pricing.md", "concept.md", "market.md")]
    manifest = {
        "title": title,
        "author": p.answer("pen_name") or load_settings().author_name,
        "imprint": p.answer("imprint") or load_settings().imprint,
        "category": p.answer("category"),
        "keywords": listing.get("seven_keywords", []),
        "categories": listing.get("categories", []),
        "ebook_price": p.answer("ebook_price"),
        "print_price": p.answer("print_price"),
        "page_count": p.answer("page_count"),
        "trim": p.answer("trim"),
        "word_count": p.answer("word_count"),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "ai_assisted": True,
    }
    build_pack(p.dir, p.build / "store_pack.zip", files, manifest)
    included = sum(1 for f in files if f.exists())
    return StepResult(f"store_pack.zip built with {included} files", ["build/store_pack.zip"])



def _step_kdp_prefill(p: Project, ctx: JobContext) -> StepResult:
    """Amazon publishes no write API, so this drives a real, visible browser and
    stops before the store's own publish button."""
    if not browser.available():
        raise RuntimeError(browser.INSTALL_HINT)
    listing = _json(p, "listing.json")
    concept = _json(p, "concept.json")
    s = load_settings()
    author = (p.answer("pen_name") or s.author_name or "").strip()
    first, _, last = author.partition(" ")
    book = {
        "title": (p.answer("final_title") or concept.get("title", p.name)).split(":")[0].strip(),
        "subtitle": p.answer("subtitle") or "",
        "series": p.answer("series") or "",
        "author_first": first, "author_last": last or first,
        "description_plain": listing.get("blurb_plain", ""),
        "keywords": listing.get("seven_keywords", []),
    }
    ctx.progress(0.1, "Opening a browser window - sign in there if Amazon asks")
    res = browser.kdp_prefill(book, {}, p.build, on_status=lambda m: ctx.progress(None, m))
    msg = f"Filled {len(res.filled)} field(s)"
    if res.missed:
        msg += f"; could not find: {', '.join(res.missed)}"
    return StepResult(msg + ". Nothing was submitted - finish in the browser.",
                      ["build/kdp_prefilled.png"] if (p.build / "kdp_prefilled.png").exists() else [])


def _autofill_lock(p: Project) -> dict:
    """What autopilot answers at the 'lock the title' gate: the concept's own
    title, split into title and subtitle."""
    concept = _json(p, "concept.json")
    market = _json(p, "market.json")
    full = (p.answer("final_title") or concept.get("title") or p.name).strip()
    title, _, subtitle = full.partition(":")
    out = {"final_title": title.strip() or p.name}
    if subtitle.strip() and not p.answer("subtitle"):
        out["subtitle"] = subtitle.strip()
    if market.get("demand_verdict") in ("avoid",):
        out["_autopilot_warning"] = ("The market check said AVOID and autopilot went ahead "
                                     "anyway. Read drafts/market.md before you publish.")
    return out


# ============================================================== the pipeline ==

BOOK_PIPELINE = Pipeline(
    id="book",
    title="Book / e-book",
    subtitle="Amazon KDP, Gumroad, Payhip, Etsy",
    description=("Take an idea to a listed product: concept, market check, outline, full "
                 "manuscript, EPUB, print-ready interior, cover, listing copy and a pricing "
                 "table that says which channel actually pays best."),
    icon="menu_book_rounded",
    accent="books",
    intake=[
        Field_("title", "Working title", help="A placeholder is fine — the suite will propose better ones.",
               placeholder="Untitled"),
        Field_("category", "Category", "select", options=CATEGORIES, default="Romance / romantasy", required=True),
        Field_("audience", "Who is it for", "multiline", required=True,
               placeholder="Women 25-45 who read fae romance and want a novella they can finish in a weekend"),
        Field_("angle", "Your angle, if you have one", "multiline",
               help="Leave blank and the suite will propose angles."),
        Field_("word_target", "Target word count", "number", default=30000, required=True),
        Field_("tone", "Tone", "select",
               options=["Warm and plain", "Punchy and direct", "Literary", "Playful",
                        "Authoritative", "Slow burn, atmospheric"], default="Warm and plain"),
        Field_("pen_name", "Author name on the cover",
               help="Your pen name for this line. Falls back to the name in Settings."),
        Field_("imprint", "Imprint", placeholder="e.g. Riverbend Press"),
        Field_("trim", "Paperback trim size", "select", options=list(TRIMS.keys()), default="6 x 9 in"),
    ],
    steps=[
        Step("concept", "Develop the concept", "Concept", AUTO, run=_step_concept,
             summary="Turn the brief into a title, hook, promise, reader and differentiator.",
             produces=["drafts/concept.md"], run_label="Develop concept",
             cost_hint="1 planning call"),
        Step("market", "Market check", "Concept", AUTO, run=_step_market, requires=["concept"],
             summary="A sceptical read on demand, competition, price band and search phrases.",
             produces=["drafts/market.md"], run_label="Run market check",
             cost_hint="1 research call"),
        Step("lock", "Lock the title and price", "Concept", MANUAL, requires=["market"],
             gate=REVIEW, autofill=_autofill_lock,
             summary="Your call. Read the market check, then commit.",
             instructions=(
                 "Open **drafts/market.md** and read the verdict honestly.\n\n"
                 "- If it says **avoid** or **crowded** with no gap, change the angle now. "
                 "Changing it after 30,000 words is expensive.\n"
                 "- Pick the final title. Searchable beats clever: a reader has to be able to "
                 "type it from memory.\n"
                 "- Check the title is not already taken by a big seller in the same category.\n\n"
                 "Fill in the fields below and mark this done."),
             fields=[
                 Field_("final_title", "Final title", required=True),
                 Field_("subtitle", "Subtitle (optional)"),
                 Field_("series", "Series name (optional)"),
                 Field_("chapter_count", "Number of chapters", "number",
                        help="Leave 0 to let the outline decide.", default=0),
             ],
             checklist=["Read the market verdict", "Searched Amazon for the title",
                        "Title works at thumbnail size", "Committed to the angle"],
             links=[Link("Search Amazon Books", "https://www.amazon.com/s?i=digital-text"),
                    Link("KDP category list", "https://kdp.amazon.com/en_US/help/topic/G200652170")]),

        Step("outline", "Build the outline", "Manuscript", AUTO, run=_step_outline, requires=["lock"],
             summary="Chapter map with beats, word targets and a takeaway or turn per chapter.",
             produces=["drafts/outline.md"], run_label="Build outline", cost_hint="1 planning call"),
        Step("review_outline", "Review the outline", "Manuscript", MANUAL, requires=["outline"],
             gate=REVIEW,
             summary="Ten minutes here saves a rewrite later.",
             instructions=("Open **drafts/outline.md**. You are checking three things:\n\n"
                           "1. **Does every chapter change something?** A chapter that only explains "
                           "is a chapter readers skip.\n"
                           "2. **Is the order right?** Move chapters now, not after drafting.\n"
                           "3. **Do the word targets add up** to roughly what you asked for?\n\n"
                           "Edit the file directly if you want — drafting reads it back from disk."),
             checklist=["Every chapter has a turn or takeaway", "Order makes sense",
                        "Word targets add up"]),
        Step("draft", "Write the manuscript", "Manuscript", AUTO, run=_step_draft,
             requires=["review_outline"],
             summary="Chapter by chapter, each one aware of what came before. Resumable.",
             produces=["drafts/manuscript.md"], run_label="Write all chapters",
             cost_hint="one long-form call per chapter — the expensive step"),
        Step("edit", "Line edit", "Manuscript", AUTO, run=_step_edit, requires=["draft"], optional=True,
             summary="A tightening pass over every chapter, preserving voice.",
             run_label="Run line edit", cost_hint="one editing call per chapter"),
        Step("read", "Read it yourself", "Manuscript", MANUAL, requires=["draft"],
             gate=REVIEW,
             summary="Nothing replaces this. Reviews punish books nobody read before publishing.",
             instructions=("Read **drafts/manuscript.md** end to end, out loud where you can.\n\n"
                           "You are hunting for: repeated openings, a chapter that says nothing new, "
                           "names or facts that drift, and any paragraph you skim — because readers will skim it too.\n\n"
                           "Edit the chapter files in **drafts/chapters/** directly. Everything downstream "
                           "rebuilds from those files."),
             checklist=["Read start to finish", "Fixed continuity drift",
                        "Cut at least one section I was fond of"]),
        Step("matter", "Front and back matter", "Manuscript", AUTO, run=_step_matter, requires=["read"],
             summary="Title page, copyright with the AI-assistance line, dedication, review request.",
             run_label="Write front & back matter", cost_hint="2 short calls"),

        Step("listing", "Write the store listing", "Package", AUTO, run=_step_listing, requires=["read"],
             summary="Blurb in KDP-legal HTML, seven keyword slots, categories, A+ ideas.",
             produces=["drafts/listing.md"], run_label="Write listing", cost_hint="1 marketing call"),
        Step("cover_brief", "Cover brief", "Package", AUTO, run=_step_cover_brief, requires=["lock"],
             summary="A design brief plus a ready-to-paste image prompt and palette.",
             run_label="Write cover brief", cost_hint="1 marketing call"),
        Step("cover_art", "Generate cover art", "Package", AUTO, run=_step_cover_art,
             requires=["cover_brief"], optional=True,
             summary="A generated image, a free stock photograph, or neither — the built-in "
                     "typographic cover holds up on its own.",
             fields=[
                 Field_("art_source", "Where the art comes from", "select", options=ART_SOURCES,
                        default=AI_ART,
                        help="Stock photos need a free Pexels or Pixabay key in Settings."),
                 Field_("stock_source", "Stock library", "select",
                        options=stockvideo.SOURCES, default="pexels"),
                 Field_("stock_terms_override", "Search terms", "multiline",
                        help="Comma separated. Blank uses the terms in the cover brief."),
                 Field_("cover_prompt_override", "Override the image prompt", "multiline",
                        help="Leave blank to use the brief's prompt."),
             ],
             instructions=("A stock photograph is free and instant, and it is **not exclusive** — "
                           "another book in your category can be using the same picture tomorrow. "
                           "For a series, or anything you expect to sell for years, that matters; "
                           "for a first novella it usually does not.\n\n"
                           "Two things to check before you use one: avoid photographs of "
                           "recognisable people (neither library guarantees a model release, and a "
                           "face on a cover implies the person endorses the book), and remember the "
                           "licence lets you build the cover but not sell the bare image."),
             run_label="Get cover art", cost_hint="1 image generation, or free from stock"),
        Step("epub", "Build the EPUB", "Package", AUTO, run=_step_epub, requires=["matter"],
             summary="Valid EPUB 3 with an NCX fallback, so Kindle, Kobo and Apple all accept it.",
             produces=["build/book.epub"], run_label="Build EPUB"),
        Step("interior", "Build the print interior", "Package", AUTO, run=_step_interior,
             requires=["matter"],
             summary="Mirrored margins, a gutter sized to the page count, running heads, chapter openers.",
             produces=["build/interior_print.pdf"], run_label="Build interior PDF"),
        Step("cover_build", "Build the covers", "Package", AUTO, run=_step_cover_build,
             requires=["interior", "cover_brief"],
             summary="E-book JPEG plus a full paperback wrap with the spine width computed from the real page count.",
             fields=[Field_("paper", "Paper stock", "select",
                            options=["white (b&w interior)", "cream (b&w interior)", "colour interior"],
                            default="white (b&w interior)")],
             produces=["build/cover_front.jpg", "build/cover_wrap.pdf"], run_label="Render covers"),
        Step("pricing", "Price it", "Package", AUTO, run=_step_pricing, requires=["epub", "interior"],
             summary="Royalty per channel side by side, including the delivery fee that eats illustrated books.",
             fields=[Field_("ebook_price", "E-book price (0 = recommend one)", "number", default=0),
                     Field_("print_price", "Paperback price (0 = recommend one)", "number", default=0)],
             produces=["drafts/pricing.md"], run_label="Build pricing table"),
        Step("pack", "Build the store pack", "Package", AUTO, run=_step_pack,
             requires=["epub", "interior", "cover_build", "listing", "pricing"],
             summary="One zip with every file and value you will be asked for.",
             produces=["build/store_pack.zip"], run_label="Build store pack"),

        Step("kdp_account", "Set up your KDP account", "Publish", MANUAL, optional=True,
             summary="One-time. Skip if you already publish on KDP.",
             instructions=("Create the account, then complete **both** tax and payment sections — "
                           "KDP will not let you publish until they are done, and the tax interview "
                           "can take a day to clear.\n\n"
                           "- Author/publisher name: use the imprint you set, not your legal name, "
                           "unless you want your legal name on the listing.\n"
                           "- Tax interview: have your tax ID ready. Singapore residents complete the "
                           "W-8BEN equivalent inside the interview.\n"
                           "- Payment: a bank account that accepts USD/EUR/GBP transfers."),
             checklist=["Account created", "Tax interview completed", "Bank details added"],
             links=[Link("KDP sign up", "https://kdp.amazon.com/"),
                    Link("KDP tax interview help", "https://kdp.amazon.com/en_US/help/topic/G200641090")]),
        Step("kdp_prefill", "Pre-fill the KDP form", "Publish", AUTO, run=_step_kdp_prefill,
             requires=["pack"], optional=True, needs_attention=True,
             summary="Amazon has no publishing API. This drives a visible browser, types everything "
                     "the suite already knows, and stops before Publish.",
             instructions=("Needs Playwright: `pip install playwright` then `playwright install chromium`.\n\n"
                           "You sign in yourself, including any two-factor prompt — the suite never sees "
                           "or stores your Amazon credentials. It fills the form and hands the window back "
                           "to you."),
             run_label="Open KDP and pre-fill"),
        Step("kdp_publish", "Publish on KDP", "Publish", MANUAL, requires=["pack"],
             summary="Every value you need is in the store pack. This is the paste-and-click step.",
             instructions=(
                 "Open **build/store_pack.zip** and keep **drafts/listing.md** and **drafts/pricing.md** on screen.\n\n"
                 "**Kindle e-book**\n"
                 "1. New Kindle eBook → paste title, subtitle and series from the listing.\n"
                 "2. Description: paste the **HTML** block. KDP accepts only `<p> <b> <i> <br> <ul> <li>`.\n"
                 "3. Keywords: the seven phrases, one per slot. Do not repeat words already in the title.\n"
                 "4. Categories: pick the three from the listing.\n"
                 "5. **AI content disclosure: answer YES for text, and yes for images if you generated the cover art.** "
                 "This is required and it is not a penalty.\n"
                 "6. Upload **build/book.epub** and **build/cover_front.jpg**.\n"
                 "7. Pricing: use the table in drafts/pricing.md. Enrol in KU only if the book will be "
                 "Amazon-exclusive — you cannot also sell it on Gumroad or Payhip while enrolled.\n\n"
                 "**Paperback**\n"
                 "8. Same listing, then upload **build/interior_print.pdf** and **build/cover_wrap.pdf**.\n"
                 "9. Run the online previewer and actually look at every chapter opener.\n"
                 "10. If the previewer reports a different page count than the suite estimated, "
                 "re-run *Build the covers* with the real number — the spine width depends on it."),
             fields=[Field_("kdp_asin", "ASIN once live"),
                     Field_("kdp_notes", "Anything KDP flagged", "multiline")],
             checklist=["E-book submitted", "AI disclosure answered", "Paperback submitted",
                        "Previewer checked page by page", "KU decision made deliberately"],
             links=[Link("KDP Bookshelf", "https://kdp.amazon.com/en_US/bookshelf"),
                    Link("Cover calculator", "https://kdp.amazon.com/en_US/cover-templates")]),
        Step("direct", "List on the direct stores", "Publish", MANUAL, requires=["pack"], optional=True,
             summary="Higher margin per sale, no discovery. Do it only if you are not exclusive to KU.",
             instructions=("Margins from **drafts/pricing.md**: the direct stores keep far more per sale "
                           "than Amazon, but nobody browses them — you have to send the traffic.\n\n"
                           "**Gumroad / Payhip** — upload the EPUB and a PDF, paste the short pitch, use the "
                           "cover JPEG as the product image.\n"
                           "**Etsy** — digital download listing. Etsy buyers expect a PDF, so include one. "
                           "Every listing costs $0.20 to post and renews every four months.\n\n"
                           "If the book is enrolled in Kindle Unlimited, do **not** do this — exclusivity is "
                           "enforced and violations pull the book."),
             checklist=["Not enrolled in KU (or accepted the trade-off)", "Gumroad or Payhip live",
                        "Etsy listing live"],
             links=[Link("Gumroad", "https://gumroad.com/"), Link("Payhip", "https://payhip.com/"),
                    Link("Etsy seller", "https://www.etsy.com/sell")]),
        Step("launch", "Launch week", "Publish", MANUAL, requires=["kdp_publish"], optional=True,
             summary="The two weeks after publishing decide whether the book gets seen at all.",
             instructions=("- Ask ten real people for honest reviews. Never pay for them and never trade "
                           "them — Amazon detects both and removes the book, not just the review.\n"
                           "- Post the hook line and cover wherever you already have an audience.\n"
                           "- Set up an Amazon Ads auto campaign at a small daily budget for two weeks, "
                           "then read the search-term report — the phrases that convert become your next "
                           "book's keywords.\n"
                           "- Put the book's link in the back matter of every other book you sell."),
             checklist=["Review requests sent", "Announced to my audience",
                        "Ads running or deliberately skipped", "Back matter of other books updated"]),
    ],
)
