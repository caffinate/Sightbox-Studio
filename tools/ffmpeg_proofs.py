"""The proofs behind BRIEF.md and MULTITRACK-BRIEF.md, on synthetic clips. Run it before
touching studio/media.py.

    python3 tools/ffmpeg_proofs.py [--keep DIR]

Makes clips from lavfi sources, then runs every ffmpeg command each brief carries: probe,
the v1 export graph, a single frame, a contact sheet with burnt-in timestamps, scene
changes with scores, silences, and (MULTITRACK-BRIEF.md) the v2 multi-track export
mechanics -- frame-quantized video/audio sync, amix without normalize's per-clip-count
attenuation, and audio continuing under a black video-track gap. Prints what each
returned against what the brief expects and exits non-zero on the first failure. Needs
ffmpeg and ffprobe on PATH.

The clip recipes are also what tests/test_media.py should use; nothing is checked in.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

FFMPEG = os.environ.get("STUDIO_FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("STUDIO_FFPROBE", "ffprobe")


def run(args):
    t0 = time.time()
    p = subprocess.run(args, capture_output=True)
    if p.returncode != 0:
        sys.stderr.write("FAILED: " + " ".join(args) + "\n" + p.stderr.decode(errors="replace")[-2000:] + "\n")
        sys.exit(1)
    return p, time.time() - t0


def make_clips(folder):
    """a.mp4: red then blue at 2.0 s, tone with silence 1.5-2.5 s. b.mov: testsrc2, silent. c.mp4: black then white at 2.0 s."""
    a, b, c = (os.path.join(folder, n) for n in ("a.mp4", "b.mov", "c.mp4"))
    run([FFMPEG, "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=red:s=1280x720:r=30:d=2",
         "-f", "lavfi", "-i", "color=c=blue:s=1280x720:r=30:d=2",
         "-f", "lavfi", "-i", "sine=f=440:r=44100:d=1.5",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1",
         "-f", "lavfi", "-i", "sine=f=440:r=44100:d=1.5",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a][4:a]concat=n=3:v=0:a=1[a]",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", a])
    run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", b])
    run([FFMPEG, "-v", "error", "-y",
         "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30:d=2",
         "-f", "lavfi", "-i", "color=c=white:s=1280x720:r=30:d=2",
         "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]", "-map", "[v]",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", c])
    return a, b, c


def probe(path):
    p, _ = run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    data = json.loads(p.stdout)
    video = next(s for s in data["streams"]
                 if s["codec_type"] == "video" and not int(s.get("disposition", {}).get("attached_pic", 0)))
    num, den = video.get("avg_frame_rate", "0/1").split("/")
    fps = float(num) / float(den) if float(den) else 0.0
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"])
    if not rotation and "rotate" in (video.get("tags") or {}):
        rotation = int(video["tags"]["rotate"])
    w, h = int(video["width"]), int(video["height"])
    if rotation % 180:
        w, h = h, w
    return {"duration": round(float(data["format"]["duration"]), 3), "width": w, "height": h,
            "fps": round(fps, 3), "has_audio": any(s["codec_type"] == "audio" for s in data["streams"])}


def export_args(cuts, out_path, width, height, fps, preset="veryfast"):
    """cuts: (path, in, out, has_audio). The proven graph, verbatim from the brief."""
    args = [FFMPEG, "-hide_banner", "-v", "error", "-y"]
    graph = []
    for i, (path, cin, cout, has_audio) in enumerate(cuts):
        d = cout - cin
        args += ["-ss", f"{cin:.3f}", "-t", f"{d:.3f}", "-i", path]
        graph.append(f"[{i}:v]setpts=PTS-STARTPTS,fps={fps:g},scale={width}:{height}:force_original_aspect_ratio=decrease:"
                     f"force_divisible_by=2,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]")
        if has_audio:
            graph.append(f"[{i}:a]asetpts=PTS-STARTPTS,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a{i}]")
        else:
            graph.append(f"anullsrc=r=48000:cl=stereo:d={d:.3f}[a{i}]")
    n = len(cuts)
    graph.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")
    args += ["-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", preset, "-crf", "18", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out_path]
    return args


def frame(path, t, width=480):
    p, dt = run([FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                 "-vf", f"scale={width}:-2", "-f", "image2", "-c:v", "mjpeg", "-q:v", "3", "pipe:1"])
    return p.stdout, dt


def sheet(path, duration, cols=4, rows=2, width=1280):
    interval = duration / (cols * rows)
    stages = [f"fps={1 / interval:.6f}", f"scale={width // cols}:-2",
              "drawtext=text='%{pts\\:hms}':x=8:y=8:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4",
              f"tile={cols}x{rows}:padding=2:margin=2:color=black"]
    p, dt = run([FFMPEG, "-v", "error", "-i", path, "-vf", ",".join(stages), "-frames:v", "1",
                 "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "pipe:1"])
    return p.stdout, interval, dt


def scenes(path, threshold=0.3):
    p, dt = run([FFMPEG, "-v", "info", "-i", path, "-vf", f"scale=320:-2,select='gt(scene,{threshold})',metadata=print",
                 "-an", "-f", "null", "-"])
    err = p.stderr.decode(errors="replace")
    found = [{"t": round(float(t), 3), "score": round(float(s), 3)}
             for t, s in re.findall(r"pts_time:\s*([0-9.]+)[^\n]*\n[^\n]*lavfi\.scene_score=([0-9.]+)", err)]
    return found, dt


def silences(path, noise_db=-30, min_duration=0.5):
    p, dt = run([FFMPEG, "-v", "info", "-i", path, "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
                 "-f", "null", "-"])
    err = p.stderr.decode(errors="replace")
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", err)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", err)]
    return [{"start": round(s, 3), "end": round(e, 3)} for s, e in zip(starts, ends)], dt


def make_tone(folder, name="tone.mp4", freq=440, duration=2.0):
    """A steady sine tone, audio only -- for the v2 audio-mix proofs."""
    path = os.path.join(folder, name)
    run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f={freq}:r=44100:d={duration}",
         "-c:a", "aac", path])
    return path


def frame_snap(t, fps):
    """The output-frame grid both v2 pipelines share: round to the nearest output frame."""
    return round(t * fps)


def snapped_seconds(t, fps):
    return frame_snap(t, fps) / fps


def v2_export_args(video_segments, audio_clips, out_path, width, height, fps, duration, preset="veryfast"):
    """The v2 graph: a video-only concat of frame-snapped segments (real clips and black
    filler through the same chain), plus audio genuinely mixed (adelay off the SAME
    frame-snapped grid, amix without normalize's divide-by-input-count, a limiter, and
    the whole output pinned to `duration` with -t) so neither stream can silently run
    long or short of the other.

    video_segments: (kind, path_or_None, in, out, timeline_start, timeline_end); kind is
    "clip" or "filler". audio_clips: (path, in, out, start), each already the real file.
    """
    args = [FFMPEG, "-hide_banner", "-v", "error", "-y"]
    vgraph, n = [], 0
    for kind, path, cin, cout, ts, te in video_segments:
        sf, ef = frame_snap(ts, fps), frame_snap(te, fps)
        frames = ef - sf
        if frames <= 0:
            continue  # a degenerate sub-frame interval: dropped, per the design
        d = frames / fps
        if kind == "clip":
            args += ["-ss", f"{cin:.3f}", "-t", f"{d:.3f}", "-i", path]
        else:
            args += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps:g}:d={d:.3f}"]
        vgraph.append(
            f"[{n}:v]setpts=PTS-STARTPTS,fps={fps:g},"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{n}]"
        )
        n += 1
    vgraph.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]")

    agraph, labels = [], []
    for j, (path, ain, aout, astart) in enumerate(audio_clips):
        idx = n + j
        d = aout - ain
        args += ["-ss", f"{ain:.3f}", "-t", f"{d:.3f}", "-i", path]
        delay_ms = round(snapped_seconds(astart, fps) * 1000)
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
    return args


def audio_mix_args(clips, duration, out_path, normalize=0):
    """Just the audio side, output to a WAV for easy volume measurement. clips: (path, in, out, start)."""
    args = [FFMPEG, "-v", "error", "-y"]
    agraph, labels = [], []
    for i, (path, cin, cout, start) in enumerate(clips):
        d = cout - cin
        args += ["-ss", f"{cin:.3f}", "-t", f"{d:.3f}", "-i", path]
        agraph.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
            f"adelay={round(start * 1000)}:all=1[a{i}]"
        )
        labels.append(f"[a{i}]")
    pad_idx = len(clips)
    args += ["-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration:.3f}"]
    labels.append(f"[{pad_idx}:a]")
    agraph.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:normalize={normalize}[a]")
    args += ["-filter_complex", ";".join(agraph), "-map", "[a]", "-t", f"{duration:.3f}", out_path]
    return args


def mean_volume(path):
    p, _ = run([FFMPEG, "-v", "info", "-i", path, "-af", "volumedetect", "-f", "null", "-"])
    err = p.stderr.decode(errors="replace")
    m = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", err)
    return float(m.group(1)) if m else None


def luma_at(path, t):
    """Average luma of the frame at source time t, via the same metadata=print technique scenes() uses."""
    p, _ = run([FFMPEG, "-v", "info", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1",
                "-vf", "signalstats,metadata=print", "-f", "null", "-"])
    err = p.stderr.decode(errors="replace")
    m = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", err)
    return float(m.group(1)) if m else None


def check(label, ok, detail):
    print(f"{'ok  ' if ok else 'FAIL'} {label}: {detail}")
    if not ok:
        sys.exit(1)


def main():
    keep = None
    if "--keep" in sys.argv:
        keep = sys.argv[sys.argv.index("--keep") + 1]
        os.makedirs(keep, exist_ok=True)
    folder = keep or tempfile.mkdtemp(prefix="studio-proofs-")
    try:
        if not (shutil.which(FFMPEG) and shutil.which(FFPROBE)):
            sys.exit("ffmpeg and ffprobe are needed on PATH (brew install ffmpeg; apt-get install -y ffmpeg)")
        print("clips in", folder)
        a, b, c = make_clips(folder)

        pa, pb = probe(a), probe(b)
        check("probe a", pa == {"duration": 4.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True}, pa)
        check("probe b", pb == {"duration": 3.0, "width": 640, "height": 360, "fps": 25.0, "has_audio": False}, pb)

        out = os.path.join(folder, "out.mp4")
        cuts = [(a, 0.5, 2.5, True), (b, 0.0, 1.5, False), (a, 3.0, 4.0, True)]
        _, dt = run(export_args(cuts, out, 1280, 720, 30))
        po = probe(out)
        check("export", abs(po["duration"] - 4.5) <= 0.1 and po["width"] == 1280 and po["has_audio"],
              f"{po} in {dt:.1f}s, expected 4.5 s within 0.1")

        jpeg, dt = frame(a, 2.5)
        check("frame", jpeg[:3] == b"\xff\xd8\xff", f"{len(jpeg)} bytes in {dt:.2f}s")
        with open(os.path.join(folder, "frame.jpg"), "wb") as fh:
            fh.write(jpeg)

        jpeg, interval, dt = sheet(a, pa["duration"])
        check("sheet", jpeg[:3] == b"\xff\xd8\xff", f"{len(jpeg)} bytes, 4x2 tiles every {interval:.3f}s in {dt:.2f}s")
        with open(os.path.join(folder, "sheet.jpg"), "wb") as fh:
            fh.write(jpeg)

        found, dt = scenes(c, 0.3)
        check("scenes c (black to white)", [s["t"] for s in found] == [2.0] and found[0]["score"] > 0.8, f"{found} in {dt:.2f}s")
        found, dt = scenes(a, 0.3)
        check("scenes a (red to blue, luma only)", [s["t"] for s in found] == [2.0] and 0.3 < found[0]["score"] < 0.5, f"{found} in {dt:.2f}s")
        found, dt = scenes(b, 0.3)
        check("scenes b (no cut)", found == [], f"{found} in {dt:.2f}s")

        found, dt = silences(a)
        check("silences a", len(found) == 1 and abs(found[0]["start"] - 1.5) < 0.05 and abs(found[0]["end"] - 2.5) < 0.05,
              f"{found} in {dt:.2f}s")

        # --- MULTITRACK-BRIEF.md: the v2 multi-track export mechanics ---

        tone = make_tone(folder)

        # A: amix normalize=0 keeps a sequential (non-overlapping) edit at full volume;
        # normalize=1 (the default) would attenuate it by ~20*log10(1/3) =~ -9.5 dB for
        # nothing acoustic -- three clips that never overlap, just placed back to back.
        baseline_out = os.path.join(folder, "baseline.wav")
        run(audio_mix_args([(tone, 0.0, 1.0, 0.0)], 1.0, baseline_out, normalize=0))
        baseline_db = mean_volume(baseline_out)

        seq_norm0_out = os.path.join(folder, "seq_norm0.wav")
        seq_clips = [(tone, 0.0, 1.0, 0.0), (tone, 0.0, 1.0, 1.0), (tone, 0.0, 1.0, 2.0)]
        run(audio_mix_args(seq_clips, 3.0, seq_norm0_out, normalize=0))
        seq_norm0_db = mean_volume(seq_norm0_out)

        seq_norm1_out = os.path.join(folder, "seq_norm1.wav")
        run(audio_mix_args(seq_clips, 3.0, seq_norm1_out, normalize=1))
        seq_norm1_db = mean_volume(seq_norm1_out)

        check("amix normalize=0 avoids the sequential-edit attenuation bug",
              abs(seq_norm0_db - baseline_db) < 1.0 and (baseline_db - seq_norm1_db) > 6.0,
              f"baseline {baseline_db:.1f} dB, normalize=0 {seq_norm0_db:.1f} dB (should match baseline), "
              f"normalize=1 {seq_norm1_db:.1f} dB (should be markedly quieter for no acoustic reason)")

        # B: video and audio, snapped to the same frame grid, land the cut at the same
        # instant. c.mp4 is black 0-2s, white 2-4s (from make_clips). Cut the video at a
        # deliberately non-frame-aligned raw time (0.5173s at 30fps = 15.519 frames) and
        # confirm the actual (snapped) video cut and the actual (snapped) audio cut agree,
        # not just "close by luck".
        raw_cut = 0.5173
        fps = 30
        snapped_cut = snapped_seconds(raw_cut, fps)  # 16 frames / 30 = 0.53333...
        video_segments = [
            ("clip", c, 0.0, raw_cut, 0.0, raw_cut),
            ("clip", c, 2.0, 2.0 + (1.0 - raw_cut), raw_cut, 1.0),
        ]
        audio_clips = [(tone, 0.0, snapped_cut, 0.0)]  # sound stops exactly at the snapped cut
        sync_out = os.path.join(folder, "sync.mp4")
        _, dt = run(v2_export_args(video_segments, audio_clips, sync_out, 1280, 720, fps, 1.0))
        video_cut, _ = scenes(sync_out, 0.3)
        audio_cut, _ = silences(sync_out, min_duration=0.1)
        video_t = video_cut[0]["t"] if video_cut else None
        audio_t = audio_cut[0]["start"] if audio_cut else None
        check("v2 export: video and audio cuts land on the same frame-snapped instant",
              video_t is not None and audio_t is not None
              and abs(video_t - snapped_cut) <= 1 / fps and abs(audio_t - snapped_cut) <= 1 / fps
              and abs(video_t - audio_t) <= 1 / fps,
              f"raw cut {raw_cut}s -> snapped {snapped_cut:.4f}s; video cut detected at {video_t}, "
              f"audio cut detected at {audio_t} (1 frame = {1/fps:.4f}s) in {dt:.1f}s")

        # C: a video-track gap (no clip covers it, so it's black filler) with an audio
        # clip still playing underneath -- proves picture and sound are decoupled on
        # purpose, not by accident. c.mp4's black portion [0,1) is a real segment; [1,2)
        # is filler (no source at all); the tone runs the whole 2s underneath.
        gap_segments = [
            ("clip", c, 0.0, 1.0, 0.0, 1.0),
            ("filler", None, None, None, 1.0, 2.0),
        ]
        gap_audio = [(tone, 0.0, 2.0, 0.0)]
        gap_out = os.path.join(folder, "gap.mp4")
        run(v2_export_args(gap_segments, gap_audio, gap_out, 1280, 720, fps, 2.0))
        gap_silences, _ = silences(gap_out, min_duration=0.1)
        luma = luma_at(gap_out, 1.5)
        check("v2 export: audio keeps playing under a black video-track gap",
              gap_silences == [] and luma is not None and luma < 30.0,
              f"silences over the gap: {gap_silences} (should be none); "
              f"luma at t=1.5 (inside the filler): {luma} (should be near-black, <30 -- "
              f"H.264 compression noise keeps true black off 0)")

        print("ALL PROOFS PASSED")
    finally:
        if not keep:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
