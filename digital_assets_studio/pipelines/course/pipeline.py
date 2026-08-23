"""Online course pipeline: curriculum to a sellable, watchable course."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ...config import ASSETS_DIR, ROLE_MARKETING, ROLE_PLANNING, ROLE_SCRIPT
from ...core.jobs import JobContext
from ...core.llm import router
from ...core.pipeline import (AUTO, EXTERNAL, MANUAL, REVIEW, Field_, Link, Pipeline, Step,
                              StepResult)
from ...core.projects import Project
from ...core.publishing import tts, video
from ...core.settings import load as load_settings
from ..youtube.art import BrandSpec
from .slides import deck_pdf, render_lesson, workbook_pdf

FONT_FILE = ASSETS_DIR / "fonts" / "Poppins-Medium.ttf"

NOTE = """You design paid online courses that people finish. Rules: every lesson
changes what the student can do, not what they know about. No lesson exists to pad
the runtime. Never promise income, and never claim a credential the course does not
confer."""


def _json_file(p: Project, name: str) -> dict:
    raw = p.read_text(f"drafts/{name}", "")
    return json.loads(raw) if raw else {}


def _brand(p: Project) -> BrandSpec:
    light = p.answer("theme", "Light") == "Light"
    return BrandSpec(
        name=p.answer("title") or p.name,
        accent=p.answer("accent", "#5B5BD6"),
        bg="#FFFFFF" if light else "#0E1117",
        bg2="#EEF1F8" if light else "#1B2A4A",
        ink="#101828" if light else "#F4F6FA",
        muted="#586274" if light else "#9AA6B8")


def _step_curriculum(p: Project, ctx: JobContext) -> StepResult:
    ctx.progress(0.3, "Designing the curriculum")
    prompt = f"""Design a course curriculum.

Subject: {p.answer('subject')}
Student: {p.answer('student')}
Where they start: {p.answer('starting_point')}
What they should be able to do at the end: {p.answer('outcome')}
Length: {p.answer('length')}
Price: {p.answer('price')}

Return JSON:
  title            - the course title, searchable rather than clever
  promise          - one sentence, an outcome not a topic
  prerequisites    - array, or empty
  modules          - array of {{title, why_it_matters, lessons: [
                       {{title, outcome, slides: [{{heading, bullets: [3-5 short strings]}}],
                         script, exercise_prompts: [2-3 strings], minutes}}
                     ]}}
                     Aim for the requested length. Each lesson: 3 to 6 slides, and a
                     'script' that is the spoken narration for the whole lesson, written
                     to be read aloud and matching the slides in order.
  not_covered      - array of 3 things this course deliberately does not cover
  who_should_skip  - one sentence
