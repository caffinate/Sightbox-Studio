"""The cut list: the editor's whole state, and the one write path onto it.

A project is a list of sources (media files) and a list of cuts. A cut is a span of
one source, `in` to `out` in source seconds, placed on a single track in list order.
There are no gaps and no overlaps; the timeline is derived from the list. Every
change, from the page, the command line, curl or an agent, is a small JSON object
with an `op`, applied by `Project.apply`. A batch either applies whole or not at all.

Ops and their fields:

    add_source     path                      -> {"source": id}   (idempotent per file)
    remove_source  source                    (refused while any cut uses it)
    add_cut        source [in] [out] [at]    -> {"cut": id}      (defaults: whole source, at the end)
    trim           cut [in] [out]
    split          cut at                    -> {"cut": id of the second half}   (at is source time)
    move           cut to                    (to is the index on the track)
    remove_cut     cut
    set_output     [width] [height] [fps]
    rename         name

Times are seconds, rounded to milliseconds. This module knows nothing about ffmpeg:
`add_source` takes a probe function, so `studio.media.probe` is injected by callers
and tests can pass a fake.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
from typing import Any, Callable, Optional

VERSION = 1
SLACK = 0.05        # seconds a cut may run past a source's probed duration
MIN_CUT = 0.02      # seconds; the shortest cut allowed
OPS = (
    "add_source", "remove_source", "add_cut", "trim", "split", "move",
    "remove_cut", "set_output", "rename",
)
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mts", ".m2ts", ".mxf")

Probe = Callable[[str], dict]
Resolve = Callable[[str], str]


class ChangeError(ValueError):
    """A change that cannot be applied. The message says why."""


def _number(value: Any, name: str, minimum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ChangeError(f"{name} must be a number")
    if minimum is not None and value < minimum:
        raise ChangeError(f"{name} must be at least {minimum}")
    return round(float(value), 3)


def _index(value: Any, name: str, limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChangeError(f"{name} must be an integer index")
    if value < 0 or value > limit:
        raise ChangeError(f"{name} must be between 0 and {limit}")
    return value


class Project:
    """The cut list. Sources and cuts are plain dicts so the JSON on disk is the truth."""

    def __init__(self, name: str = "Untitled", sources: Optional[list] = None,
                 cuts: Optional[list] = None, output: Optional[dict] = None,
                 next_ids: Optional[dict] = None, revision: int = 0):
        self.name = name
        self.sources: list[dict] = sources or []
        self.cuts: list[dict] = cuts or []
        self.output: Optional[dict] = output
        self.next_ids: dict = dict(next_ids or {"source": 1, "cut": 1})
        self.revision = revision

    # ---- serialisation -------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        version = data.get("version", VERSION)
        if version != VERSION:
            raise ValueError(f"cut list version {version} is not supported (this is version {VERSION})")
        return cls(
            name=str(data.get("name") or "Untitled"),
            sources=[dict(s) for s in data.get("sources", [])],
            cuts=[dict(c) for c in data.get("cuts", [])],
            output=dict(data["output"]) if data.get("output") else None,
            next_ids=data.get("next"),
            revision=int(data.get("revision", 0)),
        )

    def to_dict(self) -> dict:
        return {
            "version": VERSION,
            "revision": self.revision,
            "name": self.name,
            "output": dict(self.output) if self.output else None,
            "sources": [dict(s) for s in self.sources],
            "cuts": [dict(c) for c in self.cuts],
            "next": dict(self.next_ids),
        }

    def copy(self) -> "Project":
        return Project.from_dict(copy.deepcopy(self.to_dict()))

    # ---- lookups -------------------------------------------------------

    def source(self, sid: Any) -> dict:
        for s in self.sources:
            if s["id"] == sid:
                return s
        raise ChangeError(f"no source {sid!r}")

    def cut(self, cid: Any) -> dict:
        for c in self.cuts:
            if c["id"] == cid:
                return c
        raise ChangeError(f"no cut {cid!r}")

    def cut_index(self, cid: Any) -> int:
        for i, c in enumerate(self.cuts):
            if c["id"] == cid:
                return i
        raise ChangeError(f"no cut {cid!r}")

    def next_id(self, kind: str) -> str:
        n = self.next_ids.get(kind, 1)
        self.next_ids[kind] = n + 1
        return f"{kind[0]}{n}"

    # ---- derived -------------------------------------------------------

    def timeline(self) -> list[dict]:
        """Each cut with its place on the track: start, end and duration in timeline seconds."""
        out, at = [], 0.0
        for c in self.cuts:
            d = round(c["out"] - c["in"], 3)
            out.append({
                "cut": c["id"], "source": c["source"], "in": c["in"], "out": c["out"],
                "start": round(at, 3), "end": round(at + d, 3), "duration": d,
            })
            at += d
        return out

    def duration(self) -> float:
        return round(sum(c["out"] - c["in"] for c in self.cuts), 3)

    def locate(self, t: float) -> Optional[dict]:
        """The timeline entry under timeline second t, or the last one at the very end."""
        entries = self.timeline()
        for e in entries:
            if e["start"] <= t < e["end"]:
                return e
        if entries and t >= entries[-1]["end"]:
            return entries[-1]
        return None

    # ---- the write path -----------------------------------------------

    def apply(self, change: dict, probe: Optional[Probe] = None, resolve: Optional[Resolve] = None) -> dict:
        """Apply one change in place. Returns a small result dict. Raises ChangeError."""
        if not isinstance(change, dict):
            raise ChangeError("a change must be an object with an op")
        op = change.get("op")
        if op not in OPS:
            raise ChangeError(f"unknown op {op!r}; the ops are {', '.join(OPS)}")
        return getattr(self, f"_op_{op}")(change, probe, resolve)

    def _op_add_source(self, change: dict, probe: Optional[Probe], resolve: Optional[Resolve]) -> dict:
        path = change.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ChangeError("add_source needs a path")
        path = os.path.expanduser(path.strip())
        absolute = resolve(path) if resolve else os.path.abspath(path)
        for s in self.sources:
            existing = resolve(s["path"]) if resolve else os.path.abspath(s["path"])
            if os.path.normcase(existing) == os.path.normcase(absolute):
                return {"source": s["id"], "existing": True}
        if probe is None:
            raise ChangeError("add_source needs ffprobe to read the file")
        info = probe(absolute)
        sid = self.next_id("source")
        self.sources.append({
            "id": sid,
            "path": path,
            "name": os.path.basename(absolute),
            "duration": round(float(info["duration"]), 3),
            "width": int(info["width"]),
            "height": int(info["height"]),
            "fps": round(float(info["fps"]), 3),
            "has_audio": bool(info.get("has_audio", False)),
        })
        if self.output is None:
            self.output = {"width": int(info["width"]), "height": int(info["height"]), "fps": round(float(info["fps"]), 3)}
        return {"source": sid}

    def _op_remove_source(self, change: dict, probe: Any, resolve: Any) -> dict:
        s = self.source(change.get("source"))
        users = [c["id"] for c in self.cuts if c["source"] == s["id"]]
        if users:
            raise ChangeError(f"source {s['id']} is used by cut(s) {', '.join(users)}; remove those first")
        self.sources.remove(s)
        return {"source": s["id"]}

    def _op_add_cut(self, change: dict, probe: Any, resolve: Any) -> dict:
        s = self.source(change.get("source"))
        start = _number(change.get("in", 0.0), "in", 0.0)
        end = _number(change.get("out", s["duration"]), "out")
        self._check_span(s, start, end)
        at = _index(change.get("at", len(self.cuts)), "at", len(self.cuts))
        cid = self.next_id("cut")
        self.cuts.insert(at, {"id": cid, "source": s["id"], "in": start, "out": end})
        return {"cut": cid}

    def _op_trim(self, change: dict, probe: Any, resolve: Any) -> dict:
        c = self.cut(change.get("cut"))
        s = self.source(c["source"])
        start = _number(change.get("in", c["in"]), "in", 0.0)
        end = _number(change.get("out", c["out"]), "out")
        self._check_span(s, start, end)
        c["in"], c["out"] = start, end
        return {"cut": c["id"]}

    def _op_split(self, change: dict, probe: Any, resolve: Any) -> dict:
        c = self.cut(change.get("cut"))
        if "at" not in change:
            raise ChangeError("split needs `at`, a source time inside the cut")
        at = _number(change["at"], "at")
        if at < c["in"] + MIN_CUT or at > c["out"] - MIN_CUT:
            raise ChangeError(f"at must be inside the cut, between {c['in'] + MIN_CUT:.3f} and {c['out'] - MIN_CUT:.3f}")
        second = {"id": self.next_id("cut"), "source": c["source"], "in": at, "out": c["out"]}
        c["out"] = at
        self.cuts.insert(self.cut_index(c["id"]) + 1, second)
        return {"cut": second["id"]}

    def _op_move(self, change: dict, probe: Any, resolve: Any) -> dict:
        c = self.cut(change.get("cut"))
        to = _index(change.get("to"), "to", len(self.cuts) - 1)
        self.cuts.remove(c)
        self.cuts.insert(to, c)
        return {"cut": c["id"], "to": to}

    def _op_remove_cut(self, change: dict, probe: Any, resolve: Any) -> dict:
        c = self.cut(change.get("cut"))
        self.cuts.remove(c)
        return {"cut": c["id"]}

    def _op_set_output(self, change: dict, probe: Any, resolve: Any) -> dict:
        current = dict(self.output or {"width": 1920, "height": 1080, "fps": 30.0})
        for key in ("width", "height"):
            if key in change:
                v = change[key]
                if isinstance(v, bool) or not isinstance(v, int) or v < 16 or v % 2:
                    raise ChangeError(f"{key} must be an even integer of at least 16")
                current[key] = v
        if "fps" in change:
            current["fps"] = _number(change["fps"], "fps", 1.0)
        self.output = current
        return {"output": dict(current)}

    def _op_rename(self, change: dict, probe: Any, resolve: Any) -> dict:
        name = change.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ChangeError("rename needs a name")
        self.name = name.strip()
        return {"name": self.name}

    @staticmethod
    def _check_span(source: dict, start: float, end: float) -> None:
        if end - start < MIN_CUT:
            raise ChangeError(f"a cut must be at least {MIN_CUT} seconds long (in {start}, out {end})")
        if end > source["duration"] + SLACK:
            raise ChangeError(f"out {end} is past the end of {source['name']} ({source['duration']} s)")


def apply_changes(project: Project, changes: list, probe: Optional[Probe] = None,
                  resolve: Optional[Resolve] = None) -> tuple[Project, list[dict]]:
    """Apply a batch to a copy. Either every change applies or none does."""
    if not isinstance(changes, list) or not changes:
        raise ChangeError("changes must be a non-empty list")
    draft = project.copy()
    results = []
    for i, change in enumerate(changes):
        try:
            results.append(draft.apply(change, probe, resolve))
        except ChangeError as e:
            op = change.get("op") if isinstance(change, dict) else "?"
            raise ChangeError(f"change {i} ({op}): {e}") from e
    return draft, results


class Store:
    """The cut list on disk, plus a journal of every batch applied to it."""

    def __init__(self, path: str):
        self.path = os.path.abspath(os.path.expanduser(path))
        self.dir = os.path.dirname(self.path)
        stem, _ = os.path.splitext(os.path.basename(self.path))
        self.journal_path = os.path.join(self.dir, f"{stem}.journal.jsonl")
        self._lock = threading.Lock()

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def create(self, name: str) -> Project:
        if self.exists():
            raise FileExistsError(self.path)
        project = Project(name=name)
        self.save(project)
        return project

    def load(self) -> Project:
        with open(self.path, encoding="utf-8") as fh:
            return Project.from_dict(json.load(fh))

    def save(self, project: Project) -> None:
        """Write atomically. Every save is a new revision."""
        project.revision += 1
        os.makedirs(self.dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".studio-", suffix=".json", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(project.to_dict(), fh, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def resolve(self, path: str) -> str:
        path = os.path.expanduser(path)
        if os.path.isabs(path):
            return os.path.normpath(path)
        return os.path.normpath(os.path.join(self.dir, path))

    def relativise(self, path: str) -> str:
        """A path inside the project's folder is kept relative so the folder can move."""
        absolute = self.resolve(path)
        try:
            rel = os.path.relpath(absolute, self.dir)
        except ValueError:
            return absolute
        if rel.startswith(os.pardir):
            return absolute
        return rel

    def apply(self, changes: list, by: str, probe: Optional[Probe] = None) -> tuple[Project, list[dict]]:
        """Load, apply a batch whole, save, journal. The lock covers threads in one process."""
        if not isinstance(changes, list):
            raise ChangeError("changes must be a non-empty list")
        prepared = []
        for change in changes:
            if isinstance(change, dict) and change.get("op") == "add_source" and isinstance(change.get("path"), str):
                change = dict(change)
                change["path"] = self.relativise(change["path"])
            prepared.append(change)
        with self._lock:
            project = self.load()
            draft, results = apply_changes(project, prepared, probe, self.resolve)
            self.save(draft)
            self._journal(by, prepared, results)
            return draft, results

    def _journal(self, by: str, changes: list, results: list) -> None:
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "by": by, "changes": changes, "results": results}
        with open(self.journal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def describe(self, project: Optional[Project] = None) -> dict:
        """What the page and the agent read: the project, the timeline, and where it lives."""
        project = project or self.load()
        return {
            "version": str(project.revision),
            "file": self.path,
            "dir": self.dir,
            "project": project.to_dict(),
            "timeline": project.timeline(),
            "duration": project.duration(),
        }

    def media_files(self, media_dir: Optional[str] = None, project: Optional[Project] = None) -> dict:
        """Video files in a folder, marked with the source id when already added."""
        folder = os.path.abspath(os.path.expanduser(media_dir or self.dir))
        project = project or self.load()
        known = {os.path.normcase(self.resolve(s["path"])): s["id"] for s in project.sources}
        files = []
        try:
            names = sorted(os.listdir(folder), key=str.lower)
        except OSError:
            names = []
        for name in names:
            full = os.path.join(folder, name)
            if name.startswith(".") or not os.path.isfile(full):
                continue
            if not name.lower().endswith(VIDEO_EXTENSIONS):
                continue
            files.append({
                "name": name,
                "path": full,
                "bytes": os.path.getsize(full),
                "source": known.get(os.path.normcase(os.path.normpath(full))),
            })
        return {"dir": folder, "files": files}
