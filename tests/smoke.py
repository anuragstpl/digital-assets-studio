"""Headless smoke test: build every screen and run the offline half of every
pipeline with a stubbed model, so a broken control or a broken builder shows up
here rather than on the user's first run."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

WORK = tempfile.mkdtemp(prefix="aipath-smoke-")
os.environ["DAS_HOME"] = WORK
# never the real OS credential store: these suites both read keys (which
# makes results depend on whoever is running them) and write fixtures over
# them, which destroys live keys
os.environ["DAS_KEYVAULT"] = "memory"
# and never the real analytics dashboard: a key is baked into config.py for
# release builds, and a test run must not show up as real usage
os.environ["DAS_TELEMETRY"] = "0"
os.environ["DAS_STRICT_RENDER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import flet as ft  # noqa: E402

from digital_assets_studio.config import ensure_dirs  # noqa: E402
from digital_assets_studio.core import projects as pj  # noqa: E402
from digital_assets_studio.core.jobs import JobContext  # noqa: E402
from digital_assets_studio.core.projects import Project  # noqa: E402
from digital_assets_studio.core.llm import router  # noqa: E402
from digital_assets_studio.core.llm.base import Completion  # noqa: E402
from digital_assets_studio.pipelines import PIPELINES, get as get_pipeline  # noqa: E402

FAILURES: list[str] = []


def check(name: str, fn):
    try:
        fn()
        print(f"  PASS  {name}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{name}: {exc}")
        print(f"  FAIL  {name}: {exc}")
        traceback.print_exc(limit=4)


# --------------------------------------------------------------- fake model --

FAKE = {
    "title": "The Salt Ledger", "hook": "A hook.", "promise": "A promise.",
    "reader": "A reader.", "not_for": "Not for.", "differentiator": "Different.",
    "voice": "warm, plain", "comparable_titles": ["A", "B", "C"], "risks": ["r1"],
    "demand_verdict": "workable", "reasoning": "Because.", "competition": "They do X.",
    "gap": "A gap.", "price_band": "$4.99-$7.99", "title_alternates": ["T1", "T2", "T3", "T4"],
    "keywords_seed": ["k1", "k2", "k3", "k4", "k5", "k6", "k7"], "warnings": ["w1"],
    "premise": "A premise.", "structure": "Three acts.",
    "chapters": [{"number": i, "title": f"Chapter {i}", "purpose": "p",
                  "beats": ["b1", "b2"], "takeaway": "t", "turn": "t",
                  "word_target": 900} for i in range(1, 4)],
    "blurb_html": "<p>Blurb</p>", "blurb_plain": "Blurb", "short_pitch": "Pitch",
    "hook_line": "Hook", "seven_keywords": [f"kw{i}" for i in range(7)],
    "categories": ["c1", "c2", "c3"], "age_range": "", "aplus_ideas": ["a1"],
    "concept_line": "A line.", "image_prompt": "A prompt.", "negative_prompt": "no text",
    "palette": ["#101322", "#25305C", "#C9A227", "#F2EFE6"],
    "title_color": "#F7F3EA", "subtitle_color": "#D9CFAF", "author_color": "#F7F3EA",
    "title_case": "upper", "mood": "dark, tense, warm",
    "stock_search_terms": ["salt crystals", "misty forest", "old ledger"],
    # printables
    "product_title": "The Test Kit", "bonus_ideas": ["b1"], "what_not_to_add": ["w1"],
    "pages": [{"type": "cover", "heading": "The Test Kit", "subtitle": "sub"},
              {"type": "monthly", "heading": "Month"},
              {"type": "weekly", "heading": "Week"},
              {"type": "daily", "heading": "Day"},
              {"type": "habits", "heading": "Habits"},
              {"type": "checklist", "heading": "Do this", "items": ["one", "two", "three"]},
              {"type": "worksheet", "heading": "Think", "prompts": ["Why?", "What next?"]},
              {"type": "notes", "heading": "Notes", "ruling": "dotted"}],
    "etsy_title": "Test Kit Printable Planner",
    "etsy_tags": [f"tag{i}" for i in range(13)],
    "etsy_description": "A description.", "gumroad_summary": "Short.",
    "materials": ["PDF"], "faq": [{"question": "q", "answer": "a"}],
    "licence_text": "Personal use only.",
    # course
    "modules": [{"title": "Module one", "why_it_matters": "because",
                 "lessons": [{"title": "Lesson one", "outcome": "do a thing",
                              "slides": [{"heading": "Point", "bullets": ["a", "b", "c"]},
                                         {"heading": "Second", "bullets": ["d", "e"]}],
                              "script": "First paragraph of narration.\n\nSecond paragraph.\n\nThird.",
                              "exercise_prompts": ["What will you do?", "By when?"],
                              "minutes": 12}]}],
    "not_covered": ["x"], "who_should_skip": "nobody",
    "headline": "Do the thing", "subhead": "Really do it",
    "who_for": ["a"], "who_not_for": ["b"], "curriculum_blurb": "You will build a thing.",
    "objections": [{"objection": "o", "answer": "a"}], "guarantee": "30 days.",
    "udemy_title": "Do The Thing", "udemy_subtitle": "A subtitle",
    "udemy_description": "<p>Hi</p>", "email_sequence": [{"subject": "s", "body": "b"}],
    # podcast
    "show_names": ["Show One"], "recommended": "Show One — because",
    "tagline": "A tagline", "segments": ["Cold open", "Main"],
    "first_8_episodes": [{"title": "Ep one", "hook": "h", "why_now": "n"}],
    "cold_open": "Here is the hook.",
    "blocks": [{"heading": "Part one", "speaker": "host", "text": "Words.", "seconds": 60}],
    "ad_slots": [0], "outro": "Thanks for listening.",
    "chapters_notes": [], "takeaways": ["t"], "links": [{"label": "l", "note": "n"}],
    "social_posts": ["post"], "summary": "A summary.",
    # audiobook
    "opening": "Title, by Author, narrated by a synthesised voice.",
    "closing": "You have been listening to Title.",
}


def fake_text(role, user, system="", **kw):
    return Completion("## Chapter\n\nA paragraph of prose that stands in for real drafting. "
                      "It is long enough to typeset and short enough to be quick.\n\n"
                      "- a bullet\n- another\n", model="fake", input_tokens=10, output_tokens=20)


def fake_json(role, user, system="", **kw):
    return dict(FAKE)


def fake_image(prompt, count=1, size="1024x1024", timeout=300.0):
    raise RuntimeError("no image provider configured (expected in the smoke test)")


router.text = fake_text
router.text_json = fake_json
router.image = fake_image

import digital_assets_studio.pipelines.books.writing as bw  # noqa: E402

bw.router = router


# --------------------------------------------------------------------- UI ----

class StubPage:
    def __init__(self):
        self.controls = []
        self.theme = None
        self.dark_theme = None
        self.theme_mode = None
        self.bgcolor = None
        self.window = type("W", (), {"width": 0, "height": 0})()

    def add(self, *c):
        self.controls.extend(c)

    def update(self):
        pass

    def open(self, x):
        pass

    def close(self, x):
        pass

    def launch_url(self, u):
        pass


def test_views():
    from digital_assets_studio.ui.studio import Studio

    ensure_dirs()
    page = StubPage()
    studio = Studio(page)
    for route in ("home", "projects", "activity", "settings"):
        studio.route = route
        studio.refresh()
        assert page.controls, f"{route} produced nothing"
    for pipeline in PIPELINES:
        studio.start_new(pipeline.id)
        studio.refresh()
    proj = pj.create("Smoke book", "book", {"category": "Romance / romantasy",
                                            "audience": "readers", "word_target": 2700,
                                            "tone": "Warm and plain"})
    studio.open_project(proj.id)
    pl = get_pipeline("book")
    for step in pl.steps:
        studio.select_step(step.id)
    for pid in ("youtube", "mobile"):
        p2 = pj.create(f"Smoke {pid}", pid, {"topic": "t", "audience": "a", "app_name": "App",
                                             "what_it_does": "does", "language": "English"})
        studio.open_project(p2.id)
        for step in get_pipeline(pid).steps:
            studio.select_step(step.id)


def test_book_pipeline():
    from digital_assets_studio.core import pipeline as pipe

    proj = pj.create("Offline book", "book", {
        "category": "Romance / romantasy", "audience": "romantasy readers",
        "word_target": 2700, "tone": "Slow burn, atmospheric",
        "pen_name": "Test Author", "imprint": "Test Press", "trim": "6 x 9 in",
        "final_title": "The Salt Ledger", "chapter_count": 3})
    proj.ensure_dirs()
    pl = get_pipeline("book")
    ctx = JobContext("smoke", "smoke")
    order = ["concept", "market", "outline", "draft", "matter", "listing",
             "cover_brief", "epub", "interior", "cover_build", "pricing", "pack"]
    for sid in order:
        step = pl.step(sid)
        for req in step.requires:
            if not proj.is_done(req):
                pipe.mark_manual_done(proj, pl.step(req))
        pipe.execute(pl, proj, step, ctx)
    for rel in ("build/book.epub", "build/interior_print.pdf", "build/cover_front.jpg",
                "build/cover_wrap.pdf", "build/store_pack.zip", "drafts/pricing.md"):
        assert proj.exists(rel), f"missing artifact {rel}"
    import zipfile
    with zipfile.ZipFile(proj.dir / "build" / "book.epub") as z:
        assert z.namelist()[0] == "mimetype"
        import xml.dom.minidom as md
        md.parseString(z.read("OEBPS/content.opf"))
    print(f"        built {proj.answer('page_count')} print pages, "
          f"{proj.answer('word_count')} words, epub {proj.answer('epub_mb')} MB")


def test_youtube_offline():
    from digital_assets_studio.core import pipeline as pipe

    proj = pj.create("Offline channel", "youtube", {
        "topic": "solving real problems with AI", "audience": "small business owners",
        "language": "English", "format": "Long-form plus a Short from each",
        "theme": "Light and warm", "channel_name": "AI Ideas", "handle": "@AIIdeasSolved"})
    proj.ensure_dirs()
    pl = get_pipeline("youtube")
    ctx = JobContext("smoke", "smoke")
    for sid in ("positioning", "brand", "art"):
        step = pl.step(sid)
        for req in step.requires:
            if not proj.is_done(req):
                pipe.mark_manual_done(proj, pl.step(req))
        pipe.execute(pl, proj, step, ctx)
    assert proj.exists("build/banner.png") and proj.exists("build/avatar.png")


def test_mobile_offline():
    from digital_assets_studio.core import pipeline as pipe

    proj = pj.create("Offline app", "mobile", {
        "app_name": "Test App", "package": "com.test.app", "what_it_does": "does things",
        "audience": "people", "platforms": "Android and iOS", "model": "Free",
        "data_collected": "email, name", "third_parties": "none", "ads": "No ads",
        "analytics": "None", "delete_url": "https://example.com/delete"})
    proj.ensure_dirs()
    pl = get_pipeline("mobile")
    ctx = JobContext("smoke", "smoke")
    for sid in ("positioning", "listing", "privacy", "screenshots"):
        step = pl.step(sid)
        for req in step.requires:
            if not proj.is_done(req):
                pipe.mark_manual_done(proj, pl.step(req))
        pipe.execute(pl, proj, step, ctx)
    assert proj.exists("build/feature_graphic.png")
    assert len(list((proj.dir / "build" / "screenshots" / "play").glob("*.png"))) >= 1


def test_run_all():
    from digital_assets_studio.core import pipeline as pipe

    proj = pj.create("Auto run", "book", {
        "category": "Business how-to", "audience": "founders", "word_target": 2000,
        "tone": "Punchy and direct", "final_title": "Ship It"})
    proj.ensure_dirs()
    pl = get_pipeline("book")
    out = pipe.run_all(pl, proj, JobContext("smoke", "smoke"))
    assert out["ran"], "run_all did nothing"
    assert out["waiting_on"] is not None, "run_all should stop at the first human gate"
    print(f"        ran {len(out['ran'])} steps, stopped at: {out['waiting_on'].title}")


def test_autopilot():
    """One press should clear the review gates and stop only at a real one."""
    from digital_assets_studio.core import pipeline as pipe

    proj = pj.create("Autopilot book", "book", {
        "category": "Romance / romantasy", "audience": "romantasy readers",
        "word_target": 2400, "tone": "Slow burn, atmospheric",
        "pen_name": "Test Author", "imprint": "Test Press", "chapter_count": 3})
    proj.ensure_dirs()
    pl = get_pipeline("book")
    out = pipe.run_all(pl, proj, JobContext("smoke", "smoke"), autopilot=True, include_optional=True)
    gate = out["waiting_on"]
    assert gate is not None, "autopilot should still stop at an external gate"
    assert gate.gate == pipe.EXTERNAL, f"stopped at a review gate: {gate.id}"
    for sid in ("concept", "market", "lock", "outline", "review_outline", "draft",
                "read", "matter", "epub", "interior", "cover_build", "pricing", "pack"):
        assert proj.is_done(sid), f"autopilot skipped {sid}"
    for rel in ("build/book.epub", "build/interior_print.pdf", "build/cover_front.jpg",
                "build/store_pack.zip"):
        assert proj.exists(rel), f"autopilot produced no {rel}"
    print(f"        {len(out['ran'])} run, {len(out['approved'])} gates approved, "
          f"stopped at: {gate.title} ({gate.gate})")


def test_no_pipeline_stops_before_a_deliverable():
    """Every pipeline must reach its build phase on autopilot alone."""
    from digital_assets_studio.core import pipeline as pipe

    for pid, answers in (
        ("youtube", {"topic": "AI for shops", "audience": "owners", "language": "English",
                     "format": "Shorts only", "theme": "Light and warm",
                     "mode": "Create a new channel"}),
        ("mobile", {"app_name": "T", "package": "com.t.t", "what_it_does": "x", "audience": "y",
                    "platforms": "Android only", "model": "Free", "data_collected": "email",
                    "third_parties": "none", "ads": "No ads", "analytics": "None",
                    "delete_url": "https://e.com/d"}),
    ):
        proj = pj.create(f"Auto {pid}", pid, answers)
        proj.ensure_dirs()
        out = pipe.run_all(get_pipeline(pid), proj, JobContext("smoke", "smoke"),
                           autopilot=True, include_optional=False)
        gate = out["waiting_on"]
        assert gate is None or gate.gate == pipe.EXTERNAL, f"{pid} stopped at {gate.id}"
        print(f"        {pid}: {len(out['ran'])} run, "
              f"stopped at {gate.title if gate else 'nothing'}")

    # an existing-channel project with no credentials must fail loudly at the link
    # step and must not claim it is waiting on a review gate it cannot reach
    proj = pj.create("Existing no creds", "youtube", {
        "topic": "t", "audience": "a", "language": "English", "format": "Shorts only",
        "theme": "Light and warm", "mode": "Use a channel I already have"})
    proj.ensure_dirs()
    out = pipe.run_all(get_pipeline("youtube"), proj, JobContext("smoke", "smoke"),
                       autopilot=True)
    assert out["failed"], "linking with no credentials should have failed"
    assert out["waiting_on"] is None or out["waiting_on"].gate == pipe.EXTERNAL
    print(f"        existing-channel with no credentials: "
          f"reported {len(out['failed'])} failure(s), no phantom gate")


def _run_steps(pid: str, answers: dict, step_ids: list[str], name: str = "") -> Project:
    from digital_assets_studio.core import pipeline as pipe

    proj = pj.create(name or f"Offline {pid}", pid, answers)
    proj.ensure_dirs()
    pl = get_pipeline(pid)
    ctx = JobContext("smoke", "smoke")
    for sid in step_ids:
        step = pl.step(sid)
        assert step is not None, f"{pid} has no step {sid}"
        for req in step.requires:
            if not proj.is_done(req):
                pipe.mark_manual_done(proj, pl.step(req))
        pipe.execute(pl, proj, step, ctx)
    return proj


def test_selecting_a_step_keeps_the_list():
    """Regression: rebuilding the step list resets its scroll, so clicking a step
    low in a 22-step pipeline threw the user back to the top."""
    from digital_assets_studio.ui.studio import Studio

    page = StubPage()
    studio = Studio(page)
    proj = pj.create("Scroll test", "book", {"category": "Fantasy", "audience": "a",
                                             "word_target": 2000, "tone": "Literary"})
    studio.open_project(proj.id)
    pl = get_pipeline("book")

    list_control = studio.pane_list.content
    rows_before = dict(studio.step_rows)
    detail_before = studio.pane_detail.content

    studio.select_step(pl.steps[-1].id)

    assert studio.pane_list.content is list_control, "the step list was rebuilt"
    assert studio.step_rows[pl.steps[0].id] is rows_before[pl.steps[0].id], "rows were recreated"
    assert studio.pane_detail.content is not detail_before, "the detail pane did not change"

    last = studio.step_rows[pl.steps[-1].id]
    first = studio.step_rows[pl.steps[0].id]
    assert last["container"].bgcolor is not None, "the selected row is not highlighted"
    assert first["container"].bgcolor is None, "the old selection stayed highlighted"

    # a status change must still show up without rebuilding.
    # Note studio.project is its own reload from disk, not the object above.
    from digital_assets_studio.core import pipeline as pipe
    pipe.mark_manual_done(studio.project, pl.step("lock"))
    studio.update_panes(header=True)
    assert studio.pane_list.content is list_control, "marking done rebuilt the list"
    assert studio.step_rows["lock"]["status"].value == "done"
    print("        list preserved across selection, status changes still propagate")


def test_youtube_branches():
    """An existing channel must skip the naming and creation steps, and neither
    branch may leave a step blocked by the other branch's prerequisites."""
    from digital_assets_studio.core import pipeline as pipe

    pl = get_pipeline("youtube")
    base = {"topic": "AI for shops", "audience": "owners", "language": "English",
            "format": "Shorts only", "theme": "Light and warm"}

    existing = pj.create("Existing channel", "youtube",
                         {**base, "mode": "Use a channel I already have"})
    new = pj.create("New channel", "youtube", {**base, "mode": "Create a new channel"})

    ex_ids = [s.id for s in pl.active_steps(existing)]
    new_ids = [s.id for s in pl.active_steps(new)]
    for gone in ("positioning", "brand", "choose_name", "create_channel"):
        assert gone not in ex_ids, f"{gone} should not apply to an existing channel"
        assert gone in new_ids, f"{gone} should apply to a new channel"
    for present in ("channel_link", "analyse"):
        assert present in ex_ids, f"{present} missing from the existing-channel branch"
        assert present not in new_ids, f"{present} should not apply to a new channel"

    # cross-branch prerequisites must not block anything
    art = pl.step("art")
    assert "channel_link" in pl.blocked(existing, art)
    assert "choose_name" not in pl.blocked(existing, art), "blocked by the other branch"
    assert "choose_name" in pl.blocked(new, art)
    assert "channel_link" not in pl.blocked(new, art), "blocked by the other branch"

    topics = pl.step("topics")
    assert pl.blocked(existing, topics) == ["analyse"]
    assert pl.blocked(new, topics) == ["positioning"]

    # progress counts only the branch you are on
    assert pl.progress(existing)[1] < pl.progress(new)[1]
    print(f"        existing: {len(ex_ids)} steps, new: {len(new_ids)} steps, "
          f"no cross-branch blocking")


