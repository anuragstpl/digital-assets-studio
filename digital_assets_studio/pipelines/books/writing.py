"""Every LLM call the book pipeline makes, in one file.

Prompts live here rather than being scattered through the step definitions, so
you can tune the house voice in a single place.
"""
from __future__ import annotations

import json
import re
from typing import Any

from ...config import (ROLE_EDITING, ROLE_LONGFORM, ROLE_MARKETING, ROLE_METADATA,
                       ROLE_PLANNING, ROLE_RESEARCH)
from ...core.llm import router

HOUSE_STYLE = """You write commercial non-fiction and fiction for independent
publishing. House rules:
- Concrete over abstract. Name the thing, give the number, show the example.
- No filler openings ("In today's fast-paced world"), no throat-clearing, no
  summary paragraphs that repeat the section just written.
- Short paragraphs. Vary sentence length. Never pad to hit a word count.
- Second person for how-to, third for narrative. Stay consistent.
- Never invent statistics, studies, prices, laws or quotations. If a number
  matters and you are not certain of it, say what it depends on instead.
"""


# ------------------------------------------------------------------ concept --

def develop_concept(seed: dict[str, Any]) -> dict[str, Any]:
    prompt = f"""Develop a publishable book concept from this brief.

Working title: {seed.get('title') or '(none yet)'}
Category: {seed.get('category')}
Audience: {seed.get('audience')}
Angle the author wants: {seed.get('angle') or '(open)'}
Target length: {seed.get('word_target')} words
Tone: {seed.get('tone')}

Return JSON with exactly these keys:
  title            - a sharp, searchable title (subtitle allowed after a colon)
  hook             - one sentence a browsing reader would stop for
  promise          - what the reader can do or feel by the last page
  reader           - who this is for, specifically, in one sentence
  not_for          - who should NOT buy it, in one sentence (this sharpens the listing)
  differentiator   - what this book does that the obvious competitors do not
  voice            - two or three adjectives describing the narrative voice
  comparable_titles- array of 3 real, well-known comparable books
  risks            - array of up to 3 things that could make this book fail
"""
    return router.text_json(ROLE_PLANNING, prompt, HOUSE_STYLE)


def market_check(concept: dict[str, Any], category: str) -> dict[str, Any]:
    prompt = f"""Assess this book concept as an acquisitions editor who has seen
the category's sales data. Be sceptical; a flattering answer is a useless one.

Concept: {json.dumps(concept, indent=2)}
Category: {category}

Return JSON with keys:
  demand_verdict   - one of "strong", "workable", "crowded", "avoid"
  reasoning        - 2-4 sentences, plain and specific
  competition      - what the incumbent books in this niche already do well
  gap              - the one gap worth aiming at, or "" if there is none
  price_band       - suggested list price range as a string, and why in the same string
  title_alternates - array of 4 alternative titles, each more searchable than clever
  keywords_seed    - array of 10 phrases a buyer would actually type into a store search
  warnings         - array of concrete risks (saturation, seasonality, policy, review-bombing)
"""
    return router.text_json(ROLE_RESEARCH, prompt, HOUSE_STYLE)


# ------------------------------------------------------------------ outline --

def build_outline(concept: dict[str, Any], word_target: int, chapters: int, fiction: bool) -> dict[str, Any]:
    kind = "novel" if fiction else "non-fiction book"
    extra = (
        "For fiction include: a one-line premise, the protagonist's want vs need, "
        "the antagonistic force, and per chapter a 'turn' field naming what changes."
        if fiction else
        "For non-fiction, every chapter must teach one thing the reader can act on. "
        "Per chapter include a 'takeaway' field: the single sentence the reader keeps."
    )
    prompt = f"""Write the chapter plan for this {kind}.

Concept: {json.dumps(concept, indent=2)}
Total target: {word_target} words across {chapters} chapters.
{extra}

Return JSON:
  premise    - one sentence
  structure  - name the shape you are using and why, in one sentence
  chapters   - array of {chapters} objects with keys:
                 number, title, purpose, beats (array of 3-6 short strings),
                 {'turn' if fiction else 'takeaway'}, word_target (integer)
The word_target values must sum to roughly {word_target}."""
    return router.text_json(ROLE_PLANNING, prompt, HOUSE_STYLE, max_tokens=8000)


# ----------------------------------------------------------------- drafting --

def draft_chapter(concept: dict, outline: dict, chapter: dict, previous_summary: str, fiction: bool) -> str:
    target = int(chapter.get("word_target") or 1800)
    beats = "\n".join(f"- {b}" for b in chapter.get("beats", []))
    anchor = chapter.get("turn") or chapter.get("takeaway") or ""
    prompt = f"""Write chapter {chapter.get('number')} in full.

BOOK
Title: {concept.get('title')}
Voice: {concept.get('voice')}
Reader: {concept.get('reader')}
Premise: {outline.get('premise', '')}

WHAT CAME BEFORE
{previous_summary or '(this is the opening chapter)'}

THIS CHAPTER
Title: {chapter.get('title')}
Purpose: {chapter.get('purpose')}
Beats:
{beats}
{'Turn' if fiction else 'Takeaway'}: {anchor}
Length: about {target} words.

Rules:
- Output Markdown. Start with "## {chapter.get('title')}" and nothing above it.
- Do not write a chapter number line, a summary box, or a "in this chapter" preamble.
- {'Scene first, exposition never. Dialogue carries the turn.' if fiction else 'Open on a concrete situation, not a definition. Use a worked example.'}
- Do not end with a recap paragraph.
"""
    return router.text(ROLE_LONGFORM, prompt, HOUSE_STYLE, max_tokens=max(4000, int(target * 2.2))).text.strip()


