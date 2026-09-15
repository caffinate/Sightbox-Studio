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
        cls.tone = ffmpeg_proofs.make_tone(cls.tmp, duration=4.0)

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

    def _three_clip_project(self):
        project = Project(name="Test", output={"width": 1280, "height": 720, "fps": 30.0})
        project.apply({"op": "add_source", "path": self.a}, probe=lambda p: {
            "duration": 4.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True})
        project.apply({"op": "add_source", "path": self.b}, probe=lambda p: {
            "duration": 3.0, "width": 640, "height": 360, "fps": 25.0, "has_audio": False})
        project.apply({"op": "add_clip", "source": "s1", "in": 0.5, "out": 2.5})
        project.apply({"op": "add_clip", "source": "s2", "in": 0.0, "out": 1.5})
        project.apply({"op": "add_clip", "source": "s1", "in": 3.0, "out": 4.0})
        return project

    def test_export_duration_within_a_tenth_of_a_second(self):
        out = os.path.join(self.tmp, "out.mp4")
        result = media.export(self._three_clip_project(), lambda p: p, out)
        self.assertTrue(os.path.exists(out))
        self.assertAlmostEqual(result["duration"], 4.5, delta=0.1)
        info = media.probe(out)
        self.assertEqual((info["width"], info["height"], info["has_audio"]), (1280, 720, True))

    def test_export_needs_clips(self):
        empty = Project(name="Empty", output={"width": 1280, "height": 720, "fps": 30.0})
        with self.assertRaises(ChangeError):
            media.export(empty, lambda p: p, os.path.join(self.tmp, "x.mp4"))

    def test_export_needs_output(self):
        no_output = self._three_clip_project()
        no_output.output = None
        with self.assertRaises(ChangeError):
            media.export(no_output, lambda p: p, os.path.join(self.tmp, "y.mp4"))

    def test_export_decouples_video_and_audio_across_a_track_gap(self):
        # video: a real clip [0,1) then a filler gap [1,2) (nothing on any video track);
        # audio: one continuous clip spanning the whole [0,2) -- built by adding it linked
        # to a throwaway video clip on a scratch track, then removing just the video half,
        # exactly how a real edit ends up with an audio-only clip (every source has video).
        project = Project(name="Gap", output={"width": 1280, "height": 720, "fps": 30.0})
        project.apply({"op": "add_source", "path": self.a}, probe=lambda p: {
            "duration": 4.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True})
        project.apply({"op": "add_source", "path": self.tone}, probe=lambda p: {
            "duration": 4.0, "width": 1280, "height": 720, "fps": 30.0, "has_audio": True})
        project.apply({"op": "add_clip", "source": "s1", "in": 0.0, "out": 1.0, "with_audio": False})
        project.apply({"op": "add_track", "kind": "video"})
        r = project.apply({"op": "add_clip", "source": "s2", "in": 0.0, "out": 2.0,
                           "video_track": "v2", "start": 0.0})
        project.apply({"op": "remove_clip", "clip": r["clip"]})

        out = os.path.join(self.tmp, "gap.mp4")
        result = media.export(project, lambda p: p, out)
        self.assertAlmostEqual(result["duration"], 2.0, delta=0.1)
        self.assertEqual(media.silences(out, has_audio=True), [])  # audio plays straight through the gap

    def test_frame_is_a_jpeg(self):
        data = media.frame(self.a, 2.5)
        self.assertEqual(data[:3], b"\xff\xd8\xff")

    def test_sheet_is_a_jpeg_with_the_tile_arithmetic(self):
        data, interval, cols, rows = media.sheet(self.a, 4.0, cols=4, rows=2)
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        self.assertEqual((cols, rows), (4, 2))
        self.assertAlmostEqual(interval, 0.5)

    def test_scenes_finds_the_cut_black_to_white(self):
        found = media.scenes(self.c, 0.3)
        self.assertEqual([f["t"] for f in found], [2.0])
        self.assertGreater(found[0]["score"], 0.8)

    def test_scenes_finds_nothing_on_a_still_clip(self):
        self.assertEqual(media.scenes(self.b, 0.3), [])

    def test_silences_finds_the_gap(self):
        found = media.silences(self.a, has_audio=True)
        self.assertEqual(len(found), 1)
        self.assertAlmostEqual(found[0]["start"], 1.5, delta=0.05)
        self.assertAlmostEqual(found[0]["end"], 2.5, delta=0.05)

    def test_silences_without_audio_skips_ffmpeg(self):
        self.assertEqual(media.silences(self.b, has_audio=False), [])


if __name__ == "__main__":
    unittest.main()