def test_public_api_surface():
    """Every symbol the pipelines reach for must exist.

    An edit once deleted google_auth.oauth_installed_app and nothing failed
    loudly - the pipeline swallowed it as a step error. This is the cheap guard
    against that whole class of mistake."""
    import importlib

    expected = {
        "digital_assets_studio.core.publishing.google_auth":
            ["oauth_installed_app", "refresh_access_token", "ServiceAccount", "TokenStore",
             "YOUTUBE_SCOPES", "PLAY_SCOPE"],
        "digital_assets_studio.core.publishing.youtube":
            ["connect", "connected", "token", "my_channel", "channel_summary", "recent_uploads",
             "upload_video", "set_thumbnail", "upload_caption", "ensure_playlist",
             "add_to_playlist", "post_comment", "CATEGORIES", "STORE",
             "accounts", "get_account", "connected_accounts", "add_account", "remove_account",
             "default_slug", "set_default", "refresh_account", "resolve", "channel_choices",
             "save_client", "oauth_client", "disconnect", "Account"],
        "digital_assets_studio.core.publishing.aivideo":
            ["has_key", "list_models", "video_models", "test", "create_video", "poll_video",
             "generate_video", "gather", "download", "AIVideoError", "DEFAULT_VIDEO_MODEL",
             "RESOLUTIONS"],
        "digital_assets_studio.core.publishing.play":
            ["save_service_account", "connected", "publish", "check_access", "Edit", "TRACKS"],
        "digital_assets_studio.core.publishing.appstore":
            ["save_credentials", "connected", "list_apps", "latest_version", "create_version",
             "update_localization", "upload_screenshot", "submit_for_review"],
        "digital_assets_studio.core.publishing.tts":
            ["synthesize", "synthesize_scenes", "write_srt", "ffprobe_duration", "ENGINES",
             "EDGE_VOICES", "OPENAI_VOICES", "edge_available"],
        "digital_assets_studio.core.publishing.video":
            ["render", "cut_short", "probe", "available", "Scene", "caption_filter", "ffpath",
             "LANDSCAPE", "PORTRAIT"],
        "digital_assets_studio.core.publishing.audio":
            ["master", "measure", "concat", "make_m4b", "retail_sample", "rss_feed", "duration"],
        "digital_assets_studio.core.publishing.stockvideo":
            ["search", "gather", "compose", "assign_clips", "Scene", "search_photos",
             "best_photo", "credit_line", "has_key", "save_key", "test_source", "SOURCES"],
        "digital_assets_studio.core.publishing.mpt":
            ["generate", "ping", "configured", "base_url", "save_base_url", "build_params"],
        "digital_assets_studio.core.publishing.browser":
            ["available", "kdp_prefill", "Session", "INSTALL_HINT"],
        "digital_assets_studio.core.editor.timeline":
            ["Timeline", "Clip", "Text", "Audio", "load", "kind_for", "clamp",
             "CUT", "FADE", "DISSOLVE", "SLIDE", "TRANSITIONS", "VIDEO", "IMAGE", "COLOUR",
             "VOICE", "MUSIC", "POSITIONS", "LANDSCAPE", "PORTRAIT"],
        "digital_assets_studio.core.editor.render":
            ["render", "plan", "available", "preview_frame", "frame", "segment_job",
             "join_job", "finish_job", "text_filters", "caption_filter", "voice_tracks",
             "audio_map", "atempo_chain", "ffpath", "ffcolour", "Job", "RenderError"],
        "digital_assets_studio.core.editor.analyze":
            ["probe", "duration", "silences", "scene_cuts", "keep_spans", "parse_silences",
             "parse_scene_cuts", "available", "Media"],
        "digital_assets_studio.core.editor.ai":
            ["assemble", "sources", "describe", "apply_ops", "plan_edit", "auto_edit",
             "suggest_titles", "cut_dead_air", "silence_cuts", "crop", "highlight_start",
             "OPS_HELP", "EditorError"],
        "digital_assets_studio.core.editor.publish":
            ["publish", "default_metadata", "PublishError"],
    }
    missing = []
    for module_name, names in expected.items():
        module = importlib.import_module(module_name)
        for name in names:
            if not hasattr(module, name):
                missing.append(f"{module_name}.{name}")
    assert not missing, "missing public symbols: " + ", ".join(missing)
    print(f"        {sum(len(v) for v in expected.values())} public symbols across "
          f"{len(expected)} modules")


