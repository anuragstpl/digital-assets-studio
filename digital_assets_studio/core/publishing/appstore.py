"""App Store Connect API.

Metadata, screenshots and review submission are all API-driven. The binary is
not: Apple only accepts builds through Transporter or `xcrun altool`, both of
which need macOS. The suite writes the exact command for that one step.
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import httpx
import jwt

from .. import keyvault

log = logging.getLogger(__name__)

BASE = "https://api.appstoreconnect.apple.com/v1"   # module-level for tests
K_ISSUER = "appstore::issuer_id"
K_KEY_ID = "appstore::key_id"
K_PRIVATE = "appstore::p8"


class AppStoreError(RuntimeError):
    pass


def save_credentials(issuer_id: str, key_id: str, p8_contents: str) -> None:
    if "PRIVATE KEY" not in p8_contents:
        raise AppStoreError("That does not look like a .p8 private key file.")
    keyvault.set_secret(K_ISSUER, issuer_id.strip())
    keyvault.set_secret(K_KEY_ID, key_id.strip())
    keyvault.set_secret(K_PRIVATE, p8_contents)


def connected() -> bool:
    return all(keyvault.get_secret(k) for k in (K_ISSUER, K_KEY_ID, K_PRIVATE))


def _token() -> str:
    issuer, key_id, p8 = (keyvault.get_secret(K_ISSUER), keyvault.get_secret(K_KEY_ID),
                          keyvault.get_secret(K_PRIVATE))
    if not all((issuer, key_id, p8)):
        raise AppStoreError("App Store Connect credentials are not set. Add them in Settings › Publishing.")
    now = int(time.time())
    return jwt.encode(
        {"iss": issuer, "iat": now, "exp": now + 1200, "aud": "appstoreconnect-v1"},
        p8, algorithm="ES256", headers={"kid": key_id, "typ": "JWT"},
    )


def _h() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}


def _check(r: httpx.Response) -> dict:
    if r.status_code >= 400:
        try:
            errs = r.json().get("errors", [])
            msg = "; ".join(f"{e.get('title')}: {e.get('detail')}" for e in errs) or r.text[:300]
        except Exception:  # noqa: BLE001
            msg = r.text[:300]
        raise AppStoreError(f"App Store Connect {r.status_code}: {msg}")
    return r.json() if r.content else {}


def list_apps() -> list[dict]:
    r = httpx.get(f"{BASE}/apps", headers=_h(), params={"limit": 100}, timeout=60)
    return [{"id": a["id"], "name": a["attributes"]["name"],
             "bundle_id": a["attributes"]["bundleId"], "sku": a["attributes"].get("sku")}
            for a in _check(r).get("data", [])]


def check_access() -> str:
    apps = list_apps()
    return f"OK - {len(apps)} app(s) visible to this key."


def latest_version(app_id: str, platform: str = "IOS") -> dict | None:
    r = httpx.get(f"{BASE}/apps/{app_id}/appStoreVersions", headers=_h(),
                  params={"filter[platform]": platform, "limit": 5}, timeout=60)
    data = _check(r).get("data", [])
    return data[0] if data else None


def create_version(app_id: str, version_string: str, platform: str = "IOS") -> dict:
    payload = {"data": {"type": "appStoreVersions",
                        "attributes": {"platform": platform, "versionString": version_string},
                        "relationships": {"app": {"data": {"type": "apps", "id": app_id}}}}}
    return _check(httpx.post(f"{BASE}/appStoreVersions", headers=_h(), json=payload, timeout=60))


def update_localization(version_id: str, locale: str = "en-US", description: str = "",
                        keywords: str = "", whats_new: str = "", promotional_text: str = "",
                        support_url: str = "", marketing_url: str = "") -> dict:
    if keywords and len(keywords) > 100:
        raise AppStoreError(f"Apple caps the keywords field at 100 characters; yours is {len(keywords)}.")
    if description and len(description) > 4000:
        raise AppStoreError("The description is capped at 4000 characters.")
    r = httpx.get(f"{BASE}/appStoreVersions/{version_id}/appStoreVersionLocalizations",
                  headers=_h(), params={"limit": 50}, timeout=60)
    existing = next((d for d in _check(r).get("data", [])
                     if d["attributes"].get("locale") == locale), None)
    attrs = {k: v for k, v in {
        "description": description, "keywords": keywords, "whatsNew": whats_new,
        "promotionalText": promotional_text, "supportUrl": support_url,
        "marketingUrl": marketing_url}.items() if v}
    if existing:
        payload = {"data": {"type": "appStoreVersionLocalizations", "id": existing["id"],
                            "attributes": attrs}}
        return _check(httpx.patch(f"{BASE}/appStoreVersionLocalizations/{existing['id']}",
                                  headers=_h(), json=payload, timeout=60))
    payload = {"data": {"type": "appStoreVersionLocalizations",
                        "attributes": {**attrs, "locale": locale},
                        "relationships": {"appStoreVersion": {
                            "data": {"type": "appStoreVersions", "id": version_id}}}}}
    return _check(httpx.post(f"{BASE}/appStoreVersionLocalizations", headers=_h(),
                             json=payload, timeout=60))


def upload_screenshot(screenshot_set_id: str, image: Path) -> dict:
    """Apple's three-step upload: reserve, PUT to the returned operations, commit."""
    image = Path(image)
    data = image.read_bytes()
    reserve = _check(httpx.post(
        f"{BASE}/appScreenshots", headers=_h(), timeout=60,
        json={"data": {"type": "appScreenshots",
                       "attributes": {"fileSize": len(data), "fileName": image.name},
                       "relationships": {"appScreenshotSet": {
                           "data": {"type": "appScreenshotSets", "id": screenshot_set_id}}}}}))
    sid = reserve["data"]["id"]
    for op in reserve["data"]["attributes"]["uploadOperations"]:
        headers = {h["name"]: h["value"] for h in op.get("requestHeaders", [])}
        chunk = data[op["offset"]:op["offset"] + op["length"]]
        r = httpx.request(op["method"], op["url"], content=chunk, headers=headers, timeout=300)
        if r.status_code >= 400:
            raise AppStoreError(f"Screenshot upload failed with {r.status_code}")
    checksum = hashlib.md5(data).hexdigest()  # noqa: S324 - Apple specifies md5 here
    return _check(httpx.patch(
        f"{BASE}/appScreenshots/{sid}", headers=_h(), timeout=120,
        json={"data": {"type": "appScreenshots", "id": sid,
                       "attributes": {"uploaded": True, "sourceFileChecksum": checksum}}}))


def submit_for_review(version_id: str) -> dict:
    payload = {"data": {"type": "appStoreVersionSubmissions",
                        "relationships": {"appStoreVersion": {
                            "data": {"type": "appStoreVersions", "id": version_id}}}}}
    return _check(httpx.post(f"{BASE}/appStoreVersionSubmissions", headers=_h(),
                             json=payload, timeout=60))


def transporter_command(ipa_path: str, apple_id: str = "$APPLE_ID",
                        app_password: str = "$APP_SPECIFIC_PASSWORD") -> str:
    """The one step Apple will not let any API do."""
    return (f'xcrun altool --upload-app -f "{ipa_path}" -t ios '
            f'-u "{apple_id}" -p "{app_password}"')
