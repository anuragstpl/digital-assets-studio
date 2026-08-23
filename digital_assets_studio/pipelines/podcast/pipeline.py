"""Podcast pipeline: a show concept to a valid RSS feed with episodes in it."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ...config import ROLE_MARKETING, ROLE_METADATA, ROLE_PLANNING, ROLE_SCRIPT
from ...core.jobs import JobContext
from ...core.llm import router
from ...core.pipeline import (AUTO, EXTERNAL, MANUAL, REVIEW, Field_, Link, Pipeline, Step,
                              StepResult)
from ...core.projects import Project
from ...core.publishing import audio, tts
from ...core.settings import load as load_settings
from ..books.cover import CoverSpec, render_front, save_jpeg

CATEGORIES = ["Technology", "Business", "Education", "Arts", "Health & Fitness", "News",
              "Society & Culture", "Comedy", "Science", "True Crime", "Fiction"]

NOTE = """You make podcasts people finish. Rules: the first thirty seconds decide
everything, so no long intro music and no "welcome back to the show" before the hook.
Never invent statistics or quotations. Write for the ear: short sentences, no
subordinate clauses stacked three deep."""


def _json_file(p: Project, name: str) -> dict:
    raw = p.read_text(f"drafts/{name}", "")
    return json.loads(raw) if raw else {}


def _step_concept(p: Project, ctx: JobContext) -> StepResult:
    ctx.progress(0.3, "Shaping the show")
    prompt = f"""Design this podcast.

Topic: {p.answer('topic')}
Listener: {p.answer('listener')}
Format: {p.answer('format')}
Episode length: {p.answer('minutes')} minutes
Cadence: {p.answer('cadence')}

Return JSON:
  show_names       - array of 6 candidate names, each easy to say and to search
  recommended      - "Name — why", one string
  tagline          - under 10 words
  description      - the show description for Apple and Spotify, 400-600 characters
  segments         - array of the recurring segments each episode has, in order
  first_8_episodes - array of {{title, hook, why_now}}
  category         - one of {CATEGORIES}
  keywords         - array of 12
  palette          - object with bg, bg2, accent, ink as hex strings
"""
    data = router.text_json(ROLE_PLANNING, prompt, NOTE, max_tokens=5000)
    p.write_text("drafts/concept.json", json.dumps(data, indent=2))
    md = [f"# {data.get('recommended','')}", "", data.get("tagline", ""), "",
          "## Description", "", data.get("description", ""), "", "## Names", ""]
    md += [f"- {n}" for n in data.get("show_names", [])]
    md += ["", "## Segments", ""] + [f"{i}. {s}" for i, s in enumerate(data.get("segments", []), 1)]
    md += ["", "## First eight episodes", ""]
    for i, e in enumerate(data.get("first_8_episodes", []), 1):
        md += [f"{i}. **{e.get('title','')}** — {e.get('hook','')}  _{e.get('why_now','')}_"]
    p.write_text("drafts/concept.md", "\n".join(md))
    return StepResult(f"Show designed: {data.get('recommended','')}", ["drafts/concept.md"],
                      {"category": data.get("category", "Technology")})


def _step_cover(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    concept = _json_file(p, "concept.json")
    pal = concept.get("palette") if isinstance(concept.get("palette"), dict) else {}
    name = p.answer("show_name") or str(concept.get("recommended", p.name)).split("—")[0].strip()
    spec = CoverSpec(
        title=name, subtitle=p.answer("tagline") or concept.get("tagline", ""),
        author=p.answer("host") or s.author_name or "",
        palette=[pal.get("bg", "#10131C"), pal.get("bg2", "#26356B"),
                 pal.get("accent", "#E8B44A"), pal.get("ink", "#F4F1E8")])
    ctx.progress(0.5, "Rendering the 3000×3000 show cover")
    save_jpeg(render_front(spec, None, width=3000, height=3000), p.build / "show_cover.jpg")
    return StepResult("Cover rendered at 3000×3000 — Apple's minimum is 1400, its maximum 3000",
                      ["build/show_cover.jpg"])


def _step_script(p: Project, ctx: JobContext) -> StepResult:
    concept = _json_file(p, "concept.json")
    episodes = concept.get("first_8_episodes", [])
    number = int(p.answer("episode_number", 1) or 1)
    chosen = p.answer("episode_title") or (episodes[min(number - 1, len(episodes) - 1)].get("title")
                                           if episodes else f"Episode {number}")
    minutes = int(p.answer("minutes", 25) or 25)
    ctx.progress(0.4, f"Writing episode {number}: {chosen}")
    prompt = f"""Write episode {number} in full.

Show: {concept.get('recommended','')}
Tagline: {concept.get('tagline','')}
Segments: {json.dumps(concept.get('segments', []))}
Episode: {chosen}
Length: about {minutes} minutes ({minutes * 150} words of speech)
Format: {p.answer('format')}