def summarise_for_continuity(chapter_markdown: str) -> str:
    prompt = ("Summarise what happened / what was established in this chapter in at most 90 words, "
              "as continuity notes for the writer of the next chapter. Facts only.\n\n"
              + chapter_markdown[:12000])
    return router.text(ROLE_METADATA, prompt, max_tokens=400).text.strip()


def line_edit(chapter_markdown: str, voice: str) -> str:
    prompt = f"""Line-edit this chapter. Voice to preserve: {voice}.

Do: cut filler, kill repeated openings, fix limp verbs, break up walls of text,
remove any recap paragraph at the end, fix inconsistent tense or person.
Do NOT: add new facts, change the argument, lengthen it, or add headings.

Return the edited Markdown only.

---
{chapter_markdown}"""
    return router.text(ROLE_EDITING, prompt, HOUSE_STYLE,
                       max_tokens=max(4000, len(chapter_markdown) // 2)).text.strip()


def front_matter(concept: dict, author: str, imprint: str, year: int, fiction: bool) -> str:
    prompt = f"""Write the front matter for this book as Markdown.

Title: {concept.get('title')}
Author: {author}
Imprint: {imprint}
Year: {year}
Fiction: {fiction}

Include, each as its own "## " section: Title Page, Copyright, {'' if fiction else 'A Note on How to Use This Book, '}Dedication (leave a tasteful placeholder).
The copyright page must include: copyright line, all-rights-reserved line, a
disclaimer appropriate to the genre, and a line stating the book was produced
with AI assistance. Keep the whole thing under 350 words."""
    return router.text(ROLE_MARKETING, prompt, HOUSE_STYLE, max_tokens=1500).text.strip()


def back_matter(concept: dict, author: str, imprint: str) -> str:
    prompt = f"""Write the back matter for "{concept.get('title')}" by {author} ({imprint}) as Markdown.
Sections: "## Thank You" (a short, non-grovelling review request), "## Also by {author}"
(placeholder list), "## Stay in Touch" (newsletter placeholder). Under 250 words."""
    return router.text(ROLE_MARKETING, prompt, HOUSE_STYLE, max_tokens=900).text.strip()


# ---------------------------------------------------------------- marketing --

def store_copy(concept: dict, outline: dict, category: str) -> dict[str, Any]:
    prompt = f"""Write the store listing for this book.

Concept: {json.dumps(concept, indent=2)}
Premise: {outline.get('premise', '')}
Category: {category}

Return JSON:
  blurb_html     - the sales description, 150-250 words, using only <p>, <b>, <i>,
                   <br> and <ul>/<li> tags (that is all Amazon KDP accepts)
  blurb_plain    - the same copy as plain text for stores that reject HTML
  short_pitch    - under 40 words, for social and for Gumroad's summary field
  hook_line      - the single strongest line, for an ad or a thumbnail
  seven_keywords - array of exactly 7 keyword phrases for KDP's 7 slots; each
                   under 50 characters, none repeating a word already in the title
  categories     - array of 3 suggested store categories, most specific first
  age_range      - a string, or "" if not applicable
  aplus_ideas    - array of 3 A+ content module ideas
"""
    return router.text_json(ROLE_MARKETING, prompt, HOUSE_STYLE, max_tokens=3000)


def cover_brief(concept: dict, category: str) -> dict[str, Any]:
    prompt = f"""Design brief for this book cover. It must read at thumbnail size in a
store grid, and it must look like it belongs in its category.

Concept: {json.dumps(concept, indent=2)}
Category: {category}

Return JSON:
  concept_line     - the visual idea in one sentence
  image_prompt     - a full prompt for an image model producing the BACKGROUND art
                     only: no text, no lettering, no book mockup, no hands holding
                     a book. Describe subject, composition with clear empty space
                     for the title, lighting, colour palette, and art style.
  negative_prompt  - what the image model must avoid
  stock_search_terms - array of 5 search terms for a free stock photo library, most
                     promising first. Plain concrete nouns, one or two words, the
                     kind of thing photographers actually tag: "misty forest",
                     "salt crystals", "empty desk". No abstractions, no brand names,
                     no requests for text in the image, and nothing that needs a
                     specific recognisable person.
  palette          - array of 4 hex colours
  title_color      - hex, must pass contrast against the art
  subtitle_color   - hex
  author_color     - hex
  title_case       - "upper" or "title"
  mood             - three adjectives
"""
    return router.text_json(ROLE_MARKETING, prompt, HOUSE_STYLE, max_tokens=2000)


# ------------------------------------------------------------------- utils --

def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))
