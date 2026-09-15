"""The local server: the page, Range media, the JSON API.

Binds `127.0.0.1` only. The project file is reloaded from disk on every request;
`studio.project.Store` holds the lock across load, apply, save and journal for a
POST. Standard library only: `http.server`, nothing else.
"""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from . import media
from .project import ChangeError, Store

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
PAGE_PATH = os.path.join(APP_DIR, "studio.html")

CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}

CHUNK = 1024 * 1024


class RangeNotSatisfiable(Exception):
    pass


def _parse_range(header: Optional[str], size: int):
    """The first range in a `Range` header, as an inclusive `(start, end)`, or
    `None` when there is no range to honour. Raises `RangeNotSatisfiable` for a
    start past the end of the file."""
    if not header or not header.startswith("bytes="):
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None
    start_s, _, end_s = spec.partition("-")
    if start_s == "":
        if end_s == "":
            return None
        length = int(end_s)
        if length <= 0:
            return None
        start, end = max(size - length, 0), size - 1
    else:
        start = int(start_s)
        end = int(end_s) if end_s else size - 1
    if start >= size:
        raise RangeNotSatisfiable()
    return start, min(end, size - 1)


def _make_handler(store: Store, media_dir: Optional[str]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "StudioHTTP/0.1"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            pass

        # ---- helpers ----------------------------------------------------

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error_json(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                raise ChangeError(f"malformed JSON body: {exc}") from exc
            if not isinstance(data, dict):
                raise ChangeError("the request body must be a JSON object")
            return data

        def _file(self, path: str, content_type: str) -> None:
            try:
                with open(path, "rb") as fh:
                    body = fh.read()
            except OSError:
                self._error_json(404, "not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _media(self, source_id: str, head_only: bool) -> None:
            try:
                proj = store.load()
                src = proj.source(source_id)
            except (OSError, ChangeError):
                self._error_json(404, "unknown source")
                return
            path = store.resolve(src["path"])
            if not os.path.isfile(path):
                self._error_json(404, "the source's file is missing")
                return
            size = os.path.getsize(path)
            ext = os.path.splitext(path)[1].lower()
            content_type = CONTENT_TYPES.get(ext, "application/octet-stream")
            try:
                rng = _parse_range(self.headers.get("Range"), size)
            except RangeNotSatisfiable:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            if rng is None:
                start, end, status = 0, size - 1, 200
            else:
                start, end, status = rng[0], rng[1], 206
            length = end - start + 1
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
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

        # ---- routing ------------------------------------------------------

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            path = self.path.split("?", 1)[0]
            if path in ("/", "/studio.html"):
                self._file(PAGE_PATH, "text/html; charset=utf-8")
            elif path == "/api/project":
                self._json(200, store.describe())
            elif path == "/api/media":
                self._json(200, store.media_files(media_dir))
            elif path.startswith("/media/"):
                self._media(path[len("/media/"):], head_only=False)
            else:
                self._error_json(404, "not found")

        def do_HEAD(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path.startswith("/media/"):
                self._media(path[len("/media/"):], head_only=True)
            else:
                self._error_json(404, "not found")

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            try:
                body = self._read_json()
            except ChangeError as exc:
                self._error_json(400, str(exc))
                return
            if path == "/api/changes":
                self._changes(body)
            elif path == "/api/export":
                self._export(body)
            else:
                self._error_json(404, "not found")

        def _changes(self, body: dict) -> None:
            changes = body.get("changes")
            by = body.get("by") or "agent"
            try:
                _, results = store.apply(changes, by, probe=media.probe if media.available() else None)
            except ChangeError as exc:
                self._error_json(400, str(exc))
                return
            payload = store.describe()
            payload["results"] = results
            self._json(200, payload)

        def _export(self, body: dict) -> None:
            if not media.available():
                self._error_json(500, "ffmpeg and ffprobe are not on PATH")
                return
            project = store.load()
            path = body.get("path") or os.path.join(store.dir, f"{media.slug(project.name)}.mp4")
            preset = body.get("preset") or "medium"
            t0 = time.time()
            try:
                result = media.export(project, store.resolve, path, preset)
            except media.MediaError as exc:
                self._error_json(500, str(exc))
                return
            self._json(200, {
                "path": path,
                "bytes": result["bytes"],
                "seconds": round(time.time() - t0, 3),
                "duration": result["duration"],
            })

    return Handler


def make_server(project_path: str, port: int = 3200, media_dir: Optional[str] = None) -> ThreadingHTTPServer:
    store = Store(project_path)
    if not store.exists():
        raise FileNotFoundError(project_path)
    handler_cls = _make_handler(store, media_dir)
    return ThreadingHTTPServer(("127.0.0.1", port), handler_cls)


def serve(project_path: str, port: int = 3200, media_dir: Optional[str] = None) -> None:
    httpd = make_server(project_path, port, media_dir)
    host, bound_port = httpd.server_address
    print(f"http://{host}:{bound_port}/studio.html")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
