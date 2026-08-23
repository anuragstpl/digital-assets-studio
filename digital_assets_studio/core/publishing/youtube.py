"""YouTube Data API v3: upload, thumbnail, captions, playlists, branding.

This is the real thing - a video published from here appears on the channel.
Uploads use the resumable protocol so a dropped connection resumes instead of
starting a 400 MB file again.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

import httpx

from .google_auth import YOUTUBE_SCOPES, AuthError, TokenStore

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3"
CHUNK = 8 * 1024 * 1024

STORE = TokenStore("youtube")

CATEGORIES = {
    "Film & Animation": "1", "Autos & Vehicles": "2", "Music": "10", "Pets & Animals": "15",
    "Sports": "17", "Travel & Events": "19", "Gaming": "20", "People & Blogs": "22",
    "Comedy": "23", "Entertainment": "24", "News & Politics": "25", "Howto & Style": "26",
    "Education": "27", "Science & Technology": "28", "Nonprofits & Activism": "29",
}


class YouTubeError(RuntimeError):
    pass


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
            hint = ("  A new project gets its own upload bucket — 100 videos.insert calls a day — "
                    "plus 10,000 units a day for everything else. A fully decorated upload "
                    "(thumbnail 50, captions 400, playlist 50) spends about 550 of those units, "
                    "so the 10,000 runs out around 18 videos before the upload bucket does. "
                    "Request more in the Google Cloud console, or skip captions on bulk runs.")
        if r.status_code == 403 and "youtubeSignupRequired" in reasons:
            hint = "  That Google account has no YouTube channel yet."
        raise YouTubeError(f"YouTube API {r.status_code}: {msg}{hint}")
    return r.json() if r.content else {}


def connected() -> bool:
    return STORE.connected


def connect() -> str:
    """Opens the browser once; afterwards the refresh token does the work."""
    STORE.connect(YOUTUBE_SCOPES)
    return "Connected to YouTube."


def token() -> str:
    return STORE.token(YOUTUBE_SCOPES)


def my_channel() -> dict:
    r = httpx.get(f"{API}/channels",
                  params={"part": "snippet,statistics,brandingSettings,contentDetails",
                          "mine": "true"},
                  headers=_headers(token()), timeout=60)
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

    tk = token()
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
    with path.open("rb") as fh, httpx.Client(timeout=httpx.Timeout(900.0, connect=30.0)) as client:
        while sent < size:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            end = sent + len(chunk) - 1
            for attempt in range(4):
                r = client.put(
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


def set_thumbnail(video_id: str, image: Path) -> dict:
    image = Path(image)
    if image.stat().st_size > 2 * 1024 * 1024:
        raise YouTubeError("Thumbnails must be under 2 MB.")
    r = httpx.post(f"{UPLOAD}/thumbnails/set", params={"videoId": video_id},
                   headers={**_headers(token()), "Content-Type": "image/jpeg"},
                   content=image.read_bytes(), timeout=180)
    return _check(r)


def upload_caption(video_id: str, caption_file: Path, language: str = "en",
                   name: str = "Subtitles") -> dict:
    caption_file = Path(caption_file)
    meta = {"snippet": {"videoId": video_id, "language": language, "name": name, "isDraft": False}}
    files = {
        "metadata": ("metadata.json", json.dumps(meta), "application/json"),
        "file": (caption_file.name, caption_file.read_bytes(), "application/octet-stream"),
    }
    r = httpx.post(f"{UPLOAD}/captions", params={"part": "snippet", "uploadType": "multipart"},
                   headers=_headers(token()), files=files, timeout=180)
    return _check(r)


def ensure_playlist(title: str, description: str = "", privacy: str = "public") -> str:
    tk = token()
    r = httpx.get(f"{API}/playlists", params={"part": "snippet", "mine": "true", "maxResults": 50},
                  headers=_headers(tk), timeout=60)
    for item in _check(r).get("items", []):
        if item["snippet"]["title"].strip().lower() == title.strip().lower():
            return item["id"]
    r = httpx.post(f"{API}/playlists", params={"part": "snippet,status"}, headers=_headers(tk),
                   json={"snippet": {"title": title, "description": description},
                         "status": {"privacyStatus": privacy}}, timeout=60)
    return _check(r)["id"]


def add_to_playlist(playlist_id: str, video_id: str) -> dict:
    r = httpx.post(f"{API}/playlistItems", params={"part": "snippet"}, headers=_headers(token()),
                   json={"snippet": {"playlistId": playlist_id,
                                     "resourceId": {"kind": "youtube#video", "videoId": video_id}}},
                   timeout=60)
    return _check(r)


def update_channel_branding(description: str = "", keywords: list[str] | None = None,
                            country: str = "") -> dict:
    """Updates the About text. Avatar and banner images are NOT settable by API -
    Google removed that - so those two stay a manual upload."""
    ch = my_channel()
    branding = ch.get("brandingSettings", {})
    channel = branding.setdefault("channel", {})
    if description:
        channel["description"] = description[:1000]
    if keywords:
        channel["keywords"] = " ".join(f'"{k}"' if " " in k else k for k in keywords)[:500]
    if country:
        channel["country"] = country
    r = httpx.put(f"{API}/channels", params={"part": "brandingSettings"}, headers=_headers(token()),
                  json={"id": ch["id"], "brandingSettings": branding}, timeout=60)
    return _check(r)


def recent_uploads(limit: int = 20) -> list[dict]:
    """The channel's own uploads playlist - what the channel actually publishes,
    which is more honest than what its About text claims."""
    tk = token()
    ch = my_channel()
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


def channel_summary() -> dict:
    ch = my_channel()
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


def post_comment(video_id: str, text: str) -> dict:
    r = httpx.post(f"{API}/commentThreads", params={"part": "snippet"}, headers=_headers(token()),
                   json={"snippet": {"videoId": video_id,
                                     "topLevelComment": {"snippet": {"textOriginal": text}}}},
                   timeout=60)
    return _check(r)
