"""The cut list: the editor's whole state, and the one write path onto it.

A project is a list of sources (media files), a list of tracks, and a list of clips.
A clip is a span of one source, `in` to `out` in source seconds, placed on one track at
an authored timeline second, `start`. Two clips on the SAME track must not overlap in
time; clips on DIFFERENT tracks are expected to, and often will -- that is the entire
point of tracks. Video tracks are priority-ordered (later in `tracks` = higher priority);
where video tracks overlap in time, the highest-priority track's clip is what's visible.
Audio tracks all mix together. Every change, from the page, the command line, curl or an
agent, is a small JSON object with an `op`, applied by `Project.apply`. A batch either
applies whole or not at all.

A clip created from a source with both video and audio gets two rows -- a video clip and
an audio clip -- sharing a `link` id, so the UI can treat them as one editorial unit; a
cascading trim or move affects both while linked. `unlink` (or moving one to a different
track) breaks the shared id so each can move or trim independently -- this is what makes
an offset J-cut/L-cut of a single original clip possible. `remove_clip` removes exactly
one clip; a linked sibling survives, now unlinked -- a clip "turning into audio-only or
video-only" is just this.

Ops and their fields:

    add_source     path                                  -> {"source": id}   (idempotent per file)
    remove_source  source                                (refused while any clip uses it)
    set_output     [width] [height] [fps]
    rename         name
    add_track      kind [name]                            -> {"track": id}
    remove_track   track                                  (refused while any clip is on it, or if it's the last of its kind)
    reorder_track  track to                                (to is an index among same-kind tracks)
    add_clip       source [in] [out] [start]
                   [video_track] [audio_track] [with_audio]   -> {"clip": id, "sibling": id_or_null}
    trim           clip [in] [out]                         -> {"clip": id, "sibling": id_or_null}
    move           clip start [track]                      -> {"clip": id, "sibling": id_or_null, "unlinked": bool}
    unlink         clip                                    -> {"clip": id, "sibling": id_or_null}
    split          clip at                                 -> {"clip": id of the second half, "sibling": id_or_null}
    remove_clip    clip                                    -> {"clip": id}

Times are seconds, rounded to milliseconds. This module knows nothing about ffmpeg:
`add_source` takes a probe function, so `studio.media.probe` is injected by callers and
tests can pass a fake. Every registered source has a video stream (`media.probe` refuses
a file without one), so `add_clip` always places a video clip; only the audio sibling is
ever conditional.

A version-1 project file (a flat `cuts` list, no `tracks`/`clips`) is upgraded to this
shape on load -- see `_migrate_v1`. It is a pure function of the old cut list alone, so
two independent readers loading the same not-yet-saved v1 file always agree on every id.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
from typing import Any, Callable, Optional

VERSION = 2
SLACK = 0.05        # seconds a clip may run past a source's probed duration
MIN_CUT = 0.02      # seconds; the shortest clip allowed
OPS = (
    "add_source", "remove_source", "set_output", "rename",
    "add_track", "remove_track", "reorder_track",
    "add_clip", "trim", "move", "unlink", "split", "remove_clip",
)
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mts", ".m2ts", ".mxf")

DEFAULT_TRACKS = ({"id": "v1", "kind": "video", "name": "V1"}, {"id": "a1", "kind": "audio", "name": "A1"})
DEFAULT_NEXT_IDS = {"source": 1, "track_video": 2, "track_audio": 2, "clip": 1, "link": 1}

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


def _migrate_v1(data: dict) -> dict:
    """A pure function of a v1-shaped dict's cut list. Fixed default track ids, and every
    clip/link id derived purely from cut index, so this never depends on anything but the
    cuts themselves -- two independent loads of the same file always mint identical ids.
    """
    cuts = data.get("cuts", [])
    sources = data.get("sources", [])
    by_id = {s["id"]: s for s in sources}
    clips = []
    at = 0.0
    for i, c in enumerate(cuts):
        d = round(c["out"] - c["in"], 3)
        start = round(at, 3)
        source = by_id.get(c["source"])
        has_audio = bool(source and source.get("has_audio"))
        link = f"L{i + 1}" if has_audio else None
        video_id = f"c{2 * i + 1}"
        clips.append({"id": video_id, "track": "v1", "source": c["source"],
                      "in": c["in"], "out": c["out"], "start": start, "link": link})
        if has_audio:
            audio_id = f"c{2 * i + 2}"
            clips.append({"id": audio_id, "track": "a1", "source": c["source"],
                          "in": c["in"], "out": c["out"], "start": start, "link": link})
        at += d
    next_ids = dict(DEFAULT_NEXT_IDS)
    next_ids["source"] = int((data.get("next") or {}).get("source", 1))
    next_ids["clip"] = len(clips) + 1
    next_ids["link"] = len(cuts) + 1
    return {
        "version": 2,
        "revision": data.get("revision", 0),
        "name": data.get("name"),
        "output": data.get("output"),
        "sources": sources,
        "tracks": [dict(t) for t in DEFAULT_TRACKS],
        "clips": clips,
        "next": next_ids,
    }


class Project:
    """The cut list. Sources, tracks and clips are plain dicts so the JSON on disk is the truth."""

    def __init__(self, name: str = "Untitled", sources: Optional[list] = None,
                 tracks: Optional[list] = None, clips: Optional[list] = None,
                 output: Optional[dict] = None, next_ids: Optional[dict] = None, revision: int = 0):
        self.name = name
        self.sources: list[dict] = sources or []
        self.tracks: list[dict] = tracks if tracks else [dict(t) for t in DEFAULT_TRACKS]
        self.clips: list[dict] = clips or []
        self.output: Optional[dict] = output
        self.next_ids: dict = dict(next_ids or DEFAULT_NEXT_IDS)
        self.revision = revision

    # ---- serialisation -------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Project":
        version = data.get("version", VERSION)
        if version == 1:
            data = _migrate_v1(data)
        elif version != VERSION:
            raise ValueError(f"cut list version {version} is not supported (this is version {VERSION})")
        return cls(
            name=str(data.get("name") or "Untitled"),
            sources=[dict(s) for s in data.get("sources", [])],
            tracks=[dict(t) for t in data.get("tracks", [])],
            clips=[dict(c) for c in data.get("clips", [])],
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
            "tracks": [dict(t) for t in self.tracks],
            "clips": [dict(c) for c in self.clips],
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

    def track(self, tid: Any) -> dict:
        for t in self.tracks:
            if t["id"] == tid:
                return t
        raise ChangeError(f"no track {tid!r}")

    def clip(self, cid: Any) -> dict:
        for c in self.clips:
            if c["id"] == cid:
                return c
        raise ChangeError(f"no clip {cid!r}")

    def clips_on(self, track_id: str) -> list:
        return [c for c in self.clips if c["track"] == track_id]

    def track_priority(self, track_id: str) -> int:
        """Index among same-kind tracks; higher means higher priority (topmost, for video)."""
        kind = self.track(track_id)["kind"]
        same_kind = [t["id"] for t in self.tracks if t["kind"] == kind]
        return same_kind.index(track_id)

    def next_clip_id(self) -> str:
        n = self.next_ids.get("clip", 1)
        self.next_ids["clip"] = n + 1
        return f"c{n}"

    def next_link_id(self) -> str:
        n = self.next_ids.get("link", 1)
        self.next_ids["link"] = n + 1
        return f"L{n}"

    def next_track_id(self, kind: str) -> str:
        key = f"track_{kind}"
        n = self.next_ids.get(key, 2)
        self.next_ids[key] = n + 1
        return f"{'v' if kind == 'video' else 'a'}{n}"

    def next_id(self, kind: str) -> str:
        n = self.next_ids.get(kind, 1)
        self.next_ids[kind] = n + 1
        return f"{kind[0]}{n}"

    # ---- derived -------------------------------------------------------

    @staticmethod
    def _clip_end(c: dict) -> float:
        return round(c["start"] + (c["out"] - c["in"]), 3)

    def _track_end(self, track_id: str) -> float:
        cs = self.clips_on(track_id)
        return max((self._clip_end(c) for c in cs), default=0.0)

    def _overlaps(self, track_id: str, start: float, end: float, exclude_clip: Any = None) -> Optional[dict]:
        for c in self.clips_on(track_id):
            if c["id"] == exclude_clip:
                continue
            cs, ce = c["start"], self._clip_end(c)
            if start < ce and cs < end:
                return c
        return None

    def _sibling(self, c: dict) -> Optional[dict]:
        link = c.get("link")
        if not link:
            return None
        for other in self.clips:
            if other is not c and other.get("link") == link:
                return other
        return None

    def timeline(self) -> list[dict]:
        """Every clip, raw: one entry per clip, legitimately overlapping across tracks."""
        out = []
        for c in self.clips:
            d = round(c["out"] - c["in"], 3)
            out.append({
                "clip": c["id"], "track": c["track"], "source": c["source"],
                "in": c["in"], "out": c["out"], "start": c["start"],
                "end": round(c["start"] + d, 3), "duration": d, "link": c.get("link"),
            })
        return out

    def duration(self) -> float:
        if not self.clips:
            return 0.0
        return round(max(self._clip_end(c) for c in self.clips), 3)

    def video_segments(self) -> list[dict]:
        """The flattened picture: at every instant, the highest-priority video track's
        covering clip wins (never blended); an interval no video track covers is a
        filler marker. Pure Python, no ffmpeg -- the single source of truth media.py's
        exporter, the page's preview, and an MCP agent all read, instead of each
        re-resolving track priority independently.
        """
        total = self.duration()
        video_clips = [c for c in self.clips if self.track(c["track"])["kind"] == "video"]
        if total <= 0:
            return []
        if not video_clips:
            return [{"kind": "filler", "source": None, "in": None, "out": None,
                     "start": 0.0, "end": total, "duration": total}]
        breakpoints = sorted({0.0, total} | {c["start"] for c in video_clips} | {self._clip_end(c) for c in video_clips})
        segments = []
        for a, b in zip(breakpoints, breakpoints[1:]):
            if b - a <= 0:
                continue
            mid = (a + b) / 2
            covering = [c for c in video_clips if c["start"] <= mid < self._clip_end(c)]
            if covering:
                winner = max(covering, key=lambda c: self.track_priority(c["track"]))
                segments.append({
                    "kind": "clip", "source": winner["source"],
                    "in": round(winner["in"] + (a - winner["start"]), 3),
                    "out": round(winner["in"] + (b - winner["start"]), 3),
                    "start": round(a, 3), "end": round(b, 3), "duration": round(b - a, 3),
                })
            else:
                segments.append({"kind": "filler", "source": None, "in": None, "out": None,
                                  "start": round(a, 3), "end": round(b, 3), "duration": round(b - a, 3)})
        merged = []
        for seg in segments:
            prev = merged[-1] if merged else None
            contiguous = prev and prev["kind"] == seg["kind"] and prev.get("source") == seg.get("source") and (
                seg["kind"] == "filler" or abs(prev["out"] - seg["in"]) < 1e-9)
            if contiguous:
                prev["out"], prev["end"] = seg["out"], seg["end"]
                prev["duration"] = round(prev["end"] - prev["start"], 3)
            else:
                merged.append(dict(seg))
        return merged

    def locate(self, t: float) -> Optional[dict]:
        """The video segment under timeline second t, or the last one at the very end."""
        entries = self.video_segments()
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
        users = [c["id"] for c in self.clips if c["source"] == s["id"]]
        if users:
            raise ChangeError(f"source {s['id']} is used by clip(s) {', '.join(users)}; remove those first")
        self.sources.remove(s)
        return {"source": s["id"]}

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

    def _op_add_track(self, change: dict, probe: Any, resolve: Any) -> dict:
        kind = change.get("kind")
        if kind not in ("video", "audio"):
            raise ChangeError("add_track needs kind 'video' or 'audio'")
        tid = self.next_track_id(kind)
        name = change.get("name")
        if name is not None and (not isinstance(name, str) or not name.strip()):
            raise ChangeError("name must be a non-empty string")
        self.tracks.append({"id": tid, "kind": kind, "name": (name.strip() if name else tid.upper())})
        return {"track": tid}

    def _op_remove_track(self, change: dict, probe: Any, resolve: Any) -> dict:
        t = self.track(change.get("track"))
        users = [c["id"] for c in self.clips if c["track"] == t["id"]]
        if users:
            raise ChangeError(f"track {t['id']} is used by clip(s) {', '.join(users)}; remove those first")
        same_kind = [tr for tr in self.tracks if tr["kind"] == t["kind"]]
        if len(same_kind) <= 1:
            raise ChangeError(f"cannot remove the last {t['kind']} track")
        self.tracks.remove(t)
        return {"track": t["id"]}

    def _op_reorder_track(self, change: dict, probe: Any, resolve: Any) -> dict:
        t = self.track(change.get("track"))
        same_kind_ids = [tr["id"] for tr in self.tracks if tr["kind"] == t["kind"]]
        to = _index(change.get("to"), "to", len(same_kind_ids) - 1)
        self.tracks.remove(t)
        remaining = [tr["id"] for tr in self.tracks if tr["kind"] == t["kind"]]
        if to >= len(remaining):
            insert_at = (self._track_index(remaining[-1]) + 1) if remaining else len(self.tracks)
        else:
            insert_at = self._track_index(remaining[to])
        self.tracks.insert(insert_at, t)
        return {"track": t["id"], "to": to}

    def _track_index(self, tid: str) -> int:
        for i, tr in enumerate(self.tracks):
            if tr["id"] == tid:
                return i
        raise ChangeError(f"no track {tid!r}")

    def _op_add_clip(self, change: dict, probe: Any, resolve: Any) -> dict:
        s = self.source(change.get("source"))
        start_in = _number(change.get("in", 0.0), "in", 0.0)
        end_out = _number(change.get("out", s["duration"]), "out")
        self._check_span(s, start_in, end_out)

        with_audio = change.get("with_audio", True)
        if not isinstance(with_audio, bool):
            raise ChangeError("with_audio must be true or false")
        make_audio = bool(s.get("has_audio")) and with_audio

        video_track_id = change.get("video_track", "v1")
        vt = self.track(video_track_id)
        if vt["kind"] != "video":
            raise ChangeError(f"video_track {video_track_id!r} is not a video track")

        audio_track_id = None
        if make_audio:
            audio_track_id = change.get("audio_track", "a1")
            at = self.track(audio_track_id)
            if at["kind"] != "audio":
                raise ChangeError(f"audio_track {audio_track_id!r} is not an audio track")

        duration = round(end_out - start_in, 3)
        if "start" in change:
            start = _number(change["start"], "start", 0.0)
        else:
            candidates = [self._track_end(video_track_id)]
            if make_audio:
                candidates.append(self._track_end(audio_track_id))
            start = max(candidates)
        end = round(start + duration, 3)

        collision = self._overlaps(video_track_id, start, end)
        if collision:
            raise ChangeError(f"clip would overlap {collision['id']} on track {video_track_id}")
        if make_audio:
            collision = self._overlaps(audio_track_id, start, end)
            if collision:
                raise ChangeError(f"clip would overlap {collision['id']} on track {audio_track_id}")

        link = self.next_link_id() if make_audio else None
        video_id = self.next_clip_id()
        self.clips.append({"id": video_id, "track": video_track_id, "source": s["id"],
                           "in": start_in, "out": end_out, "start": start, "link": link})
        sibling = None
        if make_audio:
            sibling = self.next_clip_id()
            self.clips.append({"id": sibling, "track": audio_track_id, "source": s["id"],
                               "in": start_in, "out": end_out, "start": start, "link": link})
        return {"clip": video_id, "sibling": sibling}

    def _op_trim(self, change: dict, probe: Any, resolve: Any) -> dict:
        c = self.clip(change.get("clip"))
        s = self.source(c["source"])
        new_in = _number(change.get("in", c["in"]), "in", 0.0)
        new_out = _number(change.get("out", c["out"]), "out")
        self._check_span(s, new_in, new_out)
        sibling = self._sibling(c)
        c["in"], c["out"] = new_in, new_out
        if sibling is not None:
            sibling["in"], sibling["out"] = new_in, new_out
        return {"clip": c["id"], "sibling": sibling["id"] if sibling else None}

    def _op_move(self, change: dict, probe: Any, resolve: Any) -> dict:
        c = self.clip(change.get("clip"))
        if "start" not in change:
            raise ChangeError("move needs `start`")
        new_start = _number(change["start"], "start", 0.0)
        target_track = change.get("track")
        sibling = self._sibling(c)

        if target_track is not None:
            tt = self.track(target_track)
            current_kind = self.track(c["track"])["kind"]
            if tt["kind"] != current_kind:
                raise ChangeError(f"a {current_kind} clip cannot move to {target_track!r} ({tt['kind']})")
            end = round(new_start + (c["out"] - c["in"]), 3)
            collision = self._overlaps(target_track, new_start, end, exclude_clip=c["id"])
            if collision:
                raise ChangeError(f"clip would overlap {collision['id']} on track {target_track}")
            c["track"], c["start"] = target_track, new_start
            unlinked = sibling is not None
            if sibling is not None:
                c["link"] = None
                sibling["link"] = None
            return {"clip": c["id"], "sibling": sibling["id"] if sibling else None, "unlinked": unlinked}

        delta = round(new_start - c["start"], 3)
        end = round(new_start + (c["out"] - c["in"]), 3)
        collision = self._overlaps(c["track"], new_start, end, exclude_clip=c["id"])
        if collision:
            raise ChangeError(f"clip would overlap {collision['id']} on track {c['track']}")
        sib_new_start = None
        if sibling is not None:
            sib_new_start = round(sibling["start"] + delta, 3)
            if sib_new_start < 0:
                raise ChangeError("move would push the linked clip before the start of the timeline")
            sib_end = round(sib_new_start + (sibling["out"] - sibling["in"]), 3)
            collision = self._overlaps(sibling["track"], sib_new_start, sib_end, exclude_clip=sibling["id"])
            if collision:
                raise ChangeError(f"clip would overlap {collision['id']} on track {sibling['track']}")
        c["start"] = new_start
        if sibling is not None:
            sibling["start"] = sib_new_start
        return {"clip": c["id"], "sibling": sibling["id"] if sibling else None, "unlinked": False}

    def _op_unlink(self, change: dict, probe: Any, resolve: Any) -> dict:
        c = self.clip(change.get("clip"))
        sibling = self._sibling(c)
        if sibling is not None:
            c["link"] = None
            sibling["link"] = None
        return {"clip": c["id"], "sibling": sibling["id"] if sibling else None}

    def _op_split(self, change: dict, probe: Any, resolve: Any) -> dict:
        c = self.clip(change.get("clip"))
        if "at" not in change:
            raise ChangeError("split needs `at`, a source time inside the clip")
        at = _number(change["at"], "at")
        if at < c["in"] + MIN_CUT or at > c["out"] - MIN_CUT:
            raise ChangeError(f"at must be inside the clip, between {c['in'] + MIN_CUT:.3f} and {c['out'] - MIN_CUT:.3f}")
        sibling = self._sibling(c)

        # A clip always exclusively owns its own contiguous [start, end) span (the overlap
        # rule enforces this everywhere else), and the two halves below exactly partition
        # that same span with no gap and no extension past either original edge -- so a
        # split can never collide with anything else on the same track. No check needed.
        new_start_c = round(c["start"] + (at - c["in"]), 3)
        new_start_s = round(sibling["start"] + (at - sibling["in"]), 3) if sibling is not None else None

        new_link = self.next_link_id() if sibling is not None else None
        second_id = self.next_clip_id()
        self.clips.append({"id": second_id, "track": c["track"], "source": c["source"],
                           "in": at, "out": c["out"], "start": new_start_c, "link": new_link})
        c["out"] = at

        second_sibling = None
        if sibling is not None:
            second_sibling = self.next_clip_id()
            self.clips.append({"id": second_sibling, "track": sibling["track"], "source": sibling["source"],
                               "in": at, "out": sibling["out"], "start": new_start_s, "link": new_link})
            sibling["out"] = at
        return {"clip": second_id, "sibling": second_sibling}

    def _op_remove_clip(self, change: dict, probe: Any, resolve: Any) -> dict:
        c = self.clip(change.get("clip"))
        sibling = self._sibling(c)
        if sibling is not None:
            sibling["link"] = None
        self.clips.remove(c)
        return {"clip": c["id"]}

    @staticmethod
    def _check_span(source: dict, start: float, end: float) -> None:
        if end - start < MIN_CUT:
            raise ChangeError(f"a clip must be at least {MIN_CUT} seconds long (in {start}, out {end})")
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

    def _raw_version(self) -> Any:
        with open(self.path, encoding="utf-8") as fh:
            return json.load(fh).get("version", VERSION)

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
            migrating = self.exists() and self._raw_version() == 1
            project = self.load()
            draft, results = apply_changes(project, prepared, probe, self.resolve)
            self.save(draft)
            if migrating:
                self._journal_migration()
            self._journal(by, prepared, results)
            return draft, results

    def _journal(self, by: str, changes: list, results: list) -> None:
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "by": by, "changes": changes, "results": results}
        with open(self.journal_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    def _journal_migration(self) -> None:
        """Marks, in the journal, the moment a v1 project's vocabulary changed to v2's."""
        entry = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "op": "_migrate_v1_to_v2"}
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
            "video_segments": project.video_segments(),
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