def test_oauth_callback():
    """Regression: the browser also requests /favicon.ico from the callback
    server. That request carries no OAuth state, and treating it as a forged
    callback threw away sign-ins that had actually succeeded."""
    import secrets
    import threading
    import time as _time
    from http.server import HTTPServer

    import httpx

    from digital_assets_studio.core.publishing import google_auth as ga

    def run(paths):
        ga._CallbackHandler.code = None
        ga._CallbackHandler.error = None
        ga._CallbackHandler.state = secrets.token_urlsafe(8)
        server = HTTPServer(("127.0.0.1", 0), ga._CallbackHandler)
        port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def fire():
            _time.sleep(0.15)
            for path in paths:
                try:
                    httpx.get(f"http://127.0.0.1:{port}"
                              f"{path.format(state=ga._CallbackHandler.state)}", timeout=5)
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=fire, daemon=True).start()
        out = ga._await_callback(server, timeout=6)
        server.shutdown()
        return out

    good = "/?code=GOODCODE&state={state}"
    for label, paths in (("callback then favicon", [good, "/favicon.ico"]),
                         ("favicon then callback", ["/favicon.ico", good]),
                         ("callback then a reload", [good, good])):
        code, error = run(paths)
        assert code == "GOODCODE", f"{label}: lost the code"
        assert not error, f"{label}: reported {error!r} despite succeeding"

    code, error = run(["/?code=EVIL&state=wrong"])
    assert code is None and "state" in (error or ""), "a forged callback must still be rejected"

    code, error = run(["/?error=access_denied&state={state}"])
    assert code is None and error == "access_denied", "a denied consent must surface"
    print("        favicon, reloads, forged state and denied consent all handled")


