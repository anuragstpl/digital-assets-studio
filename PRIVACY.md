# Privacy

Digital Assets Studio runs on your machine. Your drafts, manuscripts, audio,
video, projects and API keys stay there. This page says exactly what does leave,
and how to stop it.

## Your keys and your work

- **API keys** go into your operating system's credential store — Windows
  Credential Manager, macOS Keychain, Secret Service on Linux. If no keychain
  exists, they fall back to an obfuscated file in your workspace and the app
  says so, loudly, in Settings. They are never sent anywhere except to the
  service they belong to.
- **Projects** — drafts, builds, images, audio, video — are plain files in your
  workspace folder. Nothing about them is uploaded anywhere unless you press a
  publish step yourself.
- **Model calls** go directly from your machine to whichever provider you
  configured. Nothing is proxied through us; there is no "us" in the request
  path at all.
- **Publishing steps** talk directly to YouTube, Google Play, App Store Connect
  and so on, using your own credentials.

## Anonymous usage analytics

The app sends a small anonymous event when it starts, when you create a project,
and when an automated step fails. It goes to [Aptabase](https://aptabase.com), a
privacy-first analytics service, and it exists so the project can tell which
releases people actually run, on which platforms, and which steps are broken in
the wild.

**Every event carries exactly this:**

| Field | Example | Why |
|---|---|---|
| `appVersion` | `0.6.0` | which releases are in use |
| `osName` / `osVersion` | `Windows` / `11` | which platforms to test |
| `deviceModel` | `AMD64` | architecture, for the right build |
| `locale` | `en-IN` | which languages matter |
| `isDebug` | `false` | separates real installs from runs from source |
| `sessionId` | random, per launch | groups one launch's events |
| `eventName` | `app_started`, `project_created`, `step_failed` | what happened |
| `props` | `{"kind": "youtube"}` or `{"step": "render"}` | which pipeline or step |

**It never carries:** your API keys, project names, titles, drafts, prompts,
scripts, file paths, file contents, error messages, your email, your IP address
in storage, your name, or anything a model wrote. Properties are filtered to
short plain scalars in code before sending, so a call site cannot leak a path or
a stack trace even by mistake.

**Location.** No location is collected by the app. Aptabase derives a country
from the request's IP address at ingest and discards the address; there is no
GPS, no city, no precise location, and no IP stored.

**There is no identifier that follows you.** The session id is random and new on
every launch. Nothing links one run of the app to another, or to you.

### Turning it off

Any one of these turns it off completely:

- **Settings › General › Analytics** — flip the switch. Takes effect at once.
- `DO_NOT_TRACK=1` in your environment — the conventional opt-out, honoured.
- `DAS_TELEMETRY=0` in your environment.
- Build from source. A source build has no analytics key compiled in and sends
  nothing at all unless you set `DAS_APTABASE_KEY` yourself.

### It cannot affect the app

Analytics runs on one background thread with a bounded queue. It never blocks
the interface, never delays startup or shutdown, and every error inside it is
swallowed. With no connection — on a plane, behind a proxy, on an air-gapped
machine — events are dropped, the sender gives up for that session after three
failures, and the app behaves identically in every other respect. This is
covered by a test that asserts it.

### A note on consent

Analytics is **on by default**, with the switch above to turn it off. If you are
distributing this app to users in the EU, the UK, or another jurisdiction with
prior-consent rules for non-essential analytics, on-by-default may not be
sufficient there and you should switch to an explicit first-run prompt. The
consent model is a one-line change in `core/telemetry.py`.

## Questions

Open an issue: https://github.com/anuragstpl/digital-assets-studio/issues
