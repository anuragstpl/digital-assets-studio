"""The YouTube channel pipeline: positioning to published video."""
from __future__ import annotations

import json
from pathlib import Path

from ...config import (ASSETS_DIR, ROLE_MARKETING, ROLE_METADATA, ROLE_PLANNING, ROLE_RESEARCH,
                       ROLE_SCRIPT)
from ...core.jobs import JobContext
from ...core.llm import router
from ...core.pipeline import (AUTO, EXTERNAL, MANUAL, REVIEW, Field_, Link, Pipeline,
                              Step, StepResult)
from ...core.projects import Project
from ...core.publishing import mpt, stockvideo, tts, video, youtube as yt
from .art import (BrandSpec, render_avatar, render_banner, render_scene, render_thumbnail,
                  save, SLIDE, SLIDE_PORTRAIT)

FONT_FILE = ASSETS_DIR / "fonts" / "Poppins-Medium.ttf"

VOICE_NOTE = """You are planning a YouTube channel that has to survive contact with
the algorithm. Rules: the unit is a PROBLEM or a QUESTION, never a tool round-up
(those rot in 90 days). Never invent statistics, product prices, feature lists or
availability. If a fact would change what a viewer does, flag it for verification
rather than asserting it."""


def _json(p: Project, name: str) -> dict:
    raw = p.read_text(f"drafts/{name}", "")
    return json.loads(raw) if raw else {}


def _step_positioning(p: Project, ctx: JobContext) -> StepResult:
    ctx.progress(0.2, "Working out the positioning")
    prompt = f"""Design the positioning for this channel.

Topic: {p.answer('topic')}
Language: {p.answer('language')}
Format: {p.answer('format')}
Audience: {p.answer('audience')}
Why the creator is credible: {p.answer('credibility') or '(not stated)'}

Return JSON:
  promise         - the one sentence a subscriber is agreeing to
  unit            - what one video always is (the repeatable shape)
  episode_shape   - array of 5-7 named beats every episode follows
  differentiator  - the segment or habit competitors will not copy
  tagline         - under 8 words
  avoid           - array of 4 content traps in this niche
  first_10_titles - array of 10 video titles that are searches people actually make
  cadence         - a realistic publishing rhythm for a solo creator
"""
    data = router.text_json(ROLE_PLANNING, prompt, VOICE_NOTE, max_tokens=3000)
    p.write_text("drafts/positioning.json", json.dumps(data, indent=2))
    md = [f"# Positioning", "", f"**Promise** — {data.get('promise','')}", "",
          f"**The unit** — {data.get('unit','')}", "",
          f"**Tagline** — {data.get('tagline','')}", "",
          f"**Differentiator** — {data.get('differentiator','')}", "",
          f"**Cadence** — {data.get('cadence','')}", "", "## Episode shape", ""]
    md += [f"{i}. {b}" for i, b in enumerate(data.get("episode_shape", []), 1)]
    md += ["", "## Traps to avoid", ""] + [f"- {a}" for a in data.get("avoid", [])]
    md += ["", "## First ten titles", ""] + [f"- {t}" for t in data.get("first_10_titles", [])]
    p.write_text("drafts/positioning.md", "\n".join(md))
    return StepResult(f"Positioning set: {data.get('tagline','')}",
                      ["drafts/positioning.md", "drafts/positioning.json"],
                      {"tagline": data.get("tagline", "")})


def _step_brand(p: Project, ctx: JobContext) -> StepResult:
    pos = _json(p, "positioning.json")
    ctx.progress(0.3, "Naming and colouring the channel")
    prompt = f"""Name and style this channel.

Topic: {p.answer('topic')}
Promise: {pos.get('promise','')}
Preferred theme: {p.answer('theme')}

Return JSON:
  names          - array of 6 channel names, each under 22 characters, no numbers, easy to say aloud
  recommended    - the best one from that list, and why, as one string "Name — reason"
  handles        - array of 6 matching @handles
  description    - the channel About text, 3 short paragraphs, under 900 characters
  keywords       - array of 12 channel keywords
  palette        - object with bg, bg2, accent, ink, muted as hex strings, honouring the preferred theme
  thumbnail_rule - one sentence describing the thumbnail formula this channel will always follow
"""
    data = router.text_json(ROLE_MARKETING, prompt, VOICE_NOTE, max_tokens=2500)
    p.write_text("drafts/brand.json", json.dumps(data, indent=2))
    md = ["# Brand", "", f"**Recommended** — {data.get('recommended','')}", "", "## Name options", ""]
    md += [f"- {n}" for n in data.get("names", [])]
    md += ["", "## Handles", ""] + [f"- {h}" for h in data.get("handles", [])]
    md += ["", "## Channel description", "", data.get("description", ""), "",
           "## Keywords", "", ", ".join(data.get("keywords", [])), "",
           f"**Thumbnail rule** — {data.get('thumbnail_rule','')}"]
    p.write_text("drafts/brand.md", "\n".join(md))
    return StepResult("Names, handles, description and palette ready",
                      ["drafts/brand.md", "drafts/brand.json"])


def _brand_spec(p: Project) -> BrandSpec:
    b = _json(p, "brand.json")
    pos = _json(p, "positioning.json")
    pal = b.get("palette")
    if isinstance(pal, list):  # models sometimes return a list of hexes instead of named keys
        keys = ["bg", "bg2", "accent", "ink", "muted"]
        pal = {k: v for k, v in zip(keys, [c for c in pal if isinstance(c, str)])}
    if not isinstance(pal, dict):
        pal = {}
    return BrandSpec(
        name=p.answer("channel_name") or (b.get("names") or ["Channel"])[0],
        tagline=p.answer("tagline") or pos.get("tagline", ""),
        handle=p.answer("handle") or (b.get("handles") or [""])[0],
        bg=pal.get("bg", "#12161F"), bg2=pal.get("bg2", "#1B2A4A"),
        accent=pal.get("accent", "#0E7C7B"), ink=pal.get("ink", "#F7F4EF"),
        muted=pal.get("muted", "#B9C2D0"),
    )


def _step_art(p: Project, ctx: JobContext) -> StepResult:
    spec = _brand_spec(p)
    ctx.progress(0.3, "Rendering avatar")
    save(render_avatar(spec), p.build / "avatar.png")
    ctx.progress(0.6, "Rendering banner")
    save(render_banner(spec), p.build / "banner.png")
    save(render_banner(spec, show_safe_area=True), p.build / "banner_safe_area_guide.png")
    return StepResult("Avatar 800×800 and banner 2560×1440 rendered, all type inside the 1546×423 mobile crop",
                      ["build/avatar.png", "build/banner.png", "build/banner_safe_area_guide.png"])


