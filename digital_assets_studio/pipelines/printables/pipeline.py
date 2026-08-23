"""Printables and template packs — the Etsy / Gumroad digital-download product."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ...config import ROLE_MARKETING, ROLE_METADATA, ROLE_PLANNING
from ...core.jobs import JobContext
from ...core.llm import router
from ...core.pipeline import (AUTO, EXTERNAL, MANUAL, REVIEW, Field_, Link, Pipeline, Step,
                              StepResult)
from ...core.projects import Project
from ...core.settings import load as load_settings
from ..books.cover import CoverSpec, render_front, save_jpeg
from .pages import SIZES, Style, build_pdf

PAGE_TYPES = ["cover", "daily", "weekly", "monthly", "habits", "checklist", "worksheet", "notes"]

NOTE = """You design digital download products that sell on Etsy and Gumroad. A pack
succeeds when a buyer can print it and use it the same afternoon. Avoid filler pages,
avoid anything that needs a specific app, and never promise a licence you cannot grant."""


def _json_file(p: Project, name: str) -> dict:
    raw = p.read_text(f"drafts/{name}", "")
    return json.loads(raw) if raw else {}


def _step_plan(p: Project, ctx: JobContext) -> StepResult:
    ctx.progress(0.3, "Designing the pack")
    prompt = f"""Design a printable pack that is worth paying for.

Topic: {p.answer('topic')}
Buyer: {p.answer('buyer')}
Price point: {p.answer('price')}
Style: {p.answer('style')}

Return JSON:
  product_title    - what it is called on the listing, under 60 characters
  promise          - the outcome, in one sentence
  pages            - array of 12 to 24 page objects, each {{
                       "type": one of {PAGE_TYPES},
                       "heading": the printed page title,
                       "subtitle": optional line under it,
                       "items": array of checklist lines, only for type "checklist",
                       "prompts": array of questions, only for type "worksheet",
                       "ruling": "lined" | "dotted" | "grid", only for type "notes"
                     }}
                     The first page must be type "cover". Order them the way a buyer
                     would work through them. Checklist items must be specific to the
                     topic, never generic.
  bonus_ideas      - array of 3 add-ons that would justify a higher price
  what_not_to_add  - array of 2 things buyers in this niche complain about
