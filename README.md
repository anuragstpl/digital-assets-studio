<div align="center">

<img src="packaging/icon_512.png" width="120" alt="Artalo Digi Suit">

# Artalo Digi Suit

**One suite. Every digital asset.**

Take a digital product from idea to published — books, printables, audiobooks,
YouTube videos, podcasts, courses and mobile apps — automating every step the
platform allows, and stopping only where a person is genuinely required.

[![tests](https://github.com/anuragstpl/digital-assets-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/anuragstpl/digital-assets-studio/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/anuragstpl/digital-assets-studio?sort=semver)](https://github.com/anuragstpl/digital-assets-studio/releases/latest)
[![licence](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
[![python](https://img.shields.io/badge/python-3.10--3.12-blue)](https://www.python.org/)

</div>

---

## Install

**Windows** — download the installer from
[Releases](https://github.com/anuragstpl/digital-assets-studio/releases/latest) and run it.
SmartScreen will say the publisher is unknown, because the build is not
code-signed: **More info → Run anyway**. A portable zip is there too if you would
rather not install anything.

**macOS** — **there is no macOS build in 0.8.0.** The DMG job fails on both Apple
silicon and Intel runners and no `.dmg` is published, so run it from source
instead (below); it works, it is just three commands rather than a download. A
DMG will come back when that build is fixed — follow
[the issues page](https://github.com/anuragstpl/digital-assets-studio/issues) if
you want to know when.

**From source** — any platform, Python 3.10–3.12:

```bash
git clone https://github.com/anuragstpl/digital-assets-studio.git
cd digital-assets-studio
python -m pip install -r requirements.txt
python run.py
```

Or let the script make a virtual environment and shortcuts for you:

```bash
bash packaging/install.sh                                   # macOS / Linux
powershell -ExecutionPolicy Bypass -File packaging\install.ps1   # Windows
```

Check the machine at any time with `python doctor.py`, and a built app with
`--selftest`.

### Optional extras

Each unlocks one feature; the app runs without them and says which are missing in
Settings.

| Extra | Unlocks | Install |
|---|---|---|
| **ffmpeg** | video render, audio mastering | `winget install Gyan.FFmpeg` · `brew install ffmpeg` |
| **edge-tts** | free voiceovers | `pip install edge-tts` |
| **Playwright** | assisted KDP publishing | `pip install playwright && playwright install chromium` |
| **pypdf, pdf2image** | single-page printables, real page previews | `pip install pypdf pdf2image` |

### Bring your own keys

The app ships with no credentials and no accounts. You supply an LLM key —
Anthropic, OpenAI, Google, DeepSeek, OpenRouter, or a local model through
Ollama or LM Studio — and whichever publishing accounts you want to use. Keys go
straight into your OS credential store; see [SECURITY.md](SECURITY.md).

## What it actually does

Seven pipelines, 98 steps, 67 of them automated.

| Pipeline | Automated | Needs you |
|---|---|---|
| **Book / e-book** | concept, market check, outline, full manuscript, line edit, front & back matter, EPUB 3, print-ready interior PDF, cover + paperback wrap (art from an image model, **a free Pexels/Pixabay photo**, or neither), listing copy, keyword slots, royalty table across six channels, store-pack zip, KDP form pre-fill | read your own book, click Amazon's Publish |
| **Printables & templates** | pack design, vector PDFs in US Letter *and* A4, single-page export, listing images from real page previews, Etsy title + 13 tags, FAQ, licence, download zip | upload to Etsy or Gumroad |
| **Audiobook** | pull chapters from a book project, rewrite as narration-ready speech, voice auditions, narration, **ACX mastering with the levels measured and proved**, chaptered M4B, retail sample, square cover | pick the voice, upload to ACX |
| **YouTube channel** | *existing channel:* reads its About text, keywords and last 25 uploads and sets the positioning from them · *new channel:* positioning, naming, About text · then both: avatar + banner, topic bank, script, adversarial fact-check, voiceover, scene art, ffmpeg render, thumbnails, metadata, **upload + thumbnail + subtitles + playlist + pinned comment** | create the Google account (new channels only), upload avatar/banner |
| **Podcast** | show concept, cover, scripted episodes with per-speaker voices, levelled audio, show notes with chapter marks, **a valid RSS feed** | host the MP3s, submit the feed once |
| **Online course** | curriculum, 16:9 slides, printable workbook, narration cut per slide, **rendered lesson videos**, sales page, launch emails, course zip | upload to your platform |
| **Mobile app** | positioning, both store listings inside every character limit, privacy policy, data-safety answer sheets, framed screenshots, feature graphic, **Play release: bundle + listing + images + rollout**, App Store metadata | Play's two console-only forms, the iOS binary |

### Free stock photos on covers

Book and audiobook covers can pull their background from Pexels or Pixabay
instead of an image model. The cover brief writes the search terms, and the photo
closest to a cover's 1:1.6 shape wins — a landscape photo cropped to a cover loses
most of the frame, which is why so many stock covers look like a detail from
someone else's photograph. The credit line is written into the back matter and
into `build/IMAGE_CREDITS.txt`.

Worth knowing before you use one: stock photos are **not exclusive**, so another
book in your category can use the same picture tomorrow. That matters for a series
and rarely matters for a first novella. Avoid photographs of recognisable people —
neither library guarantees a model release, and a face on a cover implies that
person endorses the book. Both licences let you build a cover from the image; they
do not let you sell the image itself.

### Six video engines

A YouTube project picks one, and the pipeline reshapes around it — the steps that
belong to the other five disappear:

| Engine | What it needs | What you get |
|---|---|---|
| **Designed slides** (default) | nothing but ffmpeg | typographic slides in your channel palette, a slow Ken Burns move, captions cut to the narration |
| **Stock footage** | a free Pexels or Pixabay key | the script's visual briefs turned into search terms, free clips downloaded and cut to each narration block |
| **AI video** | an OpenRouter key — the same one the text roles use | original footage generated from each scene's visual brief by Veo, Sora, Kling, Seedance, Wan, Hailuo or whatever else OpenRouter serves that day, laid under your own narration |
| **MoneyPrinterTurbo** | your own instance running (`python main.py` in its folder, API on port 8080 — *not* `webui.bat`, which is a separate Streamlit app on 8501) | the whole job handed to it, the finished file collected |
| **A video I already have** | a file, or a folder to take the newest video from | no render at all: the file is copied into the project untouched and goes straight to metadata, thumbnails and upload |
| **AI timeline editor** | nothing but ffmpeg | a real cut you can open and change: one clip per narration block, trimmed, reordered, crossfaded, titled and scored in the editor, then rendered from the timeline |

All of them produce **long-form 16:9 and vertical 9:16**. The Shorts step will
either crop the long video or, on the stock, AI and MoneyPrinterTurbo engines,
render a genuinely fresh vertical cut rather than a letterboxed crop.

**AI video is the only engine here that costs real money per video.** Video models
bill by the second, so the step buys a pool of short clips and the renderer reuses
them across the scenes rather than generating one per scene; you set how many, and
clips already on disk are never bought twice. Start at 720p with two or three clips
and look at the result before scaling it up. Settings › Publishing › AI video lists
what OpenRouter currently serves and what each model charges — the catalogue
changes often enough that a model name from last month is not worth trusting.

The **own-file** engine is for the case where you cut the video somewhere else and
only want the publishing half: it still writes the metadata, renders the thumbnails
and uploads, and it never touches or re-encodes your original.

### The video editor

A timeline editor lives in the sidebar, and it opens on any project — not only one
using the editor engine. It assembles a first cut from what the project already
has: one clip per narration block, the scene art or the footage over it, the voice
laid on top, the subtitles already timed. From there it is an editor.

- **Cut it by hand.** Trim in and out points, split at the playhead, reorder,
  duplicate, delete. Per-clip speed, volume, brightness, contrast and saturation,
  a slow push on the stills, and a cut, fade, dissolve or slide between any two
  clips. The preview shows the frame the playhead is on.
- **Or say what you want.** Type *lose the first eight seconds, dissolve between
  the scenes, and put a title on the hook* and a model answers with a list of
  operations — trim, reorder, split, speed, transition, title, music, fade — which
  are validated against the timeline before any of them is applied. It can only
  ask for edits the editor already knows how to make, every one is checked, and
  anything it invents comes back as a refusal with a reason rather than a
  corrupted edit.
- **Cut the dead air.** The silences are measured out of the audio itself, then
  the take is split into the parts where somebody is speaking, padded so a cut
  never clips the start of a word.
- **Make a Short.** Any window of the edit becomes a fresh vertical timeline —
  the same sources, re-pointed, so it costs one render and no quality.
- **Publish it.** Title and description arrive pre-filled from the metadata step;
  the editor uploads the rendered cut, sets the thumbnail and the subtitle track,
  adds it to a playlist and hands back the link. Visibility defaults to private,
  and going public asks first.

The edit is a JSON file in the project (`edit/timeline.json`), so it survives a
crash, can be diffed, and can be handed back to a model. Rendering is three ffmpeg
passes — normalise each clip, join them (a run of straight cuts is copied rather
than re-encoded), then one finishing pass for titles, subtitles, music and the
fade — and the pipeline's upload step publishes whatever the editor last rendered.

The stock engine is a native implementation of the approach
[MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) popularised
(harry0703, MIT), built on the ffmpeg and httpx this suite already requires. It
is deliberately not vendored: its stack — MoviePy, Streamlit, faster-whisper,
Azure Speech, litellm, redis — is heavier than everything else here combined, and
pinning it would make this app's install fragile. If you already run it, put its
URL in Settings › Publishing and use it as the engine instead.

### Pipelines that branch

A pipeline is not always the same list of steps. The YouTube pipeline asks at the
start whether you already have a channel; if you do, it skips naming and creation
entirely, reads the real channel over the API, and derives the positioning from
what it actually publishes rather than inventing one. Everything downstream —
topics, scripts, thumbnails, upload — is identical on both branches.

Steps declare `applies_when`, so a branch you are not on is hidden from the list,
excluded from the progress count, and never blocks a step that depends on the
*other* branch's equivalent.

**Running more than one channel?** The Google account picker at connect time
decides which channel uploads go to — the API has no per-upload switch. Link the
channel and check the name it reports before uploading.

### Two run buttons

**Run what can run** executes every automated step in dependency order and stops
the moment anything needs a decision.

**Autopilot** goes further. It also clears the *review* gates — "lock the title",
"check the outline", "pick the voice" — by answering them from what the models
already produced, and it works around anything it cannot clear rather than
halting, so a missing ffmpeg costs you the video and nothing else. It stops only
at gates that genuinely need a person: creating an account, passing a tax
interview, clicking Publish on a store. Start it, leave, come back to a finished
product waiting for its final click.

Every gate says which kind it is, so you always know whether autopilot will
handle it.

## The honest boundaries

Some things no software can automate, and the suite says so rather than pretending:

- **Creating accounts** (Google, Amazon KDP, Play Console, Apple Developer) and the
  tax and bank interviews behind them. Identity-gated on purpose.
- **Amazon KDP has no publishing API.** The suite drives a visible browser, types
  everything it already knows, and stops before Amazon's own Publish button. Your
  credentials are never typed by it and never stored by it.
- **Play's Data safety and content rating forms** have no API. The suite writes the
  exact answers into a table so the forms become a five-minute copy job.
- **Apple accepts binaries only through Transporter/altool**, which needs macOS. The
  suite writes the command; metadata and screenshots go by API.
- **YouTube avatar and banner images** cannot be set by API — Google removed that.
  The About text, keywords and country can.
- **A YouTube token is tied to one channel.** The API has no per-upload channel
  switch: the channel is chosen in the browser, on the account picker, at sign-in.
  So running several channels means signing in once per channel. Settings ›
  Publishing › YouTube lists them all, one is starred as the default, and every
  project picks between them in the *YouTube channel* box on its upload step.
- **Etsy, Gumroad and Payhip** have no usable product-creation API for digital
  downloads. The suite builds the zip and writes every field; you upload it.
- **Podcast hosting** is a file-serving problem, not an API one. The suite writes
  a valid feed; you put the MP3s somewhere public.

## Privacy and analytics

Your keys, drafts and projects stay on your machine; model and publishing calls
go straight from you to the service concerned. The app also sends a small
anonymous event on start, on creating a project, and when an automated step
fails — app version, OS, architecture, language, and the pipeline or step id.
Never your keys, project names, drafts, prompts, paths or location.

Turn it off in **Settings › General › Analytics**, or with `DO_NOT_TRACK=1` or
`DAS_TELEMETRY=0`. A build from source has no analytics key compiled in and sends
nothing at all. It runs on a background thread and cannot block or break the app;
offline, it drops events and gives up. Full detail: [PRIVACY.md](PRIVACY.md).

## Upgrading from AIpath Studio

The app was renamed in 0.4.0. On first launch it copies your old workspace —
projects, settings, saved keys — into the new location and renames the old folder
to `… (migrated)` rather than deleting it. Keys stored in the OS keychain under
the previous name are moved across the first time each one is read. Nothing is
lost and nothing is destroyed; if anything looks wrong, the old folder is still
sitting there.

## Install

```bash
python -m pip install -r requirements.txt
python run.py
```

Python 3.10–3.12. Check the machine first with:

```bash
python doctor.py
```

Optional extras, each unlocking one feature:

```bash
pip install edge-tts                       # free voiceovers, audiobooks, podcasts
pip install playwright && playwright install chromium   # assisted KDP publishing
pip install pypdf pdf2image                # single-page printables, real page previews
winget install Gyan.FFmpeg                 # video and audio rendering (Windows)
brew install ffmpeg                        # video and audio rendering (macOS)
```

### Build a standalone app

```bash
pip install flet-cli
flet pack run.py --name "Artalo Digi Suit" --add-data "digital_assets_studio/assets;assets"
```

## First run

1. **Settings › Providers** — paste the keys you have. Anthropic, OpenAI, Google,
   **DeepSeek**, OpenRouter, any local OpenAI-compatible endpoint (Ollama, LM Studio),
   plus image providers (Imagen, OpenAI images, local Stable Diffusion). Press *Test
   connection* on each.
2. **Settings › Model routing** — every job in the suite asks for a *role*, not a
   model: planning, research, long-form drafting, editing, marketing, metadata,
   scripts, image prompts, code. Point the cheap roles at a cheap or local model and
   keep the strong one for drafting. That single screen is most of your running cost.
3. **Settings › Publishing** — connect YouTube (OAuth), Google Play (service account
   JSON) and App Store Connect (.p8 key). One-time each. Connect one YouTube
   channel per channel you publish to; the starred one is what a project uses
   until it picks another.
4. **Home** — pick what you are shipping.

The app opens in a light theme on a white background. Settings › General has a dark-mode
switch if you prefer it; once you choose, the suite never overrides you.

Keys go into the OS credential store — Windows Credential Manager, macOS Keychain.
If no keychain exists, they fall into an encoded file in the workspace and the app
says so plainly, because that is obfuscation, not encryption.

## Where your work lives

```
%LOCALAPPDATA%\DigitalAssetsStudio\          (Windows)
~/Library/Application Support/Artalo Digi Suit/   (macOS)
    settings.json          providers and routing — never secrets
    projects/<slug>/
        project.json       answers and step status
        drafts/            everything the models wrote, as Markdown
        build/             EPUB, PDFs, covers, video, screenshots, zips
        notes/             yours
```

The folder is the source of truth. Delete it and the project is gone — there is no
hidden database, and you can back it up by copying one directory.

## Extending it

Adding a pipeline is one file. A `Pipeline` is an ordered list of `Step`s in phases;
each step is either `AUTO` with a `run(project, ctx)` callable, or `MANUAL` with
instructions, links and a checklist. Steps declare `requires` and `produces`, which
is what lets the runner order them, grey out what is not ready, and tell you exactly
what a blocked step is waiting for.

Every model call goes through `core/llm/router.py` by role — no pipeline ever names
a model, so re-routing costs nothing.

```bash
python tests/smoke.py         # every screen, every pipeline, offline with a stub model
python tests/integration.py   # every publishing connector against mock API servers
```

The integration suite stands up local HTTP servers that speak YouTube, Google
Play, App Store Connect, Pexels, Pixabay and MoneyPrinterTurbo, then asserts what
the connectors actually send and how they parse what comes back — resumable
upload chunking, Apple's three-step screenshot protocol, Play's atomic edit and
rollback, quota and permission errors. It needs no credentials and no network.

## Credits

- **[MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)** (harry0703,
  MIT) — the stock-footage video approach, and an optional engine the suite can
  drive over its API. Not bundled; see above.
- Footage comes from **Pexels** and **Pixabay** under their own free licences.
  Both allow commercial use without attribution, but neither allows reselling the
  clips as-is — check the licence before building a product that is mostly footage.

## Contributing

Bug reports, new asset types and new integrations are all welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md). Adding a pipeline is one file, and both test
suites run without credentials or network access.

## Licence

MIT — see [LICENSE](LICENSE). Use it, fork it, sell what you make with it.

Poppins and Lora are bundled under the SIL Open Font License 1.1 (see
`digital_assets_studio/assets/fonts/OFL.txt`) and may be used in the books,
covers and videos this app produces.

---

<div align="center">
<sub>Built because publishing a book, a channel and an app should not mean doing
the same forty steps by hand three times.</sub>
</div>