def _step_topics(p: Project, ctx: JobContext) -> StepResult:
    pos = _json(p, "positioning.json")
    ctx.progress(0.3, "Building the topic bank")
    prompt = f"""Build a topic bank of 20 videos for this channel.

Promise: {pos.get('promise','')}
Unit: {pos.get('unit','')}
Audience: {p.answer('audience')}

Each topic must be a problem someone would type into a search box tonight.

Return JSON: {{"topics": [{{"title": ..., "search_intent": ..., "who_it_is_for": ...,
"payoff": ..., "difficulty": "easy|medium|hard", "evergreen": true|false,
"facts_to_verify": [strings]}}]}}"""
    data = router.text_json(ROLE_PLANNING, prompt, VOICE_NOTE, max_tokens=6000)
    topics = data.get("topics", data if isinstance(data, list) else [])
    p.write_text("drafts/topics.json", json.dumps(topics, indent=2))
    md = ["# Topic bank", ""]
    for i, t in enumerate(topics, 1):
        md += [f"## {i}. {t.get('title','')}",
               f"- **Search intent:** {t.get('search_intent','')}",
               f"- **For:** {t.get('who_it_is_for','')}",
               f"- **Payoff:** {t.get('payoff','')}",
               f"- **Difficulty:** {t.get('difficulty','')} · "
               f"{'evergreen' if t.get('evergreen') else 'time-sensitive'}", ""]
        if t.get("facts_to_verify"):
            md += ["- **Verify before publishing:** " + "; ".join(t["facts_to_verify"]), ""]
    p.write_text("drafts/topics.md", "\n".join(md))
    return StepResult(f"{len(topics)} topics banked", ["drafts/topics.md", "drafts/topics.json"])


def _step_script(p: Project, ctx: JobContext) -> StepResult:
    pos = _json(p, "positioning.json")
    topic = p.answer("episode_topic")
    if not topic:
        topics = json.loads(p.read_text("drafts/topics.json", "[]"))
        topic = (topics[0].get("title") if topics else "") or "Untitled episode"
    minutes = int(p.answer("episode_minutes", 8) or 8)
    ctx.progress(0.4, f"Writing the script for “{topic}”")
    prompt = f"""Write the full script for one episode.

Episode: {topic}
Channel promise: {pos.get('promise','')}
Episode shape: {json.dumps(pos.get('episode_shape', []))}
Language: {p.answer('language')}
Length: about {minutes} minutes of narration (roughly {minutes * 145} words).

Return JSON:
  hook          - the first 12 seconds, word for word. No "in this video".
  scenes        - array of objects: {{id, heading, narration, on_screen, b_roll_prompt, seconds}}
                  narration is the exact words to read; on_screen is the text overlay;
                  b_roll_prompt is a prompt for an illustration, described in a flat
                  vector style with no text in the image.
  cant_do       - what the method in this video still cannot do, stated plainly
  cta           - the closing line
  facts_to_check- array of every factual claim made, each as a checkable sentence
"""
    data = router.text_json(ROLE_SCRIPT, prompt, VOICE_NOTE, max_tokens=9000)
    slug = "".join(c for c in topic.lower().replace(" ", "-") if c.isalnum() or c == "-")[:50]
    p.write_text(f"drafts/episodes/{slug}.json", json.dumps(data, indent=2))
    md = [f"# {topic}", "", "## Hook", "", data.get("hook", ""), ""]
    narration = [data.get("hook", "")]
    for s in data.get("scenes", []):
        md += [f"## {s.get('heading','')}  _({s.get('seconds','?')}s)_", "",
               f"**Narration**\n\n{s.get('narration','')}", "",
               f"**On screen:** {s.get('on_screen','')}", "",
               f"**B-roll prompt:** `{s.get('b_roll_prompt','')}`", ""]
        narration.append(s.get("narration", ""))
    md += ["## What it still can't do", "", data.get("cant_do", ""), "",
           "## Call to action", "", data.get("cta", "")]
    p.write_text(f"drafts/episodes/{slug}.md", "\n".join(md))
    p.write_text(f"drafts/episodes/{slug}.narration.txt", "\n\n".join(x for x in narration if x))
    return StepResult(f"Script written — {len(data.get('scenes', []))} scenes",
                      [f"drafts/episodes/{slug}.md", f"drafts/episodes/{slug}.narration.txt"],
                      {"episode_slug": slug, "episode_title": topic})


def _step_factcheck(p: Project, ctx: JobContext) -> StepResult:
    slug = p.answer("episode_slug")
    data = json.loads(p.read_text(f"drafts/episodes/{slug}.json", "{}"))
    claims = data.get("facts_to_check") or []
    if not claims:
        claims = [s.get("narration", "")[:300] for s in data.get("scenes", [])]
    ctx.progress(0.3, f"Adversarial pass over {len(claims)} claims")
    prompt = f"""You are the second, adversarial fact-checking pass. The first pass wrote
these claims and is likely to have been confidently wrong about at least one.

Claims:
{json.dumps(claims, indent=2)}

For each claim return an object: {{claim, verdict: "verified"|"unverifiable"|"wrong"|"stale-risk",
reason, what_to_check, safer_wording}}.
Be harsh on: product availability by country, pricing, free-tier limits, whether a
tool still exists under that name, and anything that changed within the last year.
Return JSON: {{"checks": [...], "must_fix": [strings]}}"""
    out = router.text_json(ROLE_RESEARCH, prompt, VOICE_NOTE, max_tokens=6000)
    p.write_text(f"drafts/episodes/{slug}.factcheck.json", json.dumps(out, indent=2))
    md = ["# Fact check — second pass", ""]
    for c in out.get("checks", []):
        md += [f"### {c.get('verdict','?').upper()} — {c.get('claim','')}",
               f"- {c.get('reason','')}",
               f"- **Check:** {c.get('what_to_check','')}",
               f"- **Safer wording:** {c.get('safer_wording','')}", ""]
    if out.get("must_fix"):
        md += ["## Must fix before publishing", ""] + [f"- {m}" for m in out["must_fix"]]
    p.write_text(f"drafts/episodes/{slug}.factcheck.md", "\n".join(md))
    bad = sum(1 for c in out.get("checks", []) if c.get("verdict") in ("wrong", "stale-risk"))
    return StepResult(f"{len(out.get('checks', []))} claims checked, {bad} need attention",
                      [f"drafts/episodes/{slug}.factcheck.md"])


def _step_metadata(p: Project, ctx: JobContext) -> StepResult:
    slug = p.answer("episode_slug")
    data = json.loads(p.read_text(f"drafts/episodes/{slug}.json", "{}"))
    ctx.progress(0.4, "Writing titles, description and tags")
    prompt = f"""Write the publishing metadata for this episode.

Title working name: {p.answer('episode_title')}
Hook: {data.get('hook','')}
Scenes: {json.dumps([s.get('heading') for s in data.get('scenes', [])])}

Return JSON:
  titles        - array of 6 titles under 60 characters, no clickbait that the video does not pay off
  description   - the full description: 2 opening lines that repeat the promise, then
                  timestamps placeholder line, then a resources section, then the standard footer
  tags          - array of 15 tags
  hashtags      - array of 3
  pinned_comment- the comment to pin
  thumbnail_text- 3 to 5 words maximum, for the thumbnail
  shorts_cut    - which 45 seconds of this episode to cut as a Short, and why
"""
    md_data = router.text_json(ROLE_METADATA, prompt, VOICE_NOTE, max_tokens=3000)
    p.write_text(f"drafts/episodes/{slug}.metadata.json", json.dumps(md_data, indent=2))
    md = ["# Publishing metadata", "", "## Title options", ""]
    md += [f"- {t}" for t in md_data.get("titles", [])]
    md += ["", "## Description", "", "```", md_data.get("description", ""), "```", "",
           "## Tags", "", ", ".join(md_data.get("tags", [])), "",
           "## Hashtags", "", " ".join(md_data.get("hashtags", [])), "",
           "## Pinned comment", "", md_data.get("pinned_comment", ""), "",
           f"**Thumbnail text:** {md_data.get('thumbnail_text','')}", "",
           f"**Shorts cut:** {md_data.get('shorts_cut','')}"]
    p.write_text(f"drafts/episodes/{slug}.metadata.md", "\n".join(md))
    return StepResult("Titles, description, tags and Shorts cut ready",
                      [f"drafts/episodes/{slug}.metadata.md"],
                      {"thumbnail_text": md_data.get("thumbnail_text", "")})