"""
    data = router.text_json(ROLE_PLANNING, prompt, NOTE, max_tokens=8000)
    pages = data.get("pages", [])
    for pg in pages:
        if pg.get("type") not in PAGE_TYPES:
            pg["type"] = "notes"
    data["pages"] = pages
    p.write_text("drafts/plan.json", json.dumps(data, indent=2))
    md = [f"# {data.get('product_title','')}", "", data.get("promise", ""), "", "## Pages", ""]
    md += [f"{i}. **{pg.get('heading','')}** — `{pg.get('type')}`" for i, pg in enumerate(pages, 1)]
    md += ["", "## Bonus ideas", ""] + [f"- {b}" for b in data.get("bonus_ideas", [])]
    md += ["", "## What buyers complain about", ""] + [f"- {w}" for w in data.get("what_not_to_add", [])]
    p.write_text("drafts/plan.md", "\n".join(md))
    return StepResult(f"{len(pages)} pages planned", ["drafts/plan.md"],
                      {"page_total": len(pages),
                       "product_title": data.get("product_title", p.name)})


def _style(p: Project) -> Style:
    s = load_settings()
    return Style(ink=p.answer("ink", "#1B2430"), accent=p.answer("accent", "#3B5BDB"),
                 rule=p.answer("rule", "#C9D0DA"), faint=p.answer("faint", "#E7EBF1"),
                 brand=p.answer("brand") or s.imprint or "")


def _step_build(p: Project, ctx: JobContext) -> StepResult:
    plan = _json_file(p, "plan.json")
    pages = plan.get("pages", [])
    if not pages:
        raise RuntimeError("Plan the pack first.")
    title = p.answer("product_title") or plan.get("product_title", p.name)
    made = []
    wanted = [s for s in SIZES if p.answer(f"size_{'letter' if 'Letter' in s else 'a4'}", True)]
    if not wanted:
        wanted = list(SIZES)
    for size in wanted:
        ctx.check()
        ctx.progress(len(made) / max(len(wanted), 1), f"Typesetting for {size}")
        st = _style(p)
        st.page = size
        tag = "letter" if "Letter" in size else "a4"
        rel = f"build/{tag}/{p.id}_{tag}.pdf"
        build_pdf(p.dir / rel, pages, st, title, plan.get("promise", ""))
        made.append(rel)
    if p.answer("split_pages", False):
        for size_rel in list(made):
            src = p.dir / size_rel
            ctx.progress(0.8, f"Splitting {src.name} into single pages")
            made.extend(_split(p, src))
    return StepResult(f"{len(pages)} pages built in {len(wanted)} paper size(s)", made)


def _split(p: Project, pdf_path: Path) -> list[str]:
    """Single-page PDFs, which is what Etsy buyers of planner kits expect."""
    try:
        from pypdf import PdfReader, PdfWriter
    except Exception:  # noqa: BLE001
        return []
    out = []
    reader = PdfReader(str(pdf_path))
    folder = pdf_path.parent / "single_pages"
    folder.mkdir(parents=True, exist_ok=True)
    for i, page in enumerate(reader.pages, start=1):
        writer = PdfWriter()
        writer.add_page(page)
        target = folder / f"{i:02d}.pdf"
        with target.open("wb") as fh:
            writer.write(fh)
        out.append(p.rel(target))
    return out


def _step_mockups(p: Project, ctx: JobContext) -> StepResult:
    """Listing images. Etsy ranks on the first one, so it gets the most care."""
    from PIL import Image, ImageDraw, ImageFilter

    plan = _json_file(p, "plan.json")
    title = p.answer("product_title") or plan.get("product_title", p.name)
    st = _style(p)
    pdfs = sorted((p.dir / "build").rglob("*_letter.pdf")) or sorted((p.dir / "build").rglob("*.pdf"))
    if not pdfs:
        raise RuntimeError("Build the PDFs first.")

    shots: list[Image.Image] = []
    try:
        from pdf2image import convert_from_path
        shots = convert_from_path(str(pdfs[0]), dpi=90)[:6]
        ctx.log(f"Rendered {len(shots)} real page previews")
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"Page previews unavailable ({exc}); building a typographic listing image instead",
                "warning")

    made = []
    hero = Image.new("RGB", (2000, 2000), "#F4F5F8")
    d = ImageDraw.Draw(hero)
    d.rectangle([0, 0, 2000, 620], fill=st.accent)
    from ..youtube.art import _font  # shared font loader

    f_title = _font("Poppins-Bold.ttf", 108)
    f_sub = _font("Poppins-Medium.ttf", 46)
    d.text((90, 190), title[:34], font=f_title, fill="#FFFFFF")
    d.text((92, 340), f"{plan.get('page_total', len(plan.get('pages', [])))} printable pages · "
                      f"US Letter + A4 · instant download", font=f_sub, fill="#FFFFFFCC")
    if shots:
        cols, pad = 3, 60
        cell = (2000 - pad * (cols + 1)) // cols
        for i, shot in enumerate(shots[:6]):
            shot.thumbnail((cell, cell * 2))
            x = pad + (i % cols) * (cell + pad)
            y = 700 + (i // cols) * (cell + pad) // 1
            shadow = Image.new("RGBA", hero.size, (0, 0, 0, 0))
            ImageDraw.Draw(shadow).rectangle(
                [x + 8, y + 12, x + shot.width + 8, y + shot.height + 12], fill=(0, 0, 0, 60))
            hero = Image.alpha_composite(hero.convert("RGBA"),
                                         shadow.filter(ImageFilter.GaussianBlur(18))).convert("RGB")
            hero.paste(shot, (x, y))
    save_jpeg(hero, p.build / "listing_01_hero.jpg")
    made.append("build/listing_01_hero.jpg")

    for i, shot in enumerate(shots[:5], start=2):
        canvas = Image.new("RGB", (2000, 2000), "#FFFFFF")
        shot2 = shot.copy()
        shot2.thumbnail((1500, 1700))
        canvas.paste(shot2, ((2000 - shot2.width) // 2, (2000 - shot2.height) // 2))
        save_jpeg(canvas, p.build / f"listing_{i:02d}.jpg")
        made.append(f"build/listing_{i:02d}.jpg")
    return StepResult(f"{len(made)} listing images at 2000×2000", made)


def _step_listing(p: Project, ctx: JobContext) -> StepResult:
    plan = _json_file(p, "plan.json")
    ctx.progress(0.4, "Writing the listing")
    prompt = f"""Write the store listing for this printable pack.