def test_rename_migration():
    """The rename must not cost anyone their projects or settings."""
    import shutil as sh

    import digital_assets_studio.config as cfg

    root = Path(tempfile.mkdtemp(prefix="rename-"))
    old, new = root / "aipath-studio", root / "digital-assets-studio"
    (old / "projects" / "my-book").mkdir(parents=True)
    (old / "projects" / "my-book" / "project.json").write_text(
        json.dumps({"id": "my-book", "name": "My Book", "kind": "book"}), encoding="utf-8")
    (old / "settings.json").write_text('{"dark_mode": false}', encoding="utf-8")

    saved = (cfg.WORKSPACE, cfg.PROJECTS_DIR, cfg.CACHE_DIR, cfg._legacy_workspaces)
    try:
        cfg.WORKSPACE, cfg.PROJECTS_DIR, cfg.CACHE_DIR = new, new / "projects", new / "cache"
        cfg._legacy_workspaces = lambda: [old]
        assert cfg.migrate_legacy_workspace() == old
        assert (new / "projects" / "my-book" / "project.json").exists(), "projects were lost"
        assert (new / "settings.json").read_text(encoding="utf-8") == '{"dark_mode": false}'
        assert not old.exists() and (root / "aipath-studio (migrated)").exists(), \
            "the old folder should be kept, renamed - never deleted"
        # a second run must not clobber the migrated data
        (new / "projects" / "later").mkdir()
        assert cfg.migrate_legacy_workspace() is None, "migration should only happen once"
        assert (new / "projects" / "later").exists()
    finally:
        cfg.WORKSPACE, cfg.PROJECTS_DIR, cfg.CACHE_DIR, cfg._legacy_workspaces = saved
        sh.rmtree(root, ignore_errors=True)

    # every name the app has shipped under has to keep resolving, or a rename
    # quietly costs people their projects and their saved keys
    names = [p.name for p in cfg._legacy_workspaces()]
    expected = ["digital-assets-studio", "aipath-studio"]      # the XDG names, always
    if sys.platform == "win32":
        expected += ["DigitalAssetsStudio", "AIpathStudio"]
    elif sys.platform == "darwin":
        expected += ["Digital Assets Studio", "AIpath Studio"]
    for gone in expected:
        assert gone in names, f"{gone} is no longer migrated: {names}"

    from digital_assets_studio.core import keyvault
    assert keyvault.SERVICE == "ArtaloDigiSuit", keyvault.SERVICE
    for legacy in ("DigitalAssetsStudio", "AIpathStudio"):
        assert legacy in keyvault.LEGACY_SERVICES, \
            f"keys saved under {legacy} must still be readable"
    print(f"        projects, settings and keys survive; {len(names)} old workspaces "
          f"still migrate")


def test_cover_art_sources():
    """Three sources, and the two that can fail must degrade to the typographic
    cover rather than killing the run."""
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.pipelines.books.pipeline import AI_ART, NO_ART, STOCK_ART

    pl = get_pipeline("book")
    for source, expect in ((NO_ART, "Skipped by choice"),
                           (STOCK_ART, "No stock photo"),
                           (AI_ART, "No AI art")):
        proj = pj.create(f"Cover {source[:9]}", "book", {
            "category": "Fantasy", "audience": "a", "word_target": 1200,
            "tone": "Literary", "final_title": "T", "art_source": source})
        proj.ensure_dirs()
        pipe.mark_manual_done(proj, pl.step("lock"))
        pipe.execute(pl, proj, pl.step("concept"), JobContext("smoke", "smoke"))
        pipe.execute(pl, proj, pl.step("cover_brief"), JobContext("smoke", "smoke"))
        result = pipe.execute(pl, proj, pl.step("cover_art"), JobContext("smoke", "smoke"))
        assert expect in result.message, f"{source}: unexpected message {result.message!r}"
        assert proj.status("cover_art") == "done", f"{source} should not fail the step"
    brief = json.loads(proj.read_text("drafts/cover_brief.json", "{}"))
    assert brief.get("stock_search_terms"), "the cover brief must supply stock search terms"
    print("        AI / stock / none all degrade cleanly to the typographic cover")


def test_stock_photo_ranking():
    """A cover is 1:1.6, so the photo closest to that shape must win."""
    from digital_assets_studio.core.publishing.stockvideo import Photo, credit_line

    shots = [Photo("wide", 3000, 1500, "pexels"), Photo("tall", 1200, 1920, "pexels"),
             Photo("square", 2000, 2000, "pexels")]
    target = 1 / 1.6
    shots.sort(key=lambda p: abs(p.ratio - target))
    assert shots[0].url == "tall", "portrait photo should rank first for a book cover"
    assert "Pexels" in credit_line(Photo("u", 1, 1, "pexels", "Jane"))


def test_file_and_channel_fields():
    """The two field types the video work added.

    A file field must stay typeable when the page cannot open a dialog - a
    headless build, a locked-down desktop - and a select whose options come from
    the outside world must still draw when that world is empty, or the whole
    upload step goes blank instead of one box."""
    from digital_assets_studio.core.pipeline import Field_
    from digital_assets_studio.ui.components import build_field
    from digital_assets_studio.ui.studio import Studio
    from digital_assets_studio.theme import palette

    p = palette(False)
    seen = {}

    row = build_field(p, Field_("source_video", "Video file", "file",
                                extensions=["mp4"]), "", lambda k, v: seen.__setitem__(k, v),
                      browse=None)
    assert len(row.controls) == 2, "a file field needs its Browse button"
    assert row.controls[1].on_click is None, "Browse must be inert with no picker"

    picked = build_field(p, Field_("source_folder", "Folder", "folder"), "",
                         lambda k, v: seen.__setitem__(k, v),
                         browse=lambda f, cb: cb("D:/exports"))
    picked.controls[1].on_click(None)
    assert seen["source_folder"] == "D:/exports", seen

    empty = build_field(p, Field_("yt_channel", "YouTube channel", "select",
                                  options=["(no channel connected yet)"],
                                  options_fn=lambda: []), "", lambda k, v: None)
    assert [o.key for o in empty.options] == ["(no channel connected yet)"],         "an empty live list must fall back to the static options"

    live = build_field(p, Field_("yt_channel", "YouTube channel", "select",
                                 options=["fallback"],
                                 options_fn=lambda: ["Main (@main)", "Side (@side)"]),
                       "Side (@side)", lambda k, v: None)
    assert live.value == "Side (@side)", live.value

    angry = Field_("yt_channel", "YouTube channel", "select", options=["fallback"],
                   options_fn=lambda: (_ for _ in ()).throw(RuntimeError("network down")))
    assert angry.choices() == ["fallback"], "a failing options_fn must not break the form"

    # the picker is lazy, and a page without an overlay must not crash the screen
    studio = Studio(StubPage())
    studio.browse_for(Field_("source_video", "Video file", "file"), lambda path: None)
    assert studio._picker is None, "no picker should have been mounted on a stub page"
    print("        file and folder browsing, and a channel list that may be empty")


def test_a_run_reports_progress_live():
    """A run must repaint as it goes, not only when it finishes.

    Steps execute on a worker thread. Before this, nothing told the screen that a
    step had started or ended, so a long run sat on its old statuses looking
    frozen and then jumped to the finished state - which reads as the app having
    done nothing until you navigate away and back.
    """
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.events import BUS, TOPIC_STEP
    from digital_assets_studio.ui.studio import Studio

    seen = []
    off = BUS.subscribe(TOPIC_STEP, lambda p: seen.append((p["step"], p["status"])))
    try:
        proj = pj.create("Live progress", "book", {
            "category": "Fantasy", "audience": "a", "word_target": 900,
            "tone": "Literary"})
        proj.ensure_dirs()
        pl = get_pipeline("book")
        pipe.execute(pl, proj, pl.step("concept"), JobContext("t", "t"))
        assert seen == [("concept", "running"), ("concept", "done")], seen

        seen.clear()
        boom = pl.step("concept")
        real, boom.run = boom.run, lambda p, c: (_ for _ in ()).throw(RuntimeError("nope"))
        try:
            pipe.execute(pl, proj, boom, JobContext("t", "t"))
        except RuntimeError:
            pass
        finally:
            boom.run = real
        assert seen == [("concept", "running"), ("concept", "failed")], seen
    finally:
        off()

    # the screen follows a run, and only a run
    studio = Studio(StubPage())
    studio.open_project(proj.id)
    studio.selected_step = "lock"
    studio._following = False
    studio._on_step({"project": proj.id, "step": "concept", "status": "running"})
    assert studio.selected_step == "lock", "a single step must not steal your selection"
    studio._following = True
    studio._on_step({"project": proj.id, "step": "concept", "status": "running"})
    assert studio.selected_step == "concept", "a run should show the step it is on"
    studio._on_step({"project": "someone-else", "step": "outline", "status": "running"})
    assert studio.selected_step == "concept", "another project's run must not move this one"
    print("        running/done/failed announced, and the screen follows a run only")


