# Contributing

Thanks for looking. This is a working tool before it is a codebase, so the bar for
a change is: does it make shipping a digital product less tedious, and does it stay
honest about what it cannot do?

## Getting set up

```bash
git clone https://github.com/anuragstpl/digital-assets-studio.git
cd digital-assets-studio
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python doctor.py                 # tells you what is missing and how to get it
python run.py
```

Python 3.10–3.12. Flet 0.28.3 is pinned deliberately: the 0.80+ line is a different
API and the UI does not run on it.

## Running the tests

```bash
python tests/lint.py          # undefined names and dead code, across every file
python tests/smoke.py         # every screen, every pipeline, offline with a stub model
python tests/integration.py   # every publishing connector against mock API servers
python tests/editor.py        # what the video editor actually renders, frame by frame
```

None of them needs credentials or network access. All three must pass before a
pull request. `tests/lint.py` needs `pip install pyflakes`; it skips with a note
if that is absent locally, and CI enforces it either way.

That third suite exists because of a specific failure: an automated step that no
test happened to execute carried a missing import, every suite passed, and it only
broke when a user pressed the button. pyflakes reads every line whether it runs or
not.

`tests/smoke.py` builds every screen with a stub page and runs each pipeline against
a fake model, so a broken control or a broken step fails here rather than on a user's
first run. `tests/editor.py` renders real videos from flat colours and pure tones and then
reads them back: the average colour of a frame says which clip is on screen at
that moment, and the mean volume of a window says what is audible in it. That is
how a late cut, a clip in the wrong order, a crossfade that does not blend, a
title that was not drawn or narration under the wrong scene gets caught - none of
which shows up in an exit code. `tests/integration.py` stands up local HTTP
servers that speak YouTube,
Google Play, App Store Connect, Pexels, Pixabay and MoneyPrinterTurbo, then asserts
what the connectors send and how they parse the replies.

## How the code is arranged

```
digital_assets_studio/
  core/
    pipeline.py       the Step/Pipeline engine, gates, and the autopilot runner
    projects.py       one folder per project; the folder is the source of truth
    llm/router.py     every model call, routed by role rather than model name
    publishing/       one module per external service
  pipelines/<asset>/  one package per asset type
  ui/                 Flet views; components.py holds every shared widget
  theme.py            all design tokens; the whole app is reskinned from this file
```

## Adding a pipeline

One file. A `Pipeline` is an ordered list of `Step`s grouped into phases. A step is
either `AUTO` with a `run(project, ctx)` callable, or `MANUAL` with instructions,
links and a checklist. Declare `requires` and `produces` and the runner works out
the order, what is blocked, and what to tell the user it is waiting for.

Register it in `pipelines/__init__.py` and it appears on the home screen.

## House rules

- **Never claim an integration works when it does not.** If a platform has no API,
  say so in the step and write out what the person has to do instead. Half of this
  app's value is that it does not pretend.
- **Every model call goes through `core/llm/router.py` by role.** No pipeline names
  a model; that is a settings decision.
- **Errors must say what to do next.** "403 Forbidden" is not an error message;
  "invite the service account in Play Console → Users and permissions" is.
- **No new dependency without a good reason.** The suite runs on ffmpeg, Pillow,
  reportlab, httpx and Flet. Optional features degrade with a message rather than
  crashing when their dependency is absent.
- **Colours come from `theme.py`** and must clear WCAG AA in both themes.

## Pull requests

Small and focused beats large and sweeping. Say what you changed and why; if it
touches a pipeline, say which step and what it now produces. Add a test when you fix
a bug — every regression test in here exists because something actually broke.
