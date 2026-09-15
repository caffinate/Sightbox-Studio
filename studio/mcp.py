"""The MCP server: the same operations over stdio JSON-RPC 2.0, one JSON message
per line, in on stdin, out on stdout. Standard library only. Nothing but protocol
messages ever reaches stdout; logging goes to stderr. Every batch this applies is
attributed `by "agent"`.

`Handler` holds the protocol and tool logic and does no I/O of its own, so
tests can drive it in-process; `serve` is the stdio loop around it.
"""

from __future__ import annotations

import base64
import json
import sys
from typing import Any, Optional

from . import media
from .project import ChangeError, Store
from .server import slugify

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "studio"
SERVER_VERSION = "0.1.0"

TOOLS = [
    {"name": "project_get", "description": "The cut list, the timeline, and where the project lives.",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "sources_list", "description": "Every source: id, name, duration, size, audio.",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "media_list", "description": "Video files in the project's folder, and which are already added.",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "source_add", "description": "Register a media file as a source.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "source_probe", "description": "What ffprobe says about a file, without adding it.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "source_frame", "description": "A single JPEG frame from a source at a source time.",
     "inputSchema": {"type": "object", "properties": {
         "source": {"type": "string"}, "t": {"type": "number"},
         "width": {"type": "integer", "default": 640}}, "required": ["source", "t"]}},
    {"name": "source_sheet", "description": "A contact sheet JPEG with timestamps burnt in, and the tile arithmetic.",
     "inputSchema": {"type": "object", "properties": {
         "source": {"type": "string"}, "cols": {"type": "integer", "default": 6},
         "rows": {"type": "integer", "default": 5}, "width": {"type": "integer", "default": 1920}},
         "required": ["source"]}},
    {"name": "source_scenes", "description": "Scene changes: source time and score for each frame past the threshold.",
     "inputSchema": {"type": "object", "properties": {
         "source": {"type": "string"}, "threshold": {"type": "number", "default": 0.3}}, "required": ["source"]}},
    {"name": "source_silences", "description": "Silent spans, start and end in source seconds.",
     "inputSchema": {"type": "object", "properties": {
         "source": {"type": "string"}, "noise_db": {"type": "number", "default": -30},
         "min_duration": {"type": "number", "default": 0.5}}, "required": ["source"]}},
    {"name": "changes_apply", "description": "Apply a batch of ops to the cut list; whole or not at all.",
     "inputSchema": {"type": "object", "properties": {
         "changes": {"type": "array", "items": {"type": "object"}}}, "required": ["changes"]}},
    {"name": "export", "description": "Export the cut list to an MP4.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string"}, "preset": {"type": "string"}}, "required": []}},
]


class Handler:
    """One JSON-RPC message in, one response (or None for a notification) out."""

    def __init__(self, store: Store):
        self.store = store

    def handle(self, message: dict) -> Optional[dict]:
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            version = requested if requested in PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
            return self._result(msg_id, {
                "protocolVersion": version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._result(msg_id, self._call_tool(params.get("name"), params.get("arguments") or {}))
        return self._error(msg_id, -32601, f"unknown method {method!r}")

    @staticmethod
    def _result(msg_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    def _call_tool(self, name: str, args: dict) -> dict:
        try:
            return {"content": self._dispatch(name, args)}
        except (ChangeError, TypeError, ValueError, KeyError) as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}

    def _dispatch(self, name: str, args: dict) -> list:
        if name == "project_get":
            return [self._text(self.store.describe())]
        if name == "sources_list":
            sources = self.store.load().sources
            rows = [{"id": s["id"], "name": s["name"], "duration": s["duration"],
                     "width": s["width"], "height": s["height"], "has_audio": s["has_audio"]} for s in sources]
            return [self._text(rows)]
        if name == "media_list":
            return [self._text(self.store.media_files())]
        if name == "source_add":
            path = self._need_str(args, "path")
            _, results = self.store.apply([{"op": "add_source", "path": path}], by="agent", probe=media.probe)
            return [self._text(results[0])]
        if name == "source_probe":
            path = self._need_str(args, "path")
            return [self._text(media.probe(self.store.resolve(path)))]
        if name == "source_frame":
            source = self.store.load().source(args.get("source"))
            t = float(args["t"])
            width = int(args.get("width", 640))
            data = media.frame(self.store.resolve(source["path"]), t, width)
            return [self._image(data), self._text(f"{source['id']} at {t:.3f} s", raw=True)]
        if name == "source_sheet":
            source = self.store.load().source(args.get("source"))
            cols, rows, width = int(args.get("cols", 6)), int(args.get("rows", 5)), int(args.get("width", 1920))
            data, interval, cols, rows = media.sheet(self.store.resolve(source["path"]), source["duration"],
                                                       cols, rows, width)
            note = f"{source['id']}: {cols}x{rows} tiles every {interval:.3f} s"
            return [self._image(data), self._text(note, raw=True)]
        if name == "source_scenes":
            source = self.store.load().source(args.get("source"))
            threshold = float(args.get("threshold", 0.3))
            return [self._text(media.scenes(self.store.resolve(source["path"]), threshold))]
        if name == "source_silences":
            source = self.store.load().source(args.get("source"))
            noise_db = float(args.get("noise_db", -30))
            min_duration = float(args.get("min_duration", 0.5))
            found = media.silences(self.store.resolve(source["path"]), source.get("has_audio", False),
                                    noise_db, min_duration, duration=source["duration"])
            return [self._text(found)]
        if name == "changes_apply":
            changes = args.get("changes")
            project, results = self.store.apply(changes, by="agent", probe=media.probe)
            body = {"results": results}
            body.update(self.store.describe(project))
            return [self._text(body)]
        if name == "export":
            project = self.store.load()
            path = args.get("path") or f"{self.store.dir}/{slugify(project.name)}.mp4"
            path = self.store.resolve(path)
            preset = args.get("preset") or "medium"
            return [self._text(media.export(project, self.store.resolve, path, preset))]
        raise ChangeError(f"unknown tool {name!r}")

    @staticmethod
    def _need_str(args: dict, key: str) -> str:
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ChangeError(f"{key} must be a non-empty string")
        return value

    @staticmethod
    def _text(value: Any, raw: bool = False) -> dict:
        return {"type": "text", "text": value if raw else json.dumps(value, indent=2)}

    @staticmethod
    def _image(data: bytes) -> dict:
        return {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mimeType": "image/jpeg"}


def serve(project_path: str) -> None:
    store = Store(project_path)
    if not store.exists():
        sys.exit(f"no project at {store.path}; run `studio new` first")
    handler = Handler(store)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"bad JSON-RPC message: {e}", file=sys.stderr)
            continue
        msg_id = message.get("id") if isinstance(message, dict) else None
        try:
            response = handler.handle(message)
        except Exception as e:  # a bug, not a tool failure: keep the loop alive
            print(f"internal error handling {message!r}: {e}", file=sys.stderr)
            response = handler._error(msg_id, -32603, str(e))
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