def _step_thumbnail(p: Project, ctx: JobContext) -> StepResult:
    spec = _brand_spec(p)
    headline = p.answer("thumbnail_text") or p.answer("episode_title") or spec.name
    slug = p.answer("episode_slug") or "episode"
    ctx.progress(0.4, "Rendering thumbnail variants")
    paths = []
    for i, (kicker, badge) in enumerate(
            [(p.answer("thumbnail_kicker", ""), ""), ("", "HOW TO"), ("", "FREE")], start=1):
        img = render_thumbnail(spec, headline, kicker=kicker, badge=badge)
        rel = f"build/thumbnails/{slug}_v{i}.jpg"
        save(img, p.dir / rel)
        paths.append(rel)
    return StepResult("3 thumbnail variants at 1280×720 — test them at phone size before picking", paths)



def _episode(p: Project) -> tuple[str, dict]:
    slug = p.answer("episode_slug")
    if not slug:
        raise RuntimeError("Write an episode script first.")
    data = json.loads(p.read_text(f"drafts/episodes/{slug}.json", "{}"))
    if not data:
        raise RuntimeError(f"Could not read the script for {slug}.")
    return slug, data


def _step_voiceover(p: Project, ctx: JobContext) -> StepResult:
    slug, data = _episode(p)
    lines = [data.get("hook", "")] + [s.get("narration", "") for s in data.get("scenes", [])]
    lines = [x.strip() for x in lines if x and x.strip()]
    engine = tts.ENGINES.get(p.answer("tts_engine", "edge-tts (free)"), "edge")
    voice_label = p.answer("tts_voice", "English (US, male)")
    voice = tts.EDGE_VOICES.get(voice_label, voice_label)
    if engine == "openai":
        voice = p.answer("openai_voice", "onyx")
    ctx.progress(0.05, f"Voicing {len(lines)} blocks with {engine}")
    clips = tts.synthesize_scenes(lines, p.dir / "build" / "voice" / slug, engine, voice,
                                  progress=lambda f, m: ctx.progress(0.05 + f * 0.9, m))
    tts.write_srt(clips, p.dir / "build" / "voice" / f"{slug}.srt")
    total = sum(c.seconds for c in clips)
    p.write_text(f"build/voice/{slug}.timings.json",
                 json.dumps([{"seconds": c.seconds, "file": c.path.name} for c in clips], indent=2))
    return StepResult(f"{len(clips)} audio blocks, {total / 60:.1f} minutes total",
                      [f"build/voice/{slug}.srt"], {"episode_seconds": round(total, 2)})


def _step_scene_art(p: Project, ctx: JobContext) -> StepResult:
    slug, data = _episode(p)
    spec = _brand_spec(p)
    portrait = p.answer("orientation", "Landscape 16:9") == "Portrait 9:16"
    size = SLIDE_PORTRAIT if portrait else SLIDE
    use_ai = bool(p.answer("ai_broll", False))
    made = []
    blocks = [{"heading": p.answer("episode_title") or spec.name, "on_screen": "", "b_roll_prompt": ""}]
    blocks += data.get("scenes", [])
    for i, sc in enumerate(blocks):
        ctx.check()
        ctx.progress(i / max(len(blocks), 1), f"Drawing scene {i + 1} of {len(blocks)}")
        art = None
        if use_ai and sc.get("b_roll_prompt"):
            try:
                art = router.image(sc["b_roll_prompt"], count=1,
                                   size="1024x1536" if portrait else "1536x1024").images[0]
            except Exception as exc:  # noqa: BLE001
                ctx.log(f"Scene {i + 1}: image model unavailable ({exc}); drawing it instead", "warning")
        rel = f"build/scenes/{slug}/{i:03d}.jpg"
        save(render_scene(spec, sc.get("heading", ""), sc.get("on_screen", ""), i, art, size),
             p.dir / rel)
        made.append(rel)
    return StepResult(f"{len(made)} scene images rendered", made)


def _step_stock_terms(p: Project, ctx: JobContext) -> StepResult:
    slug, data = _episode(p)
    scenes = data.get("scenes", [])
    briefs = [s.get("b_roll_prompt") or s.get("heading", "") for s in scenes]
    ctx.progress(0.3, "Turning the visual briefs into stock search terms")
    prompt = f"""Turn each visual brief into stock-footage search terms.

Stock libraries match on plain, concrete nouns: "market stall", "hands typing",
"city traffic". They do not match on abstractions, brand names, text-in-image
requests, or anything with a specific person in it.

Briefs, in order:
{json.dumps(briefs, indent=2)}

Return JSON: {{"terms": [[...], [...]]}} — one array of 2 to 3 search terms per
brief, in the same order. Each term is one or two words."""
    out = router.text_json(ROLE_IMAGE_PROMPT, prompt, VOICE_NOTE, max_tokens=2500)
    terms = out.get("terms", [])
    flat = [t for group in terms for t in (group if isinstance(group, list) else [group])]
    if not flat:
        flat = [b.split()[0] for b in briefs if b][:8] or ["abstract background"]
    p.write_text(f"drafts/episodes/{slug}.stock_terms.json",
                 json.dumps({"per_scene": terms, "all": flat}, indent=2))
    return StepResult(f"{len(flat)} search terms across {len(briefs)} scenes",
                      [f"drafts/episodes/{slug}.stock_terms.json"])


def _step_stock_footage(p: Project, ctx: JobContext) -> StepResult:
    slug, _ = _episode(p)
    terms_file = json.loads(p.read_text(f"drafts/episodes/{slug}.stock_terms.json", "{}"))
    terms = terms_file.get("all") or []
    voice_dir = p.dir / "build" / "voice" / slug
    audio = sorted(voice_dir.glob("scene_*.mp3"))
    if not audio:
        raise RuntimeError("Generate the voiceover first — the footage is cut to its length.")
    total = sum(tts.ffprobe_duration(a) for a in audio)
    source = p.answer("stock_source", "pexels")
    files = stockvideo.gather(terms, total, p.dir / "build" / "stock" / slug,
                              portrait=_portrait(p), source=source,
                              clip_seconds=float(p.answer("clip_seconds", 5) or 5),
                              progress=lambda f, m: ctx.progress(f, m))
    return StepResult(f"{len(files)} clips downloaded from {source} — "
                      f"enough for {total / 60:.1f} minutes",
                      [p.rel(f) for f in files[:6]], {"stock_clip_count": len(files)})


def _render_stock(p: Project, ctx: JobContext, slug: str, portrait: bool,
                  out: Path) -> None:
    data = json.loads(p.read_text(f"drafts/episodes/{slug}.json", "{}"))
    blocks = [data.get("hook", "")] + [x.get("narration", "") for x in data.get("scenes", [])]
    blocks = [b.strip() for b in blocks if b and b.strip()]
    audio = sorted((p.dir / "build" / "voice" / slug).glob("scene_*.mp3"))
    clips = sorted((p.dir / "build" / "stock" / slug).glob("*.mp4"))
    if not clips:
        raise RuntimeError("No stock clips downloaded. Run the stock footage step first.")
    burn = bool(p.answer("burn_captions", True))
    scenes = [stockvideo.Scene(a, tts.ffprobe_duration(a),
                               blocks[i][:220] if burn and i < len(blocks) else "")
              for i, a in enumerate(audio)]
    stockvideo.assign_clips(clips, scenes,
                            clip_seconds=float(p.answer("clip_seconds", 5) or 5))
    music = Path(p.answer("music_path", "")).expanduser() if p.answer("music_path") else None
    stockvideo.compose(scenes, out,
                       size=video.PORTRAIT if portrait else video.LANDSCAPE,
                       font=FONT_FILE, music=music if music and music.exists() else None,
                       progress=lambda f, m: ctx.progress(f, m))


