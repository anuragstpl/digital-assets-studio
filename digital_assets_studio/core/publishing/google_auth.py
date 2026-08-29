"""Google authentication without the google-* SDK stack.

Two flows, both hand-rolled on httpx so the app stays light and the failure
messages stay readable:

  * installed-app OAuth  - for YouTube, where the account is a person.
  * service-account JWT  - for Google Play, where the account is a robot.

Refresh tokens are stored in the OS key vault, never on disk in the project.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import jwt

from .. import keyvault

log = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
PLAY_SCOPE = "https://www.googleapis.com/auth/androidpublisher"


class AuthError(RuntimeError):
    pass


# ------------------------------------------------------------ installed app --

_HTML_OK = """<!doctype html><meta charset="utf-8"><title>Connected</title>
<body style="font-family:system-ui;background:#0E1117;color:#E8ECF3;display:flex;
height:100vh;align-items:center;justify-content:center;margin:0">
<div style="text-align:center"><h1 style="font-weight:600">Connected</h1>
<p style="color:#9AA6B8">You can close this tab and go back to Artalo Digi Suit.</p></div>"""


class _CallbackHandler(BaseHTTPRequestHandler):
    """Catches Google's redirect back to 127.0.0.1.

    Two things browsers do that a naive handler gets wrong: they request
    /favicon.ico alongside the page, and they will happily re-request the
    callback URL if the tab is reloaded. Neither carries the OAuth state, so
    treating every request as a callback throws away a sign-in that worked.
    """

    code: str | None = None
    error: str | None = None
    state: str = ""

    def _reply(self, status: int = 200, body: bytes | None = None) -> None:
        self.send_response(status)
        if body is None:
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        cls = type(self)

        # anything without OAuth parameters is not the callback - favicons,
        # devtools probes, the browser guessing at /.well-known
        if "code" not in params and "error" not in params:
            self._reply(204)
            return

        # already finished; a reload must not undo it
        if cls.code:
            self._reply(200, _HTML_OK.encode())
            return

        if params.get("state", [""])[0] != cls.state:
            cls.error = ("the callback carried the wrong state value - if you have two sign-ins "
                         "open at once, close them and try again")
        elif "code" in params:
            cls.code = params["code"][0]
            cls.error = None
        else:
            cls.error = params.get("error", ["unknown error"])[0]
        self._reply(200, _HTML_OK.encode())

    def log_message(self, *args):  # noqa: A003
        pass


def _await_callback(server: HTTPServer, timeout: float) -> tuple[str | None, str | None]:
    """Wait for the browser to come back. A code always beats an error - a stray
    request arriving afterwards must not invalidate a sign-in that succeeded."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _CallbackHandler.code:
            return _CallbackHandler.code, None
        if _CallbackHandler.error:
            # give the real callback a moment in case the error came from a
            # favicon request that raced ahead of it
            time.sleep(0.4)
            return _CallbackHandler.code, None if _CallbackHandler.code else _CallbackHandler.error
        time.sleep(0.2)
    return _CallbackHandler.code, _CallbackHandler.error


def oauth_installed_app(client_id: str, client_secret: str, scopes: list[str],
                        timeout: float = 300.0) -> dict:
    """Run the loopback OAuth flow and return the token payload."""
    if not client_id or not client_secret:
        raise AuthError("Add your Google OAuth client ID and secret in Settings first.")

    _CallbackHandler.code = None
    _CallbackHandler.error = None
    _CallbackHandler.state = secrets.token_urlsafe(16)

    server = HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_port
    redirect = f"http://127.0.0.1:{port}/"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "state": _CallbackHandler.state,
    })
    webbrowser.open(url)

    code, error = _await_callback(server, timeout)
    server.shutdown()

    if not code:
        if error:
            raise AuthError(f"Google sign-in failed: {error}")
        raise AuthError("Timed out waiting for Google sign-in. The browser tab may not have "
                        f"opened - you can paste this URL yourself:\n{url}")

    r = httpx.post(TOKEN_URL, data={
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    }, timeout=60)
    if r.status_code >= 400:
        detail = r.text[:300]
        if "redirect_uri_mismatch" in detail:
            raise AuthError(
                "Google rejected the sign-in with redirect_uri_mismatch.\n\n"
                "That means the OAuth client is the wrong type. It must be created as an "
                "Application type of **Desktop app**; a Web application client cannot sign in "
                "from a desktop app. Make a Desktop app client in the Google Cloud console and "
                "paste those credentials instead.")
        raise AuthError(f"Token exchange failed: {detail}")
    return r.json()


