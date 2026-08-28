"""YouTube Data API v3: upload, thumbnail, captions, playlists, branding.

This is the real thing - a video published from here appears on the channel.
Uploads use the resumable protocol so a dropped connection resumes instead of
starting a 400 MB file again.

**Channels.** A YouTube OAuth token is bound to one channel: the API has no
per-upload channel switch, because the channel is decided in the browser at
sign-in, on the account picker. Running several channels therefore means several
sign-ins, so every credential here lives under an *account* - a named slot with
its own refresh token, sharing the one OAuth client. Every call takes an
``account`` argument; leaving it blank uses the one you marked as default, and
an ambiguous blank raises rather than quietly publishing to whichever channel
happened to be connected first.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import httpx

from ...config import WORKSPACE
from .google_auth import YOUTUBE_SCOPES, AuthError, TokenStore  # noqa: F401 - AuthError re-exported

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
CHUNK = 8 * 1024 * 1024

# The first channel anyone connects keeps the original credential names, so an
# install that predates multi-channel support carries on working untouched.
DEFAULT_SLUG = "default"
STORE = TokenStore("youtube")

ACCOUNTS_FILE = WORKSPACE / "youtube_accounts.json"

CATEGORIES = {
    "Film & Animation": "1", "Autos & Vehicles": "2", "Music": "10", "Pets & Animals": "15",
    "Sports": "17", "Travel & Events": "19", "Gaming": "20", "People & Blogs": "22",
    "Comedy": "23", "Entertainment": "24", "News & Politics": "25", "Howto & Style": "26",
    "Education": "27", "Science & Technology": "28", "Nonprofits & Activism": "29",
}


class YouTubeError(RuntimeError):
    pass


# ------------------------------------------------------------------ accounts --

@dataclass
class Account:
    """One connected channel: its own refresh token, the shared OAuth client."""
    slug: str = DEFAULT_SLUG
    label: str = ""
    title: str = ""
    handle: str = ""
    channel_id: str = ""

    @property
    def store(self) -> TokenStore:
        return STORE if self.slug == DEFAULT_SLUG else TokenStore(f"youtube.{self.slug}")

    @property
    def connected(self) -> bool:
        return self.store.connected

    @property
    def display(self) -> str:
        """What the user picks from. Stable enough to store in a project answer."""
        if self.title:
            return f"{self.title} ({self.handle})" if self.handle else self.title
        return self.label or self.slug


_registry_lock = threading.RLock()


def _read_registry() -> dict:
    if not ACCOUNTS_FILE.exists():
        return {"default": DEFAULT_SLUG, "accounts": []}
    try:
        data = json.loads(ACCOUNTS_FILE.read_text("utf-8"))
    except Exception:  # noqa: BLE001
        log.warning("youtube_accounts.json unreadable; starting from one account")
        return {"default": DEFAULT_SLUG, "accounts": []}
    if not isinstance(data, dict):
        return {"default": DEFAULT_SLUG, "accounts": []}
    data.setdefault("default", DEFAULT_SLUG)
    data.setdefault("accounts", [])
    return data


def _write_registry(data: dict) -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    tmp = ACCOUNTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    tmp.replace(ACCOUNTS_FILE)


def accounts() -> list[Account]:
    """Every account slot, the default one first. Always at least one."""
    with _registry_lock:
        data = _read_registry()
    known: list[Account] = []
    seen: set[str] = set()
    for raw in data.get("accounts", []):
        if not isinstance(raw, dict) or not raw.get("slug") or raw["slug"] in seen:
            continue
        seen.add(raw["slug"])
        known.append(Account(**{k: raw.get(k, "") or "" for k in
                                ("slug", "label", "title", "handle", "channel_id")}))
    if DEFAULT_SLUG not in seen:
        known.insert(0, Account(DEFAULT_SLUG, label="First channel"))
    return known


def get_account(slug: str) -> Account:
    """The slot with this slug, or an empty one carrying it."""
    for a in accounts():
        if a.slug == slug:
            return a
    return Account(slug or DEFAULT_SLUG)


def connected_accounts() -> list[Account]:
    return [a for a in accounts() if a.connected]


def default_slug() -> str:
    """The slot uploads go to when a project does not name one."""
    with _registry_lock:
        want = _read_registry().get("default") or DEFAULT_SLUG
    live = connected_accounts()
    if any(a.slug == want for a in live):
        return want
    if len(live) == 1:
        return live[0].slug
    return want


def set_default(slug: str) -> None:
    with _registry_lock:
        data = _read_registry()
        data["default"] = slug or DEFAULT_SLUG
        _write_registry(data)


def _save_account(acc: Account) -> None:
    """Write one slot back, in place.

    In place matters: the settings screen lists these in registry order, and
    re-reading a channel used to drop it to the bottom of your own list."""
    with _registry_lock:
        data = _read_registry()
        rows = [r for r in data.get("accounts", []) if isinstance(r, dict) and r.get("slug")]
        row = asdict(acc)
        for i, existing in enumerate(rows):
            if existing.get("slug") == acc.slug:
                rows[i] = row
                break
        else:
            rows.append(row)
        data["accounts"] = rows
        _write_registry(data)


def add_account(label: str = "") -> Account:
    """Make a new empty slot. Nothing is connected until connect() runs on it."""
    taken = {a.slug for a in accounts()}
    if DEFAULT_SLUG not in taken or not get_account(DEFAULT_SLUG).connected:
        acc = Account(DEFAULT_SLUG, label=label or "First channel")
    else:
        base = re.sub(r"[^a-z0-9]+", "-", (label or "channel").lower()).strip("-") or "channel"
        slug, n = base, 2
        while slug in taken:
            slug, n = f"{base}-{n}", n + 1
        acc = Account(slug, label=label or slug)
    _save_account(acc)
    return acc


def remove_account(slug: str) -> None:
    """Forget a slot and the sign-in behind it. The default slot is emptied, not
    deleted - it is where a single-channel install lives."""
    acc = get_account(slug)
    try:
        acc.store.disconnect()
    except Exception:  # noqa: BLE001
        log.warning("could not clear the saved sign-in for %s", slug)
    with _registry_lock:
        data = _read_registry()
        if slug == DEFAULT_SLUG:
            data["accounts"] = [dict(r, title="", handle="", channel_id="")
                                if isinstance(r, dict) and r.get("slug") == slug else r
                                for r in data.get("accounts", [])]
        else:
            data["accounts"] = [r for r in data.get("accounts", [])
                                if isinstance(r, dict) and r.get("slug") != slug]
            if data.get("default") == slug:
                data["default"] = DEFAULT_SLUG
        _write_registry(data)


def save_client(client_id: str, client_secret: str) -> None:
    """One OAuth client serves every channel - the account picker in the browser
    is what separates them - so the credentials are mirrored into each slot."""
    STORE.save_client(client_id, client_secret)
    for acc in accounts():
        if acc.slug != DEFAULT_SLUG:
            acc.store.save_client(client_id, client_secret)


def oauth_client() -> tuple[str, str]:
    """The shared Desktop-app OAuth client every channel signs in through."""
    return STORE.client()


def resolve(answer: str = "") -> str:
    """Turn whatever a project recorded into an account slug.

    Accepts a slug, a channel title, a handle, or the label shown in the picker.
    A blank answer means the default channel - never 'whichever signed in first',
    which is the failure this whole mechanism exists to prevent. It raises rather
    than guess in the one case that is genuinely ambiguous: several channels
    connected and the default among them gone.
    """
    want = (answer or "").strip()
    live = connected_accounts()
    if want:
        for a in accounts():
            if want in (a.slug, a.display, a.label, a.title, a.channel_id):
                return a.slug
        lowered = want.lower().lstrip("@")
        for a in accounts():
            if lowered in (a.display.lower(), (a.title or "").lower(),
                           (a.handle or "").lower().lstrip("@")):
                return a.slug
        raise YouTubeError(
            f"No connected YouTube channel matches '{want}'. Connected right now: "
            f"{', '.join(a.display for a in live) or 'none'}. Connect it in "
            f"Settings > Publishing > YouTube, or pick a different one on this step.")
    if len(live) <= 1:
        return live[0].slug if live else default_slug()
    want = default_slug()
    if any(a.slug == want for a in live):
        return want
    raise YouTubeError(
        "Several YouTube channels are connected and none of them is marked as the "
        "default, so there is no safe channel to publish to.\n\nPick one in the "
        "'YouTube channel' box on this step, or star one in Settings > Publishing > "
        "YouTube. Connected: " + ", ".join(a.display for a in live))


def channel_choices() -> list[str]:
    """Labels for the channel picker, the default one first.

    The order matters: a dropdown shows its first entry when a project has not
    chosen yet, and what it shows has to be the channel that would actually
    receive the upload."""
    live = connected_accounts()
    want = default_slug()
    live.sort(key=lambda a: a.slug != want)
    return [a.display for a in live]


def _store(account: str = "") -> TokenStore:
    return get_account(resolve(account)).store


def refresh_account(slug: str = "") -> Account:
    """Ask YouTube who this slot actually is, and remember it."""
    slug = slug or DEFAULT_SLUG
    acc = get_account(slug)
    info = channel_summary(account=slug)
    acc.title = info.get("title", "")
    acc.handle = info.get("handle", "")
    acc.channel_id = info.get("id", "")
    if not acc.label:
        acc.label = acc.title or slug
    _save_account(acc)
    return acc


# --------------------------------------------------------------------- auth --

def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        try:
            err = r.json().get("error", {})
            msg = err.get("message", r.text[:300])
            reasons = ", ".join(e.get("reason", "") for e in err.get("errors", []))
        except Exception:  # noqa: BLE001
            msg, reasons = r.text[:300], ""
        hint = ""
        if "quota" in (msg + reasons).lower():
            hint = ("  A new project gets its own upload bucket - 100 videos.insert calls a day - "
                    "plus 10,000 units a day for everything else. A fully decorated upload "
                    "(thumbnail 50, captions 400, playlist 50) spends about 550 of those units, "
                    "so the 10,000 runs out around 18 videos before the upload bucket does. "
                    "Request more in the Google Cloud console, or skip captions on bulk runs.")
        if r.status_code == 403 and "youtubeSignupRequired" in reasons:
            hint = "  That Google account has no YouTube channel yet."
        raise YouTubeError(f"YouTube API {r.status_code}: {msg}{hint}")
    return r.json() if r.content else {}


def connected(account: str = "") -> bool:
    """True when the named slot can upload; blank means 'any channel at all'."""
    if account:
        try:
            return get_account(resolve(account)).connected
        except YouTubeError:
            return False
    return bool(connected_accounts())


def connect(account: str = "") -> str:
    """Opens the browser once; afterwards the refresh token does the work."""
    slug = (account or "").strip() or DEFAULT_SLUG
    acc = get_account(slug)
    if acc.slug != DEFAULT_SLUG:
        # a fresh slot has no client of its own yet
        acc.store.save_client(*STORE.client())
    acc.store.connect(YOUTUBE_SCOPES)
    try:
        acc = refresh_account(acc.slug)
    except Exception as exc:  # noqa: BLE001
        _save_account(acc)
        log.warning("connected but could not read the channel back: %s", exc)
        return "Connected to YouTube."
    if len(connected_accounts()) == 1:
        set_default(acc.slug)
    return f"Connected to {acc.display}."


def token(account: str = "") -> str:
    return _store(account).token(YOUTUBE_SCOPES)


def disconnect(account: str = "") -> None:
    remove_account(resolve(account) if account else DEFAULT_SLUG)


def my_channel(account: str = "") -> dict:
    r = httpx.get(f"{API}/channels",
                  params={"part": "snippet,statistics,brandingSettings,contentDetails",
                          "mine": "true"},
                  headers=_headers(token(account)), timeout=60)
    data = _check(r)
    items = data.get("items") or []
    if not items:
        raise YouTubeError("That Google account does not own a YouTube channel yet. "
                           "Create one at youtube.com first - it takes a minute and cannot be done by API.")
    return items[0]


def upload_video(
    path: Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    category: str = "Education",
    privacy: str = "private",
    publish_at: str | None = None,
    made_for_kids: bool = False,
    language: str = "en",
    progress: Callable[[float, str], None] | None = None,
    account: str = "",
) -> dict:
    """Resumable upload. privacy: private | unlisted | public.
    publish_at is an RFC3339 UTC timestamp and requires privacy='private'."""
    path = Path(path)
    if not path.exists():
        raise YouTubeError(f"Video file not found: {path}")
    size = path.stat().st_size

    status: dict = {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids}
    if publish_at:
        status["publishAt"] = publish_at
        status["privacyStatus"] = "private"

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": (tags or [])[:30],
            "categoryId": CATEGORIES.get(category, "27"),
            "defaultLanguage": language,
            "defaultAudioLanguage": language,
        },
        "status": status,
    }

    tk = token(account)
    init = httpx.post(
        f"{UPLOAD}/videos",
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={**_headers(tk), "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Length": str(size), "X-Upload-Content-Type": "video/*"},
        content=json.dumps(body), timeout=120,
    )
    _check(init)
    session_url = init.headers.get("Location")
    if not session_url:
        raise YouTubeError("YouTube did not return an upload session URL.")

    sent = 0
    with path.open("rb") as fh,             httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0)) as http:
        while sent < size:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            end = sent + len(chunk) - 1
            for attempt in range(4):
                r = http.put(
                    session_url, content=chunk,
                    headers={"Content-Length": str(len(chunk)),
                             "Content-Range": f"bytes {sent}-{end}/{size}"},
                )
                if r.status_code in (200, 201):
                    if progress:
                        progress(1.0, "Upload complete")
                    return r.json()
                if r.status_code == 308:
                    sent = end + 1
                    if progress:
                        progress(sent / size, f"Uploaded {sent / 1_048_576:.0f} of {size / 1_048_576:.0f} MB")
                    break
                if r.status_code in (500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                _check(r)
            else:
                raise YouTubeError("Upload stalled after repeated server errors.")
    raise YouTubeError("Upload finished without a confirmation from YouTube.")


def set_thumbnail(video_id: str, image: Path, account: str = "") -> dict:
    image = Path(image)
    if image.stat().st_size > 2 * 1024 * 1024:
        raise YouTubeError("Thumbnails must be under 2 MB.")
    r = httpx.post(f"{UPLOAD}/thumbnails/set", params={"videoId": video_id},
                   headers={**_headers(token(account)), "Content-Type": "image/jpeg"},
                   content=image.read_bytes(), timeout=180)
    return _check(r)


def upload_caption(video_id: str, caption_file: Path, language: str = "en",
                   name: str = "Subtitles", account: str = "") -> dict:
    caption_file = Path(caption_file)
    meta = {"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}}
    files = {
        "metadata": ("metadata.json", json.dumps(meta), "application/json"),
        "file": (caption_file.name, caption_file.read_bytes(), "application/octet-stream"),
    }
    r = httpx.post(f"{UPLOAD}/captions", params={"part": "snippet", "uploadType": "multipart"},
                   headers=_headers(token(account)), files=files, timeout=180)
    return _check(r)


def ensure_playlist(title: str, description: str = "", privacy: str = "public",
                    account: str = "") -> str:
    tk = token(account)
    r = httpx.get(f"{API}/playlists", params={"part": "snippet", "mine": "true", "maxResults": 50},
                  headers=_headers(tk), timeout=60)
    for item in _check(r).get("items", []):
        if item["snippet"]["title"].strip().lower() == title.strip().lower():
            return item["id"]
    r = httpx.post(f"{API}/playlists", params={"part": "snippet,status"}, headers=_headers(tk),
                   json={"snippet": {"title": title, "description": description},
                         "status": {"privacyStatus": privacy}}, timeout=60)
    return _check(r)["id"]


def add_to_playlist(playlist_id: str, video_id: str, account: str = "") -> dict:
    r = httpx.post(f"{API}/playlistItems", params={"part": "snippet"},
                   headers=_headers(token(account)),
                   json={"snippet": {"playlistId": playlist_id,
                                     "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
                   timeout=60)
    return _check(r)


def update_channel_branding(description: str = "", keywords: list[str] | None = None,
                            country: str = "", account: str = "") -> dict:
    """Updates the About text. Avatar and banner images are NOT settable by API -
    Google removed that - so those two stay a manual upload."""
    ch = my_channel(account)
    branding = ch.get("brandingSettings", {})
    channel = branding.setdefault("channel", {})
    if description:
        channel["description"] = description[:1000]
    if keywords:
        channel["keywords"] = " ".join(f'"{k}"' if " " in k else k for k in keywords)[:500]
    if country:
        channel["country"] = country
    r = httpx.put(f"{API}/channels", params={"part": "brandingSettings"},
                  headers=_headers(token(account)),
                  json={"id": ch["id"], "brandingSettings": branding}, timeout=60)
    return _check(r)


def recent_uploads(limit: int = 20, account: str = "") -> list[dict]:
    """The channel's own uploads playlist - what the channel actually publishes,
    which is more honest than what its About text claims."""
    tk = token(account)
    ch = my_channel(account)
    uploads = (ch.get("contentDetails", {}) or {}).get("relatedPlaylists", {}).get("uploads")
    if not uploads:
        r = httpx.get(f"{API}/channels", params={"part": "contentDetails", "mine": "true"},
                      headers=_headers(tk), timeout=60)
        items = _check(r).get("items", [])
        uploads = (items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
                   if items else None)
    if not uploads:
        return []
    r = httpx.get(f"{API}/playlistItems",
                  params={"part": "snippet", "playlistId": uploads,
                          "maxResults": min(max(limit, 1), 50)},
                  headers=_headers(tk), timeout=60)
    out = []
    for item in _check(r).get("items", []):
        sn = item.get("snippet", {})
        out.append({"title": sn.get("title", ""),
                    "description": (sn.get("description", "") or "")[:400],
                    "published": sn.get("publishedAt", ""),
                    "video_id": sn.get("resourceId", {}).get("videoId", "")})
    return out


def channel_summary(account: str = "") -> dict:
    ch = my_channel(account)
    sn = ch.get("snippet", {})
    stats = ch.get("statistics", {})
    branding = (ch.get("brandingSettings", {}) or {}).get("channel", {})
    return {
        "id": ch.get("id", ""),
        "title": sn.get("title", ""),
        "handle": sn.get("customUrl", ""),
        "description": branding.get("description") or sn.get("description", ""),
        "keywords": branding.get("keywords", ""),
        "country": branding.get("country", ""),
        "published": sn.get("publishedAt", ""),
        "subscribers": stats.get("subscriberCount", "hidden"),
        "videos": stats.get("videoCount", "0"),
        "views": stats.get("viewCount", "0"),
    }


def post_comment(video_id: str, text: str, account: str = "") -> dict:
    r = httpx.post(f"{API}/commentThreads", params={"part": "snippet"},
                   headers=_headers(token(account)),
                   json={"snippet": {"videoId": video_id,
                                     "topLevelComment": {"snippet": {"textOriginal": text}}}},
                   timeout=60)
    return _check(r)