"""
    data = router.text_json(ROLE_PLANNING, prompt, NOTE, max_tokens=16000)
    p.write_text("drafts/curriculum.json", json.dumps(data, indent=2))
    lessons = [l for m in data.get("modules", []) for l in m.get("lessons", [])]
    md = [f"# {data.get('title','')}", "", data.get("promise", ""), ""]
    if data.get("prerequisites"):
        md += ["**Prerequisites** — " + "; ".join(data["prerequisites"]), ""]
    for mi, m in enumerate(data.get("modules", []), 1):
        md += [f"## Module {mi}: {m.get('title','')}", "", f"_{m.get('why_it_matters','')}_", ""]
        for li, l in enumerate(m.get("lessons", []), 1):
            md += [f"{mi}.{li} **{l.get('title','')}** — {l.get('outcome','')} "
                   f"({l.get('minutes', '?')} min)"]
        md.append("")
    md += ["## Deliberately not covered", ""] + [f"- {x}" for x in data.get("not_covered", [])]
    md += ["", f"**Who should skip this** — {data.get('who_should_skip','')}"]
    p.write_text("drafts/curriculum.md", "\n".join(md))
    minutes = sum(int(l.get("minutes", 0) or 0) for l in lessons)
    return StepResult(f"{len(data.get('modules', []))} modules, {len(lessons)} lessons, ~{minutes} minutes",
                      ["drafts/curriculum.md"],
                      {"lesson_total": len(lessons), "course_title": data.get("title", p.name)})


def _lessons(p: Project) -> list[dict]:
    data = _json_file(p, "curriculum.json")
    return [l for m in data.get("modules", []) for l in m.get("lessons", [])]


def _step_slides(p: Project, ctx: JobContext) -> StepResult:
    lessons = _lessons(p)
    if not lessons:
        raise RuntimeError("Design the curriculum first.")
    spec = _brand(p)
    all_images: list[Path] = []
    for i, lesson in enumerate(lessons, start=1):
        ctx.check()
        ctx.progress(i / len(lessons), f"Rendering slides for lesson {i}")
        all_images += render_lesson(spec, lesson, p.dir / "build" / "slides", i)
    deck_pdf(all_images, p.build / "slides.pdf")
    return StepResult(f"{len(all_images)} slides rendered and bound into slides.pdf",
                      ["build/slides.pdf"], {"slide_total": len(all_images)})


def _step_workbook(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    data = _json_file(p, "curriculum.json")
    lessons = _lessons(p)
    ctx.progress(0.5, "Building the workbook")
    workbook_pdf(p.build / "workbook.pdf", data.get("title", p.name), lessons,
                 p.answer("brand") or s.imprint or "")
    return StepResult(f"Workbook built — one exercise page per lesson ({len(lessons)})",
                      ["build/workbook.pdf"])


def _step_narrate(p: Project, ctx: JobContext) -> StepResult:
    lessons = _lessons(p)
    engine = tts.ENGINES.get(p.answer("tts_engine", "edge-tts (free)"), "edge")
    label = p.answer("voice", "English (US, female)")
    voice = tts.EDGE_VOICES.get(label, label)
    if engine == "openai":
        voice = p.answer("openai_voice", "nova")
    made = []
    for i, lesson in enumerate(lessons, start=1):
        ctx.check()
        script = (lesson.get("script") or "").strip()
        if not script:
            continue
        ctx.progress(i / len(lessons), f"Narrating lesson {i} of {len(lessons)}")
        # one clip per slide so the picture changes on the right sentence
        chunks = [c for c in _split_for_slides(script, len(lesson.get("slides", [])) + 1)
                  if c and c.strip()]
        if not chunks:
            continue
        clips = tts.synthesize_scenes(chunks, p.dir / "build" / "audio" / f"{i:02d}",
                                      engine, voice)
        made.append(f"build/audio/{i:02d}")
        tts.write_srt(clips, p.dir / f"build/audio/{i:02d}.srt")
    return StepResult(f"{len(made)} lessons narrated", made[:5])


def _split_for_slides(script: str, parts: int) -> list[str]:
    """Cut the narration into roughly equal pieces on paragraph boundaries."""
    paras = [x.strip() for x in script.split("\n\n") if x.strip()] or [script]
    if parts <= 1 or len(paras) <= parts:
        return paras
    per = len(paras) / parts
    out, i = [], 0.0
    for k in range(parts):
        chunk = paras[int(i):int(i + per) or int(i) + 1]
        out.append(" ".join(chunk))
        i += per
    return [c for c in out if c.strip()]


def _step_video(p: Project, ctx: JobContext) -> StepResult:
    if not video.available():
        raise RuntimeError("ffmpeg is not installed or not on PATH.\n"
                           "  Windows:  winget install Gyan.FFmpeg\n"
                           "  macOS:    brew install ffmpeg")
    lessons = _lessons(p)
    made = []
    for i, _ in enumerate(lessons, start=1):
        ctx.check()
        audio_dir = p.dir / "build" / "audio" / f"{i:02d}"
        clips = sorted(audio_dir.glob("scene_*.mp3"))
        slides = sorted((p.dir / "build" / "slides").glob(f"{i:02d}_*.png"))
        if not clips or not slides:
            ctx.log(f"Lesson {i}: nothing to render yet", "warning")
            continue
        ctx.progress(i / len(lessons), f"Rendering lesson {i} video")
        scenes = [video.Scene(slides[min(k, len(slides) - 1)], c, tts.ffprobe_duration(c))
                  for k, c in enumerate(clips)]
        out = p.build / "lessons" / f"lesson_{i:02d}.mp4"
        video.render(scenes, out, font=FONT_FILE, zoom=False)
        made.append(p.rel(out))
    if not made:
        raise RuntimeError("No lesson videos were produced. Narrate the lessons first.")
    return StepResult(f"{len(made)} lesson videos rendered", made[:6])


def _step_sales(p: Project, ctx: JobContext) -> StepResult:
    data = _json_file(p, "curriculum.json")
    ctx.progress(0.4, "Writing the sales page")
    prompt = f"""Write the sales page and store metadata for this course.

