"""ffprobe and ffmpeg, wrapped: probe a file, export the timeline. No shell strings.

`FFMPEG` and `FFPROBE` come from `STUDIO_FFMPEG` / `STUDIO_FFPROBE` when set, else
`ffmpeg` and `ffprobe` on PATH. `available()` says whether both are present; callers
skip (tests) or refuse (the server, the CLI) when they are not. Every ffmpeg or
ffprobe failure raises `MediaError` with the command's stderr tail in the message.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Optional

FFMPEG = os.environ.get("STUDIO_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("STUDIO_FFPROBE", "ffprobe")

STDERR_TAIL = 2000


class MediaError(RuntimeError):
    """ffmpeg or ffprobe failed. The message carries the stderr tail."""


def available() -> bool:
    return bool(shutil.which(FFMPEG) and shutil.which(FFPROBE))


def _run(args: list, capture_stdout: bool = False) -> subprocess.CompletedProcess:
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[-STDERR_TAIL:]
        raise MediaError(f"{os.path.basename(args[0])} failed: {tail.strip()}")
    return proc


def probe(path: str) -> dict:
    """The source fields `studio.project.add_source` needs: duration, width, height, fps, has_audio."""
    proc = _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    data = json.loads(proc.stdout)
    video = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not int(s.get("disposition", {}).get("attached_pic", 0)):
            video = s
            break
    if video is None:
        raise MediaError(f"{path} has no video stream")
    num, _, den = video.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den) if den and float(den) else 0.0
    rotation = 0
    for sd in video.get("side_data_list") or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if not rotation and "rotate" in (video.get("tags") or {}):
        rotation = int(video["tags"]["rotate"])
    width, height = int(video["width"]), int(video["height"])
    if rotation % 180:
        width, height = height, width
    duration = data.get("format", {}).get("duration")
    if duration is None:
        raise MediaError(f"{path}: ffprobe reported no duration")
    return {
        "duration": round(float(duration), 3),
        "width": width,
        "height": height,
        "fps": round(fps, 3),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "export"


def _export_args(cuts: list, out_path: str, width: int, height: int, fps: float, preset: str) -> list:
    """cuts: [(path, in, out, has_audio), ...]. The proven graph from BRIEF.md, verbatim."""
    args = [FFMPEG, "-hide_banner", "-v", "error", "-y"]
    graph = []
    for i, (path, cin, cout, has_audio) in enumerate(cuts):
        d = cout - cin
        args += ["-ss", f"{cin:.3f}", "-t", f"{d:.3f}", "-i", path]
        graph.append(
            f"[{i}:v]setpts=PTS-STARTPTS,fps={fps:g},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]"
        )
        if has_audio:
            graph.append(f"[{i}:a]asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{i}]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo:d={d:.3f}[a{i}]")
    n = len(cuts)
    graph.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    args += [
        "-filter_complex", ";".join(graph),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", preset, "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        out_path,
    ]
    return args


def export(project, resolve, out_path: str, preset: str = "medium") -> dict:
    """Render `project`'s timeline to `out_path`. `resolve` turns a stored source path
    (possibly relative to the project's folder) into an absolute path. Returns
    `{"bytes", "duration"}`; `duration` is the exported file's, probed back."""
    if not project.cuts:
        raise MediaError("nothing to export: the timeline has no cuts")
    output = project.output or {"width": 1920, "height": 1080, "fps": 30.0}
    sources = {s["id"]: s for s in project.sources}
    cuts = []
    for c in project.cuts:
        s = sources[c["source"]]
        cuts.append((resolve(s["path"]), c["in"], c["out"], bool(s.get("has_audio"))))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    args = _export_args(cuts, out_path, output["width"], output["height"], float(output["fps"]), preset)
    _run(args)
    size = os.path.getsize(out_path)
    duration = probe(out_path)["duration"]
    return {"bytes": size, "duration": duration}