Product: {plan.get('product_title','')}
Promise: {plan.get('promise','')}
Pages: {json.dumps([pg.get('heading') for pg in plan.get('pages', [])])}
Buyer: {p.answer('buyer')}
Price: {p.answer('price')}

Return JSON:
  etsy_title      - max 140 characters, front-loaded with what a buyer searches
  etsy_tags       - exactly 13 tags, each max 20 characters
  etsy_description- the full description: what it is, what is included as a list,
                    how it is delivered, how to print it, the licence, and a
                    no-refunds-on-digital line
  gumroad_summary - under 200 characters
  materials       - array of file formats included
  faq             - array of 4 {{question, answer}} pairs answering the questions that
                    actually generate refund requests
  licence_text    - a personal-use licence in plain English, under 180 words
"""
    data = router.text_json(ROLE_MARKETING, prompt, NOTE, max_tokens=4000)
    p.write_text("drafts/listing.json", json.dumps(data, indent=2))
    md = ["# Store listing", "", f"**Etsy title ({len(data.get('etsy_title',''))}/140)**", "",
          data.get("etsy_title", ""), "", "## Tags", "",
          ", ".join(data.get("etsy_tags", [])), "", "## Description", "",
          data.get("etsy_description", ""), "", "## Gumroad summary", "",
          data.get("gumroad_summary", ""), "", "## FAQ", ""]
    for item in data.get("faq", []):
        md += [f"**{item.get('question','')}**", "", item.get("answer", ""), ""]
    p.write_text("drafts/listing.md", "\n".join(md))
    p.write_text("build/LICENCE.txt", data.get("licence_text", ""))
    tags = data.get("etsy_tags", [])
    warn = "" if len(tags) == 13 else f" (model returned {len(tags)} tags, Etsy allows 13)"
    return StepResult("Listing, tags, FAQ and licence written" + warn,
                      ["drafts/listing.md", "build/LICENCE.txt"])


def _step_pack(p: Project, ctx: JobContext) -> StepResult:
    plan = _json_file(p, "plan.json")
    out = p.build / "download_pack.zip"
    files = [f for f in p.build.rglob("*") if f.is_file()
             and f.suffix.lower() in (".pdf", ".txt") and f.name != "download_pack.zip"]
    if not files:
        raise RuntimeError("Build the PDFs first.")
    readme = (f"{plan.get('product_title', p.name)}\n\n"
              f"{plan.get('promise','')}\n\n"
              "WHAT IS INSIDE\n"
              "  letter/  — US Letter, 8.5 x 11 in\n"
              "  a4/      — A4, 210 x 297 mm\n\n"
              "HOW TO PRINT\n"
              "  Print at 100% or 'actual size'. Do not use 'fit to page' — it shrinks the\n"
              "  margins and the pages stop lining up in a binder.\n\n"
              "LICENCE\n"
              "  See LICENCE.txt. Personal use. Not for resale or redistribution.\n")
    p.write_text("build/README.txt", readme)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=str(f.relative_to(p.build)))
        z.writestr("README.txt", readme)
    mb = out.stat().st_size / 1_048_576
    return StepResult(f"download_pack.zip built — {len(files)} files, {mb:.1f} MB",
                      ["build/download_pack.zip"])


PRINTABLES_PIPELINE = Pipeline(
    id="printables",
    title="Printables & templates",
    subtitle="Etsy, Gumroad, Payhip",
    description=("Planner kits, checklists, workbooks and trackers as real vector PDFs in "
                 "both paper sizes, with listing images, tags, licence and a download pack."),
    icon="grid_on_rounded",
    accent="books",
    intake=[
        Field_("topic", "What is the pack about", "multiline", required=True,
               placeholder="A twelve-month wedding planning binder"),
        Field_("buyer", "Who buys it", "multiline", required=True),
        Field_("price", "Price point", "select",
               options=["$3-5", "$5-10", "$10-20", "$20-50", "$50+"], default="$10-20"),
        Field_("style", "Visual style", "select",
               options=["Clean and minimal", "Warm and editorial", "Bold and colourful",
                        "Classic and formal"], default="Clean and minimal"),
        Field_("brand", "Brand line printed in the footer", placeholder="e.g. Riverbend Press"),
    ],
    steps=[
        Step("plan", "Plan the pack", "Design", AUTO, run=_step_plan,
             summary="What pages, in what order, with the checklist and worksheet content written.",
             produces=["drafts/plan.md"], run_label="Plan the pack"),
        Step("review_plan", "Check the page list", "Design", MANUAL, requires=["plan"], gate=REVIEW,
             summary="Cheap to reorder now, tedious after the PDFs exist.",
             instructions=("Open `drafts/plan.md`.\n\n"
                           "- Cut any page a buyer would skip. A 16-page pack that all gets used "
                           "beats a 40-page pack that does not.\n"
                           "- Checklist items must be specific to the topic. Generic ones read as "
                           "AI filler and turn into refunds.\n"
                           "- Edit `drafts/plan.json` directly if you want to change page types or order."),
             checklist=["No filler pages", "Checklists are specific", "Order matches how it is used"]),
        Step("build", "Build the PDFs", "Design", AUTO, run=_step_build, requires=["review_plan"],
             summary="Vector PDFs at exact trim, in both paper sizes.",
             fields=[
                 Field_("accent", "Accent colour", default="#3B5BDB"),
                 Field_("ink", "Ink colour", default="#1B2430"),
                 Field_("size_letter", "US Letter", "switch", default=True),
                 Field_("size_a4", "A4", "switch", default=True),
                 Field_("split_pages", "Also export single-page PDFs", "switch", default=False,
                        help="Needs pypdf installed. Buyers of planner kits often prefer these."),
             ],
             produces=["build/letter", "build/a4"], run_label="Build PDFs"),
        Step("mockups", "Make the listing images", "Sell", AUTO, run=_step_mockups,
             requires=["build"],
             summary="A hero image plus real page previews, square at 2000×2000.",
             produces=["build/listing_01_hero.jpg"], run_label="Make listing images"),
        Step("listing", "Write the listing", "Sell", AUTO, run=_step_listing, requires=["plan"],
             summary="Etsy title and 13 tags, full description, FAQ, and a personal-use licence.",
             produces=["drafts/listing.md"], run_label="Write listing"),
        Step("pack", "Build the download", "Sell", AUTO, run=_step_pack,
             requires=["build", "listing"],
             summary="One zip with both paper sizes, the licence and printing instructions.",
             produces=["build/download_pack.zip"], run_label="Build download pack"),
        Step("publish", "List it", "Sell", MANUAL, requires=["pack"],
             summary="Everything is in build/. This is upload and paste.",
             instructions=("**Etsy** — Add a listing → Digital. Upload `build/download_pack.zip` "
                           "(Etsy's per-file cap is 20 MB, five files). Title and 13 tags come from "
                           "`drafts/listing.md`. The first listing image decides whether anyone clicks: "
                           "use `build/listing_01_hero.jpg`. Listings cost $0.20 and renew every four months.\n\n"
                           "**Gumroad or Payhip** — same zip, paste the summary, use the hero as the cover. "
                           "Higher margin, no discovery: you bring the traffic.\n\n"
                           "Two things that prevent most refunds: say **instant digital download, no "
                           "physical item shipped** in the first line, and say **print at 100%, not "
                           "fit-to-page** in both the listing and the README."),
             fields=[Field_("etsy_url", "Etsy listing URL"), Field_("gumroad_url", "Gumroad URL")],
             checklist=["Zip uploaded", "13 tags used", "Hero image first",
                        "'Digital download' stated up front", "Print-at-100% note included"],
             links=[Link("Etsy seller", "https://www.etsy.com/sell"), Link("Gumroad", "https://gumroad.com/"),
                    Link("Payhip", "https://payhip.com/")]),
    ],
)