def _render_mpt(p: Project, ctx: JobContext, slug: str, portrait: bool, out: Path) -> None:
    data = json.loads(p.read_text(f"drafts/episodes/{slug}.json", "{}"))
    script = "\n\n".join([data.get("hook", "")]
                          + [x.get("narration", "") for x in data.get("scenes", [])]).strip()
    terms_file = json.loads(p.read_text(f"drafts/episodes/{slug}.stock_terms.json", "{}"))
    voice_label = p.answer("tts_voice", "English (US, male)")
    mpt.generate(
        subject=p.answer("episode_title") or slug,
        dest=out,
        script=script,
        terms=terms_file.get("all") or None,
        aspect="9:16" if portrait else "16:9",
        voice=tts.EDGE_VOICES.get(voice_label, ""),
        source=p.answer("stock_source", "pexels"),
        clip_seconds=int(float(p.answer("clip_seconds", 5) or 5)),
        subtitles=bool(p.answer("burn_captions", True)),
        progress=lambda f, m: ctx.progress(f, m))


def _step_render(p: Project, ctx: JobContext) -> StepResult:
    slug, _ = _episode(p)
    engine = _engine(p)
    portrait = _portrait(p)
    out = p.build / f"{slug}.mp4"

    if engine != ENGINE_MPT and not video.available():
        raise RuntimeError(
            "ffmpeg is not installed or not on PATH.\n"
            "  Windows:  winget install Gyan.FFmpeg\n"
            "  macOS:    brew install ffmpeg\n"
            "Then restart Digital Assets Studio and run this step again.")

    if engine == ENGINE_STOCK:
        _render_stock(p, ctx, slug, portrait, out)
    elif engine == ENGINE_MPT:
        _render_mpt(p, ctx, slug, portrait, out)
    else:
        voice_dir = p.dir / "build" / "voice" / slug
        scene_dir = p.dir / "build" / "scenes" / slug
        audio = sorted(voice_dir.glob("scene_*.mp3"))
        images = sorted(scene_dir.glob("*.jpg"))
        if not audio:
            raise RuntimeError("No voiceover found. Run the voiceover step first.")
        if not images:
            raise RuntimeError("No scene images found. Run the scene art step first.")
        burn = bool(p.answer("burn_captions", True))
        d = json.loads(p.read_text(f"drafts/episodes/{slug}.json", "{}"))
        blocks = [d.get("hook", "")] + [x.get("narration", "") for x in d.get("scenes", [])]
        blocks = [b.strip() for b in blocks if b and b.strip()]
        scenes = []
        for i, a in enumerate(audio):
            img = images[min(i, len(images) - 1)]
            caption = blocks[i][:220] if burn and i < len(blocks) else ""
            scenes.append(video.Scene(img, a, tts.ffprobe_duration(a), caption))
        music = Path(p.answer("music_path", "")).expanduser() if p.answer("music_path") else None
        video.render(scenes, out, size=video.PORTRAIT if portrait else video.LANDSCAPE,
                     font=FONT_FILE, music=music if music and music.exists() else None,
                     progress=lambda f, m: ctx.progress(f, m))

    info = video.probe(out)
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    mb = out.stat().st_size / 1_048_576
    shape = "portrait 1080×1920" if portrait else "landscape 1920×1080"
    return StepResult(f"Rendered with {engine} — {dur / 60:.1f} minutes, {mb:.0f} MB, {shape}",
                      [f"build/{slug}.mp4", f"build/voice/{slug}.srt"],
                      {"video_file": f"build/{slug}.mp4", "video_seconds": round(dur, 1)})


def _step_upload(p: Project, ctx: JobContext) -> StepResult:
    slug, _ = _episode(p)
    meta = json.loads(p.read_text(f"drafts/episodes/{slug}.metadata.json", "{}"))
    rel = p.answer("video_file") or f"build/{slug}.mp4"
    path = p.dir / rel
    if not path.exists():
        raise RuntimeError(f"No rendered video at {rel}. Run the render step, or point the "
                           f"'video file' field at your own edit.")
    if not yt.connected():
        ctx.log("Not connected to YouTube yet - a browser window will open for sign-in.")
    titles = meta.get("titles") or [p.answer("episode_title") or slug]
    title = p.answer("final_title") or titles[0]
    description = meta.get("description", "")

    ctx.progress(0.05, "Starting the upload")
    result = yt.upload_video(
        path, title=title, description=description, tags=meta.get("tags", []),
        category=p.answer("yt_category", "Education"),
        privacy=p.answer("privacy", "private"),
        publish_at=p.answer("publish_at") or None,
        made_for_kids=bool(p.answer("made_for_kids", False)),
        language=p.answer("yt_language_code", "en"),
        progress=lambda f, m: ctx.progress(0.05 + f * 0.8, m),
    )
    video_id = result.get("id", "")
    artifacts = []

    thumbs = sorted((p.dir / "build" / "thumbnails").glob(f"{slug}_v*.jpg"))
    pick = p.answer("thumbnail_choice", 1)
    if thumbs:
        chosen = thumbs[min(max(int(pick or 1), 1), len(thumbs)) - 1]
        try:
            ctx.progress(0.88, "Setting the thumbnail")
            yt.set_thumbnail(video_id, chosen)
            artifacts.append(str(chosen.relative_to(p.dir)).replace("\\", "/"))
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Thumbnail not set: {exc}", "warning")

    srt = p.dir / "build" / "voice" / f"{slug}.srt"
    if srt.exists():
        try:
            ctx.progress(0.92, "Uploading subtitles")
            yt.upload_caption(video_id, srt, language=p.answer("yt_language_code", "en"))
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Subtitles not uploaded: {exc}", "warning")

    playlist = p.answer("playlist_name", "")
    if playlist:
        try:
            ctx.progress(0.95, f"Adding to playlist {playlist}")
            yt.add_to_playlist(yt.ensure_playlist(playlist), video_id)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Playlist not updated: {exc}", "warning")

    pinned = meta.get("pinned_comment", "")
    if pinned and p.answer("privacy", "private") == "public":
        try:
            yt.post_comment(video_id, pinned)
        except Exception as exc:  # noqa: BLE001
            ctx.log(f"Comment not posted: {exc}", "warning")

    url = f"https://youtu.be/{video_id}"
    p.write_text(f"build/{slug}.upload.json", json.dumps(result, indent=2)[:20000])
    return StepResult(f"Uploaded as {p.answer('privacy', 'private')} — {url}",
                      artifacts + [f"build/{slug}.upload.json"],
                      {"video_url": url, "video_id": video_id})


