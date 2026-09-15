"""The local server: the page, the JSON API, Range media. Standard library only.

Binds 127.0.0.1. The project file is reloaded from disk on every request, so the
file is the truth; `Store` holds a lock across load, apply, save and journal for a
POST. Media is served only for a registered source id, by `/media/<source id>`,
never by path.
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import media
from .project import ChangeError, Store

APP_HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "studio.html")

CONTENT_TYPES = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v",
    ".webm": "video/webm", ".mkv": "video/x-matroska",
}

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
CHUNK = 1024 * 1024


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "export"


class Handler(BaseHTTPRequestHandler):
    server_version = "SightboxStudio/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    @property
    def store(self) -> Store:
        return self.server.store

    # ---- response helpers ----------------------------------------------

    def _json(self, code: int, payload: dict, with_body: bool = True) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _error(self, code: int, message: str, with_body: bool = True) -> None:
        self._json(code, {"error": message}, with_body)

    # ---- routing ---------------------------------------------------------

    def do_GET(self):
        self._route(with_body=True)

    def do_HEAD(self):
        self._route(with_body=False)

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return self._error(400, "the request body must be JSON")
        if not isinstance(payload, dict):
            return self._error(400, "the request body must be a JSON object")
        try:
            if parsed.path == "/api/changes":
                return self._post_changes(payload)
            if parsed.path == "/api/export":
                return self._post_export(payload)
        except ChangeError as e:
            return self._error(400, str(e))
        self._error(404, "not found")

    def _route(self, with_body: bool):
        path = urlparse(self.path).path
        try:
            if path in ("/", "/studio.html"):
                return self._get_page(with_body)
            if path == "/api/project":
                return self._json(200, self.store.describe(), with_body)
            if path == "/api/media":
                return self._json(200, self.store.media_files(self.server.media_dir), with_body)
            if path.startswith("/media/"):
                return self._get_media_file(path[len("/media/"):], with_body)
            self._error(404, "not found", with_body)
        except ChangeError as e:
            self._error(400, str(e), with_body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- GET handlers ------------------------------------------------

    def _get_page(self, with_body: bool):
        if not os.path.isfile(APP_HTML):
            return self._error(404, "the page is not built yet", with_body)
        with open(APP_HTML, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _get_media_file(self, source_id: str, with_body: bool):
        project = self.store.load()
        try:
            source = project.source(source_id)
        except ChangeError:
            return self._error(404, f"no source {source_id!r}", with_body)
        path = self.store.resolve(source["path"])
        if not os.path.isfile(path):
            return self._error(404, f"missing file: {path}", with_body)
        size = os.path.getsize(path)
        content_type = CONTENT_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
        start, end, status = 0, size - 1, 200
        range_header = self.headers.get("Range")
        if range_header:
            m = RANGE_RE.match(range_header.strip())
            if not m or (m.group(1) == "" and m.group(2) == ""):
                return self._error(416, "bad Range header", with_body)
            s, e = m.groups()
            if s == "":
                start, end = max(size - int(e), 0), size - 1
            else:
                start = int(s)
                end = int(e) if e else size - 1
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, size - 1)
            status = 206
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not with_body:
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = fh.read(min(CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---- POST handlers -------------------------------------------------

    def _post_changes(self, payload: dict):
        by = payload.get("by") or "agent"
        changes = payload.get("changes")
        project, results = self.store.apply(changes, by=by, probe=media.probe)
        body = {"results": results}
        body.update(self.store.describe(project))
        self._json(200, body)

    def _post_export(self, payload: dict):
        project = self.store.load()
        path = payload.get("path") or os.path.join(self.store.dir, f"{slugify(project.name)}.mp4")
        if not os.path.isabs(os.path.expanduser(path)):
            path = os.path.join(self.store.dir, path)
        path = os.path.abspath(os.path.expanduser(path))
        preset = payload.get("preset") or "medium"
        try:
            result = media.export(project, self.store.resolve, path, preset)
        except ChangeError as e:
            return self._error(500, str(e))
        self._json(200, result)


def make_server(project_path: str, port: int = 3200, media_dir: str = None) -> ThreadingHTTPServer:
    store = Store(project_path)
    if not store.exists():
        raise ChangeError(f"no project at {store.path}; run `studio new` first")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.store = store
    httpd.media_dir = media_dir
    return httpd


def serve(project_path: str, port: int = 3200, media_dir: str = None) -> None:
    httpd = make_server(project_path, port, media_dir)
    host, bound_port = httpd.server_address
    print(f"Sightbox Studio: http://{host}:{bound_port}/studio.html")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
