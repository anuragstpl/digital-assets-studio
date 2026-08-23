<div align="center">

<img src="packaging/icon_512.png" width="120" alt="Digital Assets Studio">

# Digital Assets Studio

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

**macOS** — download the `.dmg` for your chip (`arm64` for Apple silicon,
`x86_64` for Intel) and drag the app to Applications. It is ad-hoc signed but not
notarised, so the first launch needs **right-click → Open → Open**.

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

Seven pipelines, 92 steps, 61 of them automated.

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

### Three video engines

A YouTube project picks one, and the pipeline reshapes around it:

| Engine | What it needs | What you get |
|---|---|---|
| **Designed slides** (default) | nothing but ffmpeg | typographic slides in your channel palette, a slow Ken Burns move, captions cut to the narration |
| **Stock footage** | a free Pexels or Pixabay key | the script's visual briefs turned into search terms, free clips downloaded and cut to each narration block |
| **MoneyPrinterTurbo** | your own instance running (`python main.py` in its folder, API on port 8080 — *not* `webui.bat`, which is a separate Streamlit app on 8501) | the whole job handed to it, the finished file collected |

All three produce **long-form 16:9 and vertical 9:16**. The Shorts step will
either crop the long video or, on the stock and MoneyPrinterTurbo engines,
render a genuinely fresh vertical cut rather than a letterboxed crop.

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
- **Etsy, Gumroad and Payhip** have no usable product-creation API for digital
  downloads. The suite builds the zip and writes every field; you upload it.
- **Podcast hosting** is a file-serving problem, not an API one. The suite writes
  a valid feed; you put the MP3s somewhere public.

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
flet pack run.py --name "Digital Assets Studio" --add-data "digital_assets_studio/assets;assets"
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
   JSON) and App Store Connect (.p8 key). One-time each.
4. **Home** — pick what you are shipping.

The app opens in a light theme on a white background. Settings › General has a dark-mode
switch if you prefer it; once you choose, the suite never overrides you.

Keys go into the OS credential store — Windows Credential Manager, macOS Keychain.
If no keychain exists, they fall into an encoded file in the workspace and the app
says so plainly, because that is obfuscation, not encryption.

## Where your work lives

```
%LOCALAPPDATA%\DigitalAssetsStudio\          (Windows)
~/Library/Application Support/Digital Assets Studio/   (macOS)
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