def refresh_access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    r = httpx.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token",
    }, timeout=60)
    if r.status_code >= 400:
        body = r.text[:300]
        if "invalid_grant" in body:
            raise AuthError(
                "Google refused the saved sign-in (invalid_grant).\n\n"
                "The usual cause: your OAuth consent screen is still in Testing. Google "
                "expires refresh tokens after 7 days for testing apps, so it works for a "
                "week and then stops.\n\n"
                "Fix it once: Google Cloud console → APIs & Services → OAuth consent screen "
                "→ Publish app → In production. You will see an 'unverified app' warning when "
                "you sign in; click Advanced → Go to (your app). Verification is only needed "
                "for public distribution, not for your own account.\n\n"
                "Then press Connect again in Settings.")
        raise AuthError(f"Could not refresh the Google token: {body}. "
                        f"Reconnect the account in Settings.")
    return r.json()["access_token"]


# ---------------------------------------------------------- service account --

@dataclass
class ServiceAccount:
    client_email: str
    private_key: str
    token_uri: str = TOKEN_URL

    @classmethod
    def from_json(cls, raw: str) -> "ServiceAccount":
        try:
            d = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AuthError("That does not look like a service-account JSON key file.") from exc
        missing = [k for k in ("client_email", "private_key") if not d.get(k)]
        if missing:
            raise AuthError(f"The service-account key is missing: {', '.join(missing)}")
        return cls(d["client_email"], d["private_key"], d.get("token_uri", TOKEN_URL))

    def access_token(self, scope: str) -> str:
        now = int(time.time())
        claim = {
            "iss": self.client_email, "scope": scope, "aud": self.token_uri,
            "iat": now, "exp": now + 3600,
        }
        assertion = jwt.encode(claim, self.private_key, algorithm="RS256")
        r = httpx.post(self.token_uri, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }, timeout=60)
        if r.status_code >= 400:
            raise AuthError(f"Service account rejected: {r.text[:300]}")
        return r.json()["access_token"]


# ------------------------------------------------------------- token store --

class TokenStore:
    """Small wrapper so each integration keeps its own credentials."""

    def __init__(self, name: str) -> None:
        self.name = name

    def _k(self, part: str) -> str:
        return f"google::{self.name}::{part}"

    def save_client(self, client_id: str, client_secret: str) -> None:
        keyvault.set_secret(self._k("client_id"), client_id)
        keyvault.set_secret(self._k("client_secret"), client_secret)

    def client(self) -> tuple[str, str]:
        return keyvault.get_secret(self._k("client_id")), keyvault.get_secret(self._k("client_secret"))

    def save_refresh(self, refresh_token: str) -> None:
        keyvault.set_secret(self._k("refresh_token"), refresh_token)

    @property
    def connected(self) -> bool:
        return bool(keyvault.get_secret(self._k("refresh_token")))

    def connect(self, scopes: list[str]) -> str:
        cid, cs = self.client()
        payload = oauth_installed_app(cid, cs, scopes)
        if payload.get("refresh_token"):
            self.save_refresh(payload["refresh_token"])
        return payload.get("access_token", "")

    def token(self, scopes: list[str]) -> str:
        cid, cs = self.client()
        rt = keyvault.get_secret(self._k("refresh_token"))
        if not rt:
            return self.connect(scopes)
        return refresh_access_token(cid, cs, rt)

    def disconnect(self) -> None:
        keyvault.delete_secret(self._k("refresh_token"))
