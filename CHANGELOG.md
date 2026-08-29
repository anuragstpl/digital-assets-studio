# Changelog

All notable changes are recorded here. Versions follow [semantic versioning](https://semver.org).

## [0.8.0] - 2026-08-29

### Changed
- **The app is now Artalo Digi Suit.** New name, new executable, new installer,
  new workspace folder and a new credential-store entry. Nothing is left behind:
  an existing install is migrated on first run - projects and settings are copied
  across from the Digital Assets Studio folder (and from AIpath Studio before it),
  the old folder is renamed rather than deleted, and saved keys are read from
  every previous service name and moved over the first time they are used.
- The Python package is still `digital_assets_studio` and the repository URL is
  unchanged; renaming either would break every import and every existing link
  without changing anything a user sees.

## [0.7.2] - 2026-08-29

### Fixed
- **The window binary was never installed, so no packaged build could open.**
  `flet[desktop]` is an optional extra, and requirements.txt only asked for
  `flet` - which installs the framework and no view. Every build made on a clean
  machine, CI included, therefore shipped without one, and a source install on a
  clean environment had the same problem. The app starts, cannot find a view,
  relaunches itself through `sys.executable`, and repeats that forever.
- The Windows release job reported success on exactly that build, because the app
  is windowed: PowerShell does not wait for it and never sees its exit code. The
  verify step now waits, prints what the selftest said, and fails on it - and a
  second step checks the view binary is in the bundle at all.
- `packaging/das.spec` refuses to build without a view rather than producing a
  package that cannot open, and collects whichever view package is installed
  (`flet_desktop`, or `flet_desktop_light` on Linux).

**0.7.0 and 0.7.1 are affected and should not be used.** 0.7.1 also has no macOS
build: the same missing dependency failed the DMG job, which is the correct
outcome for a build that could not have opened a window.

## [0.7.1] - 2026-08-29

### Fixed
- **The packaged app could not open a window.** flet_desktop carries the Flutter
  binary that *is* the window, and it is imported lazily, so PyInstaller never
  saw it and collected none of it. A build without it does not crash: it starts,
  fails to find the view, relaunches itself through `sys.executable`, and repeats
  that forever - dozens of processes, no window, no error. Every packaged build
  is affected, including the 0.7.0 installers and DMG; install 0.7.1 instead.
- `--selftest` now checks that the view binary is actually in the bundle, so this
  class of failure fails the build rather than reaching a download page. It
  passed on the broken build precisely because it never opens a window.

## [0.7.0] - 2026-08-29

### Added
- **A video editor, with an AI that edits.** A timeline screen in the sidebar,
  open on any project: trim, split, reorder, speed, volume, colour, transitions,
  on-screen titles, a ducked music bed, burned subtitles and a fade to black,
  with a preview of the frame under the playhead. It assembles a first cut from
  the project's own narration and scenes, cuts dead air by measuring the silences,
  crops a vertical Short out of any window, and publishes the rendered edit to
  YouTube — thumbnail, subtitles, playlist and link — without going back through
  the pipeline. Plain-English direction is answered with a list of operations that
  are validated against the timeline before any is applied, so a model can only
  ask for edits the editor already knows how to make.
- **A sixth video engine, "AI timeline editor"**, which renders a YouTube episode
  from that timeline instead of straight from the media, so the cut is yours to
  change before it is published.

### Fixed
- **A per-cent sign made burned-in text disappear.** ffmpeg's drawtext reads its
  text as a template unless told otherwise, so a caption or title containing
  "50% off" or "up 12%" rendered as nothing at all - no error, no warning, just a
  missing line. Both burn-in paths now switch expansion off. This affected the
  designed-slides captions as well as the editor's titles.
- ffmpeg's output is now decoded as UTF-8 rather than by the platform default, so
  an accented character in a filename no longer raises a `UnicodeDecodeError` in
  place of the render error it was trying to report.

## [0.6.0]

### Added
- **Anonymous usage analytics**, sent to Aptabase: app version, OS, architecture,
  language, and which pipeline or failing step — nothing else. No keys, no
  project names, no drafts, no paths, no location, and no identifier that
  survives a restart. On by default with a switch in Settings › General, and
  `DO_NOT_TRACK=1` / `DAS_TELEMETRY=0` are honoured. It runs on one background
  thread and is incapable of blocking or breaking the app: with no connection it
  drops events and gives up for the session. See PRIVACY.md.
- **AI video engine.** A YouTube project can now generate its own footage through
  OpenRouter — Veo, Sora, Kling, Seedance, Wan, Hailuo and the rest behind the one
  key the text roles already use. Each scene's visual brief becomes a clip; the
  clips are pooled and reused across the scenes rather than bought one per scene,
  and clips already on disk are never bought twice. Settings › Publishing › AI
  video lists what OpenRouter currently serves.
- **Publish a video you made elsewhere.** A new engine skips rendering entirely:
  point it at a file, or at the folder your editor exports to and let it take the
  newest video. The file is copied in untouched, then metadata, thumbnails and
  upload run exactly as they do for a rendered episode.
- **File and folder pickers** on the steps that take a path — the video file, the
  export folder, the background music track — instead of typing one.
- **Several YouTube channels.** Settings › Publishing › YouTube now holds one slot
  per channel, each with its own sign-in through the same OAuth client, one of
  them starred as the default. The upload and Shorts steps have a *YouTube channel*
  box, and both name the channel they published to in their result.

### Changed
- An upload with no channel chosen now goes to the channel you starred, not to
  whichever one happened to sign in first, and it refuses rather than guess when
  several are connected and the starred one is gone.

### Fixed
- **A run now repaints while it runs.** Steps execute on a worker thread and
  nothing told the screen when one started or finished, so a long run sat on its
  old statuses and only caught up at the end. Rows now go to *running* live, the
  progress count climbs, the activity log streams, and the selection follows the
  step being worked on.
- The step list is rebuilt when a run changes which steps apply, instead of only
  restyling rows that may no longer exist.
- Re-reading a YouTube channel no longer moves it to the bottom of the list in
  Settings.
- A video under 1 MB no longer reports as "0 MB".
- **The test suites no longer touch the real OS credential store.** They read it
  — so results depended on whoever ran them — and wrote fixtures over it, which
  destroyed live keys. Both suites now run against an isolated in-process store
  (`DAS_KEYVAULT=memory`). This also fixes a test that could open a real Google
  sign-in on the developer's own account.

## [0.5.0] — first public release

Seven pipelines, 92 steps, 61 of them automated.

### Assets it produces
- **Books** — concept, market check, outline, manuscript, line edit, EPUB 3, print
  interior PDF, cover and paperback wrap, listing copy, royalty table, store pack
- **Printables** — vector PDFs in US Letter and A4, listing images, Etsy tags, licence
- **Audiobooks** — narration-ready text, TTS, ACX-compliant mastering, chaptered M4B
- **YouTube** — positioning, scripts with an adversarial fact-check, three video
  engines, thumbnails, and API upload with subtitles, playlist and pinned comment
- **Podcasts** — show design, scripted episodes, levelled audio, valid RSS feed
- **Courses** — curriculum, slides, workbook, narrated lesson videos, sales page
- **Mobile apps** — store listings, privacy pack, framed screenshots, Play release

### Automation
- Two run modes: *Run what can run*, and *Autopilot* which also clears review gates
- Real API integrations: YouTube Data v3, Google Play Developer v3, App Store Connect
- Three YouTube video engines: designed slides, Pexels/Pixabay stock footage, or a
  self-hosted MoneyPrinterTurbo instance

### Quality
- 21 offline checks and 25 integration checks against mock API servers
- Every colour clears WCAG AA contrast in both themes