Title: {data.get('title','')}
Promise: {data.get('promise','')}
Modules: {json.dumps([m.get('title') for m in data.get('modules', [])])}
Student: {p.answer('student')}
Price: {p.answer('price')}

Return JSON:
  headline          - the top of the sales page, an outcome, under 70 characters
  subhead           - one sentence
  who_for           - array of 4 bullet lines
  who_not_for       - array of 2 bullet lines
  curriculum_blurb  - 120 words on what they will build or be able to do
  objections        - array of 4 {{objection, answer}}
  guarantee         - a refund policy you can actually honour, 2 sentences
  udemy_title       - max 60 characters
  udemy_subtitle    - max 120 characters
  udemy_description - HTML using only <p>, <ul>, <li>, <strong>
  gumroad_summary   - under 200 characters
  email_sequence    - array of 3 {{subject, body}} launch emails
"""
    out = router.text_json(ROLE_MARKETING, prompt, NOTE, max_tokens=6000)
    p.write_text("drafts/sales.json", json.dumps(out, indent=2))
    md = [f"# {out.get('headline','')}", "", out.get("subhead", ""), "", "## Who it is for", ""]
    md += [f"- {x}" for x in out.get("who_for", [])]
    md += ["", "## Who it is not for", ""] + [f"- {x}" for x in out.get("who_not_for", [])]
    md += ["", "## What you will be able to do", "", out.get("curriculum_blurb", ""), "",
           "## Objections", ""]
    for o in out.get("objections", []):
        md += [f"**{o.get('objection','')}**", "", o.get("answer", ""), ""]
    md += ["## Guarantee", "", out.get("guarantee", ""), "", "## Udemy", "",
           f"**Title** — {out.get('udemy_title','')}", "",
           f"**Subtitle** — {out.get('udemy_subtitle','')}", "",
           "```html", out.get("udemy_description", ""), "```", "",
           "## Launch emails", ""]
    for e in out.get("email_sequence", []):
        md += [f"### {e.get('subject','')}", "", e.get("body", ""), ""]
    p.write_text("drafts/sales.md", "\n".join(md))
    return StepResult("Sales page, store copy and a 3-email launch sequence written",
                      ["drafts/sales.md"])


def _step_pack(p: Project, ctx: JobContext) -> StepResult:
    out = p.build / "course_pack.zip"
    include = [p.build / "slides.pdf", p.build / "workbook.pdf",
               p.dir / "drafts" / "curriculum.md", p.dir / "drafts" / "sales.md"]
    videos = sorted((p.build / "lessons").glob("*.mp4")) if (p.build / "lessons").exists() else []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in include:
            if f.exists():
                z.write(f, arcname=f.name)
        for v in videos:
            z.write(v, arcname=f"lessons/{v.name}")
    mb = out.stat().st_size / 1_048_576
    return StepResult(f"course_pack.zip — {len(videos)} lesson video(s), {mb:.0f} MB",
                      ["build/course_pack.zip"])


COURSE_PIPELINE = Pipeline(
    id="course",
    title="Online course",
    subtitle="Udemy, Teachable, Gumroad",
    description=("Curriculum, slides, narrated lesson videos, a printable workbook and a "
                 "sales page — the whole product, not just the outline."),
    icon="school_rounded",
    accent="apps",
    intake=[
        Field_("subject", "What does it teach", "multiline", required=True),
        Field_("student", "Who is the student", "multiline", required=True),
        Field_("starting_point", "What do they already know", "multiline"),
        Field_("outcome", "What can they do at the end", "multiline", required=True),
        Field_("length", "Length", "select",
               options=["Short (under 1 hour)", "Standard (2-3 hours)", "Deep (5-8 hours)"],
               default="Standard (2-3 hours)"),
        Field_("price", "Price", "select",
               options=["Free lead magnet", "$19-49", "$49-99", "$99-299", "$299+"],
               default="$49-99"),
        Field_("theme", "Slide theme", "select", options=["Light", "Dark"], default="Light"),
        Field_("accent", "Accent colour", default="#5B5BD6"),
    ],
    steps=[
        Step("curriculum", "Design the curriculum", "Design", AUTO, run=_step_curriculum,
             summary="Modules, lessons, slides and the spoken script for each one.",
             produces=["drafts/curriculum.md"], run_label="Design curriculum",
             cost_hint="one large planning call"),
        Step("review_curriculum", "Check the curriculum", "Design", MANUAL, requires=["curriculum"],
             gate=REVIEW,
             summary="Cut lessons now, before they become slides, audio and video.",
             instructions=("Open `drafts/curriculum.md`.\n\n"
                           "- Every lesson should change what the student can **do**. If a lesson "
                           "only informs, merge or cut it.\n"
                           "- Check the 'deliberately not covered' list — that section is what stops "
                           "refund requests from people who bought the wrong thing.\n"
                           "- Edit `drafts/curriculum.json` directly to reorder or rewrite."),
             checklist=["Every lesson has an action outcome", "Nothing padding the runtime",
                        "Scope boundaries stated"]),
        Step("slides", "Render the slides", "Produce", AUTO, run=_step_slides,
             requires=["review_curriculum"],
             summary="16:9 slides as images, bound into a downloadable PDF deck.",
             produces=["build/slides.pdf"], run_label="Render slides"),
        Step("workbook", "Build the workbook", "Produce", AUTO, run=_step_workbook,
             requires=["review_curriculum"],
             summary="A printable exercise page per lesson. The part students actually keep.",
             produces=["build/workbook.pdf"], run_label="Build workbook"),
        Step("narrate", "Narrate the lessons", "Produce", AUTO, run=_step_narrate,
             requires=["review_curriculum"],
             summary="One audio clip per slide, so the picture turns on the right sentence.",
             fields=[Field_("tts_engine", "Voice engine", "select",
                            options=list(tts.ENGINES.keys()), default="edge-tts (free)"),
                     Field_("voice", "Voice", "select", options=list(tts.EDGE_VOICES.keys()),
                            default="English (US, female)")],
             produces=["build/audio"], run_label="Narrate lessons"),
        Step("video", "Render lesson videos", "Produce", AUTO, run=_step_video,
             requires=["slides", "narrate"],
             summary="Slides plus narration, cut to the audio, as one MP4 per lesson.",
             produces=["build/lessons"], run_label="Render videos",
             cost_hint="CPU-bound, roughly real time"),
        Step("sales", "Write the sales page", "Sell", AUTO, run=_step_sales,
             requires=["curriculum"],
             summary="Headline, objections, guarantee, Udemy fields and three launch emails.",
             produces=["drafts/sales.md"], run_label="Write sales page"),
        Step("pack", "Package the course", "Sell", AUTO, run=_step_pack,
             requires=["slides", "workbook", "sales"],
             summary="One zip: deck, workbook, videos and the copy.",
             produces=["build/course_pack.zip"], run_label="Package course"),
        Step("publish", "Put it on a platform", "Sell", MANUAL, requires=["pack"],
             summary="Three routes, and they are not equivalent.",
             instructions=("**Gumroad or Payhip** — upload `build/course_pack.zip`, paste the summary. "
                           "You keep the most and you bring all the traffic. Best if you already have "
                           "an audience.\n\n"
                           "**Teachable / Podia** — a real course player, drip content, completion "
                           "tracking. A monthly fee, and again you bring the traffic.\n\n"
                           "**Udemy** — they have the buyers, and that is the whole trade: their "
                           "discounting is aggressive and your $99 course will sell at their price, "
                           "not yours. Use it for reach, not for margin, and never as your only channel.\n\n"
                           "Upload the lesson MP4s from `build/lessons/` and attach `workbook.pdf` "
                           "and `slides.pdf` as resources — attached resources measurably raise "
                           "completion, and completion is what generates reviews."),
             fields=[Field_("course_url", "Course URL once live")],
             checklist=["Videos uploaded", "Workbook attached as a resource",
                        "Pricing decided per platform", "Refund policy matches the guarantee"],
             links=[Link("Gumroad", "https://gumroad.com/"), Link("Teachable", "https://teachable.com/"),
                    Link("Udemy instructor", "https://www.udemy.com/teaching/")]),
    ],
)