def test_analytics_never_breaks_anything():
    """Analytics must be invisible to the app, especially with no network.

    The whole point of this test is the offline case: a user on a plane must get
    the same app as everyone else, so nothing here may block, raise, or be slow.
    """
    import time
    from digital_assets_studio.core import telemetry
    from digital_assets_studio.core.settings import load as load_settings, save as save_settings

    # with no key configured it is completely inert, whatever else is true
    real_key, telemetry.app_key = telemetry.app_key, lambda: ""
    try:
        assert not telemetry.enabled(), "no key must mean no analytics"
        telemetry.track("app_started")       # must not raise
        assert telemetry._queue.qsize() == 0, "nothing should be queued with no key"
    finally:
        telemetry.app_key = real_key

    # a dead address stands in for being offline; the suite-wide opt-out is
    # lifted here and only here, and never points at the real dashboard
    os.environ["DAS_APTABASE_KEY"] = "A-SH-0000000000"
    os.environ["DAS_APTABASE_URL"] = "http://127.0.0.1:9"
    os.environ.pop("DAS_TELEMETRY", None)
    try:
        s = load_settings()
        s.analytics = True
        save_settings(s)
        telemetry._stopped = False
        telemetry._failures = 0
        assert telemetry.enabled(), "it should be on with a key and consent"

        started = time.time()
        for i in range(200):                 # far more than the queue holds
            telemetry.track("app_started", {"n": i})
        assert time.time() - started < 2.0, "track() blocked the caller"

        # the sender gives up rather than retrying a dead host forever
        for _ in range(100):
            if telemetry._stopped:
                break
            time.sleep(0.1)
        assert telemetry._stopped, "it should stop trying after repeated failures"
        telemetry.track("app_started")       # still safe once stopped
        telemetry.shutdown(timeout=1.0)

        # every way of saying no
        s.analytics = False
        save_settings(s)
        assert not telemetry.enabled(), "the settings switch must turn it off"
        s.analytics = True
        save_settings(s)
        telemetry._stopped = False
        for var in ("DO_NOT_TRACK", "DAS_TELEMETRY"):
            os.environ[var] = "1" if var == "DO_NOT_TRACK" else "0"
            assert not telemetry.enabled(), f"{var} must turn it off"
            del os.environ[var]
    finally:
        del os.environ["DAS_APTABASE_KEY"], os.environ["DAS_APTABASE_URL"]
        os.environ["DAS_TELEMETRY"] = "0"      # back under the suite-wide guard
        telemetry._stopped = False

    # nothing sensitive can leave, whatever a call site passes
    dirty = telemetry._clean({
        "kind": "youtube",
        "api_key": "sk-live-must-never-leave",     # a scalar, but truncated
        "path": "C:/Users/someone/secret/" + "x" * 200,
        "draft": {"chapter": "the whole manuscript"},
        "artifacts": ["a", "b"],
        "n": 3, "ok": True,
    })
    assert dirty["kind"] == "youtube"
    assert isinstance(dirty["n"], int) and dirty["ok"] is True
    assert "draft" not in dirty and "artifacts" not in dirty,         "only plain scalars may be sent"
    assert all(len(str(v)) <= 64 for v in dirty.values()), "values must be truncated"

    event = telemetry._event("app_started", {"kind": "book"})
    for field in ("timestamp", "sessionId", "eventName", "systemProps", "props"):
        assert field in event, f"Aptabase requires {field}"
    blob = json.dumps(event)
    for leak in (str(Path.home()), "sk-", "provider::"):
        assert leak not in blob, f"{leak!r} must never appear in an event"
    print("        inert without a key, non-blocking offline, gives up, leaks nothing")


def test_video_engines():
    """Each engine must activate its own steps and leave the others out, and the
    render step must never be blocked by a branch this project is not using."""
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.pipelines.youtube.pipeline import (ENGINE_AI, ENGINE_EDIT,
                                                                  ENGINE_LOCAL, ENGINE_MPT,
                                                                  ENGINE_SLIDES, ENGINE_STOCK,
                                                                  ENGINES)

    pl = get_pipeline("youtube")
    base = {"topic": "t", "audience": "a", "language": "English", "format": "Shorts only",
            "theme": "Light and warm", "mode": "Use a channel I already have"}
    expected = {
        ENGINE_SLIDES: ({"scene_art", "voiceover"},
                        {"stock_terms", "stock_footage", "ai_clips"}),
        ENGINE_STOCK: ({"stock_terms", "stock_footage", "voiceover"},
                       {"scene_art", "ai_clips"}),
        ENGINE_AI: ({"ai_clips", "voiceover"},
                    {"scene_art", "stock_terms", "stock_footage"}),
        ENGINE_MPT: (set(),
                     {"scene_art", "stock_terms", "stock_footage", "voiceover", "ai_clips"}),
        ENGINE_LOCAL: (set(),
                       {"scene_art", "stock_terms", "stock_footage", "voiceover", "ai_clips",
                        "edit"}),
        ENGINE_EDIT: ({"scene_art", "voiceover", "edit"},
                      {"stock_terms", "stock_footage", "ai_clips"}),
    }
    assert set(expected) == set(ENGINES), "an engine was added without a branching test"
    for engine, (present, absent) in expected.items():
        proj = pj.create(f"Engine {engine[:12]}", "youtube", {**base, "video_engine": engine})
        ids = {s.id for s in pl.active_steps(proj)}
        for want in present:
            assert want in ids, f"{engine}: {want} should apply"
        for gone in absent:
            assert gone not in ids, f"{engine}: {gone} should not apply"
        if engine != ENGINE_EDIT:
            assert "edit" not in ids, f"{engine}: the editor step belongs to the editor engine"
        blocked = set(pl.blocked(proj, pl.step("render")))
        assert blocked <= (present | {"fix_script"}), \
            f"{engine}: render blocked by a branch it does not use: {blocked}"
    print("        slides / stock / AI / MoneyPrinterTurbo / own-file / editor each "
          "activate only their own steps")


def test_caption_wrapping():
    """Long captions used to run off the frame in portrait instead of wrapping."""
    import tempfile
    from pathlib import Path as P

    from digital_assets_studio.config import ASSETS_DIR
    from digital_assets_studio.core.publishing.video import caption_filter, ffpath

    font = ASSETS_DIR / "fonts" / "Poppins-Medium.ttf"
    with tempfile.TemporaryDirectory() as tmp:
        long_text = ("This caption is deliberately long enough that it would run straight "
                     "off the edge of a portrait frame if nothing wrapped it.")
        f = caption_filter(long_text, 1080, 1920, font, P(tmp), 0)
        assert "textfile=" in f, "captions should go through a file, not the filter string"
        assert "expansion=none" in f, \
            "narration containing a per-cent sign would drop the whole caption"
        written = next(P(tmp).glob("caption_*.txt")).read_text(encoding="utf-8")
        lines = written.split("\n")
        assert len(lines) > 1, "long caption was not wrapped"
        assert all(len(l) <= 45 for l in lines), f"a wrapped line is too long: {lines}"
        assert caption_filter("", 1080, 1920, font, P(tmp), 1) == ""
    # windows drive letters must be escaped or the whole filtergraph fails to parse
    assert ffpath(P("C:/Users/ASUS/f.ttf")).startswith("C\\:"), "drive colon not escaped"
    print(f"        wrapped into {len(lines)} lines, drive colons escaped")


