"""ffprobe and ffmpeg on synthetic clips. Skipped whole when ffmpeg is absent."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import ffmpeg_proofs  # noqa: E402  (the recipe for synthetic clips, per BRIEF.md)

from studio import media
from studio.project import ChangeError, Project


@unittest.skipUnless(media.available(), "ffmpeg/ffprobe not on PATH")
class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="studio-media-test-")
        cls.a, cls.b, cls.c = ffmpeg_proofs.make_clips(cls.tmp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_probe_a(self):
        self.assertEqual(media.probe(self.a),
                          {"duration": 4.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True})

    def test_probe_b_has_no_audio(self):
        self.assertEqual(media.probe(self.b),
                          {"duration": 3.0, "width": 640, "height": 360, "fps": 25.0, "has_audio": False})

    def test_probe_missing_file(self):
        with self.assertRaises(ChangeError):
            media.probe(os.path.join(self.tmp, "nope.mp4"))

    def _three_cut_project(self):
        project = Project(name="Test", output={"width": 1280, "height": 720, "fps": 30.0})
        project.sources = [
            {"id": "s1", "path": self.a, "name": "a.mp4", "duration": 4.0,
             "width": 1280, "height": 720, "fps": 30.0, "has_audio": True},
            {"id": "s2", "path": self.b, "name": "b.mov", "duration": 3.0,
             "width": 640, "height": 360, "fps": 25.0, "has_audio": False},
        ]
        project.cuts = [
            {"id": "c1", "source": "s1", "in": 0.5, "out": 2.5},
            {"id": "c2", "source": "s2", "in": 0.0, "out": 1.5},
            {"id": "c3", "source": "s1", "in": 3.0, "out": 4.0},
        ]
        return project

    def test_export_duration_within_a_tenth_of_a_second(self):
        out = os.path.join(self.tmp, "out.mp4")
        result = media.export(self._three_cut_project(), lambda p: p, out)
        self.assertTrue(os.path.exists(out))
        self.assertAlmostEqual(result["duration"], 4.5, delta=0.1)
        info = media.probe(out)
        self.assertEqual((info["width"], info["height"], info["has_audio"]), (1280, 720, True))

    def test_export_needs_cuts(self):
        empty = Project(name="Empty", output={"width": 1280, "height": 720, "fps": 30.0})
        with self.assertRaises(ChangeError):
            media.export(empty, lambda p: p, os.path.join(self.tmp, "x.mp4"))

    def test_export_needs_output(self):
        no_output = self._three_cut_project()
        no_output.output = None
        with self.assertRaises(ChangeError):
            media.export(no_output, lambda p: p, os.path.join(self.tmp, "y.mp4"))


if __name__ == "__main__":
    unittest.main()
