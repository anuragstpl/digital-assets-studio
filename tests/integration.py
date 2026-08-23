"""Integration tests for every publishing connector, against mock servers.

These exercise the code that only ever runs when you press Publish: request
shapes, headers, pagination, resumable uploads, error handling. Offline tests
cannot reach any of it.

    python tests/integration.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

WORK = tempfile.mkdtemp(prefix="das-integration-")
os.environ["DAS_HOME"] = WORK
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from mockserver import MockAPI  # noqa: E402

FAILURES: list[str] = []
PASSED = 0


def check(name: str, fn):
    global PASSED
    try:
        fn()
        PASSED += 1
        print(f"  PASS  {name}")
    except Exception as exc:  # noqa: BLE001
        FAILURES.append(f"{name}: {exc}")
        print(f"  FAIL  {name}: {exc}")
        traceback.print_exc(limit=5)


def tmpfile(name: str, size: int = 2048, data: bytes | None = None) -> Path:
    p = Path(WORK) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if data is not None else b"x" * size)
    return p


# =============================================================== YouTube ====

CHANNEL = {
    "items": [{
        "id": "UC123",
        "snippet": {"title": "AI Ideas", "customUrl": "@AIIdeasSolved",
                    "description": "About text", "publishedAt": "2026-08-19T00:00:00Z"},
        "statistics": {"subscriberCount": "42", "videoCount": "7", "viewCount": "900"},
        "brandingSettings": {"channel": {"description": "Branded about", "keywords": "ai ideas",
                                         "country": "SG"}},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
    }]
}


def _youtube_api(api: MockAPI):
    from digital_assets_studio.core.publishing import youtube as yt
    yt.API = api.base + "/youtube/v3"
    yt.UPLOAD = api.base + "/upload/youtube/v3"
    yt.token = lambda: "test-token"
    return yt


def test_youtube_channel_read():
    with MockAPI() as api:
        yt = _youtube_api(api)
        api.json_route("GET", "/youtube/v3/channels", CHANNEL)
        api.json_route("GET", "/youtube/v3/playlistItems", {
            "items": [{"snippet": {"title": "Ep 1", "description": "d",
                                   "publishedAt": "2026-08-20T00:00:00Z",
                                   "resourceId": {"videoId": "vid1"}}}]})
        info = yt.channel_summary()
        assert info["title"] == "AI Ideas", info
        assert info["handle"] == "@AIIdeasSolved"
        assert info["description"] == "Branded about", "branding description must win over snippet"
        assert info["subscribers"] == "42" and info["videos"] == "7"

        req = api.one("GET", "/channels")
        assert req.headers.get("authorization") == "Bearer test-token", "missing bearer token"
        assert req.query.get("mine") == "true"
        assert "contentDetails" in req.query.get("part", ""), \
            "contentDetails is needed for the uploads playlist"

        uploads = yt.recent_uploads(5)
        assert uploads and uploads[0]["video_id"] == "vid1"
        assert api.one("GET", "/playlistItems").query["playlistId"] == "UU123"


def test_youtube_resumable_upload():
    with MockAPI() as api:
        yt = _youtube_api(api)
        yt.CHUNK = 1 << 20                       # 1 MB, so a small file still spans chunks
        video = tmpfile("video.mp4", size=(1 << 20) * 2 + 500)
        state = {"received": 0}

        @api.route("POST", r"/upload/youtube/v3/videos")
        def start(req, m):
            body = req.json()
            assert body["snippet"]["title"] == "My Episode"
            assert body["status"]["privacyStatus"] == "private"
            assert body["status"]["selfDeclaredMadeForKids"] is False
            assert body["snippet"]["categoryId"] == "27", "Education should map to 27"
            return 200, {"Location": api.base + "/resume/abc"}, b"{}"

        @api.route("PUT", r"/resume/abc")
        def chunk(req, m):
            rng = req.headers["content-range"]
            start_byte = int(rng.split(" ")[1].split("-")[0])
            assert start_byte == state["received"], \
                f"chunk arrived at {start_byte}, expected {state['received']}"
            state["received"] += len(req.body)
            total = int(rng.rsplit("/", 1)[1])
            if state["received"] >= total:
                return 200, {"Content-Type": "application/json"}, json.dumps(
                    {"id": "NEWVIDEO", "snippet": {"title": "My Episode"}}).encode()
            return 308, {"Range": f"bytes=0-{state['received'] - 1}"}, b""

        seen = []
        out = yt.upload_video(video, title="My Episode", description="d", tags=["a"],
                              category="Education", privacy="private",
                              progress=lambda f, m: seen.append(f))
        assert out["id"] == "NEWVIDEO", out
        assert state["received"] == video.stat().st_size, "not every byte was uploaded"
        assert len(api.sent("PUT", "/resume/abc")) == 3, "expected three chunks"
        assert seen and seen[-1] == 1.0, "progress never reached 100%"


def test_youtube_extras():
    with MockAPI() as api:
        yt = _youtube_api(api)
        api.json_route("POST", "/upload/youtube/v3/thumbnails/set", {"items": [{}]})
        api.json_route("POST", "/upload/youtube/v3/captions", {"id": "cap1"})
        api.json_route("GET", "/youtube/v3/playlists",
                       {"items": [{"id": "PL_EXISTING", "snippet": {"title": "AI Ideas"}}]})
        api.json_route("POST", "/youtube/v3/playlists", {"id": "PL_NEW"})
        api.json_route("POST", "/youtube/v3/playlistItems", {"id": "PLI"})
        api.json_route("POST", "/youtube/v3/commentThreads", {"id": "CT"})

        yt.set_thumbnail("vid1", tmpfile("thumb.jpg", 4096))
        t = api.one("POST", "/thumbnails/set")
        assert t.query["videoId"] == "vid1"
        assert t.headers["content-type"] == "image/jpeg"
        assert len(t.body) == 4096, "the image bytes were not sent"

        yt.upload_caption("vid1", tmpfile("subs.srt", data=b"1\n00:00:00,000 --> 00:00:01,000\nhi\n"),
                          language="en")
        c = api.one("POST", "/captions")
        assert "multipart/form-data" in c.headers["content-type"]
        assert b'"videoId": "vid1"' in c.body or b'"videoId":"vid1"' in c.body

        assert yt.ensure_playlist("AI Ideas") == "PL_EXISTING", "should reuse a playlist by name"
        assert yt.ensure_playlist("Brand New") == "PL_NEW", "should create a missing playlist"
        yt.add_to_playlist("PL_EXISTING", "vid1")
        body = api.one("POST", "/playlistItems").json()
        assert body["snippet"]["resourceId"]["videoId"] == "vid1"

        yt.post_comment("vid1", "pinned")
        assert api.one("POST", "/commentThreads").json()["snippet"]["topLevelComment"][
            "snippet"]["textOriginal"] == "pinned"


def test_youtube_errors():
    with MockAPI() as api:
        yt = _youtube_api(api)
        api.json_route("GET", "/youtube/v3/channels", {"items": []})
        try:
            yt.my_channel()
            raise AssertionError("an account with no channel should raise")
        except yt.YouTubeError as exc:
            assert "does not own a YouTube channel" in str(exc), str(exc)

    with MockAPI() as api:
        yt = _youtube_api(api)
        api.json_route("POST", "/upload/youtube/v3/videos", {
            "error": {"message": "quota exceeded",
                      "errors": [{"reason": "quotaExceeded"}]}}, status=403)
        try:
            yt.upload_video(tmpfile("v2.mp4"), title="t")
            raise AssertionError("a quota error should raise")
        except yt.YouTubeError as exc:
            assert "quota" in str(exc).lower() and "upload bucket" in str(exc), \
                f"the quota error should explain the budget: {exc}"


# ============================================================ Google Play ====

def _play(api: MockAPI):
    from digital_assets_studio.core.publishing import play
    play.BASE = api.base + "/androidpublisher/v3"
    play.UPLOAD = api.base + "/upload/androidpublisher/v3"
    play._token = lambda: "play-token"
    return play


def test_play_full_release():
    with MockAPI() as api:
        play = _play(api)
        pkg = "com.example.notes"
        api.json_route("POST", rf"/androidpublisher/v3/applications/{pkg}/edits",
                       {"id": "EDIT1"})
        api.json_route("POST", rf"/upload/androidpublisher/v3/applications/{pkg}/edits/EDIT1/bundles",
                       {"versionCode": 42, "sha256": "abc"})
        api.json_route("PUT", rf"/androidpublisher/v3/applications/{pkg}/edits/EDIT1/listings/en-US",
                       {"language": "en-US"})
        api.json_route("DELETE",
                       rf"/androidpublisher/v3/applications/{pkg}/edits/EDIT1/listings/en-US/\w+", {})
        api.json_route("POST",
                       rf"/upload/androidpublisher/v3/applications/{pkg}/edits/EDIT1/listings/en-US/\w+",
                       {"image": {"id": "img"}})
        api.json_route("PUT", rf"/androidpublisher/v3/applications/{pkg}/edits/EDIT1/tracks/internal",
                       {"track": "internal"})
        api.json_route("POST", rf"/androidpublisher/v3/applications/{pkg}/edits/EDIT1:commit",
                       {"id": "EDIT1"})

        shots = [tmpfile(f"shots/{i}.png", 3000) for i in range(1, 4)]
        out = play.publish(
            package=pkg, aab=tmpfile("app.aab", 5000),
            listing={"title": "Example Notes", "short_description": "Notes that sync",
                     "full_description": "Long text"},
            images={"phoneScreenshots": shots, "featureGraphic": [tmpfile("fg.png", 2000)]},
            track="internal", release_notes={"en-US": "First release"}, rollout=0.1)

        assert out["version_code"] == 42, out
        listing = api.one("PUT", "/listings/en-US").json()
        assert listing["title"] == "Example Notes"
        bundle = api.one("POST", "/edits/EDIT1/bundles")
        assert bundle.query.get("uploadType") == "media"
        assert len(bundle.body) == 5000, "the bundle bytes were not sent"
        assert len(api.sent("POST", "/listings/en-US/phoneScreenshots")) == 3
        assert api.sent("DELETE", "/listings/en-US/phoneScreenshots"), \
            "old screenshots must be cleared before uploading new ones"
        track = api.one("PUT", "/tracks/internal").json()
        release = track["releases"][0]
        assert release["versionCodes"] == ["42"]
        assert release["status"] == "inProgress" and release["userFraction"] == 0.1, release
        assert release["releaseNotes"][0]["text"] == "First release"
        assert api.sent("POST", "/edits/EDIT1:commit"), "the edit was never committed"


def test_play_guards_and_errors():
    with MockAPI() as api:
        play = _play(api)
        pkg = "com.x.y"
        api.json_route("POST", rf"/androidpublisher/v3/applications/{pkg}/edits", {"id": "E"})
        api.json_route("DELETE", rf"/androidpublisher/v3/applications/{pkg}/edits/E", {})
        api.json_route("POST", rf"/androidpublisher/v3/applications/{pkg}/edits/E:commit", {})
        edit = play.Edit(pkg)
        with edit:
            for field, value, cap in (("title", "x" * 31, 30),
                                      ("short", "x" * 81, 80),
                                      ("full", "x" * 4001, 4000)):
                try:
                    edit.update_listing("en-US",
                                        "x" * 31 if field == "title" else "ok",
                                        "x" * 81 if field == "short" else "ok",
                                        "x" * 4001 if field == "full" else "ok")
                    raise AssertionError(f"{field} over {cap} chars should be refused locally")
                except play.PlayError as exc:
                    assert str(cap) in str(exc), str(exc)

    # a failed edit must not commit
    with MockAPI() as api:
        play = _play(api)
        pkg = "com.x.z"
        api.json_route("POST", rf"/androidpublisher/v3/applications/{pkg}/edits", {"id": "E2"})
        api.json_route("DELETE", rf"/androidpublisher/v3/applications/{pkg}/edits/E2", {})
        try:
            with play.Edit(pkg) as e:
                raise RuntimeError("something went wrong mid-edit")
        except RuntimeError:
            pass
        assert not api.sent("POST", "/edits/E2:commit"), "a failed edit must never commit"
        assert api.sent("DELETE", "/edits/E2"), "a failed edit should be discarded"

    with MockAPI() as api:
        play = _play(api)
        api.json_route("POST", r"/androidpublisher/v3/applications/com.x.q/edits",
                       {"error": {"message": "The caller does not have permission"}}, status=403)
        try:
            play.check_access("com.x.q")
            raise AssertionError("a permission error should raise")
        except play.PlayError as exc:
            assert "Users and permissions" in str(exc), f"should say how to fix it: {exc}"


# ======================================================== App Store Connect ==

def _appstore(api: MockAPI):
    from digital_assets_studio.core.publishing import appstore
    appstore.BASE = api.base + "/v1"
    appstore._token = lambda: "apple-token"
    return appstore


def test_appstore_metadata_and_screenshots():
    with MockAPI() as api:
        appstore = _appstore(api)
        api.json_route("GET", r"/v1/apps", {"data": [
            {"id": "APP1", "attributes": {"name": "Example Notes", "bundleId": "com.example.notes",
                                          "sku": "SKU1"}}]})
        api.json_route("GET", r"/v1/apps/APP1/appStoreVersions",
                       {"data": [{"id": "VER1", "attributes": {"versionString": "1.0.0"}}]})
        api.json_route("GET", r"/v1/appStoreVersions/VER1/appStoreVersionLocalizations",
                       {"data": [{"id": "LOC1", "attributes": {"locale": "en-US"}}]})
        api.json_route("PATCH", r"/v1/appStoreVersionLocalizations/LOC1", {"data": {"id": "LOC1"}})
        api.json_route("POST", r"/v1/appStoreVersionSubmissions", {"data": {"id": "SUB1"}})

        apps = appstore.list_apps()
        assert apps[0]["bundle_id"] == "com.example.notes"
        assert api.one("GET", "/apps").headers["authorization"] == "Bearer apple-token"

        version = appstore.latest_version("APP1")
        assert version["id"] == "VER1"
        appstore.update_localization("VER1", "en-US", description="A description",
                                     keywords="cards,qr,contacts", whats_new="Fixes")
        patched = api.one("PATCH", "/appStoreVersionLocalizations/LOC1").json()
        assert patched["data"]["attributes"]["keywords"] == "cards,qr,contacts"
        assert "locale" not in patched["data"]["attributes"], \
            "locale must not be patched onto an existing localisation"

        try:
            appstore.update_localization("VER1", "en-US", keywords="x" * 101)
            raise AssertionError("over-long keywords should be refused locally")
        except appstore.AppStoreError as exc:
            assert "100 characters" in str(exc), str(exc)

        appstore.submit_for_review("VER1")
        assert api.sent("POST", "/appStoreVersionSubmissions")


def test_appstore_screenshot_upload():
    with MockAPI() as api:
        appstore = _appstore(api)
        image = tmpfile("screen.png", 1024)
        api.json_route("POST", r"/v1/appScreenshots", lambda req, m: {"data": {
            "id": "SHOT1",
            "attributes": {"uploadOperations": [
                {"method": "PUT", "url": api.base + "/blob/part1", "offset": 0,
                 "length": 512, "requestHeaders": [{"name": "X-Part", "value": "1"}]},
                {"method": "PUT", "url": api.base + "/blob/part2", "offset": 512,
                 "length": 512, "requestHeaders": []}]}}})
        api.json_route("PUT", r"/blob/part\d", b"")
        api.json_route("PATCH", r"/v1/appScreenshots/SHOT1", {"data": {"id": "SHOT1"}})

        appstore.upload_screenshot("SET1", image)
        reserve = api.one("POST", "/appScreenshots").json()
        assert reserve["data"]["attributes"]["fileSize"] == 1024
        parts = api.sent("PUT", "/blob/")
        assert len(parts) == 2, "both upload operations must run"
        assert len(parts[0].body) == 512 and len(parts[1].body) == 512, "wrong byte ranges"
        assert parts[0].headers.get("x-part") == "1", "Apple's request headers must be sent"
        commit = api.one("PATCH", "/appScreenshots/SHOT1").json()
        assert commit["data"]["attributes"]["uploaded"] is True
        assert len(commit["data"]["attributes"]["sourceFileChecksum"]) == 32, "md5 expected"


# ========================================================== Stock media =====

def _stock(api: MockAPI):
    from digital_assets_studio.core import keyvault
    from digital_assets_studio.core.publishing import stockvideo as sv
    sv.PEXELS_VIDEO_URL = api.base + "/pexels/videos/search"
    sv.PEXELS_PHOTO_URL = api.base + "/pexels/v1/search"
    sv.PIXABAY_VIDEO_URL = api.base + "/pixabay/videos"
    sv.PIXABAY_PHOTO_URL = api.base + "/pixabay/photos"
    keyvault.set_secret(sv.PEXELS_KEY, "pexels-key")
    keyvault.set_secret(sv.PIXABAY_KEY, "pixabay-key")
    return sv


def test_stock_video_search_and_download():
    with MockAPI() as api:
        sv = _stock(api)
        api.json_route("GET", r"/pexels/videos/search", lambda req, m: {"videos": [
            {"duration": 12, "image": "prev",
             "video_files": [
                 {"file_type": "video/mp4", "width": 640, "height": 360,
                  "link": api.base + f"/clip/small_{req.query['query'].replace(' ', '_')}.mp4"},
                 {"file_type": "video/mp4", "width": 1920, "height": 1080,
                  "link": api.base + f"/clip/hd_{req.query['query'].replace(' ', '_')}.mp4"},
             ]}]})
        api.json_route("GET", r"/pixabay/videos", {"hits": [
            {"duration": 9, "videos": {"large": {"url": "http://x/large.mp4",
                                                 "width": 1920, "height": 1080}}}]})
        api.route("GET", r"/clip/.*")(lambda req, m: (200, {"Content-Type": "video/mp4"},
                                                      b"m" * 50_000))

        clips = sv.search("market stall", source="pexels", portrait=False, limit=5)
        assert clips, "no clips parsed"
        assert clips[0].width == 1920, "should take the smallest file that still clears 720p"
        q = api.one("GET", "/pexels/videos/search")
        assert q.headers.get("authorization") == "pexels-key", "Pexels uses a bare key header"
        assert q.query["orientation"] == "landscape"

        pix = sv.search("shop", source="pixabay", portrait=False)
        assert pix and pix[0].source == "pixabay"
        assert api.one("GET", "/pixabay/videos").query["key"] == "pixabay-key"

        dest = Path(WORK) / "clips"
        files = sv.gather(["market stall", "hands typing"], seconds_needed=20, dest=dest,
                          source="pexels", clip_seconds=5)
        assert len(files) >= 2, f"expected several clips, got {files}"
        assert all(f.stat().st_size == 50_000 for f in files), "clips did not download"
        again = sv.gather(["market stall"], seconds_needed=6, dest=dest, source="pexels")
        assert again, "a second gather should reuse the cache"


def test_stock_photos_for_covers():
    with MockAPI() as api:
        sv = _stock(api)
        api.json_route("GET", r"/pexels/v1/search", {"photos": [
            {"width": 3000, "height": 1500, "photographer": "Wide Guy",
             "src": {"original": api.base + "/img/wide.jpg"}, "alt": "wide"},
            {"width": 1500, "height": 2400, "photographer": "Tall Person",
             "src": {"original": api.base + "/img/tall.jpg"}, "alt": "tall"},
            {"width": 900, "height": 1440, "photographer": "Too Small",
             "src": {"original": api.base + "/img/small.jpg"}, "alt": "small"},
        ]})
        api.route("GET", r"/img/.*")(lambda req, m: (200, {"Content-Type": "image/jpeg"},
                                                     b"\xff\xd8" + b"j" * 4000))

        data, photo = sv.best_photo(["salt crystals"], source="pexels", orientation="portrait",
                                    min_width=1400, target_ratio=1 / 1.6)
        assert photo.credit == "Tall Person", \
            f"the photo nearest a cover's shape should win, got {photo.credit}"
        assert photo.width == 1500, "the under-size photo should have been filtered out"
        assert len(data) > 1000, "the image bytes did not download"
        assert "Pexels" in sv.credit_line(photo)
        assert api.one("GET", "/pexels/v1/search").query["orientation"] == "portrait"


def test_stock_missing_keys():
    from digital_assets_studio.core import keyvault
    from digital_assets_studio.core.publishing import stockvideo as sv
    keyvault.delete_secret(sv.PEXELS_KEY)
    try:
        sv.search("x", source="pexels")
        raise AssertionError("a missing key should raise")
    except sv.StockError as exc:
        assert "Settings" in str(exc) and "free" in str(exc), str(exc)
    keyvault.set_secret(sv.PEXELS_KEY, "pexels-key")


# ==================================================== MoneyPrinterTurbo =====

def test_mpt_end_to_end():
    from digital_assets_studio.core.publishing import mpt
    with MockAPI() as api:
        mpt.save_base_url(api.base)
        polls = {"n": 0}

        api.json_route("POST", r"/api/v1/videos",
                       {"status": 200, "data": {"task_id": "TASK1"}})

        def task(req, m):
            polls["n"] += 1
            if polls["n"] < 3:
                return {"status": 200, "data": {"state": 4, "progress": polls["n"] * 30}}
            return {"status": 200, "data": {"state": 1, "progress": 100,
                                            "combined_videos": ["final-1.mp4"]}}

        api.json_route("GET", r"/api/v1/tasks/TASK1", task)
        api.route("GET", r"/api/v1/download/final-1.mp4")(
            lambda req, m: (200, {"Content-Type": "video/mp4"}, b"v" * 60_000))
        api.json_route("GET", r"/api/v1/musics", {"status": 200, "data": {"files": []}})

        assert "reachable" in mpt.ping()

        seen = []
        out = mpt.generate("AI for shopkeepers", Path(WORK) / "mpt.mp4",
                           script="Some narration", terms=["shop", "india"], aspect="9:16",
                           progress=lambda f, m: seen.append(m))
        assert out.stat().st_size == 60_000, "the finished video did not download"
        body = api.one("POST", "/api/v1/videos").json()
        assert body["video_aspect"] == "9:16" and body["video_subject"] == "AI for shopkeepers"
        assert body["video_terms"] == ["shop", "india"]
        assert polls["n"] >= 3, "it should have polled until the video appeared"
        assert any("%" in m for m in seen), "progress should report percentages"


def test_mpt_failure_paths():
    from digital_assets_studio.core.publishing import mpt
    with MockAPI() as api:
        mpt.save_base_url(api.base)
        api.json_route("POST", r"/api/v1/videos", {"status": 200, "data": {"task_id": "T2"}})
        api.json_route("GET", r"/api/v1/tasks/T2",
                       {"status": 200, "data": {"state": -1, "message": "no pexels key"}})
        try:
            mpt.generate("x", Path(WORK) / "no.mp4", progress=None)
            raise AssertionError("a failed task should raise")
        except mpt.MPTError as exc:
            assert "failed" in str(exc).lower(), str(exc)

    mpt.save_base_url("http://127.0.0.1:9")   # nothing listening
    try:
        mpt.ping()
        raise AssertionError("an unreachable server should raise")
    except mpt.MPTError as exc:
        assert "python main.py" in str(exc), f"the hint must name the right command: {exc}"


# ========================================================== Google auth =====

def test_service_account_and_refresh():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from digital_assets_studio.core.publishing import google_auth as ga

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()

    with MockAPI() as api:
        api.json_route("POST", r"/token", {"access_token": "ACCESS1", "expires_in": 3600})
        sa = ga.ServiceAccount.from_json(json.dumps({
            "client_email": "robot@project.iam.gserviceaccount.com",
            "private_key": pem, "token_uri": api.base + "/token"}))
        token = sa.access_token(ga.PLAY_SCOPE)
        assert token == "ACCESS1"
        sent = api.one("POST", "/token")
        form = dict(pair.split("=", 1) for pair in sent.text.split("&"))
        assert form["grant_type"].endswith("jwt-bearer")
        assert "assertion" in form and len(form["assertion"]) > 100, "no signed JWT was sent"

        ga.TOKEN_URL = api.base + "/token"
        assert ga.refresh_access_token("cid", "secret", "refresh") == "ACCESS1"

    with MockAPI() as api:
        api.json_route("POST", r"/token", {"error": "invalid_grant"}, status=400)
        ga.TOKEN_URL = api.base + "/token"
        try:
            ga.refresh_access_token("cid", "secret", "expired")
            raise AssertionError("invalid_grant should raise")
        except ga.AuthError as exc:
            assert "7 days" in str(exc) and "In production" in str(exc), \
                f"the message must explain the testing-mode expiry: {exc}"


# ============================ pipeline steps against the mocked services ====

def _fake_tts():
    """Real MP3s from ffmpeg, so durations and ffprobe are exercised for real,
    without needing network access to a speech service."""
    import subprocess

    from digital_assets_studio.core.publishing import tts

    def synth(text, out, engine="edge", voice="", rate="-3%"):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        seconds = max(1.0, min(len(text) / 15.0, 6.0))
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=320:duration={seconds}", "-c:a", "libmp3lame",
                        str(out)], capture_output=True, timeout=120, check=True)
        return tts.Clip(out, tts.ffprobe_duration(out), text)

    tts.synthesize = synth
    tts.synthesize_scenes = lambda scenes, out_dir, engine="edge", voice="", rate="-3%", \
        progress=None: [synth(t, Path(out_dir) / f"scene_{i:03d}.mp3")
                        for i, t in enumerate(scenes, 1)]
    return tts


def _project(kind: str, answers: dict, name: str):
    from digital_assets_studio.core import projects as pj
    proj = pj.create(name, kind, answers)
    proj.ensure_dirs()
    return proj


def test_youtube_publish_step():
    """The upload step must wire metadata, thumbnail, subtitles, playlist and the
    pinned comment together - not just call videos.insert."""
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.jobs import JobContext
    from digital_assets_studio.pipelines import get as get_pipeline
    from digital_assets_studio.pipelines.youtube import pipeline as ytp

    with MockAPI() as api:
        yt = _youtube_api(api)
        api.json_route("GET", "/youtube/v3/channels", CHANNEL)
        api.json_route("POST", "/upload/youtube/v3/videos",
                       lambda req, m: (_ for _ in ()).throw(AssertionError("should be resumable")))
        api.routes.pop()

        @api.route("POST", r"/upload/youtube/v3/videos")
        def start(req, m):
            return 200, {"Location": api.base + "/resume/x"}, b"{}"

        @api.route("PUT", r"/resume/x")
        def put(req, m):
            return 200, {"Content-Type": "application/json"}, json.dumps({"id": "VID9"}).encode()

        api.json_route("POST", "/upload/youtube/v3/thumbnails/set", {"items": [{}]})
        api.json_route("POST", "/upload/youtube/v3/captions", {"id": "c1"})
        api.json_route("GET", "/youtube/v3/playlists", {"items": []})
        api.json_route("POST", "/youtube/v3/playlists", {"id": "PLNEW"})
        api.json_route("POST", "/youtube/v3/playlistItems", {"id": "PLI"})
        api.json_route("POST", "/youtube/v3/commentThreads", {"id": "CT"})

        proj = _project("youtube", {
            "topic": "t", "audience": "a", "language": "English", "format": "Shorts only",
            "theme": "Light and warm", "mode": "Use a channel I already have",
            "episode_slug": "ep-one", "episode_title": "Episode One",
            "privacy": "public", "playlist_name": "Season 1", "thumbnail_choice": 1,
        }, "YT publish")
        proj.write_text("drafts/episodes/ep-one.json", json.dumps(
            {"hook": "Hook line", "scenes": [{"narration": "Body"}]}))
        proj.write_text("drafts/episodes/ep-one.metadata.json", json.dumps(
            {"titles": ["Chosen title"], "description": "Full description",
             "tags": ["ai", "shops"], "pinned_comment": "Thanks for watching"}))
        proj.write_bytes("build/ep-one.mp4", b"v" * 20_000)
        proj.write_bytes("build/thumbnails/ep-one_v1.jpg", b"j" * 3000)
        proj.write_text("build/voice/ep-one.srt", "1\n00:00:00,000 --> 00:00:01,000\nhi\n")

        pl = get_pipeline("youtube")
        for req in ("metadata", "render"):
            pipe.mark_manual_done(proj, pl.step(req))
        result = pipe.execute(pl, proj, pl.step("upload"), JobContext("t", "t"))

        assert "youtu.be/VID9" in result.message, result.message
        assert proj.answer("video_id") == "VID9"
        assert api.sent("POST", "/thumbnails/set"), "the thumbnail was never set"
        assert api.sent("POST", "/captions"), "subtitles were never uploaded"
        assert api.sent("POST", "/playlists"), "the playlist was never created"
        assert api.sent("POST", "/playlistItems"), "the video was never added to the playlist"
        assert api.sent("POST", "/commentThreads"), "the pinned comment was never posted"
        body = api.one("POST", "/upload/youtube/v3/videos").json()
        assert body["snippet"]["title"] == "Chosen title", body
        assert body["snippet"]["tags"] == ["ai", "shops"]
        assert body["status"]["privacyStatus"] == "public"


def test_play_publish_step():
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.jobs import JobContext
    from digital_assets_studio.pipelines import get as get_pipeline

    with MockAPI() as api:
        play = _play(api)
        pkg = "com.test.app"
        api.json_route("POST", rf"/androidpublisher/v3/applications/{pkg}/edits", {"id": "E9"})
        api.json_route("PUT", rf"/androidpublisher/v3/applications/{pkg}/edits/E9/listings/en-US", {})
        api.json_route("DELETE",
                       rf"/androidpublisher/v3/applications/{pkg}/edits/E9/listings/en-US/\w+", {})
        api.json_route("POST",
                       rf"/upload/androidpublisher/v3/applications/{pkg}/edits/E9/listings/en-US/\w+",
                       {"image": {"id": "i"}})
        api.json_route("POST", rf"/upload/androidpublisher/v3/applications/{pkg}/edits/E9/bundles",
                       {"versionCode": 7})
        api.json_route("PUT", rf"/androidpublisher/v3/applications/{pkg}/edits/E9/tracks/internal", {})
        api.json_route("POST", rf"/androidpublisher/v3/applications/{pkg}/edits/E9:commit", {})

        proj = _project("mobile", {
            "app_name": "Test App", "package": pkg, "what_it_does": "x", "audience": "y",
            "platforms": "Android only", "model": "Free", "play_track": "internal",
            "aab_path": str(tmpfile("release.aab", 4096)),
        }, "Play publish")
        proj.write_text("drafts/listing.json", json.dumps({
            "play_title": "Test App", "play_short": "Short line",
            "play_full": "Long description", "whats_new": "First"}))
        for i in range(1, 4):
            proj.write_bytes(f"build/screenshots/play/{i:02d}.png", b"p" * 1500)
        proj.write_bytes("build/feature_graphic.png", b"f" * 900)

        pl = get_pipeline("mobile")
        for req in ("screenshots", "host_policy"):
            pipe.mark_manual_done(proj, pl.step(req))
        result = pipe.execute(pl, proj, pl.step("play_publish"), JobContext("t", "t"))

        assert "version code 7" in result.message, result.message
        assert len(api.sent("POST", "/listings/en-US/phoneScreenshots")) == 3
        assert api.sent("POST", "/listings/en-US/featureGraphic"), "feature graphic not uploaded"
        assert api.sent("POST", "/edits/E9:commit"), "the release was never committed"
        listing = api.one("PUT", "/listings/en-US").json()
        assert listing["shortDescription"] == "Short line"


def test_podcast_produce_and_feed():
    """Real ffmpeg mastering, then a feed that has to validate as XML."""
    import xml.dom.minidom as minidom

    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.jobs import JobContext
    from digital_assets_studio.pipelines import get as get_pipeline

    tts = _fake_tts()
    proj = _project("podcast", {
        "topic": "t", "listener": "l", "format": "Solo narration", "minutes": 5,
        "cadence": "Weekly", "host": "Host", "contact_email": "a@b.com",
        "show_name": "The Show", "episode_slug": "ep001", "episode_number": 1,
        "media_base_url": "https://cdn.example.com/show",
    }, "Podcast publish")
    proj.write_text("drafts/concept.json", json.dumps(
        {"recommended": "The Show", "description": "A show", "category": "Technology"}))
    proj.write_text("drafts/episodes/ep001.json", json.dumps(
        {"title": "Ep one", "cold_open": "Hook", "blocks": [{"speaker": "host", "text": "Body"}],
         "outro": "Bye"}))
    proj.write_text("drafts/episodes/ep001.notes.json", json.dumps(
        {"title": "Ep one", "summary": "A summary", "chapters": []}))
    for i, text in enumerate(["Hook", "Body words here", "Bye"], start=1):
        tts.synthesize(text, proj.dir / "build" / "raw" / "ep001" / f"{i:03d}_host.mp3")

    pl = get_pipeline("podcast")
    for req in ("concept", "choose", "script", "record", "shownotes"):
        pipe.mark_manual_done(proj, pl.step(req))
    produced = pipe.execute(pl, proj, pl.step("produce"), JobContext("t", "t"))
    assert proj.exists("build/ep001.mp3"), "no episode audio produced"
    assert "RMS" in produced.message, produced.message

    feed = pipe.execute(pl, proj, pl.step("feed"), JobContext("t", "t"))
    xml = proj.read_text("build/feed.xml")
    doc = minidom.parseString(xml)
    item = doc.getElementsByTagName("item")[0]
    enclosure = item.getElementsByTagName("enclosure")[0]
    assert enclosure.getAttribute("url") == "https://cdn.example.com/show/ep001.mp3", \
        enclosure.getAttribute("url")
    assert int(enclosure.getAttribute("length")) == (proj.dir / "build/ep001.mp3").stat().st_size
    assert doc.getElementsByTagName("itunes:duration")[0].firstChild.data != "00:00:00", \
        "the feed must carry a real duration"


def test_audiobook_master_and_package():
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.jobs import JobContext
    from digital_assets_studio.pipelines import get as get_pipeline

    tts = _fake_tts()
    proj = _project("audiobook", {"title": "Ship It", "author": "A Writer"}, "Audiobook publish")
    proj.write_text("drafts/chapters.json", json.dumps(
        [{"number": 1, "title": "One"}, {"number": 2, "title": "Two"}]))
    for name, text in (("000_opening", "Ship It by A Writer"), ("001", "Chapter one words"),
                       ("002", "Chapter two words"), ("999_closing", "The end")):
        tts.synthesize(text, proj.dir / "build" / "raw_audio" / f"{name}.mp3")

    pl = get_pipeline("audiobook")
    for req in ("gather", "prepare", "credits", "audition", "choose_voice", "narrate", "cover"):
        pipe.mark_manual_done(proj, pl.step(req))
    mastered = pipe.execute(pl, proj, pl.step("master"), JobContext("t", "t"))
    report = json.loads(proj.read_text("build/acx_report.json"))
    assert len(report) == 4, report
    assert all(r["acx"] for r in report), f"mastering left files outside ACX: {report}"
    assert "0 outside" in mastered.message, mastered.message

    packaged = pipe.execute(pl, proj, pl.step("package"), JobContext("t", "t"))
    assert proj.exists("build/audiobook.m4b"), "no M4B produced"
    assert proj.exists("build/retail_sample.mp3"), "no retail sample produced"
    assert (proj.dir / "build/audiobook.m4b").stat().st_size > 1000
    assert "chapters" in packaged.message


def test_course_video_step():
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.jobs import JobContext
    from digital_assets_studio.pipelines import get as get_pipeline

    tts = _fake_tts()
    proj = _project("course", {
        "subject": "s", "student": "st", "outcome": "o", "length": "Short (under 1 hour)",
        "price": "$49-99", "theme": "Light"}, "Course publish")
    proj.write_text("drafts/curriculum.json", json.dumps({"title": "C", "modules": [
        {"title": "M", "lessons": [{"title": "L1", "outcome": "o",
                                    "slides": [{"heading": "H", "bullets": ["a"]}],
                                    "script": "One.\n\nTwo.", "minutes": 5}]}]}))
    pl = get_pipeline("course")
    for req in ("curriculum", "review_curriculum"):
        pipe.mark_manual_done(proj, pl.step(req))
    pipe.execute(pl, proj, pl.step("slides"), JobContext("t", "t"))
    pipe.execute(pl, proj, pl.step("narrate"), JobContext("t", "t"))
    result = pipe.execute(pl, proj, pl.step("video"), JobContext("t", "t"))
    videos = list((proj.dir / "build" / "lessons").glob("*.mp4"))
    assert videos, "no lesson video rendered"
    assert videos[0].stat().st_size > 5000
    assert "1 lesson video" in result.message, result.message


def test_mastering_lands_in_window_at_any_length():
    """Regression: the gain was computed before room tone was added, so padding a
    short chapter with silence pulled the finished file below the ACX floor."""
    import subprocess

    from digital_assets_studio.core.publishing import audio

    for seconds, level in ((3.0, "-30dB"), (3.0, "-6dB"), (45.0, "-40dB"), (2.0, "-45dB")):
        src = Path(WORK) / "level.mp3"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        f"sine=frequency=300:duration={seconds}", "-af", f"volume={level}",
                        "-c:a", "libmp3lame", str(src)], capture_output=True, check=True)
        before, after = audio.master(src, Path(WORK) / "level_out.mp3")
        assert after.acx_ok, (f"{seconds}s at {level} finished at {after.mean_db:.1f} dB RMS / "
                              f"{after.peak_db:.1f} dB peak - outside ACX")
        assert after.seconds > before.seconds, "room tone was not added"


def test_appstore_push_step():
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.jobs import JobContext
    from digital_assets_studio.pipelines import get as get_pipeline

    with MockAPI() as api:
        appstore = _appstore(api)
        api.json_route("GET", r"/v1/apps", {"data": [
            {"id": "APP7", "attributes": {"name": "T", "bundleId": "com.test.app", "sku": "S"}}]})
        api.json_route("GET", r"/v1/apps/APP7/appStoreVersions", {"data": []})
        api.json_route("POST", r"/v1/appStoreVersions", {"data": {"id": "VER7"}})
        api.json_route("GET", r"/v1/appStoreVersions/VER7/appStoreVersionLocalizations",
                       {"data": []})
        api.json_route("POST", r"/v1/appStoreVersionLocalizations", {"data": {"id": "LOC7"}})

        proj = _project("mobile", {
            "app_name": "T", "bundle_id": "com.test.app", "what_it_does": "x", "audience": "y",
            "platforms": "iOS only", "model": "Free", "version_string": "1.2.0",
            "support_url": "https://example.com/support"}, "AppStore publish")
        proj.write_text("drafts/listing.json", json.dumps({
            "ios_description": "Description", "ios_keywords": "cards,qr",
            "ios_promotional_text": "Promo", "whats_new": "Notes"}))

        pl = get_pipeline("mobile")
        for req in ("screenshots", "host_policy"):
            pipe.mark_manual_done(proj, pl.step(req))
        result = pipe.execute(pl, proj, pl.step("appstore_push"), JobContext("t", "t"))

        assert "1.2.0" in result.message, result.message
        created = api.one("POST", "/appStoreVersions").json()
        assert created["data"]["attributes"]["versionString"] == "1.2.0"
        loc = api.one("POST", "/appStoreVersionLocalizations").json()
        assert loc["data"]["attributes"]["locale"] == "en-US", "a new localisation needs a locale"
        assert loc["data"]["attributes"]["keywords"] == "cards,qr"
        assert loc["data"]["attributes"]["supportUrl"] == "https://example.com/support"


def test_youtube_short_cut_step():
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.jobs import JobContext
    from digital_assets_studio.core.publishing import video
    from digital_assets_studio.pipelines import get as get_pipeline

    with MockAPI() as api:
        yt = _youtube_api(api)

        @api.route("POST", r"/upload/youtube/v3/videos")
        def start(req, m):
            return 200, {"Location": api.base + "/resume/s"}, b"{}"

        @api.route("PUT", r"/resume/s")
        def put(req, m):
            return 200, {"Content-Type": "application/json"}, json.dumps({"id": "SHORT1"}).encode()

        proj = _project("youtube", {
            "topic": "t", "audience": "a", "language": "English", "format": "Shorts only",
            "theme": "Light and warm", "mode": "Use a channel I already have",
            "episode_slug": "ep-two", "episode_title": "Ep two",
            "short_start": 1, "short_seconds": 4, "upload_short": True,
        }, "Short cut")
        proj.write_text("drafts/episodes/ep-two.json", json.dumps({"hook": "h", "scenes": []}))
        proj.write_text("drafts/episodes/ep-two.metadata.json",
                        json.dumps({"titles": ["Short title"], "description": "d", "tags": []}))
        # a real landscape video to crop
        import subprocess
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=1280x720:rate=30:duration=8", "-f", "lavfi", "-i",
                        "sine=frequency=300:duration=8", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(proj.dir / "build" / "ep-two.mp4")],
                       capture_output=True, check=True)

        pl = get_pipeline("youtube")
        pipe.mark_manual_done(proj, pl.step("render"))
        result = pipe.execute(pl, proj, pl.step("shorts"), JobContext("t", "t"))

        out = proj.dir / "build" / "ep-two_short.mp4"
        assert out.exists(), "no Short produced"
        info = video.probe(out)
        stream = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        assert (stream["width"], stream["height"]) == (1080, 1920), \
            f"a Short must be vertical, got {stream['width']}x{stream['height']}"
        assert abs(float(info["format"]["duration"]) - 4.0) < 0.6, info["format"]["duration"]
        assert "SHORT1" in result.message, result.message


def _youtube_project_for_render(engine: str, name: str):
    proj = _project("youtube", {
        "topic": "t", "audience": "a", "language": "English", "format": "Shorts only",
        "theme": "Light and warm", "mode": "Use a channel I already have",
        "video_engine": engine, "episode_slug": "ep-r", "episode_title": "Render test",
        "orientation": "Portrait 9:16", "clip_seconds": 4,
    }, name)
    proj.write_text("drafts/episodes/ep-r.json", json.dumps({
        "hook": "The hook line", "scenes": [
            {"heading": "One", "narration": "First block of narration",
             "on_screen": "one", "b_roll_prompt": "a market stall at dawn"},
            {"heading": "Two", "narration": "Second block of narration",
             "on_screen": "two", "b_roll_prompt": "hands typing on a laptop"}]}))
    return proj


def test_render_with_stock_engine():
    """The stock engine end to end: search terms, downloaded clips, real ffmpeg."""
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.jobs import JobContext
    from digital_assets_studio.core.publishing import video
    from digital_assets_studio.pipelines import get as get_pipeline

    tts = _fake_tts()
    with MockAPI() as api:
        sv = _stock(api)
        import subprocess
        clip = Path(WORK) / "stockclip.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=1280x720:rate=30:duration=6", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(clip)], capture_output=True, check=True)
        clip_bytes = clip.read_bytes()

        api.json_route("GET", r"/pexels/videos/search", lambda req, m: {"videos": [
            {"duration": 6, "video_files": [
                {"file_type": "video/mp4", "width": 1280, "height": 720,
                 "link": api.base + f"/c/{req.query['query'].replace(' ', '')}.mp4"}]}]})
        api.route("GET", r"/c/.*")(lambda req, m: (200, {"Content-Type": "video/mp4"}, clip_bytes))

        proj = _youtube_project_for_render("Stock footage (Pexels / Pixabay)", "Stock render")
        proj.write_text("drafts/episodes/ep-r.stock_terms.json",
                        json.dumps({"all": ["market stall", "hands typing"]}))
        for i, text in enumerate(["The hook line", "First block", "Second block"], start=1):
            tts.synthesize(text, proj.dir / "build" / "voice" / "ep-r" / f"scene_{i:03d}.mp3")

        pl = get_pipeline("youtube")
        for req in ("script", "fix_script", "voiceover", "stock_terms"):
            pipe.mark_manual_done(proj, pl.step(req))
        pipe.execute(pl, proj, pl.step("stock_footage"), JobContext("t", "t"))
        assert list((proj.dir / "build" / "stock" / "ep-r").glob("*.mp4")), "no clips downloaded"

        result = pipe.execute(pl, proj, pl.step("render"), JobContext("t", "t"))
        out = proj.dir / "build" / "ep-r.mp4"
        assert out.exists(), "no video rendered"
        info = video.probe(out)
        stream = [s for s in info["streams"] if s["codec_type"] == "video"][0]
        assert (stream["width"], stream["height"]) == (1080, 1920), \
            f"portrait was requested, got {stream['width']}x{stream['height']}"
        assert float(info["format"]["duration"]) > 2, "the render is suspiciously short"
        assert "Stock footage" in result.message, result.message


def test_render_with_moneyprinterturbo():
    from digital_assets_studio.core import pipeline as pipe
    from digital_assets_studio.core.jobs import JobContext
    from digital_assets_studio.core.publishing import mpt
    from digital_assets_studio.pipelines import get as get_pipeline

    with MockAPI() as api:
        mpt.save_base_url(api.base)
        import subprocess
        made = Path(WORK) / "mptvideo.mp4"
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                        "testsrc=size=1080x1920:rate=30:duration=3", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(made)], capture_output=True, check=True)
        payload = made.read_bytes()

        api.json_route("POST", r"/api/v1/videos", {"status": 200, "data": {"task_id": "T9"}})
        api.json_route("GET", r"/api/v1/tasks/T9",
                       {"status": 200, "data": {"state": 1, "progress": 100,
                                                "combined_videos": ["out.mp4"]}})
        api.route("GET", r"/api/v1/download/out.mp4")(
            lambda req, m: (200, {"Content-Type": "video/mp4"}, payload))

        proj = _youtube_project_for_render("MoneyPrinterTurbo server", "MPT render")
        proj.write_text("drafts/episodes/ep-r.stock_terms.json", json.dumps({"all": ["shop"]}))

        pl = get_pipeline("youtube")
        for req in ("script", "fix_script"):
            pipe.mark_manual_done(proj, pl.step(req))
        assert pl.blocked(proj, pl.step("render")) == [], \
            "with MoneyPrinterTurbo the render step needs nothing but the script"
        result = pipe.execute(pl, proj, pl.step("render"), JobContext("t", "t"))

        assert (proj.dir / "build" / "ep-r.mp4").exists(), "the finished video was not saved"
        sent = api.one("POST", "/api/v1/videos").json()
        assert sent["video_aspect"] == "9:16", sent
        assert "The hook line" in sent["video_script"], "the script was not handed over"
        assert "MoneyPrinterTurbo" in result.message, result.message


def test_kdp_browser_without_playwright():
    """With Playwright absent the step must explain how to install it, not crash."""
    from digital_assets_studio.core.publishing import browser
    real = browser.available
    browser.available = lambda: False
    try:
        from digital_assets_studio.core import pipeline as pipe
        from digital_assets_studio.core.jobs import JobContext
        from digital_assets_studio.pipelines import get as get_pipeline

        proj = _project("book", {"category": "Fantasy", "audience": "a", "word_target": 1000,
                                 "tone": "Literary", "final_title": "T"}, "KDP browser")
        pl = get_pipeline("book")
        pipe.mark_manual_done(proj, pl.step("pack"))
        try:
            pipe.execute(pl, proj, pl.step("kdp_prefill"), JobContext("t", "t"))
            raise AssertionError("it should refuse without Playwright")
        except RuntimeError as exc:
            assert "pip install playwright" in str(exc), str(exc)
    finally:
        browser.available = real


if __name__ == "__main__":
    print(f"workspace: {WORK}\n")
    check("youtube: channel read and recent uploads", test_youtube_channel_read)
    check("youtube: resumable upload in chunks", test_youtube_resumable_upload)
    check("youtube: thumbnail, captions, playlists, comment", test_youtube_extras)
    check("youtube: error messages are useful", test_youtube_errors)
    check("play: full release in one edit", test_play_full_release)
    check("play: guards, rollback and errors", test_play_guards_and_errors)
    check("appstore: metadata and submission", test_appstore_metadata_and_screenshots)
    check("appstore: three-step screenshot upload", test_appstore_screenshot_upload)
    check("stock: video search and download", test_stock_video_search_and_download)
    check("stock: photos ranked for covers", test_stock_photos_for_covers)
    check("stock: missing keys explain themselves", test_stock_missing_keys)
    check("mpt: create, poll, download", test_mpt_end_to_end)
    check("mpt: failure paths", test_mpt_failure_paths)
    check("google auth: service account and refresh", test_service_account_and_refresh)
    print()
    check("pipeline: youtube upload wires everything", test_youtube_publish_step)
    check("pipeline: play release from a real project", test_play_publish_step)
    check("pipeline: podcast audio and RSS feed", test_podcast_produce_and_feed)
    check("pipeline: audiobook mastering and M4B", test_audiobook_master_and_package)
    check("pipeline: course lesson video", test_course_video_step)
    check("pipeline: stock-footage render end to end", test_render_with_stock_engine)
    check("pipeline: MoneyPrinterTurbo render", test_render_with_moneyprinterturbo)
    check("pipeline: appstore metadata push", test_appstore_push_step)
    check("pipeline: Short is cut vertical and uploaded", test_youtube_short_cut_step)
    check("audio: mastering lands in the ACX window at any length",
          test_mastering_lands_in_window_at_any_length)
    check("pipeline: KDP step without playwright", test_kdp_browser_without_playwright)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S)")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print(f"all {PASSED} green")