def test_printables():
    proj = _run_steps("printables", {
        "topic": "wedding planning", "buyer": "engaged couples", "price": "$10-20",
        "style": "Clean and minimal", "brand": "Test Press"},
        ["plan", "build", "listing", "mockups", "pack"])
    for rel in ("build/letter", "build/a4", "build/download_pack.zip",
                "build/listing_01_hero.jpg", "build/LICENCE.txt"):
        assert proj.exists(rel), f"missing {rel}"
    pdfs = list((proj.dir / "build").rglob("*.pdf"))
    data = pdfs[0].read_bytes()
    pages = data.count(b"/Type /Page") - data.count(b"/Type /Pages")
    print(f"        {len(pdfs)} PDFs, {pages} pages each, "
          f"pack {(proj.dir / 'build/download_pack.zip').stat().st_size // 1024} KB")


def test_course():
    proj = _run_steps("course", {
        "subject": "getting first clients", "student": "new freelancers",
        "starting_point": "nothing", "outcome": "ten conversations",
        "length": "Short (under 1 hour)", "price": "$49-99", "theme": "Light"},
        ["curriculum", "slides", "workbook", "sales", "pack"])
    for rel in ("build/slides.pdf", "build/workbook.pdf", "build/course_pack.zip",
                "drafts/sales.md"):
        assert proj.exists(rel), f"missing {rel}"
    slides = list((proj.dir / "build" / "slides").glob("*.png"))
    assert slides, "no slide images rendered"
    print(f"        {len(slides)} slides, deck + workbook + pack built")


def test_audiobook_offline():
    book = _run_steps("book", {
        "category": "Business how-to", "audience": "founders", "word_target": 1500,
        "tone": "Punchy and direct", "final_title": "Ship It", "chapter_count": 3},
        ["concept", "outline", "draft"], name="Audiobook source")
    proj = _run_steps("audiobook", {
        "title": "Ship It", "author": "Test Author", "source_project": book.id},
        ["gather", "prepare", "credits", "cover"])
    assert proj.exists("build/audiobook_cover.jpg")
    assert list((proj.dir / "drafts" / "narration").glob("*.txt")), "no narration prepared"
    print(f"        {proj.answer('chapter_total')} chapters, "
          f"~{proj.answer('runtime_hours')} finished hours estimated")


def test_podcast_offline():
    proj = _run_steps("podcast", {
        "topic": "AI for small business", "listener": "shop owners",
        "format": "Solo narration", "minutes": 20, "cadence": "Weekly",
        "host": "Test Host", "contact_email": "a@b.com", "show_name": "Show One"},
        ["concept", "cover", "script", "shownotes"])
    assert proj.exists("build/show_cover.jpg")
    slug = proj.answer("episode_slug")
    assert proj.exists(f"drafts/episodes/{slug}.md")
    print(f"        cover + {slug} script and notes written")


def test_rss_is_valid():
    import xml.dom.minidom as minidom
    from datetime import datetime, timezone

    from digital_assets_studio.core.publishing import audio

    xml = audio.rss_feed("Show", "Desc & <stuff>", "Host", "a@b.com", "https://x.com",
                         "https://x.com/c.jpg",
                         [{"title": "Ep 1 & 2", "description": "<p>hi</p>",
                           "audio_url": "https://x.com/1.mp3", "bytes": 100, "seconds": 930,
                           "published": datetime.now(timezone.utc), "episode_number": 1}])
    doc = minidom.parseString(xml)
    assert doc.getElementsByTagName("item"), "feed has no items"
    assert doc.getElementsByTagName("enclosure")[0].getAttribute("length") == "100"



def test_editor_timeline_arithmetic():
    """The edit document. Every number the renderer trusts is computed here, so
    a wrong start time is a wrong video and there is nothing downstream to catch
    it."""
    from digital_assets_studio.core.editor import timeline as etl

    doc = etl.Timeline()
    a = doc.add(etl.Clip(source="a.mp4", source_in=0, source_out=10, label="A"))
    b = doc.add(etl.Clip(source="b.mp4", source_in=2, source_out=8, label="B"))
    c = doc.add(etl.Clip(source="c.jpg", kind=etl.IMAGE, source_out=4, label="C"))
    assert doc.starts() == [0.0, 10.0, 16.0], doc.starts()
    assert doc.duration == 20.0, doc.duration

    # a transition overlaps its neighbours, so it shortens the whole video
    doc.set(b.id, transition=etl.DISSOLVE, transition_seconds=1.0)
    assert doc.starts() == [0.0, 9.0, 15.0], doc.starts()
    assert doc.duration == 19.0, doc.duration

    # and it can never eat more than half of either neighbour
    doc.set(c.id, transition=etl.FADE, transition_seconds=9.0)
    assert doc.overlap(2) == 2.0, doc.overlap(2)

    # a split keeps the total length: two windows onto one file
    before = doc.duration
    tail = doc.split(a.id, 4.0)
    assert tail is not None and len(doc.clips) == 4
    assert abs(doc.duration - before) < 1e-6, f"{doc.duration} != {before}"
    assert (doc.clips[0].source_out, doc.clips[1].source_in) == (4.0, 4.0)
    assert doc.clips[1].transition == etl.CUT, "a split half must not inherit a dissolve"
    assert doc.split(a.id, 99.0) is None, "splitting outside the clip must refuse"

    # speed changes how long a clip lasts, not what it points at
    doc.set(tail.id, speed=2.0)
    assert doc.clip(tail.id).length == 3.0, doc.clip(tail.id).length
    doc.set(tail.id, speed=99)
    assert doc.clip(tail.id).speed == etl.MAX_SPEED, "speed must be clamped"

    # the playhead knows which clip it is over, and where in the file that is
    assert doc.at(0.5) is doc.clips[0]
    doc.set(tail.id, speed=1.0)
    assert doc.source_seconds(tail.id, doc.starts()[1] + 1.0) == 5.0

    # reordering never leaves the first clip fading in from nothing
    doc.move(doc.clips[-1].id, 0)
    assert doc.clips[0].transition == etl.CUT

    # round-trips through JSON without losing a value
    import tempfile
    from pathlib import Path as P
    with tempfile.TemporaryDirectory() as tmp:
        path = doc.save(P(tmp) / "timeline.json")
        again = etl.load(path)
        assert again.to_dict() == doc.to_dict(), "the document did not round-trip"
        assert etl.load(P(tmp) / "missing.json").clips == [], "a missing edit should be empty"
        (P(tmp) / "broken.json").write_text("{not json", encoding="utf-8")
        assert etl.load(P(tmp) / "broken.json").clips == [], "a corrupt edit must not raise"

    # and it says what is wrong before ffmpeg has to
    problems = doc.problems(P("/nowhere"))
    assert any("no file at" in x for x in problems), problems
    print(f"        {len(doc.clips)} clips, {doc.duration}s, overlaps and splits exact")


def test_editor_operations_are_validated():
    """A model can only ask for edits the document knows how to make, and every
    refusal has to say why rather than corrupting the timeline."""
    from digital_assets_studio.core.editor import ai as eai
    from digital_assets_studio.core.editor import timeline as etl

    doc = etl.Timeline()
    doc.add(etl.Clip(source="a.mp4", source_in=0, source_out=10, label="A"))
    doc.add(etl.Clip(source="b.mp4", source_in=0, source_out=10, label="B"))

    applied, rejected = eai.apply_ops(doc, [
        {"op": "trim", "clip": 0, "start": 1, "end": 5},
        {"op": "transition", "clip": 1, "style": "dissolve", "seconds": 0.5},
        {"op": "title", "text": "On screen", "start": 0.5, "seconds": 2},
        {"op": "speed", "clip": "1", "factor": 1.5},
        {"op": "fade_out", "seconds": 1.2},
        {"op": "note", "text": "Tightened the open"},
    ])
    assert len(applied) == 6, (applied, rejected)
    assert rejected == [], rejected
    assert (doc.clips[0].source_in, doc.clips[0].source_out) == (1.0, 5.0)
    assert doc.clips[1].transition == etl.DISSOLVE and doc.clips[1].speed == 1.5
    assert len(doc.overlays) == 1 and doc.fade_out_seconds == 1.2
    assert "Tightened" in doc.notes

    kept = doc.to_dict()
    applied, rejected = eai.apply_ops(doc, [
        {"op": "remove", "clip": "does-not-exist"},
        {"op": "transition", "clip": 0, "style": "explode"},
        {"op": "transition", "clip": 0, "style": "fade"},
        {"op": "music", "source": "nothing/here.mp3"},
        {"op": "captions", "source": "nothing/here.srt"},
        {"op": "teleport", "clip": 0},
        "not an operation",
        {"op": "title", "text": "   "},
    ], base=Path(WORK))
    assert applied == [], applied
    assert len(rejected) == 8, rejected
    assert any("does-not-exist" in r for r in rejected)
    assert any("explode" in r for r in rejected)
    assert doc.to_dict() == kept, "a rejected plan must leave the timeline untouched"

    # the plan can arrive wrapped, or not be a list at all
    assert eai.apply_ops(doc, {"operations": []}) == ([], [])
    assert eai.apply_ops(doc, "nonsense")[1], "a non-list plan must be reported"
    print(f"        6 applied, {len(rejected)} refused with reasons, document intact")