def _step_short(p: Project, ctx: JobContext) -> StepResult:
    slug, _ = _episode(p)
    meta = json.loads(p.read_text(f"drafts/episodes/{slug}.metadata.json", "{}"))
    out = p.build / f"{slug}_short.mp4"
    fresh = p.answer("short_mode", "Cut from the long video") == "Generate a fresh vertical video"
    engine = _engine(p)

    if fresh and engine == ENGINE_STOCK:
        ctx.progress(0.2, "Composing a native vertical cut from the same footage")
        _render_stock(p, ctx, slug, True, out)
    elif fresh and engine == ENGINE_MPT:
        ctx.progress(0.2, "Asking MoneyPrinterTurbo for a 9:16 version")
        _render_mpt(p, ctx, slug, True, out)
    else:
        source = p.dir / (p.answer("video_file") or f"build/{slug}.mp4")
        if not source.exists():
            raise RuntimeError("Render the long-form video first.")
        if fresh:
            ctx.log("A fresh vertical render needs the stock or MoneyPrinterTurbo engine; "
                    "cropping the long video instead.", "warning")
        start_at = float(p.answer("short_start", 0) or 0)
        dur = float(p.answer("short_seconds", 45) or 45)
        srt = p.dir / "build" / "voice" / f"{slug}.srt"
        ctx.progress(0.3, f"Cutting {dur:.0f}s from {start_at:.0f}s")
        video.cut_short(source, out, start_at, dur,
                        subtitles=srt if srt.exists() else None, font=FONT_FILE)

    artifacts = [f"build/{slug}_short.mp4"]
    if p.answer("upload_short", True):
        ctx.progress(0.75, "Uploading the Short")
        title = (meta.get("titles") or [p.answer("episode_title", "Short")])[0][:95]
        res = yt.upload_video(out, title=f"{title} #Shorts",
                              description=(meta.get("description", "")[:400]),
                              tags=meta.get("tags", [])[:15],
                              category=p.answer("yt_category", "Education"),
                              privacy=p.answer("privacy", "private"),
                              made_for_kids=bool(p.answer("made_for_kids", False)),
                              progress=lambda f, m: ctx.progress(0.75 + f * 0.2, m))
        return StepResult(f"Short uploaded — https://youtu.be/{res.get('id','')}", artifacts,
                          {"short_url": f"https://youtu.be/{res.get('id','')}"})
    return StepResult("Short built and ready to upload", artifacts)


ENGINE_SLIDES = "Designed slides (built in)"
ENGINE_STOCK = "Stock footage (Pexels / Pixabay)"
ENGINE_MPT = "MoneyPrinterTurbo server"
ENGINES = [ENGINE_SLIDES, ENGINE_STOCK, ENGINE_MPT]

NEW_CHANNEL = "Create a new channel"
EXISTING_CHANNEL = "Use a channel I already have"


def _engine(p: Project) -> str:
    return p.answer("video_engine", ENGINE_SLIDES)


def _uses_slides(p: Project) -> bool:
    return _engine(p) == ENGINE_SLIDES


def _uses_stock(p: Project) -> bool:
    return _engine(p) == ENGINE_STOCK


def _uses_mpt(p: Project) -> bool:
    return _engine(p) == ENGINE_MPT


def _needs_local_voice(p: Project) -> bool:
    """MoneyPrinterTurbo does its own narration; the other engines need ours."""
    return not _uses_mpt(p)


def _portrait(p: Project) -> bool:
    return p.answer("orientation", "Landscape 16:9") == "Portrait 9:16"


def _is_new(p: Project) -> bool:
    return p.answer("mode", EXISTING_CHANNEL) == NEW_CHANNEL


def _is_existing(p: Project) -> bool:
    return not _is_new(p)


def _step_link_channel(p: Project, ctx: JobContext) -> StepResult:
    """Read the channel you already run, straight from the API."""
    if not yt.connected():
        ctx.log("Not connected yet — a browser window will open for sign-in.")
    try:
        info = yt.channel_summary()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"{exc}\n\nConnect the account in Settings › Publishing › YouTube. "
            f"If you do not have a channel yet, change this project's mode to "
            f"'{NEW_CHANNEL}' on this step and the suite will help you build one."
        ) from exc
    p.write_text("drafts/channel.json", json.dumps(info, indent=2))
    handle = info.get("handle", "")
    md = [f"# {info.get('title','')}", "",
          f"- Handle: {handle or '(none set)'}",
          f"- Subscribers: {info.get('subscribers','hidden')}",
          f"- Videos: {info.get('videos','0')} · Views: {info.get('views','0')}",
          f"- Created: {(info.get('published') or '')[:10]}",
          f"- Country: {info.get('country') or '(not set)'}", "",
          "## About text currently live", "", info.get("description", "") or "_empty_", "",
          "## Channel keywords", "", info.get("keywords", "") or "_none_"]
    p.write_text("drafts/channel.md", "\n".join(md))
    return StepResult(
        f"Linked to “{info.get('title','')}” ({handle or 'no handle'}) — "
        f"{info.get('videos','0')} videos, {info.get('subscribers','hidden')} subscribers. "
        f"If that is the wrong channel, disconnect in Settings and sign in choosing the right one.",
        ["drafts/channel.md"],
        {"channel_name": info.get("title", ""), "handle": handle,
         "channel_id": info.get("id", "")})


def _step_analyse(p: Project, ctx: JobContext) -> StepResult:
    """Derive the positioning from what the channel already publishes, so every
    downstream step works the same as it does for a new channel."""
    info = _json(p, "channel.json")
    ctx.progress(0.2, "Reading the last uploads")
    try:
        uploads = yt.recent_uploads(25)
    except Exception as exc:  # noqa: BLE001
        ctx.log(f"Could not read the uploads list ({exc}); working from the About text alone",
                "warning")
        uploads = []
    ctx.progress(0.5, "Working out what this channel is actually about")
    prompt = f"""Read an existing YouTube channel and describe what it is really doing,
then sharpen it. Do not invent a new channel - this one has an audience already.

Channel: {info.get('title','')} ({info.get('handle','')})
Subscribers: {info.get('subscribers','hidden')} · Videos: {info.get('videos','0')}
About text: {info.get('description','')}
Channel keywords: {info.get('keywords','')}
Stated topic from the owner: {p.answer('topic')}
Audience the owner has in mind: {p.answer('audience')}
Language: {p.answer('language')}

Last uploads (newest first):
{json.dumps([u['title'] for u in uploads], indent=2) if uploads else '(none readable)'}

Return JSON:
  promise         - the one sentence a subscriber is agreeing to, as the channel
                    currently behaves
  unit            - what one video always is here
  episode_shape   - array of 5-7 beats these videos already follow, or should
  differentiator  - what this channel does that its competitors do not
  tagline         - under 8 words
  avoid           - array of 4 traps, drawn from what has not worked here
  first_10_titles - array of 10 NEXT video titles that fit this channel and are
                    searches people actually make. Do not repeat existing titles.
  cadence         - a realistic rhythm given how often it already publishes
  what_is_working - array of up to 3 observations about the existing uploads
  what_to_change  - array of up to 3 concrete changes, most important first
  palette         - object with bg, bg2, accent, ink, muted as hex strings that suit
                    this channel's subject
"""
    data = router.text_json(ROLE_RESEARCH, prompt, VOICE_NOTE, max_tokens=5000)
    p.write_text("drafts/positioning.json", json.dumps(data, indent=2))
    p.write_text("drafts/brand.json", json.dumps({
        "names": [info.get("title", p.name)],
        "recommended": info.get("title", p.name),
        "handles": [info.get("handle", "")],
        "description": info.get("description", ""),
        "keywords": (info.get("keywords", "") or "").split(),
        "palette": data.get("palette", {}),
        "thumbnail_rule": data.get("differentiator", ""),
    }, indent=2))
    md = [f"# {info.get('title','')} — where it stands", "",
          f"**Promise** — {data.get('promise','')}", "",
          f"**The unit** — {data.get('unit','')}", "",
          f"**Tagline** — {data.get('tagline','')}", "",
          f"**Cadence** — {data.get('cadence','')}", "", "## What is working", ""]
    md += [f"- {x}" for x in data.get("what_is_working", [])]
    md += ["", "## What to change", ""] + [f"- {x}" for x in data.get("what_to_change", [])]
    md += ["", "## Episode shape", ""] + [f"{i}. {b}" for i, b in enumerate(data.get("episode_shape", []), 1)]
    md += ["", "## Traps to avoid", ""] + [f"- {a}" for a in data.get("avoid", [])]
    md += ["", "## Next ten titles", ""] + [f"- {t}" for t in data.get("first_10_titles", [])]
    p.write_text("drafts/positioning.md", "\n".join(md))
    return StepResult(f"Read {len(uploads)} uploads and set the positioning from them",
                      ["drafts/positioning.md"], {"tagline": data.get("tagline", "")})


