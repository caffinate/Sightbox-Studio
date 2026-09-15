"""ffprobe and ffmpeg: probe and export. Frame, sheet, scenes and silences come in build order 4.

Every call is an argument list through `subprocess`, never a shell string. `FFMPEG`
and `FFPROBE` come from the environment variables `STUDIO_FFMPEG` and `STUDIO_FFPROBE`
when set, else `ffmpeg` and `ffprobe` on PATH. This module knows nothing about the
cut list beyond what a caller hands it: `export` takes a `Project` and a `resolve`
function so a source's stored path (possibly relative) becomes a real file path.

The proven commands, timings and expected outputs are in BRIEF.md and reproduced on
synthetic clips by `tools/ffmpeg_proofs.py`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

from .project import ChangeError, Project, Resolve

FFMPEG = os.environ.get("STUDIO_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("STUDIO_FFPROBE", "ffprobe")


def available() -> bool:
    return bool(shutil.which(FFMPEG) and shutil.which(FFPROBE))


def _run(args: list) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True)


def _fail(args: list, proc: subprocess.CompletedProcess) -> None:
    tail = proc.stderr.decode(errors="replace")[-2000:]
    raise ChangeError(f"{os.path.basename(args[0])} failed: {tail.strip() or 'no output'}")


def probe(path: str) -> dict:
    """What `add_source` and the `probe` CLI command need: duration, size, fps, audio."""
    if not shutil.which(FFPROBE):
        raise ChangeError("ffprobe was not found on PATH")
    if not os.path.isfile(path):
        raise ChangeError(f"no such file: {path}")
    p = _run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    if p.returncode != 0:
        _fail([FFPROBE], p)
    data = json.loads(p.stdout)
    video = next((s for s in data.get("streams", [])
                  if s.get("codec_type") == "video" and not int(s.get("disposition", {}).get("attached_pic", 0))),
                 None)
    if video is None:
        raise ChangeError(f"{path} has no video stream")
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den) if float(den or 0) else 0.0
    rotation = 0
    for sd in video.get("side_data_list") or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if not rotation and "rotate" in (video.get("tags") or {}):
        rotation = int(video["tags"]["rotate"])
    width, height = int(video["width"]), int(video["height"])
    if rotation % 180:
        width, height = height, width
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0.0)
    has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams", []))
    return {"duration": round(duration, 3), "width": width, "height": height,
            "fps": round(fps, 3), "has_audio": has_audio}


def export(project: Project, resolve: Resolve, out_path: str, preset: str = "medium") -> dict:
    """The proven export graph, verbatim, built from the project's cuts and output."""
    if not project.cuts:
        raise ChangeError("nothing to export: the cut list is empty")
    if not project.output:
        raise ChangeError("nothing to export: no output size is set")
    width, height, fps = project.output["width"], project.output["height"], project.output["fps"]
    args = [FFMPEG, "-hide_banner", "-v", "error", "-y"]
    graph = []
    for i, c in enumerate(project.cuts):
        source = project.source(c["source"])
        path = resolve(source["path"])
        duration = round(c["out"] - c["in"], 3)
        args += ["-ss", f"{c['in']:.3f}", "-t", f"{duration:.3f}", "-i", path]
        graph.append(
            f"[{i}:v]setpts=PTS-STARTPTS,fps={fps:g},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]"
        )
        if source.get("has_audio"):
            graph.append(f"[{i}:a]asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{i}]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo:d={duration:.3f}[a{i}]")
    n = len(project.cuts)
    graph.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    args += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", preset, "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
    t0 = time.time()
    p = _run(args)
    seconds = time.time() - t0
    if p.returncode != 0:
        _fail(args, p)
    return {"path": out_path, "bytes": os.path.getsize(out_path),
            "seconds": round(seconds, 3), "duration": probe(out_path)["duration"]}
