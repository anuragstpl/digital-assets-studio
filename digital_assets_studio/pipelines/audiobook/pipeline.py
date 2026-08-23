"""Audiobook pipeline: a manuscript to an ACX-compliant, chaptered audiobook."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ...config import PROJECTS_DIR, ROLE_EDITING, ROLE_MARKETING, ROLE_METADATA
from ...core import projects as pj
from ...core.jobs import JobContext
from ...core.llm import router
from ...core.pipeline import (AUTO, EXTERNAL, MANUAL, REVIEW, Field_, Link, Pipeline, Step,
                              StepResult)
from ...core.projects import Project
from ...core.publishing import audio, stockvideo, tts
from ...core.settings import load as load_settings
from ..books.cover import CoverSpec, render_front, save_jpeg

NARRATION_NOTE = """You prepare text for a narrator. Your output is read aloud, so
it must contain nothing that cannot be spoken: no markdown, no bullet glyphs, no
URLs read character by character, no bracketed asides."""


def _json_file(p: Project, name: str) -> dict:
    raw = p.read_text(f"drafts/{name}", "")
    return json.loads(raw) if raw else {}


def _source_chapters(p: Project) -> list[tuple[str, str]]:
    """Chapters from a book project in this workspace, or from a folder of files."""
    src_id = p.answer("source_project")
    if src_id:
        book = pj.load(src_id)
        if book is None:
            raise RuntimeError(f"No project called '{src_id}' in the workspace.")
        outline = json.loads(book.read_text("drafts/outline.json", "{}"))
        titles = {int(c.get("number", i + 1)): c.get("title", f"Chapter {i + 1}")
                  for i, c in enumerate(outline.get("chapters", []))}
        folder = book.dir / "drafts" / "chapters"
        if not folder.exists():
            raise RuntimeError(f"'{book.name}' has no drafted chapters yet.")
        out = []
        for f in sorted(folder.glob("*.md")):
            try:
                num = int(f.stem)
            except ValueError:
                num = len(out) + 1
            out.append((titles.get(num, f"Chapter {num}"), f.read_text(encoding="utf-8")))
        return out

    folder = Path(p.answer("source_folder", "")).expanduser()
    if not folder.is_dir():
        raise RuntimeError("Point this at a book project or a folder of .md/.txt chapter files.")
    out = []
    for f in sorted(folder.iterdir()):
        if f.suffix.lower() in (".md", ".txt"):
            text = f.read_text(encoding="utf-8", errors="replace")
            first = next((l.strip("# ").strip() for l in text.splitlines() if l.strip()), f.stem)
            out.append((first[:80], text))
    if not out:
        raise RuntimeError(f"No .md or .txt files in {folder}")
    return out


def _speakable(text: str) -> str:
    """Strip what a narrator cannot say. Deliberately conservative — the model
    pass afterwards handles judgement calls."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"[*_`>]+", "", text)
    text = re.sub(r"^\s*[-–—•]\s+", "", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _step_gather(p: Project, ctx: JobContext) -> StepResult:
    chapters = _source_chapters(p)
    ctx.progress(0.5, f"Found {len(chapters)} chapters")
    for i, (title, body) in enumerate(chapters, start=1):
        p.write_text(f"drafts/raw/{i:03d}.md", f"# {title}\n\n{body}")
    words = sum(len(re.findall(r"\b\w+\b", b)) for _, b in chapters)
    hours = words / 9300          # ~155 wpm, the usual audiobook pace
    p.write_text("drafts/chapters.json",
                 json.dumps([{"number": i, "title": t} for i, (t, _) in enumerate(chapters, 1)], indent=2))
    return StepResult(f"{len(chapters)} chapters, {words:,} words — about {hours:.1f} finished hours",
                      ["drafts/chapters.json"],
                      {"chapter_total": len(chapters), "word_count": words,
                       "runtime_hours": round(hours, 2)})


def _step_prepare(p: Project, ctx: JobContext) -> StepResult:
    files = sorted((p.dir / "drafts" / "raw").glob("*.md"))
    if not files:
        raise RuntimeError("Run the gather step first.")
    made = []
    for i, f in enumerate(files, start=1):
        ctx.check()
        ctx.progress(i / len(files), f"Preparing chapter {i} for the microphone")
        raw = _speakable(f.read_text(encoding="utf-8"))
        prompt = f"""Rewrite this chapter as narration-ready text.

Rules:
- Expand every number, symbol, abbreviation and unit into words as they are spoken.
- Turn any list into spoken prose with natural connectives.
- Replace anything visual ("as the table shows") with something a listener can follow.
- Change nothing else. Same content, same voice, same length. No summary, no commentary.
- Output plain text only.

---
{raw[:16000]}"""
        spoken = router.text(ROLE_EDITING, prompt, NARRATION_NOTE,
                             max_tokens=max(3000, len(raw) // 2)).text.strip()
        rel = f"drafts/narration/{i:03d}.txt"
        p.write_text(rel, spoken)
        made.append(rel)
    return StepResult(f"{len(made)} chapters prepared for narration", made)


def _step_front_back(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    title = p.answer("title") or p.name
    author = p.answer("author") or s.author_name or "the author"
    narrator = p.answer("narrator") or "a synthesised voice"
    ctx.progress(0.4, "Writing opening and closing credits")
    prompt = f"""Write the spoken opening and closing credits for an audiobook.

Title: {title}
Author: {author}
Narrator: {narrator}
Publisher: {p.answer('imprint') or s.imprint or 'independently published'}

ACX requires the opening credits to state title, author and narrator, and the
closing credits to state that the book is finished plus the same three facts.

Return JSON: {{"opening": "...", "closing": "..."}} — plain spoken text, no markdown."""
    data = router.text_json(ROLE_MARKETING, prompt, NARRATION_NOTE, max_tokens=900)
    p.write_text("drafts/narration/000_opening.txt", data.get("opening", ""))
    p.write_text("drafts/narration/999_closing.txt", data.get("closing", ""))
    return StepResult("Opening and closing credits written",
                      ["drafts/narration/000_opening.txt", "drafts/narration/999_closing.txt"])


def _voice_settings(p: Project) -> tuple[str, str]:
    engine = tts.ENGINES.get(p.answer("tts_engine", "edge-tts (free)"), "edge")
    label = p.answer("voice", "English (US, male)")
    voice = tts.EDGE_VOICES.get(label, label)
    if engine == "openai":
        voice = p.answer("openai_voice", "onyx")
    return engine, voice


def _step_audition(p: Project, ctx: JobContext) -> StepResult:
    sample = p.read_text("drafts/narration/001.txt", "")[:900]
    if not sample:
        raise RuntimeError("Prepare the narration first.")
    engine, _ = _voice_settings(p)
    candidates = list(tts.EDGE_VOICES.items())[:4] if engine == "edge" else \
        [(v, v) for v in tts.OPENAI_VOICES[:4]]
    made = []
    for i, (label, voice) in enumerate(candidates, start=1):
        ctx.check()
        ctx.progress(i / len(candidates), f"Auditioning {label}")
        try:
            clip = tts.synthesize(sample, p.dir / f"build/auditions/{i}_{voice}.mp3", engine, voice)
            made.append(p.rel(clip.path))
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"{label} unavailable: {exc}", "warning")
    if not made:
        raise RuntimeError("No voice engine produced a sample. Check Settings › Publishing.")
    return StepResult(f"{len(made)} auditions — listen before you commit {p.answer('runtime_hours', '?')} hours",
                      made)


def _step_narrate(p: Project, ctx: JobContext) -> StepResult:
    engine, voice = _voice_settings(p)
    files = sorted((p.dir / "drafts" / "narration").glob("*.txt"))
    if not files:
        raise RuntimeError("Prepare the narration first.")
    out_dir = p.dir / "build" / "raw_audio"
    made = []
    for i, f in enumerate(files, start=1):
        ctx.check()
        ctx.progress(i / len(files), f"Narrating {f.stem} ({i} of {len(files)})")
        text = f.read_text(encoding="utf-8").strip()
        if not text:
            continue
        clip = tts.synthesize(text, out_dir / f"{f.stem}.mp3", engine, voice,
                              rate=p.answer("rate", "-3%"))
        made.append(p.rel(clip.path))
    total = sum(audio.duration(p.dir / m) for m in made)
    return StepResult(f"{len(made)} files narrated — {total / 3600:.2f} hours",
                      made[:6], {"narrated_seconds": round(total, 1)})


def _step_master(p: Project, ctx: JobContext) -> StepResult:
    raw = sorted((p.dir / "build" / "raw_audio").glob("*.mp3"))
    if not raw:
        raise RuntimeError("Narrate the chapters first.")
    out_dir = p.dir / "build" / "acx"
    report, failures = [], 0
    for i, f in enumerate(raw, start=1):
        ctx.check()
        ctx.progress(i / len(raw), f"Mastering {f.stem}")
        before, after = audio.master(f, out_dir / f.name,
                                     head_silence=float(p.answer("head_silence", 0.6) or 0.6),
                                     tail_silence=float(p.answer("tail_silence", 2.0) or 2.0))
        if not after.acx_ok:
            failures += 1
        report.append({"file": f.name, "before_rms": before.mean_db, "after_rms": after.mean_db,
                       "after_peak": after.peak_db, "seconds": round(after.seconds, 1),
                       "acx": after.acx_ok})
    p.write_text("build/acx_report.json", json.dumps(report, indent=2))
    rows = ["| File | RMS before | RMS after | Peak | Length | ACX |", "|---|---:|---:|---:|---:|:--:|"]
    for r in report:
        rows.append(f"| {r['file']} | {r['before_rms']:.1f} | {r['after_rms']:.1f} | "
                    f"{r['after_peak']:.1f} | {r['seconds'] / 60:.1f} min | "
                    f"{'pass' if r['acx'] else 'FAIL'} |")
    p.write_text("build/acx_report.md",
                 "# ACX check\n\nACX wants RMS between −23 and −18 dB, peaks under −3 dB.\n\n"
                 + "\n".join(rows))
    return StepResult(f"{len(report)} files mastered, {failures} outside the ACX window",
                      ["build/acx_report.md"], {"acx_failures": failures})


def _step_package(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    files = sorted((p.dir / "build" / "acx").glob("*.mp3"))
    if not files:
        raise RuntimeError("Master the audio first.")
    chapters = json.loads(p.read_text("drafts/chapters.json", "[]"))
    titles = []
    for f in files:
        if f.stem.startswith("000"):
            titles.append("Opening Credits")
        elif f.stem.startswith("999"):
            titles.append("Closing Credits")
        else:
            try:
                num = int(f.stem)
            except ValueError:
                num = len(titles) + 1
            match = next((c["title"] for c in chapters if c["number"] == num), f"Chapter {num}")
            titles.append(match)
    title = p.answer("title") or p.name
    author = p.answer("author") or s.author_name or "Author"
    cover = p.build / "audiobook_cover.jpg"
    ctx.progress(0.4, "Building the chaptered M4B")
    audio.make_m4b(files, titles, p.build / "audiobook.m4b", title, author,
                   cover if cover.exists() else None)
    ctx.progress(0.8, "Cutting the retail sample")
    body = next((f for f in files if not f.stem.startswith("000")), files[0])
    audio.retail_sample(body, p.build / "retail_sample.mp3")
    total = sum(audio.duration(f) for f in files)
    return StepResult(f"M4B built — {len(files)} chapters, {total / 3600:.2f} hours, plus a 3-minute sample",
                      ["build/audiobook.m4b", "build/retail_sample.mp3"],
                      {"runtime_hours": round(total / 3600, 2)})


def _step_cover(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    spec = CoverSpec(
        title=p.answer("title") or p.name,
        subtitle=p.answer("subtitle", ""),
        author=p.answer("author") or s.author_name or "Author",
        imprint=p.answer("imprint") or s.imprint or "",
        palette=["#12131C", "#2B2450", "#D8A657", "#F4F1E8"])
    art = None
    note = ""
    src = Path(p.answer("cover_art_path", "")).expanduser() if p.answer("cover_art_path") else None
    if src and src.exists():
        art = src.read_bytes()
    elif p.answer("use_stock_photo", False):
        terms = [t.strip() for t in str(p.answer("stock_terms", "")).split(",") if t.strip()]
        if not terms:
            terms = [w for w in (p.answer("title") or p.name).split() if len(w) > 3][:3] or ["texture"]
        try:
            data, photo = stockvideo.best_photo(terms, source=p.answer("stock_source", "pexels"),
                                                orientation="square", min_width=1600,
                                                target_ratio=1.0,
                                                progress=lambda f, m: ctx.progress(f * 0.5, m))
            art = data
            note = "  " + stockvideo.credit_line(photo)
            p.write_text("build/IMAGE_CREDITS.txt", stockvideo.credit_line(photo) + "\n")
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"No stock photo ({exc}); using the typographic cover", "warning")
    ctx.progress(0.5, "Rendering the 3000×3000 audiobook cover")
    save_jpeg(render_front(spec, art, width=3000, height=3000), p.build / "audiobook_cover.jpg")
    return StepResult("Square cover rendered at 3000×3000 — ACX's minimum is 2400" + note,
                      ["build/audiobook_cover.jpg"])


AUDIOBOOK_PIPELINE = Pipeline(
    id="audiobook",
    title="Audiobook",
    subtitle="ACX / Audible, Apple Books, Spotify",
    description=("Turn a manuscript into a finished audiobook: narration-ready text, "
                 "synthesised or your own voice, ACX-compliant mastering with the levels "
                 "measured and proved, a chaptered M4B and a retail sample."),
    icon="headphones_rounded",
    accent="books",
    intake=[
        Field_("title", "Audiobook title", required=True),
        Field_("subtitle", "Subtitle"),
        Field_("author", "Author"),
        Field_("narrator", "Narrator credit", placeholder="e.g. a synthesised voice"),
        Field_("imprint", "Imprint"),
        Field_("source_project", "Source book project id",
               help="The folder name of a book project in this workspace. Leave blank to use a folder."),
        Field_("source_folder", "…or a folder of chapter files",
               help="Any folder of .md or .txt files, one per chapter, in order."),
    ],
    steps=[
        Step("gather", "Gather the chapters", "Prepare", AUTO, run=_step_gather,
             summary="Pull the manuscript from a book project or a folder, and estimate the runtime.",
             produces=["drafts/chapters.json"], run_label="Gather chapters"),
        Step("prepare", "Make it speakable", "Prepare", AUTO, run=_step_prepare, requires=["gather"],
             summary="Numbers, symbols, lists and anything visual rewritten as spoken words.",
             produces=["drafts/narration"], run_label="Prepare narration",
             cost_hint="one editing call per chapter"),
        Step("credits", "Write the credits", "Prepare", AUTO, run=_step_front_back,
             requires=["gather"],
             summary="ACX requires spoken opening and closing credits. These are them.",
             run_label="Write credits"),
        Step("audition", "Audition voices", "Voice", AUTO, run=_step_audition, requires=["prepare"],
             summary="A minute of the real text in four voices. Listening first is cheaper than re-narrating.",
             fields=[Field_("tts_engine", "Engine", "select", options=list(tts.ENGINES.keys()),
                            default="edge-tts (free)")],
             produces=["build/auditions"], run_label="Audition voices"),
        Step("choose_voice", "Pick the voice", "Voice", MANUAL, requires=["audition"], gate=REVIEW,
             summary="Play the auditions in build/auditions and commit.",
             instructions=("Listen to each file in `build/auditions/` on the speakers a listener would "
                           "use — phone speaker and cheap earbuds, not studio headphones.\n\n"
                           "What matters over ten hours: how the voice handles the end of sentences, "
                           "whether it rushes commas, and whether it makes your dialogue sound like "
                           "one person talking to themselves.\n\n"
                           "Autopilot will pick the first voice if you are not here."),
             fields=[Field_("voice", "Voice", "select", options=list(tts.EDGE_VOICES.keys()),
                            default="English (US, male)"),
                     Field_("rate", "Speaking rate", "select",
                            options=["-8%", "-5%", "-3%", "0%", "+3%"], default="-3%")],
             checklist=["Listened on phone speakers", "Checked a dialogue-heavy passage",
                        "Happy to hear it for hours"]),
        Step("narrate", "Narrate every chapter", "Produce", AUTO, run=_step_narrate,
             requires=["choose_voice", "credits"],
             summary="One audio file per chapter, plus the credits.",
             produces=["build/raw_audio"], run_label="Narrate",
             cost_hint="the long step — roughly a minute of compute per finished hour"),
        Step("cover", "Render the cover", "Produce", AUTO, run=_step_cover, requires=["gather"],
             summary="Square, 3000×3000, above every store's minimum.",
             fields=[
                 Field_("cover_art_path", "Background art file",
                        help="Optional. Takes precedence over everything below."),
                 Field_("use_stock_photo", "Use a free stock photograph", "switch", default=False,
                        help="Needs a Pexels or Pixabay key in Settings."),
                 Field_("stock_source", "Stock library", "select",
                        options=stockvideo.SOURCES, default="pexels"),
                 Field_("stock_terms", "Search terms",
                        help="Comma separated. Blank guesses from the title."),
             ],
             produces=["build/audiobook_cover.jpg"], run_label="Render cover"),
        Step("master", "Master to ACX levels", "Produce", AUTO, run=_step_master, requires=["narrate"],
             summary="Normalise RMS into the −23…−18 dB window, ceiling peaks at −3 dB, add room tone, "
                     "then measure again and prove it.",
             fields=[Field_("head_silence", "Room tone at the head (seconds)", "number", default=0.6),
                     Field_("tail_silence", "Room tone at the tail (seconds)", "number", default=2.0)],
             produces=["build/acx_report.md"], run_label="Master audio"),
        Step("package", "Package it", "Produce", AUTO, run=_step_package,
             requires=["master", "cover"],
             summary="Chaptered M4B with cover art embedded, plus a retail sample.",
             produces=["build/audiobook.m4b", "build/retail_sample.mp3"], run_label="Package"),
        Step("acx_submit", "Submit to ACX", "Publish", MANUAL, requires=["package"],
             summary="Upload the per-chapter MP3s — ACX wants the files, not the M4B.",
             instructions=("ACX takes **one MP3 per chapter** from `build/acx/`, not the M4B. The M4B "
                           "is for Apple Books and for selling direct.\n\n"
                           "1. Claim or add your title on ACX, matching the e-book's title exactly.\n"
                           "2. Upload the opening credits, every chapter in order, then the closing credits.\n"
                           "3. Upload `build/retail_sample.mp3` as the retail sample.\n"
                           "4. Cover: `build/audiobook_cover.jpg`, square, at least 2400×2400.\n"
                           "5. **Declare the AI narration.** Audible now has a synthetic-voice category; "
                           "misdeclaring it is what gets titles pulled, not the synthesis itself.\n\n"
                           "`build/acx_report.md` has the measured levels if their QA queries anything."),
             checklist=["Chapter files uploaded in order", "Retail sample uploaded",
                        "Cover meets 2400×2400", "AI narration declared"],
             links=[Link("ACX", "https://www.acx.com/"),
                    Link("ACX audio requirements", "https://www.acx.com/help/acx-audio-submission-requirements/201456300")]),
        Step("other_stores", "Sell it elsewhere", "Publish", MANUAL, requires=["package"], optional=True,
             summary="Non-exclusive distribution pays less per sale but reaches more shelves.",
             instructions=("ACX exclusive pays 40% and locks you in for seven years. Non-exclusive pays "
                           "25% and leaves you free.\n\n"
                           "- **Findaway Voices / Spotify for Authors** — wide distribution including "
                           "libraries, which is where audiobooks quietly earn.\n"
                           "- **Apple Books** — upload the M4B directly.\n"
                           "- **Your own store** — Gumroad or Payhip, the M4B plus the MP3 folder. "
                           "The highest margin you will ever get on it.\n\n"
                           "Decide exclusivity *before* you submit to ACX; changing it later means "
                           "waiting out the term."),
             checklist=["Exclusivity decided deliberately", "Wide distributor chosen",
                        "Direct store listing live"],
             links=[Link("Spotify for Authors", "https://authors.spotify.com/"),
                    Link("Apple Books", "https://authors.apple.com/")]),
    ],
)
