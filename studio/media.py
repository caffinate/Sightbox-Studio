"""ffprobe and ffmpeg: probe, export, and an agent's eyes on the footage
(frame, sheet, scenes, silences).

Every call is an argument list through `subprocess`, never a shell string. `FFMPEG`
and `FFPROBE` come from the environment variables `STUDIO_FFMPEG` and `STUDIO_FFPROBE`
when set, else `ffmpeg` and `ffprobe` on PATH. This module knows nothing about the cut
list beyond what a caller hands it: `export` takes a `Project` and a `resolve` function
so a source's stored path (possibly relative) becomes a real file path.

The proven commands, timings and expected outputs are in BRIEF.md and MULTITRACK-BRIEF.md,
reproduced on synthetic clips by `tools/ffmpeg_proofs.py`. `export` is the v2 multi-track
graph: `Project.video_segments()` (already priority-resolved, real clips and black filler)
becomes a video-only concat; every audio clip on every audio track is mixed separately
(`amix`, not concatenated) and delayed into position. Both pipelines snap every timing to
the SAME output-frame grid before building any ffmpeg args, so a cut lands at the same
instant on both -- ffmpeg_proofs.py proved this matters: computed independently, video's
frame-quantized timing and audio's millisecond-exact timing drift apart.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from typing import Optional

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


def _frame_snap(t: float, fps: float) -> int:
    """The output-frame grid both the video and audio pipelines share below."""
    return round(t * fps)


def export(project: Project, resolve: Resolve, out_path: str, preset: str = "medium") -> dict:
    """The proven v2 export graph: a video-only concat of Project.video_segments()
    (already priority-resolved across tracks), plus every audio clip mixed separately
    and delayed into position, both pipelines sharing one frame-snapped clock."""
    duration = project.duration()
    if duration <= 0:
        raise ChangeError("nothing to export: the cut list is empty")
    if not project.output:
        raise ChangeError("nothing to export: no output size is set")
    width, height, fps = project.output["width"], project.output["height"], project.output["fps"]

    args = [FFMPEG, "-hide_banner", "-v", "error", "-y"]
    vgraph, n = [], 0
    for seg in project.video_segments():
        frames = _frame_snap(seg["end"], fps) - _frame_snap(seg["start"], fps)
        if frames <= 0:
            continue  # a degenerate sub-frame interval: merged away by dropping it
        d = frames / fps
        if seg["kind"] == "clip":
            path = resolve(project.source(seg["source"])["path"])
            args += ["-ss", f"{seg['in']:.3f}", "-t", f"{d:.3f}", "-i", path]
        else:
            args += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps:g}:d={d:.3f}"]
        vgraph.append(
            f"[{n}:v]setpts=PTS-STARTPTS,fps={fps:g},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{n}]"
        )
        n += 1
    vgraph.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]")

    audio_clips = [c for c in project.clips if project.track(c["track"])["kind"] == "audio"]
    agraph, labels = [], []
    for j, c in enumerate(audio_clips):
        idx = n + j
        path = resolve(project.source(c["source"])["path"])
        d = c["out"] - c["in"]
        args += ["-ss", f"{c['in']:.3f}", "-t", f"{d:.3f}", "-i", path]
        delay_ms = round(_frame_snap(c["start"], fps) / fps * 1000)
        agraph.append(
            f"[{idx}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"adelay={delay_ms}:all=1[a{j}]"
        )
        labels.append(f"[a{j}]")
    pad_idx = n + len(audio_clips)
    args += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration:.3f}"]
    labels.append(f"[{pad_idx}:a]")
    agraph.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize=0[amix]")
    agraph.append("[amix]alimiter=limit=0.95[a]")

    args += ["-filter_complex", ";".join(vgraph + agraph), "-map", "[v]", "-map", "[a]",
             "-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", preset, "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
    t0 = time.time()
    p = _run(args)
    seconds = time.time() - t0
    if p.returncode != 0:
        _fail(args, p)
    return {"path": out_path, "bytes": os.path.getsize(out_path),
            "seconds": round(seconds, 3), "duration": probe(out_path)["duration"]}


def frame(path: str, t: float, width: int = 640) -> bytes:
    """A single JPEG frame at source time t."""
    args = [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
            "-vf", f"scale={width}:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "3", "pipe:1"]
    p = _run(args)
    if p.returncode != 0 or not p.stdout:
        _fail(args, p)
    return p.stdout


def sheet(path: str, duration: float, cols: int = 6, rows: int = 5, width: int = 1920) -> tuple:
    """A contact sheet JPEG with timestamps burnt in, and the tile interval."""
    interval = duration / (cols * rows)
    stages = [
        f"fps={1 / interval:.6f}", f"scale={width // cols}:-2",
        "drawtext=text='%{pts\\:hms}':x=8:y=8:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4",
        f"tile={cols}x{rows}:padding=2:margin=2:color=black",
    ]
    args = [FFMPEG, "-v", "error", "-i", path, "-vf", ",".join(stages),
            "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "pipe:1"]
    p = _run(args)
    if p.returncode != 0 or not p.stdout:
        # a build without libfreetype: try again without drawtext; the arithmetic still holds
        stages.pop(2)
        args = [FFMPEG, "-v", "error", "-i", path, "-vf", ",".join(stages),
                "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "pipe:1"]
        p = _run(args)
        if p.returncode != 0 or not p.stdout:
            _fail(args, p)
    return p.stdout, interval, cols, rows


def scenes(path: str, threshold: float = 0.3) -> list:
    """Frames where the scene score exceeds threshold, as {"t", "score"}."""
    args = [FFMPEG, "-v", "info", "-i", path,
            "-vf", f"scale=320:-2,select='gt(scene,{threshold})',metadata=print", "-an", "-f", "null", "-"]
    p = _run(args)
    err = p.stderr.decode(errors="replace")
    if p.returncode != 0 and "pts_time" not in err:
        _fail(args, p)
    return [{"t": round(float(t), 3), "score": round(float(s), 3)}
            for t, s in re.findall(r"pts_time:\s*([0-9.]+)[^\n]*\n[^\n]*lavfi\.scene_score=([0-9.]+)", err)]


def silences(path: str, has_audio: bool, noise_db: float = -30, min_duration: float = 0.5,
             duration: Optional[float] = None) -> list:
    """Silent spans as {"start", "end"}. A source without audio returns an empty list without running ffmpeg."""
    if not has_audio:
        return []
    args = [FFMPEG, "-v", "info", "-i", path, "-vn",
            "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}", "-f", "null", "-"]
    p = _run(args)
    err = p.stderr.decode(errors="replace")
    if p.returncode != 0 and "silence_start" not in err:
        _fail(args, p)
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", err)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", err)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        if e is not None:
            out.append({"start": round(s, 3), "end": round(e, 3)})
    return out
