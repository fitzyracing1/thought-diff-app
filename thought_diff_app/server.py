"""Local HTTP server for the Thought Diff app.

The server binds to an address you choose (127.0.0.1 by default). It can read
the original tree and read or write the working tree. Do not expose it beyond
localhost.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .errors import SessionError
from .session import Session

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 8_000_000

_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


class Watcher(threading.Thread):
    """Poll the working folder and re-evaluate when files change on disk."""

    def __init__(self, session: Session, interval: float = 0.4) -> None:
        super().__init__(daemon=True, name="thought-diff-watch")
        self.session = session
        self.interval = interval
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.session.poll()
            except Exception as exc:  # keep the server up if one pass fails
                sys.stderr.write(f"Thought Diff watcher: {exc}\n")

    def stop(self) -> None:
        self._stop.set()


def make_server(session: Session, host: str, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), _handler(session))
    httpd.daemon_threads = True
    return httpd


def serve(session: Session, host: str, port: int, *, watch: bool = True) -> None:
    httpd = make_server(session, host, port)
    watcher = Watcher(session) if watch else None
    if watcher is not None:
        watcher.start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        sys.stderr.write("\nStopping Thought Diff.\n")
    finally:
        if watcher is not None:
            watcher.stop()
        httpd.server_close()


def _handler(session: Session):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            if self.path.startswith("/favicon"):
                return
            sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if parsed.path in _FILES:
                self._static(parsed.path)
                return
            if parsed.path == "/api/session":
                self._call(session.snapshot)
                return
            if parsed.path == "/api/file":
                rel = (parse_qs(parsed.query).get("path") or [""])[0]
                self._call(lambda: session.file_view(rel), missing=True)
                return
            self._json(404, {"error": "Not found."})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/refresh":
                self._call(session.reevaluate)
                return
            if parsed.path == "/api/session":
                body = self._body()
                if body is None:
                    return
                self._call(lambda: session.configure(
                    working=body.get("working") or "",
                    original=body.get("original") or None,
                    git_ref=body.get("git_ref") or None,
                    repo=body.get("repo") or None,
                ))
                return
            self._json(404, {"error": "Not found."})

        def do_PUT(self) -> None:
            if urlparse(self.path).path != "/api/file":
                self._json(404, {"error": "Not found."})
                return
            body = self._body()
            if body is None:
                return
            self._call(lambda: session.write_file(body.get("path") or "", body.get("content")))

        def do_DELETE(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/file":
                self._json(404, {"error": "Not found."})
                return
            rel = (parse_qs(parsed.query).get("path") or [""])[0]
            self._call(lambda: session.delete_file(rel), missing=True)

        def _static(self, path: str) -> None:
            name, content_type = _FILES[path]
            file_path = STATIC_DIR / name
            if not file_path.is_file():
                self._json(500, {"error": f"Missing app file: {name}"})
                return
            self._bytes(200, file_path.read_bytes(), content_type)

        def _call(self, fn, missing: bool = False) -> None:
            try:
                self._json(200, fn())
            except SessionError as exc:
                text = str(exc)
                not_found = missing and ("not in the comparison" in text or "not found" in text.lower())
                self._json(404 if not_found else 400, {"error": text})

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._json(400, {"error": "Invalid Content-Length."})
                return None
            if length < 0 or length > MAX_BODY:
                self._json(413, {"error": "Request body is too large."})
                return None
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"error": "Expected a JSON body."})
                return None
            if not isinstance(data, dict):
                self._json(400, {"error": "Expected a JSON object."})
                return None
            return data

        def _json(self, code: int, payload) -> None:
            self._bytes(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

        def _bytes(self, code: int, data: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler
