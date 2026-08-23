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
             "add_to_playlist", "post_comment", "CATEGORIES", "STORE"],
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

    from digital_assets_studio.core import keyvault
    assert keyvault.SERVICE == "DigitalAssetsStudio"
    assert keyvault.LEGACY_SERVICE == "AIpathStudio", "old keychain entries must still be readable"
    print("        projects, settings and keychain entries all survive the rename")


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


def test_video_engines():
    """Each engine must activate its own steps and leave the others out, and the
    render step must never be blocked by a branch this project is not using."""
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.pipelines.youtube.pipeline import (ENGINE_MPT, ENGINE_SLIDES,
                                                          ENGINE_STOCK)

    pl = get_pipeline("youtube")
    base = {"topic": "t", "audience": "a", "language": "English", "format": "Shorts only",
            "theme": "Light and warm", "mode": "Use a channel I already have"}
    expected = {
        ENGINE_SLIDES: ({"scene_art", "voiceover"}, {"stock_terms", "stock_footage"}),
        ENGINE_STOCK: ({"stock_terms", "stock_footage", "voiceover"}, {"scene_art"}),
        ENGINE_MPT: (set(), {"scene_art", "stock_terms", "stock_footage", "voiceover"}),
    }
    for engine, (present, absent) in expected.items():
        proj = pj.create(f"Engine {engine[:12]}", "youtube", {**base, "video_engine": engine})
        ids = {s.id for s in pl.active_steps(proj)}
        for want in present:
            assert want in ids, f"{engine}: {want} should apply"
        for gone in absent:
            assert gone not in ids, f"{engine}: {gone} should not apply"
        blocked = set(pl.blocked(proj, pl.step("render")))
        assert blocked <= (present | {"fix_script"}), \
            f"{engine}: render blocked by a branch it does not use: {blocked}"
    print("        slides / stock / MoneyPrinterTurbo each activate only their own steps")


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
    check("video engines branch correctly", test_video_engines)
    check("captions wrap instead of overflowing", test_caption_wrapping)
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