def test_editor_render_plan():
    """The ffmpeg commands, checked without running ffmpeg. Every bug this file
    has ever had is visible in the argv."""
    import tempfile
    from pathlib import Path as P

    from digital_assets_studio.core.editor import render as erender
    from digital_assets_studio.core.editor import timeline as etl
    from digital_assets_studio.config import ASSETS_DIR

    doc = etl.Timeline(name="plan", width=1080, height=1920, fps=30)
    doc.add(etl.Clip(source="a.mp4", source_in=1.5, source_out=5.5, volume=0.5, label="A"))
    doc.add(etl.Clip(source="b.mp4", source_in=0, source_out=4, speed=2.0,
                     transition=etl.DISSOLVE, transition_seconds=0.6, label="B"))
    doc.add(etl.Clip(source="c.jpg", kind=etl.IMAGE, source_out=3, label="C"))
    doc.add_text(etl.Text(text="Title here", start=1.0, seconds=2.0))
    doc.set_music("music.mp3", gain_db=-20)
    doc.fade_out_seconds = 1.0

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = P(tmp)
        font = ASSETS_DIR / "fonts" / "Poppins-Medium.ttf"
        jobs = erender.plan(doc, tmpdir / "out.mp4", P("/project"), tmpdir / "work",
                            font=font, audio={str(P("/project") / "a.mp4"): True,
                                              str(P("/project") / "b.mp4"): True})
        assert len(jobs) == 5, [j.label for j in jobs]

        first = " ".join(jobs[0].argv)
        assert "-ss 1.500" in first and "-t 4.000" in first, first
        assert "volume=0.500" in first, "the clip volume was not applied"
        assert "anullsrc" in first, "every segment needs an audio stream to join on"

        second = " ".join(jobs[1].argv)
        assert "setpts=PTS/2.0000" in second, second
        assert "atempo=2.000000" in second, "audio must be sped up with the picture"

        third = " ".join(jobs[2].argv)
        assert "-loop 1" in third and "zoompan" in third, "a still needs a hold and a push"
        assert "[1:a]" in third, "a still has no sound of its own"

        join = " ".join(jobs[3].argv)
        # clip 1 dissolves, clip 2 cuts: the offset is where clip 1 starts
        assert f"offset={doc.starts()[1]:.3f}" in join, join
        assert "acrossfade=d=0.600" in join
        assert "concat=n=2:v=1:a=1" in join, "a cut boundary must not be crossfaded"

        finish = " ".join(jobs[4].argv)
        assert "drawtext" in finish and "textfile=" in finish, "titles go through a file"
        # drawtext reads its text as a template unless told not to, and one "%"
        # in a title - "50% off" - then renders the whole line as nothing, with
        # no error anywhere. Both burn-in paths have to switch expansion off.
        assert "expansion=none" in finish, \
            "a title with a per-cent sign in it would silently vanish"
        assert "enable='between(t,1.000,3.000)'" in finish, finish
        assert "volume=-20.0dB" in finish and "amix=inputs=2" in finish
        assert "normalize=0" in finish, "amix would otherwise halve the narration"
        assert f"fade=t=out:st={doc.duration - 1:.3f}" in finish

        # a straight cut list is joined by copy - no re-encode, no quality lost
        plain = etl.Timeline(width=640, height=360)
        plain.add(etl.Clip(source="a.mp4", source_out=2))
        plain.add(etl.Clip(source="b.mp4", source_out=2))
        cuts = erender.plan(plain, tmpdir / "cut.mp4", P("/project"), tmpdir / "work2")
        assert len(cuts) == 3, [j.label for j in cuts]
        assert "-c copy" in " ".join(cuts[-1].argv), "cuts should be joined losslessly"
        assert cuts[-1].argv[-1] == str(tmpdir / "cut.mp4"), "the last job writes the output"

    assert erender.atempo_chain(4.0) == [2.0, 2.0], erender.atempo_chain(4.0)
    assert erender.atempo_chain(0.25) == [0.5, 0.5], erender.atempo_chain(0.25)
    assert erender.ffcolour("#FF8800") == "0xFF8800"
    assert erender.ffpath(P("C:/Users/x/f.ttf")).startswith("C\\:"), "drive colon not escaped"
    try:
        erender.plan(etl.Timeline(), P("x.mp4"), P("."), P("."))
        raise AssertionError("an empty timeline should refuse to render")
    except erender.RenderError:
        pass
    print("        segments, crossfade offsets, drawtext, music mix and the copy join")


def test_editor_assembles_a_project():
    """The first cut, built from what a project has on disk - with no ffmpeg
    involved, because the voiceover step already recorded the durations."""
    from digital_assets_studio.core.editor import ai as eai
    from digital_assets_studio.core.editor import timeline as etl

    proj = pj.create("Editor assembly", "youtube", {
        "topic": "t", "audience": "a", "language": "English",
        "episode_slug": "ep-1", "orientation": "Portrait 9:16"})
    proj.ensure_dirs()
    for i in range(1, 4):
        proj.write_bytes(f"build/voice/ep-1/scene_{i:03d}.mp3", b"m" * 100)
        proj.write_bytes(f"build/scenes/ep-1/scene_{i:03d}.jpg", b"j" * 100)
    proj.write_text("build/voice/ep-1.timings.json", json.dumps(
        [{"seconds": 3.0, "file": "scene_001.mp3"}, {"seconds": 5.0, "file": "scene_002.mp3"},
         {"seconds": 2.0, "file": "scene_003.mp3"}]))
    proj.write_text("build/voice/ep-1.srt", "1\n00:00:00,000 --> 00:00:01,000\nhi\n")

    doc = eai.assemble(proj)
    assert len(doc.clips) == 3, doc.clips
    assert doc.size == (1080, 1920), doc.size
    assert [c.length for c in doc.clips] == [3.0, 5.0, 2.0], [c.length for c in doc.clips]
    assert doc.duration == 10.0, doc.duration
    assert all(c.kind == etl.IMAGE and c.volume == 0 for c in doc.clips), \
        "stills carry no sound of their own"
    voices = [a for a in doc.audio if a.role == etl.VOICE]
    assert [a.start for a in voices] == [0.0, 3.0, 8.0], [a.start for a in voices]
    assert all(a.gain_db == 0 for a in voices), "narration must not be ducked"
    assert doc.captions == "build/voice/ep-1.srt", doc.captions
    assert doc.problems(proj.dir) == [], doc.problems(proj.dir)

    # a project with nothing but a render still opens in the editor
    other = pj.create("Editor from a render", "youtube", {"episode_slug": "ep-2"})
    other.ensure_dirs()
    other.write_bytes("build/ep-2.mp4", b"v" * 500)
    single = eai.assemble(other)
    assert len(single.clips) == 1 and single.clips[0].volume == 1.0

    # and one with nothing at all says so, instead of making an empty video
    empty = pj.create("Editor with nothing", "youtube", {})
    empty.ensure_dirs()
    try:
        eai.assemble(empty)
        raise AssertionError("assembling from nothing should refuse")
    except eai.EditorError as exc:
        assert "nothing to edit" in str(exc), str(exc)

    described = eai.describe(doc)
    assert "id=" in described and "timeline 0.0s" in described, described
    print(f"        3 scenes, {doc.duration}s, narration at {[a.start for a in voices]}")


