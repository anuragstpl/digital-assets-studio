"""Google Play Developer API v3.

What this can do without you touching the console:
  upload an AAB or APK, write the store listing text, upload icon, feature
  graphic and screenshots, create a release on any track, set a staged rollout
  percentage, and commit the whole thing as one atomic edit.

What it cannot do, because Google exposes no API for it:
  the Data safety form, the content rating questionnaire, app access
  instructions, and the very first production release of a brand new app.
  The suite prepares exact answers for those instead.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import httpx

from .. import keyvault
from .google_auth import PLAY_SCOPE, AuthError, ServiceAccount

log = logging.getLogger(__name__)

BASE = "https://androidpublisher.googleapis.com/androidpublisher/v3"
UPLOAD = "https://androidpublisher.googleapis.com/upload/androidpublisher/v3"
SECRET = "play::service_account_json"

IMAGE_TYPES = ["icon", "featureGraphic", "phoneScreenshots", "sevenInchScreenshots",
               "tenInchScreenshots", "tvBanner", "tvScreenshots", "wearScreenshots"]

TRACKS = ["internal", "alpha", "beta", "production"]


class PlayError(RuntimeError):
    pass


def save_service_account(raw_json: str) -> str:
    sa = ServiceAccount.from_json(raw_json)
    keyvault.set_secret(SECRET, raw_json)
    return sa.client_email


def service_account() -> ServiceAccount:
    raw = keyvault.get_secret(SECRET)
    if not raw:
        raise PlayError("No Google Play service-account key saved. Add it in Settings › Publishing.")
    return ServiceAccount.from_json(raw)


def connected() -> bool:
    return bool(keyvault.get_secret(SECRET))


def _token() -> str:
    return service_account().access_token(PLAY_SCOPE)


def _check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text[:400])
        except Exception:  # noqa: BLE001
            msg = r.text[:400]
        hint = ""
        low = msg.lower()
        if "permission" in low or r.status_code == 401:
            hint = ("  Invite the service account's email as a user in Play Console › Users and "
                    "permissions, and give it release permissions for this app.")
        elif "not found" in low:
            hint = "  Check the package name, and that the app already exists in Play Console."
        elif "apk" in low and "upload" in low:
            hint = "  A brand-new app needs its first release uploaded through the console once."
        raise PlayError(f"Play API {r.status_code}: {msg}{hint}")
    return r.json() if r.content else {}


class Edit:
    """One atomic Play edit. Use as a context manager - commits on clean exit,
    and deliberately does NOT commit if anything raised."""

    def __init__(self, package: str) -> None:
        self.package = package
        self.token = _token()
        self.id: str = ""

    @property
    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def __enter__(self) -> "Edit":
        r = httpx.post(f"{BASE}/applications/{self.package}/edits", headers=self._h, timeout=90)
        self.id = _check(r)["id"]
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.delete()
        return False

    # --------------------------------------------------------------- parts --
    def upload_bundle(self, aab: Path, progress: Callable[[float, str], None] | None = None) -> dict:
        aab = Path(aab)
        if not aab.exists():
            raise PlayError(f"Bundle not found: {aab}")
        if progress:
            progress(0.1, f"Uploading {aab.name} ({aab.stat().st_size / 1_048_576:.1f} MB)")
        endpoint = "bundles" if aab.suffix.lower() == ".aab" else "apks"
        ctype = ("application/octet-stream" if aab.suffix.lower() == ".aab"
                 else "application/vnd.android.package-archive")
        r = httpx.post(
            f"{UPLOAD}/applications/{self.package}/edits/{self.id}/{endpoint}",
            params={"uploadType": "media"},
            headers={**self._h, "Content-Type": ctype},
            content=aab.read_bytes(),
            timeout=httpx.Timeout(1800.0, connect=60.0),
        )
        out = _check(r)
        if progress:
            progress(0.6, f"Uploaded version code {out.get('versionCode')}")
        return out

    def update_listing(self, language: str, title: str, short_description: str,
                       full_description: str, video: str = "") -> dict:
        if len(title) > 30:
            raise PlayError(f"Play titles are capped at 30 characters; yours is {len(title)}.")
        if len(short_description) > 80:
            raise PlayError(f"The short description is capped at 80 characters; yours is {len(short_description)}.")
        if len(full_description) > 4000:
            raise PlayError(f"The full description is capped at 4000 characters; yours is {len(full_description)}.")
        r = httpx.put(
            f"{BASE}/applications/{self.package}/edits/{self.id}/listings/{language}",
            headers=self._h,
            json={"language": language, "title": title, "shortDescription": short_description,
                  "fullDescription": full_description, "video": video},
            timeout=90)
        return _check(r)

    def delete_images(self, language: str, image_type: str) -> None:
        httpx.delete(f"{BASE}/applications/{self.package}/edits/{self.id}/listings/{language}/{image_type}",
                     headers=self._h, timeout=90)

    def upload_image(self, language: str, image_type: str, image: Path) -> dict:
        if image_type not in IMAGE_TYPES:
            raise PlayError(f"Unknown image type {image_type!r}. One of: {', '.join(IMAGE_TYPES)}")
        image = Path(image)
        mime = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
        r = httpx.post(
            f"{UPLOAD}/applications/{self.package}/edits/{self.id}/listings/{language}/{image_type}",
            params={"uploadType": "media"},
            headers={**self._h, "Content-Type": mime},
            content=image.read_bytes(), timeout=300)
        return _check(r)

    def set_track(self, track: str, version_codes: list[int], status: str = "completed",
                  user_fraction: float | None = None, release_notes: dict[str, str] | None = None) -> dict:
        if track not in TRACKS:
            raise PlayError(f"Unknown track {track!r}. One of: {', '.join(TRACKS)}")
        release: dict = {"versionCodes": [str(v) for v in version_codes], "status": status}
        if status == "inProgress" and user_fraction:
            release["userFraction"] = user_fraction
        if release_notes:
            release["releaseNotes"] = [{"language": k, "text": v[:500]} for k, v in release_notes.items()]
        r = httpx.put(f"{BASE}/applications/{self.package}/edits/{self.id}/tracks/{track}",
                      headers=self._h, json={"track": track, "releases": [release]}, timeout=90)
        return _check(r)

    def commit(self) -> dict:
        r = httpx.post(f"{BASE}/applications/{self.package}/edits/{self.id}:commit",
                       headers=self._h, timeout=180)
        return _check(r)

    def delete(self) -> None:
        try:
            httpx.delete(f"{BASE}/applications/{self.package}/edits/{self.id}",
                         headers=self._h, timeout=60)
        except Exception:  # noqa: BLE001
            pass


def check_access(package: str) -> str:
    """Cheapest possible round trip that proves the credentials work."""
    e = Edit(package)
    with e:
        pass
    return f"OK - the service account can edit {package}."


def publish(package: str, aab: Path | None, listing: dict | None, images: dict[str, list[Path]] | None,
            track: str = "internal", release_notes: dict[str, str] | None = None,
            rollout: float | None = None, language: str = "en-US",
            progress: Callable[[float, str], None] | None = None) -> dict:
    """One call that does the whole release. Anything you pass as None is left alone."""
    def say(f: float, m: str) -> None:
        if progress:
            progress(f, m)

    result: dict = {}
    with Edit(package) as edit:
        if listing:
            say(0.15, "Writing the store listing")
            result["listing"] = edit.update_listing(
                language, listing["title"], listing["short_description"],
                listing["full_description"], listing.get("video", ""))
        if images:
            for image_type, paths in images.items():
                if not paths:
                    continue
                say(0.3, f"Uploading {len(paths)} {image_type}")
                edit.delete_images(language, image_type)
                for p in paths:
                    edit.upload_image(language, image_type, p)
        version_codes: list[int] = []
        if aab:
            out = edit.upload_bundle(Path(aab), progress)
            version_codes = [int(out["versionCode"])]
            result["version_code"] = version_codes[0]
        if version_codes:
            say(0.85, f"Creating the {track} release")
            status = "inProgress" if rollout and rollout < 1.0 else "completed"
            result["track"] = edit.set_track(track, version_codes, status, rollout, release_notes)
        say(0.95, "Committing the edit")
    say(1.0, "Committed")
    return result