def _autofill_name(p: Project) -> dict:
    b = _json(p, "brand.json")
    pos = _json(p, "positioning.json")
    rec = str(b.get("recommended", ""))
    name = p.answer("channel_name") or rec.split("—")[0].strip() or (b.get("names") or [p.name])[0]
    handle = p.answer("handle") or (b.get("handles") or [""])[0]
    return {"channel_name": name, "handle": handle,
            "tagline": p.answer("tagline") or pos.get("tagline", "")}


def _autofill_factcheck(p: Project) -> dict:
    """Autopilot accepts the script, but records what it waved through."""
    slug = p.answer("episode_slug")
    data = json.loads(p.read_text(f"drafts/episodes/{slug}.factcheck.json", "{}"))
    bad = [c.get("claim", "") for c in data.get("checks", [])
           if c.get("verdict") in ("wrong", "stale-risk")]
    if bad:
        p.write_text(f"drafts/episodes/{slug}.UNRESOLVED.md",
                     "# Claims autopilot did not fix\n\n"
                     "These were flagged by the second pass and published anyway.\n\n"
                     + "\n".join(f"- {c}" for c in bad))
    return {"unresolved_claims": len(bad)}


YOUTUBE_PIPELINE = Pipeline(
    id="youtube",
    title="YouTube channel",
    subtitle="Positioning, branding, scripts, thumbnails, publishing",
    description=("Stand a channel up properly: a promise worth subscribing to, branding that "
                 "survives the mobile crop, a topic bank of real searches, scripts with an "
                 "adversarial fact-check pass, and publish-ready metadata."),
    icon="smart_display_rounded",
    accent="video",
    intake=[
        Field_("mode", "Channel", "select",
               options=[EXISTING_CHANNEL, NEW_CHANNEL], default=EXISTING_CHANNEL,
               help="An existing channel is read straight from the API — its About "
                    "text, keywords and last 25 uploads — and everything downstream "
                    "works the same either way."),
        Field_("topic", "What is the channel about", "multiline", required=True,
               placeholder="Solving real-life problems with AI, sector by sector"),
        Field_("audience", "Who is watching", "multiline", required=True),
        Field_("language", "Language", "select",
               options=["English", "Hindi", "Hinglish", "Spanish", "Portuguese", "Indonesian", "Other"],
               default="English"),
        Field_("format", "Format", "select",
               options=["Long-form plus a Short from each", "Shorts only", "Long-form only",
                        "Faceless narration + b-roll"], default="Long-form plus a Short from each"),
        Field_("theme", "Visual theme", "select",
               options=["Light and warm", "Dark and cinematic", "Bold and high contrast", "Clean and minimal"],
               default="Light and warm"),
        Field_("credibility", "Why should anyone believe you", "multiline"),
        Field_("video_engine", "Video engine", "select", options=ENGINES,
                            default=ENGINE_SLIDES,
                            help="Designed slides need nothing but ffmpeg. Stock footage needs "
                                 "a free Pexels or Pixabay key. MoneyPrinterTurbo needs its own "
                                 "server running — see Settings › Publishing."),
    ],
    steps=[
        Step("channel_link", "Link your channel", "Channel", AUTO, run=_step_link_channel,
             applies_when=_is_existing,
             summary="Reads the channel you already run: About text, keywords, subscriber "
                     "count and handle.",
             fields=[Field_("mode", "Channel", "select",
                            options=[EXISTING_CHANNEL, NEW_CHANNEL], default=EXISTING_CHANNEL,
                            help="Switch to a new channel and this branch is replaced by "
                                 "the naming and branding steps.")],
             produces=["drafts/channel.md"], run_label="Link channel"),
        Step("analyse", "Read what it already publishes", "Channel", AUTO, run=_step_analyse,
             requires=["channel_link"], applies_when=_is_existing,
             summary="Positioning derived from the last 25 uploads rather than invented — "
                     "plus what is working and what to change.",
             produces=["drafts/positioning.md"], run_label="Analyse channel",
             cost_hint="1 research call"),
        Step("positioning", "Find the positioning", "Channel", AUTO, run=_step_positioning,
             applies_when=_is_new,
             fields=[Field_("mode", "Channel", "select",
                            options=[EXISTING_CHANNEL, NEW_CHANNEL], default=EXISTING_CHANNEL,
                            help="Switch to an existing channel and the suite reads yours "
                                 "instead of designing one.")],
             summary="The promise, the repeatable unit, the episode shape, and the traps to avoid.",
             produces=["drafts/positioning.md"], run_label="Work out positioning"),
        Step("brand", "Name and style it", "Channel", AUTO, run=_step_brand,
             requires=["positioning"], applies_when=_is_new,
             summary="Channel names, handles, About text, keywords and a palette.",
             produces=["drafts/brand.md"], run_label="Generate brand options"),
        Step("choose_name", "Choose the name", "Channel", MANUAL, requires=["brand"],
             gate=REVIEW, autofill=_autofill_name, applies_when=_is_new,
             summary="Check the handle is free before you fall in love with it.",
             instructions=("Open **drafts/brand.md**.\n\n"
                           "- Say each name out loud. If you have to spell it, drop it.\n"
                           "- Check the @handle is free at `youtube.com/@thehandle`.\n"
                           "- Check the name is not an existing trademark in your category.\n\n"
                           "Then fill these in — the art step uses them."),
             fields=[Field_("channel_name", "Channel name", required=True),
                     Field_("handle", "Handle", placeholder="@YourHandle"),
                     Field_("tagline", "Tagline")],
             checklist=["Handle is free", "Name is sayable", "No obvious trademark clash"]),
        Step("create_channel", "Create the channel", "Channel", MANUAL, requires=["choose_name"],
             applies_when=_is_new,
             summary="Only you can do this — it needs your Google account.",
             instructions=("1. Sign in to YouTube with the Google account that should own this channel. "
                           "Use a dedicated account if this is a business channel.\n"
                           "2. Settings → Add or manage your channel(s) → Create a channel.\n"
                           "3. Set the name and claim the handle.\n"
                           "4. Verify your phone number — without it you cannot upload custom "
                           "thumbnails or videos over 15 minutes.\n"
                           "5. Turn on two-factor authentication. A channel with any traction is a target."),
             checklist=["Channel created", "Handle claimed", "Phone verified", "2FA on"],
             links=[Link("YouTube", "https://www.youtube.com/"),
                    Link("Channel settings", "https://www.youtube.com/account")]),
        Step("art", "Render the channel art", "Channel", AUTO, run=_step_art,
             requires=["choose_name", "channel_link"],
             summary="Avatar and banner, with every word inside the 1546×423 mobile safe area. "
                     "On an existing channel this is a refresh — nothing is changed until you "
                     "upload it yourself.",
             produces=["build/banner.png", "build/avatar.png"], run_label="Render channel art"),
        Step("brand_upload", "Upload branding", "Channel", MANUAL, requires=["art"],
             optional=True,
             summary="Paste the About text, upload the art, set the country and links. "
                     "Optional on a channel that already looks the way you want.",
             instructions=("YouTube Studio → Customisation.\n\n"
                           "- **Branding:** upload `build/avatar.png` and `build/banner.png`. "
                           "Check YouTube's own crop preview on the TV, desktop and mobile sizes — "
                           "`build/banner_safe_area_guide.png` shows where the mobile crop lands.\n"
                           "- **Basic info:** paste the description and keywords from `drafts/brand.md`, "
                           "set the country, add links.\n"
                           "- Add a channel trailer later; an empty channel converts badly."),
             checklist=["Avatar uploaded", "Banner checked on all three crops",
                        "About text pasted", "Country and links set"],
             links=[Link("YouTube Studio", "https://studio.youtube.com/")]),

        Step("topics", "Build the topic bank", "Content", AUTO, run=_step_topics,
             requires=["positioning", "analyse"],
             summary="20 videos framed as searches, each with the facts that need verifying.",
             produces=["drafts/topics.md"], run_label="Build topic bank"),
        Step("script", "Write an episode script", "Content", AUTO, run=_step_script, requires=["topics"],
             summary="Hook, scenes with narration and b-roll prompts, and the honest limits segment.",
             fields=[Field_("episode_topic", "Episode topic", help="Leave blank to use the first banked topic."),
                     Field_("episode_minutes", "Length in minutes", "number", default=8)],
             run_label="Write script", cost_hint="1 long script call"),
        Step("factcheck", "Adversarial fact check", "Content", AUTO, run=_step_factcheck, requires=["script"],
             summary="A second pass whose only job is to catch what the first pass got confidently wrong.",
             produces=["drafts/episodes/*.factcheck.md"], run_label="Run second pass",
             cost_hint="1 research call"),
        Step("fix_script", "Fix what the check found", "Content", MANUAL, requires=["factcheck"],
             gate=REVIEW, autofill=_autofill_factcheck,
             summary="Availability, pricing and free-tier limits are where this bites.",
             instructions=("Open the `*.factcheck.md` file. Anything marked **WRONG** or **STALE-RISK** "
                           "gets fixed in the script before you record it.\n\n"
                           "Verify against the primary source — the vendor's own page, not a blog. "
                           "If you cannot verify it, use the safer wording the check suggests or cut the claim."),
             checklist=["Every WRONG claim fixed", "Every price or free-tier limit verified at source",
                        "Anything unverifiable cut or softened"]),
        Step("voiceover", "Generate the voiceover", "Content", AUTO, run=_step_voiceover,
             requires=["fix_script"], applies_when=_needs_local_voice,
             summary="Every block voiced separately, so the picture can be cut to the audio exactly.",
             fields=[
                 Field_("tts_engine", "Voice engine", "select",
                        options=list(tts.ENGINES.keys()), default="edge-tts (free)",
                        help="edge-tts is free and needs `pip install edge-tts`. The others use "
                             "the API keys you already saved."),
                 Field_("tts_voice", "Voice", "select",
                        options=list(tts.EDGE_VOICES.keys()), default="English (US, male)"),
                 Field_("openai_voice", "OpenAI voice", "select",
                        options=tts.OPENAI_VOICES, default="onyx"),
             ],
             produces=["build/voice"], run_label="Generate voiceover"),
        Step("own_voice", "Use your own voice instead", "Content", MANUAL, requires=["fix_script"],
             optional=True, applies_when=_needs_local_voice,
             summary="Your own voice beats every synthetic one for retention. Optional, not required.",
             instructions=("Record the narration in `drafts/episodes/<slug>.narration.txt` however you "
                           "like, then drop one MP3 per block into "
                           "`build/voice/<slug>/` named `scene_001.mp3`, `scene_002.mp3` and so on, "
                           "in script order.\n\n"
                           "The renderer measures each file and cuts the visuals to it — the durations "
                           "do not have to match anything."),
             checklist=["Recorded", "Files named in order", "Levels checked on headphones"]),
        Step("scene_art", "Draw the scenes", "Content", AUTO, run=_step_scene_art,
             requires=["script"], applies_when=_uses_slides,
             summary="One slide per scene in the channel palette, optionally with AI b-roll behind it.",
             fields=[
                 Field_("orientation", "Orientation", "select",
                        options=["Landscape 16:9", "Portrait 9:16"], default="Landscape 16:9"),
                 Field_("ai_broll", "Generate AI b-roll behind each scene", "switch", default=False,
                        help="Costs one image generation per scene. Off draws clean typographic slides."),
             ],
             produces=["build/scenes"], run_label="Draw scenes"),
        Step("stock_terms", "Work out the search terms", "Content", AUTO, run=_step_stock_terms,
             requires=["script"], applies_when=_uses_stock,
             summary="Each scene's visual brief turned into the plain nouns stock libraries "
                     "actually match on.",
             run_label="Build search terms", cost_hint="1 cheap call"),
        Step("stock_footage", "Fetch the footage", "Content", AUTO, run=_step_stock_footage,
             requires=["stock_terms", "voiceover"], applies_when=_uses_stock,
             summary="Downloads free clips from Pexels or Pixabay, enough to cover the narration.",
             fields=[
                 Field_("stock_source", "Library", "select", options=stockvideo.SOURCES,
                        default="pexels"),
                 Field_("clip_seconds", "Seconds per clip", "number", default=5),
             ],
             produces=["build/stock"], run_label="Fetch stock clips"),
        Step("render", "Render the video", "Content", AUTO, run=_step_render,
             requires=["voiceover", "scene_art", "stock_footage", "fix_script"],
             summary="Designed slides, stock footage, or a MoneyPrinterTurbo server — whichever "
                     "engine this project uses. Captions are cut to the narration either way.",
             fields=[
                 Field_("video_engine", "Video engine", "select", options=ENGINES,
                            default=ENGINE_SLIDES,
                            help="Designed slides need nothing but ffmpeg. Stock footage needs "
                                 "a free Pexels or Pixabay key. MoneyPrinterTurbo needs its own "
                                 "server running — see Settings › Publishing."),
                 Field_("orientation", "Orientation", "select",
                        options=["Landscape 16:9", "Portrait 9:16"], default="Landscape 16:9"),
                 Field_("burn_captions", "Burn captions into the picture", "switch", default=True),
                 Field_("music_path", "Background music file",
                        help="Optional. Ducked to -22 dB under the narration."),
             ],
             produces=["build/*.mp4"], run_label="Render video",
             cost_hint="CPU-bound — roughly real time per minute of video"),

        Step("metadata", "Write the publishing metadata", "Publish", AUTO, run=_step_metadata,
             requires=["script"],
             summary="Title options, full description, tags, pinned comment and the Shorts cut.",
             produces=["drafts/episodes/*.metadata.md"], run_label="Write metadata"),
        Step("thumbnail", "Render thumbnails", "Publish", AUTO, run=_step_thumbnail, requires=["metadata"],
             summary="Three variants at 1280×720. Judge them shrunk to phone size, not full screen.",
             fields=[Field_("thumbnail_text", "Thumbnail headline", help="Blank uses the one metadata suggested."),
                     Field_("thumbnail_kicker", "Kicker label", placeholder="e.g. Small business")],
             run_label="Render thumbnails"),
        Step("yt_connect", "Connect your YouTube channel", "Publish", MANUAL, optional=True,
             summary="One-time setup in Google Cloud. Fifteen minutes, and then uploads happen "
                     "from inside the suite forever.",
             instructions=(
                 "This is Google's setup, not the suite's, and it has a few places to trip. "
                 "Follow it in order.\n\n"
                 "### 1 — Make a project and turn the API on\n"
                 "Open the Google Cloud console. Create a project (any name; it is just a "
                 "container). Then **APIs & Services → Library → YouTube Data API v3 → Enable**. "
                 "Nothing works until this is enabled, and the error you get otherwise does not "
                 "say so clearly.\n\n"
                 "### 2 — Configure the consent screen\n"
                 "**APIs & Services → OAuth consent screen.**\n\n"
                 "- User type: **External**. (Internal only exists for Workspace organisations.)\n"
                 "- App name, your email, developer email. Nothing else is required.\n"
                 "- Scopes: you can leave this empty. The suite requests what it needs at "
                 "sign-in time.\n"
                 "- **Then press Publish app → In production.**\n\n"
                 "That last line is the one that matters. If you leave the app in **Testing**, "
                 "Google expires the saved sign-in after **7 days** — it will work all week and "
                 "then fail with `invalid_grant`, and you will have no idea why. Publishing to "
                 "production makes the sign-in durable.\n\n"
                 "Because the app is not verified, the consent screen will warn you that "
                 "*Google hasn't verified this app*. That is expected and fine for your own "
                 "account: click **Advanced → Go to (your app name)**. Verification is only "
                 "required to distribute an app to the public.\n\n"
                 "### 3 — Create the credentials\n"
                 "**APIs & Services → Credentials → Create credentials → OAuth client ID.**\n\n"
                 "- Application type: **Desktop app**. This is not optional — the suite signs "
                 "in through a loopback address on your own machine, and only the Desktop type "
                 "allows that. A Web application client will be rejected with a redirect_uri "
                 "mismatch.\n"
                 "- Copy the **Client ID** and **Client secret**.\n\n"
                 "### 4 — Connect\n"
                 "Paste both into **Settings › Publishing › YouTube**, press Save, then press "
                 "**Connect account**. A browser window opens once.\n\n"
                 "**Choose the right Google account, and the right channel.** The account you "
                 "pick here is the channel every upload goes to — the API has no per-upload "
                 "channel switch. If you run several channels on one account, pick the one you "
                 "mean, then run *Link your channel* and check the name it reports before "
                 "uploading anything. To change it later: Disconnect in Settings, then Connect "
                 "again and choose differently.\n\n"
                 "After that the suite refreshes its own token; you never sign in again.\n\n"
                 "### What it will and will not let you do\n"
                 "A new project gets its own upload allowance — **100 uploads a day** — plus "
                 "**10,000 units a day** for everything else. Setting a thumbnail costs 50 of "
                 "those units, subtitles 400, adding to a playlist 50, so a fully decorated "
                 "upload spends about 550 and you will run out of general quota at roughly 18 "
                 "videos a day, well before the upload allowance. Skip subtitles on bulk runs, "
                 "or request more quota in the console.\n\n"
                 "One thing no API can do: **uploading the avatar and banner**. Google removed "
                 "that endpoint. Those two stay a manual upload in YouTube Studio."),
             fields=[Field_("yt_language_code", "Default language code", default="en",
                            help="Used for the video language and the subtitle track, e.g. en, hi.")],
             checklist=["YouTube Data API v3 enabled",
                        "Consent screen published to In production (not Testing)",
                        "OAuth client created as Desktop app",
                        "Client ID and secret saved in Settings",
                        "Connected, and the right channel confirmed"],
             links=[Link("Enable YouTube Data API v3",
                         "https://console.cloud.google.com/apis/library/youtube.googleapis.com"),
                    Link("OAuth consent screen",
                         "https://console.cloud.google.com/apis/credentials/consent"),
                    Link("Credentials", "https://console.cloud.google.com/apis/credentials"),
                    Link("Quota costs", "https://developers.google.com/youtube/v3/determine_quota_cost")]),
        Step("upload", "Upload to YouTube", "Publish", AUTO, run=_step_upload,
             requires=["metadata", "render"],
             summary="Resumable upload, then thumbnail, subtitles, playlist and pinned comment.",
             fields=[
                 Field_("final_title", "Title", help="Blank uses the first title the metadata step wrote."),
                 Field_("privacy", "Visibility", "select",
                        options=["private", "unlisted", "public"], default="private",
                        help="Upload private first, check it on the site, then flip it public."),
                 Field_("publish_at", "Schedule (UTC, RFC3339)",
                        placeholder="2026-09-01T09:00:00Z", help="Leave blank to not schedule."),
                 Field_("yt_category", "Category", "select",
                        options=list(yt.CATEGORIES.keys()), default="Education"),
                 Field_("made_for_kids", "Made for kids", "switch", default=False,
                        help="Answer honestly — this one is a legal declaration, not a preference."),
                 Field_("playlist_name", "Add to playlist"),
                 Field_("thumbnail_choice", "Thumbnail variant", "number", default=1),
                 Field_("video_file", "Video file",
                        help="Blank uses the rendered one. Point it at your own edit if you cut it elsewhere."),
             ],
             run_label="Upload to YouTube"),
        Step("shorts", "Cut and upload the Short", "Publish", AUTO, run=_step_short,
             requires=["render"], optional=True,
             summary="Vertical crop with burned subtitles, uploaded with the same metadata.",
             fields=[
                 Field_("short_mode", "How to make it", "select",
                        options=["Cut from the long video", "Generate a fresh vertical video"],
                        default="Cut from the long video",
                        help="A fresh vertical render needs the stock or MoneyPrinterTurbo engine; "
                             "with designed slides it falls back to cropping."),
                 Field_("short_start", "Start at (seconds)", "number", default=0,
                        help="The metadata step names which moment to cut."),
                 Field_("short_seconds", "Length (seconds)", "number", default=45),
                 Field_("upload_short", "Upload it too", "switch", default=True),
             ],
             run_label="Cut and upload Short"),
        Step("monetise", "Set up monetisation", "Publish", MANUAL, optional=True,
             summary="The Partner Programme is the slowest revenue line. Set up the others first.",
             instructions=("**YouTube Partner Programme** needs 1,000 subscribers plus either 4,000 "
                           "public watch hours in 12 months or 10 million Shorts views in 90 days. "
                           "That takes most channels many months — treat ad revenue as the last line to arrive, "
                           "not the first.\n\n"
                           "Set these up now instead, because they work at any subscriber count:\n\n"
                           "- **Your own products in the description** — the books and kits you already sell.\n"
                           "- **An email list.** A free companion checklist per video converts well and "
                           "you own the list.\n"
                           "- **Affiliate links**, disclosed, only for tools you actually use.\n"
                           "- **Sponsors** become realistic around 10k engaged subscribers in a commercial niche.\n\n"
                           "Whatever you do, keep the paid-promotion disclosure box ticked when it applies."),
             checklist=["Products linked in descriptions", "Email capture live",
                        "Affiliate disclosures in place", "YPP applied for when eligible"],
             links=[Link("YPP requirements", "https://support.google.com/youtube/answer/72851")]),
    ],
)
