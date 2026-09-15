"""Synthetic clips for tests, from `-f lavfi` sources. Recipes proven in
`tools/ffmpeg_proofs.py`; nothing is checked in."""

import os
import subprocess

from studio import media


def make(folder: str):
    """`a.mp4`: 4 s, 1280x720 @ 30 fps, red then blue at 2.0 s, a tone with a
    silent second from 1.5 to 2.5. `b.mov`: 3 s, 640x360 @ 25 fps, silent."""
    a = os.path.join(folder, "a.mp4")
    b = os.path.join(folder, "b.mov")
    subprocess.run(
        [
            media.FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=1280x720:r=30:d=2",
            "-f", "lavfi", "-i", "color=c=blue:s=1280x720:r=30:d=2",
            "-f", "lavfi", "-i", "sine=f=440:r=44100:d=1.5",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=1",
            "-f", "lavfi", "-i", "sine=f=440:r=44100:d=1.5",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a][4:a]concat=n=3:v=0:a=1[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", a,
        ],
        check=True, capture_output=True,
    )
    subprocess.run(
        [
            media.FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=3",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", b,
        ],
        check=True, capture_output=True,
    )
    return a, b
