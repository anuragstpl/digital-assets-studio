"""A tiny routable HTTP server for integration tests.

The publishing connectors are the part of this suite that can only be wrong at
runtime: a mistyped path, a parameter the API does not accept, a response field
read from the wrong place. None of that shows up in an offline test. This stands
up a real server, points the clients at it, and records exactly what they sent.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse


@dataclass
class Recorded:
    method: str
    path: str
    query: dict
    headers: dict
    body: bytes

    def json(self) -> dict:
        return json.loads(self.body or b"{}")

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


@dataclass
class Route:
    method: str
    pattern: re.Pattern
    handler: Callable[[Recorded, re.Match], tuple[int, dict, bytes]]


class MockAPI:
    def __init__(self) -> None:
        self.routes: list[Route] = []
        self.requests: list[Recorded] = []
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.unmatched: list[Recorded] = []

    # ------------------------------------------------------------ routing --
    def route(self, method: str, pattern: str):
        def wrap(fn):
            self.routes.append(Route(method.upper(), re.compile(pattern), fn))
            return fn
        return wrap

    def json_route(self, method: str, pattern: str, payload, status: int = 200,
                   headers: dict | None = None):
        def handler(req, match):
            body = payload(req, match) if callable(payload) else payload
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            return status, {"Content-Type": "application/json", **(headers or {})}, body
        self.routes.append(Route(method.upper(), re.compile(pattern), handler))

    # ------------------------------------------------------------ lifecycle --
    def __enter__(self) -> "MockAPI":
        api = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _dispatch(self, method: str) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                rec = Recorded(method, parsed.path,
                               {k: v[0] for k, v in parse_qs(parsed.query).items()},
                               {k.lower(): v for k, v in self.headers.items()}, body)
                api.requests.append(rec)
                for r in api.routes:
                    if r.method != method:
                        continue
                    m = r.pattern.fullmatch(parsed.path)
                    if m:
                        status, headers, payload = r.handler(rec, m)
                        self.send_response(status)
                        for k, v in (headers or {}).items():
                            self.send_header(k, v)
                        self.send_header("Content-Length", str(len(payload or b"")))
                        self.end_headers()
                        if payload:
                            self.wfile.write(payload)
                        return
                api.unmatched.append(rec)
                msg = json.dumps({"error": {"message": f"no mock route for {method} {parsed.path}"}}).encode()
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)

            def do_GET(self):  # noqa: N802
                self._dispatch("GET")

            def do_POST(self):  # noqa: N802
                self._dispatch("POST")

            def do_PUT(self):  # noqa: N802
                self._dispatch("PUT")

            def do_PATCH(self):  # noqa: N802
                self._dispatch("PATCH")

            def do_DELETE(self):  # noqa: N802
                self._dispatch("DELETE")

            def log_message(self, *args):  # noqa: A003
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> bool:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        return False

    @property
    def base(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_port}"

    # ------------------------------------------------------------ assertions --
    def sent(self, method: str, path_contains: str) -> list[Recorded]:
        return [r for r in self.requests
                if r.method == method.upper() and path_contains in r.path]

    def one(self, method: str, path_contains: str) -> Recorded:
        hits = self.sent(method, path_contains)
        assert hits, (f"expected a {method} to a path containing {path_contains!r}; "
                      f"saw {[(r.method, r.path) for r in self.requests]}")
        return hits[0]