Return JSON:
  title       - the published episode title
  cold_open   - the first 30 seconds, word for word, before any intro
  blocks      - array of {{heading, speaker, text, seconds}} covering the whole episode
                in order. speaker is "host" or "guest". text is exactly what is said.
  ad_slots    - array of integers: the block indexes where a mid-roll would fit
  outro       - the closing lines
  facts_to_check - array of every factual claim made
"""
    data = router.text_json(ROLE_SCRIPT, prompt, NOTE, max_tokens=12000)
    slug = f"ep{number:03d}"
    p.write_text(f"drafts/episodes/{slug}.json", json.dumps(data, indent=2))
    md = [f"# {data.get('title', chosen)}", "", "## Cold open", "", data.get("cold_open", ""), ""]
    for i, b in enumerate(data.get("blocks", [])):
        marker = "  ← ad slot" if i in (data.get("ad_slots") or []) else ""
        md += [f"## {b.get('heading','')} ({b.get('speaker','host')}, {b.get('seconds','?')}s){marker}",
               "", b.get("text", ""), ""]
    md += ["## Outro", "", data.get("outro", "")]
    p.write_text(f"drafts/episodes/{slug}.md", "\n".join(md))
    return StepResult(f"Episode {number} written — {len(data.get('blocks', []))} blocks",
                      [f"drafts/episodes/{slug}.md"],
                      {"episode_slug": slug, "episode_title": data.get("title", chosen)})


def _step_record(p: Project, ctx: JobContext) -> StepResult:
    slug = p.answer("episode_slug")
    data = json.loads(p.read_text(f"drafts/episodes/{slug}.json", "{}"))
    if not data:
        raise RuntimeError("Write an episode first.")
    engine = tts.ENGINES.get(p.answer("tts_engine", "edge-tts (free)"), "edge")
    host = tts.EDGE_VOICES.get(p.answer("host_voice", "English (US, male)"),
                               p.answer("host_voice", "en-US-AndrewNeural"))
    guest = tts.EDGE_VOICES.get(p.answer("guest_voice", "English (US, female)"),
                                p.answer("guest_voice", "en-US-AvaNeural"))
    blocks = [("host", data.get("cold_open", ""))]
    blocks += [(b.get("speaker", "host"), b.get("text", "")) for b in data.get("blocks", [])]
    blocks.append(("host", data.get("outro", "")))
    out_dir = p.dir / "build" / "raw" / slug
    made = []
    for i, (speaker, text) in enumerate(blocks, start=1):
        ctx.check()
        if not text.strip():
            continue
        ctx.progress(i / len(blocks), f"Recording block {i} of {len(blocks)} ({speaker})")
        voice = guest if speaker == "guest" else host
        clip = tts.synthesize(text, out_dir / f"{i:03d}_{speaker}.mp3", engine, voice)
        made.append(clip.path)
    seconds = sum(audio.duration(m) for m in made)
    return StepResult(f"{len(made)} blocks recorded — {seconds / 60:.1f} minutes",
                      [p.rel(m) for m in made[:4]], {"episode_seconds": round(seconds, 1)})


def _step_produce(p: Project, ctx: JobContext) -> StepResult:
    slug = p.answer("episode_slug")
    raw = sorted((p.dir / "build" / "raw" / slug).glob("*.mp3"))
    if not raw:
        raise RuntimeError("Record the episode first.")
    ctx.progress(0.3, "Joining the blocks")
    joined = audio.concat(raw, p.dir / f"build/{slug}_joined.mp3")
    ctx.progress(0.6, "Levelling for podcast apps")
    before, after = audio.master(joined, p.dir / f"build/{slug}.mp3",
                                 target_rms=-16.0, head_silence=0.3, tail_silence=1.0,
                                 mono=False)
    (p.dir / f"build/{slug}_joined.mp3").unlink(missing_ok=True)
    seconds = after.seconds
    size = (p.dir / f"build/{slug}.mp3").stat().st_size
    return StepResult(f"{seconds / 60:.1f} minutes, {size / 1_048_576:.1f} MB, "
                      f"RMS {after.mean_db:.1f} dB / peak {after.peak_db:.1f} dB",
                      [f"build/{slug}.mp3"],
                      {"episode_seconds": round(seconds, 1), "episode_bytes": size})


def _step_shownotes(p: Project, ctx: JobContext) -> StepResult:
    slug = p.answer("episode_slug")
    data = json.loads(p.read_text(f"drafts/episodes/{slug}.json", "{}"))
    ctx.progress(0.4, "Writing show notes")
    prompt = f"""Write the show notes for this episode.

Title: {data.get('title','')}
Blocks: {json.dumps([b.get('heading') for b in data.get('blocks', [])])}
Cold open: {data.get('cold_open','')[:600]}