def test_editor_silence_and_shorts():
    """Cutting dead air and cropping a Short, as arithmetic - the ffmpeg half is
    covered by the integration suite."""
    from digital_assets_studio.core.editor import ai as eai
    from digital_assets_studio.core.editor import analyze
    from digital_assets_studio.core.editor import timeline as etl

    spans = analyze.keep_spans(10.0, [(2.0, 4.0), (7.0, 8.0)])
    assert spans == [(0.0, 2.12), (3.88, 7.12), (7.88, 10.0)], spans
    assert analyze.keep_spans(10.0, [(0.0, 10.0)]) == [], "an all-silent take keeps nothing"
    assert analyze.parse_silences(
        "[silencedetect] silence_start: 1.5\n[silencedetect] silence_end: 3.25 | "
        "silence_duration: 1.75\n") == [(1.5, 3.25)]
    assert analyze.parse_silences("silence_start: 4.0\n") == [], \
        "a silence that never ends cannot be trimmed to"

    doc = etl.Timeline()
    clip = doc.add(etl.Clip(source="take.mp4", source_in=0, source_out=10, label="take"))
    removed = eai.silence_cuts(doc, clip, [(2.0, 4.0), (7.0, 8.0)])
    assert len(doc.clips) == 3, [c.source_in for c in doc.clips]
    assert removed > 2.5, removed
    assert doc.clips[0].source_in == 0.0 and doc.clips[-1].source_out == 10.0

    doc2 = etl.Timeline()
    only = doc2.add(etl.Clip(source="take.mp4", source_in=0, source_out=5))
    assert eai.silence_cuts(doc2, only, [(0.0, 5.0)]) == 5.0
    assert doc2.clips == [], "a take that is all silence should disappear"

    # a Short is a window onto the same sources, not a re-render
    long = etl.Timeline(width=1920, height=1080)
    long.add(etl.Clip(source="a.mp4", source_in=0, source_out=20, label="A"))
    long.add(etl.Clip(source="b.mp4", source_in=0, source_out=20, label="B"))
    long.add_text(etl.Text(text="Inside", start=25, seconds=3))
    long.add_text(etl.Text(text="Outside", start=2, seconds=1))
    long.audio.append(etl.Audio(source="v.mp3", role=etl.VOICE, start=18.0, gain_db=0.0))
    long.captions = "subs.srt"

    start = eai.highlight_start(long, 10.0)
    assert start == 0.0, f"a hint-free Short should start on the nearest clip boundary: {start}"
    short = eai.crop(long, 22.0, 10.0, portrait=True)
    assert short.size == (1080, 1920) and short.duration == 10.0, short.duration
    assert len(short.clips) == 1 and short.clips[0].source_in == 2.0, short.clips[0]
    assert [o.text for o in short.overlays] == ["Inside"], short.overlays
    assert short.overlays[0].start == 3.0, short.overlays[0].start
    assert short.captions == "", "subtitles timed to the long video must not be carried over"
    voice = [a for a in short.audio if a.role == etl.VOICE][0]
    assert voice.start == 0.0 and voice.source_in == 4.0, voice
    assert eai.highlight_start(long, 60.0) == 0.0, "a window longer than the video starts at 0"
    assert eai.highlight_start(long, 10.0, hint=5.0) == 5.0, "an explicit hint wins"
    print(f"        {len(doc.clips)} pieces after the cut, Short from {start}s intact")


def test_editor_screen():
    """The editor screen builds, and its buttons do what they say - headless, so
    a broken control shows up here rather than on someone's first render."""
    from digital_assets_studio.core.editor import ai as eai
    from digital_assets_studio.core.editor import timeline as etl
    from digital_assets_studio.ui.studio import Studio

    ensure_dirs()
    page = StubPage()
    studio = Studio(page)

    # with no project open it offers the ones you have
    studio.route = "editor"
    studio.project = None
    studio.refresh()
    assert page.controls, "the editor with no project drew nothing"

    proj = pj.create("Editor screen", "youtube", {
        "topic": "t", "audience": "a", "language": "English", "episode_slug": "ep-s",
        "video_engine": "AI timeline editor (cut it yourself)"})
    proj.ensure_dirs()
    for i in range(1, 3):
        proj.write_bytes(f"build/voice/ep-s/scene_{i:03d}.mp3", b"m" * 80)
        proj.write_bytes(f"build/scenes/ep-s/scene_{i:03d}.jpg", b"j" * 80)
    proj.write_text("build/voice/ep-s.timings.json",
                    json.dumps([{"seconds": 4.0}, {"seconds": 6.0}]))

    studio.open_editor(proj.id)
    assert studio.route == "editor" and studio.editor is not None
    ed = studio.editor
    ed.doc = eai.assemble(proj)
    ed.selected = ed.doc.clips[0].id
    ed.save(quiet=True)
    studio.refresh()
    assert page.controls, "the editor screen drew nothing"

    # every edit the buttons make goes through the same path, and saves
    ed.playhead = 2.0
    ed.split_here()
    assert len(ed.doc.clips) == 3, "split did not take"
    ed.select(ed.doc.clips[1].id)
    ed.set_prop("transition", etl.DISSOLVE)
    assert ed.doc.clips[1].transition == etl.DISSOLVE
    ed.mutate(lambda: ed.doc.duplicate(ed.doc.clips[0].id))
    assert len(ed.doc.clips) == 4
    ed.nudge(1)
    ed.mutate(lambda: ed.doc.remove(ed.doc.clips[-1].id))
    assert len(ed.doc.clips) == 3
    assert etl.load(ed.path).to_dict() == ed.doc.to_dict(), "an edit was not saved to disk"

    # the first clip can never be left transitioning in from nothing
    assert ed.doc.clips[0].transition == etl.CUT

    ed.set_canvas("Portrait 1080×1920")
    assert ed.doc.size == (1080, 1920)
    long_edit = etl.load(proj.dir / "edit" / "timeline.json").to_dict()
    ed.make_short(5.0)
    assert ed.doc.duration <= 5.0 and ed.doc.portrait, ed.doc.summary()
    assert (proj.dir / "edit" / "short.json").exists(), "the Short timeline was not written"
    assert etl.load(proj.dir / "edit" / "timeline.json").to_dict() == long_edit, \
        "cutting a Short overwrote the long edit"
    assert ed.is_short and ed.path.name == "short.json"
    ed.open_document(short=False)
    assert not ed.is_short and ed.doc.to_dict() == long_edit, "could not get back to the edit"

    # publishing before rendering is refused rather than attempted
    ed.publish()
    assert not ed.out_path.exists()
    studio.refresh()
    assert page.controls

    # the step that hands you off to this screen is wired to it
    pl = get_pipeline("youtube")
    assert pl.step("edit").opens == "editor", "the edit step must open the editor"
    studio.open_project(proj.id)
    for step in pl.active_steps(proj):
        studio.select_step(step.id)
    print(f"        {len(ed.doc.clips)} clips edited, saved, and the screen redrew each time")


if __name__ == "__main__":
    print(f"workspace: {WORK}")
    check("views build", test_views)
    check("book pipeline end to end", test_book_pipeline)
    check("youtube offline steps", test_youtube_offline)
    check("mobile offline steps", test_mobile_offline)
    check("run-all stops at the first human gate", test_run_all)
    check("autopilot clears review gates only", test_autopilot)
    check("every pipeline reaches its build phase", test_no_pipeline_stops_before_a_deliverable)
    check("selecting a step never rebuilds the list", test_selecting_a_step_keeps_the_list)
    check("youtube supports an existing channel", test_youtube_branches)
    check("every public symbol the pipelines use exists", test_public_api_surface)
    check("oauth callback survives favicon and reloads", test_oauth_callback)
    check("the rename migrates old data", test_rename_migration)
    check("cover art has three sources", test_cover_art_sources)
    check("stock photos rank by cover shape", test_stock_photo_ranking)
    check("a run repaints while it runs", test_a_run_reports_progress_live)
    check("analytics cannot break the app", test_analytics_never_breaks_anything)
    check("file pickers and the channel list", test_file_and_channel_fields)
    check("video engines branch correctly", test_video_engines)
    check("captions wrap instead of overflowing", test_caption_wrapping)
    check("editor: the timeline maths is exact", test_editor_timeline_arithmetic)
    check("editor: every AI operation is validated", test_editor_operations_are_validated)
    check("editor: the ffmpeg plan is right", test_editor_render_plan)
    check("editor: a first cut from what a project has", test_editor_assembles_a_project)
    check("editor: dead air and Shorts", test_editor_silence_and_shorts)
    check("editor: the screen builds and edits", test_editor_screen)
    check("printables pipeline", test_printables)
    check("course pipeline", test_course)
    check("audiobook offline steps", test_audiobook_offline)
    check("podcast offline steps", test_podcast_offline)
    check("podcast RSS is valid XML", test_rss_is_valid)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("all green")
    shutil.rmtree(WORK, ignore_errors=True)
