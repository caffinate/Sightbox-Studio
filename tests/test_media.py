"""ffprobe and ffmpeg, on synthetic clips. Skipped without ffmpeg on PATH."""

import os
import shutil
import tempfile
import unittest

from studio import media
from studio.project import Store

import _clips


@unittest.skipUnless(media.available(), "ffmpeg/ffprobe not on PATH")
class Media(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="studio-media-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.a, self.b = _clips.make(self.dir)

    def test_probe_reads_video_and_audio_fields(self):
        info = media.probe(self.a)
        self.assertEqual(info, {"duration": 4.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True})

    def test_probe_reads_a_silent_clip(self):
        info = media.probe(self.b)
        self.assertEqual(info, {"duration": 3.0, "width": 640, "height": 360, "fps": 25.0, "has_audio": False})

    def test_probe_raises_on_a_file_with_no_video_stream(self):
        audio_only = os.path.join(self.dir, "audio.m4a")
        import subprocess
        subprocess.run(
            [media.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=1", audio_only],
            check=True, capture_output=True,
        )
        with self.assertRaises(media.MediaError):
            media.probe(audio_only)

    def test_export_duration_is_within_tolerance_of_the_cut_list(self):
        store = Store(os.path.join(self.dir, "project.json"))
        project = store.create("Export test")
        store.apply([{"op": "add_source", "path": self.a}], "cli", probe=media.probe)
        store.apply([{"op": "add_source", "path": self.b}], "cli", probe=media.probe)
        store.apply([
            {"op": "add_cut", "source": "s1", "in": 0.5, "out": 2.5},
            {"op": "add_cut", "source": "s2", "in": 0.0, "out": 1.5},
            {"op": "add_cut", "source": "s1", "in": 3.0, "out": 4.0},
        ], "cli", probe=media.probe)
        project = store.load()
        out = os.path.join(self.dir, "out.mp4")
        result = media.export(project, store.resolve, out, preset="ultrafast")
        self.assertTrue(os.path.isfile(out))
        self.assertGreater(result["bytes"], 0)
        self.assertAlmostEqual(result["duration"], 4.5, delta=0.1)
        probed = media.probe(out)
        self.assertEqual((probed["width"], probed["height"]), (1280, 720))
        self.assertTrue(probed["has_audio"])

    def test_export_refuses_an_empty_timeline(self):
        store = Store(os.path.join(self.dir, "empty.json"))
        project = store.create("Empty")
        with self.assertRaises(media.MediaError):
            media.export(project, store.resolve, os.path.join(self.dir, "empty.mp4"))


if __name__ == "__main__":
    unittest.main()