Return JSON:
  title        - the published title, under 80 characters
  summary      - 2 short paragraphs for the episode description
  chapters     - array of {{minutes, seconds, label}} approximating the block order
  takeaways    - array of 4
  links        - array of {{label, note}} for things worth linking, with a note on
                 what to link to. Do not invent URLs.
  social_posts - array of 2 short posts
"""
    out = router.text_json(ROLE_METADATA, prompt, NOTE, max_tokens=3000)
    p.write_text(f"drafts/episodes/{slug}.notes.json", json.dumps(out, indent=2))
    md = [f"# {out.get('title','')}", "", out.get("summary", ""), "", "## Chapters", ""]
    for ch in out.get("chapters", []):
        md.append(f"- {int(ch.get('minutes', 0)):02d}:{int(ch.get('seconds', 0)):02d} {ch.get('label','')}")
    md += ["", "## Takeaways", ""] + [f"- {t}" for t in out.get("takeaways", [])]
    md += ["", "## Links to add", ""] + [f"- **{l.get('label','')}** — {l.get('note','')}"
                                         for l in out.get("links", [])]
    md += ["", "## Social", ""] + [f"- {s}" for s in out.get("social_posts", [])]
    p.write_text(f"drafts/episodes/{slug}.notes.md", "\n".join(md))
    return StepResult("Show notes, chapters and social posts written",
                      [f"drafts/episodes/{slug}.notes.md"])


def _step_feed(p: Project, ctx: JobContext) -> StepResult:
    s = load_settings()
    concept = _json_file(p, "concept.json")
    base = (p.answer("media_base_url", "") or "").rstrip("/")
    if not base:
        raise RuntimeError("Set the media base URL — the public folder your MP3s will live in. "
                           "The feed has to point at real, reachable files.")
    episodes = []
    for mp3 in sorted(p.build.glob("ep*.mp3")):
        slug = mp3.stem
        notes = json.loads(p.read_text(f"drafts/episodes/{slug}.notes.json", "{}"))
        number = int(slug.replace("ep", "") or 1)
        episodes.append({
            "title": notes.get("title", slug),
            "description": notes.get("summary", ""),
            "audio_url": f"{base}/{mp3.name}",
            "bytes": mp3.stat().st_size,
            "seconds": audio.duration(mp3),
            "published": datetime.now(timezone.utc),
            "episode_number": number,
            "guid": f"{p.id}-{slug}",
        })
    if not episodes:
        raise RuntimeError("No produced episodes yet.")
    xml = audio.rss_feed(
        title=p.answer("show_name") or str(concept.get("recommended", p.name)).split("—")[0].strip(),
        description=concept.get("description", ""),
        author=p.answer("host") or s.author_name or "",
        email=p.answer("contact_email", ""),
        site_url=p.answer("site_url", "") or base,
        cover_url=p.answer("cover_url", "") or f"{base}/show_cover.jpg",
        episodes=episodes,
        category=p.answer("category") or concept.get("category", "Technology"),
        explicit=bool(p.answer("explicit", False)))
    p.write_text("build/feed.xml", xml)
    return StepResult(f"RSS feed built with {len(episodes)} episode(s)", ["build/feed.xml"])


PODCAST_PIPELINE = Pipeline(
    id="podcast",
    title="Podcast",
    subtitle="Apple Podcasts, Spotify, any host",
    description=("A show concept, cover art, scripted episodes, synthesised or your own "
                 "voice, levelled audio and a valid RSS feed — the actual thing the "
                 "directories subscribe to."),
    icon="podcasts_rounded",
    accent="video",
    intake=[
        Field_("topic", "What is the show about", "multiline", required=True),
        Field_("listener", "Who listens", "multiline", required=True),
        Field_("format", "Format", "select",
               options=["Solo narration", "Host and guest interview", "Two co-hosts",
                        "Narrative documentary"], default="Solo narration"),
        Field_("minutes", "Episode length in minutes", "number", default=25),
        Field_("cadence", "Cadence", "select",
               options=["Weekly", "Fortnightly", "Monthly", "Seasons"], default="Weekly"),
        Field_("host", "Host name"),
        Field_("contact_email", "Contact email",
               help="Apple requires a working owner email on the feed."),
    ],
    steps=[
        Step("concept", "Design the show", "Show", AUTO, run=_step_concept,
             summary="Names, description, recurring segments and the first eight episodes.",
             produces=["drafts/concept.md"], run_label="Design the show"),
        Step("choose", "Pick the name", "Show", MANUAL, requires=["concept"], gate=REVIEW,
             summary="Check it is free in Apple Podcasts before you commit.",
             instructions=("Search the names in Apple Podcasts and Spotify. A name already in use "
                           "costs you every search you would have won.\n\n"
                           "Then check the domain or handle you would want is free too."),
             fields=[Field_("show_name", "Show name", required=True),
                     Field_("tagline", "Tagline"),
                     Field_("category", "Apple category", "select", options=CATEGORIES,
                            default="Technology")],
             checklist=["Not already used", "Easy to say out loud", "Handle available"]),
        Step("cover", "Render the cover", "Show", AUTO, run=_step_cover, requires=["choose"],
             summary="Square, 3000×3000, legible at the 55-pixel size a phone actually shows.",
             produces=["build/show_cover.jpg"], run_label="Render cover"),
        Step("script", "Write an episode", "Episode", AUTO, run=_step_script, requires=["concept"],
             summary="Cold open, every block with the exact words, ad slots and the outro.",
             fields=[Field_("episode_number", "Episode number", "number", default=1),
                     Field_("episode_title", "Episode topic",
                            help="Blank uses the planned episode for that number.")],
             run_label="Write episode", cost_hint="one long script call"),
        Step("record", "Record it", "Episode", AUTO, run=_step_record, requires=["script"],
             summary="A separate voice per speaker, one clip per block.",
             fields=[Field_("tts_engine", "Engine", "select", options=list(tts.ENGINES.keys()),
                            default="edge-tts (free)"),
                     Field_("host_voice", "Host voice", "select",
                            options=list(tts.EDGE_VOICES.keys()), default="English (US, male)"),
                     Field_("guest_voice", "Guest voice", "select",
                            options=list(tts.EDGE_VOICES.keys()), default="English (US, female)")],
             produces=["build/raw"], run_label="Record episode"),
        Step("produce", "Level and join", "Episode", AUTO, run=_step_produce, requires=["record"],
             summary="Joined, normalised to −16 dB RMS with peaks under −3 dB, which is what "
                     "podcast apps expect.",
             produces=["build/ep001.mp3"], run_label="Produce audio"),
        Step("shownotes", "Write show notes", "Episode", AUTO, run=_step_shownotes,
             requires=["script"],
             summary="Description, chapter marks, takeaways and two social posts.",
             produces=["drafts/episodes"], run_label="Write show notes"),
        Step("feed", "Build the RSS feed", "Publish", AUTO, run=_step_feed,
             requires=["produce", "shownotes"],
             summary="The file the directories actually read. Every episode, correctly tagged.",
             fields=[Field_("media_base_url", "Public URL of the folder holding your MP3s",
                            required=True, placeholder="https://cdn.example.com/mypodcast"),
                     Field_("site_url", "Show website"),
                     Field_("cover_url", "Public URL of the cover image"),
                     Field_("explicit", "Explicit content", "switch", default=False)],
             produces=["build/feed.xml"], run_label="Build feed"),
        Step("host", "Host the files", "Publish", MANUAL, requires=["feed"],
             summary="Somewhere public that serves the MP3 with byte-range support.",
             instructions=("Two routes:\n\n"
                           "**A podcast host** (Buzzsprout, Transistor, Captivate, Spotify for "
                           "Creators) — they generate the feed for you, so upload the MP3, the "
                           "cover and paste the show notes, and ignore `build/feed.xml`. Easiest, "
                           "and you get download stats.\n\n"
                           "**Self-hosted** — put the MP3s and the cover on any static host or S3 "
                           "bucket, then upload `build/feed.xml` alongside them. The URLs in the "
                           "feed must resolve publicly and the server must support byte-range "
                           "requests, or Apple's validator rejects the feed and players cannot seek.\n\n"
                           "Whichever you pick, the feed URL never changes after submission. "
                           "Moving hosts later means a 301 redirect, not a new feed."),
             fields=[Field_("feed_url", "Live feed URL")],
             checklist=["MP3 reachable in a private window", "Cover reachable",
                        "Feed validates", "Feed URL recorded somewhere safe"],
             links=[Link("Podbase feed validator", "https://podba.se/validate/"),
                    Link("Buzzsprout", "https://www.buzzsprout.com/")]),
        Step("submit", "Submit to the directories", "Publish", MANUAL, requires=["host"],
             summary="One feed, submitted once, to each directory.",
             instructions=("- **Apple Podcasts Connect** — paste the feed URL. Review takes hours to "
                           "a few days. The owner email on the feed gets a verification message, so "
                           "it must be one you can read.\n"
                           "- **Spotify for Creators** — paste the same feed URL.\n"
                           "- **YouTube Music, Amazon, Pocket Casts, Overcast** — same feed again.\n\n"
                           "After this, publishing an episode means uploading the MP3 and updating "
                           "the feed. Everything downstream pulls automatically."),
             checklist=["Apple submitted", "Spotify submitted", "Other directories submitted"],
             links=[Link("Apple Podcasts Connect", "https://podcastsconnect.apple.com/"),
                    Link("Spotify for Creators", "https://creators.spotify.com/")]),
    ],
)
