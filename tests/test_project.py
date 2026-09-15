"""The v2 cut list and the write path, with ffprobe stubbed out."""

import json
import os
import shutil
import tempfile
import unittest

from studio.project import MIN_CUT, SLACK, ChangeError, Project, Store, apply_changes


def fake_probe(duration=10.0, width=1920, height=1080, fps=30.0, has_audio=True):
    def probe(path):
        return {"duration": duration, "width": width, "height": height, "fps": fps, "has_audio": has_audio}
    return probe


class ProjectOps(unittest.TestCase):
    def setUp(self):
        self.p = Project(name="Test")
        self.probe = fake_probe()
        self.p.apply({"op": "add_source", "path": "/clips/a.mov"}, self.probe)

    def test_add_source_records_the_probe_and_sets_output(self):
        s = self.p.sources[0]
        self.assertEqual(s["id"], "s1")
        self.assertEqual(s["name"], "a.mov")
        self.assertEqual(s["duration"], 10.0)
        self.assertTrue(s["has_audio"])
        self.assertEqual(self.p.output, {"width": 1920, "height": 1080, "fps": 30.0})

    def test_add_source_is_idempotent_per_file(self):
        r = self.p.apply({"op": "add_source", "path": "/clips/a.mov"}, self.probe)
        self.assertEqual(r, {"source": "s1", "existing": True})
        self.assertEqual(len(self.p.sources), 1)

    def test_second_source_does_not_change_output(self):
        r = self.p.apply({"op": "add_source", "path": "/clips/b.mp4"}, fake_probe(width=640, height=360, fps=25.0))
        self.assertEqual(r, {"source": "s2"})
        self.assertEqual(self.p.output["width"], 1920)

    def test_add_source_needs_a_path_and_a_probe(self):
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "add_source"}, self.probe)
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "add_source", "path": "  "}, self.probe)
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "add_source", "path": "/clips/c.mov"})

    def test_default_tracks_exist(self):
        self.assertEqual([(t["id"], t["kind"]) for t in self.p.tracks], [("v1", "video"), ("a1", "audio")])

    def test_add_track(self):
        r = self.p.apply({"op": "add_track", "kind": "video"})
        self.assertEqual(r, {"track": "v2"})
        self.assertEqual(self.p.tracks[-1], {"id": "v2", "kind": "video", "name": "V2"})
        r = self.p.apply({"op": "add_track", "kind": "audio", "name": "Voiceover"})
        self.assertEqual(r, {"track": "a2"})
        self.assertEqual(self.p.tracks[-1]["name"], "Voiceover")
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "add_track", "kind": "nope"})

    def test_remove_track_refused_while_used_or_last_of_kind(self):
        self.p.apply({"op": "add_clip", "source": "s1"})
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "remove_track", "track": "v1"})  # used
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "remove_track", "track": "a1"})  # last of its kind (even though used too)
        self.p.apply({"op": "add_track", "kind": "video"})
        self.p.apply({"op": "remove_track", "track": "v2"})  # empty, not the last video track -> fine
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "remove_track", "track": "v1"})  # still used and still the last video track

    def test_reorder_track_changes_video_priority(self):
        self.p.apply({"op": "add_track", "kind": "video"})  # v2
        self.p.apply({"op": "add_track", "kind": "video"})  # v3
        self.assertEqual([t["id"] for t in self.p.tracks if t["kind"] == "video"], ["v1", "v2", "v3"])
        r = self.p.apply({"op": "reorder_track", "track": "v3", "to": 0})
        self.assertEqual(r, {"track": "v3", "to": 0})
        self.assertEqual([t["id"] for t in self.p.tracks if t["kind"] == "video"], ["v3", "v1", "v2"])

    def test_add_clip_defaults_to_the_whole_source_linked_on_v1_a1(self):
        r = self.p.apply({"op": "add_clip", "source": "s1"})
        self.assertEqual(r["clip"], "c1")
        self.assertIsNotNone(r["sibling"])
        video = self.p.clip("c1")
        audio = self.p.clip(r["sibling"])
        self.assertEqual((video["track"], video["in"], video["out"], video["start"]), ("v1", 0.0, 10.0, 0.0))
        self.assertEqual((audio["track"], audio["in"], audio["out"], audio["start"]), ("a1", 0.0, 10.0, 0.0))
        self.assertEqual(video["link"], audio["link"])

    def test_add_clip_without_audio_source_makes_no_sibling(self):
        self.p.apply({"op": "add_source", "path": "/clips/silent.mov"}, fake_probe(has_audio=False))
        r = self.p.apply({"op": "add_clip", "source": "s2"})
        self.assertIsNone(r["sibling"])
        self.assertIsNone(self.p.clip(r["clip"])["link"])

    def test_add_clip_with_audio_false_makes_video_only(self):
        r = self.p.apply({"op": "add_clip", "source": "s1", "with_audio": False})
        self.assertIsNone(r["sibling"])
        self.assertEqual(len(self.p.clips), 1)

    def test_add_clip_shared_start_is_the_later_of_both_target_tracks(self):
        self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 2})  # v1/a1 end at 2.0
        self.p.apply({"op": "add_track", "kind": "video"})  # v2, empty
        r = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 1, "video_track": "v2"})
        video = self.p.clip(r["clip"])
        audio = self.p.clip(r["sibling"])
        # v2 is empty (end 0.0) but a1 already ends at 2.0 -- the pair must start together at 2.0
        self.assertEqual(video["start"], 2.0)
        self.assertEqual(audio["start"], 2.0)

    def test_add_clip_refuses_an_overlap(self):
        self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 2})
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 1, "start": 1.0})  # overlaps [0,2) on v1

    def test_add_clip_validation(self):
        bad = [
            {"op": "add_clip", "source": "nope"},
            {"op": "add_clip", "source": "s1", "in": -1},
            {"op": "add_clip", "source": "s1", "out": 10 + SLACK + 0.01},
            {"op": "add_clip", "source": "s1", "in": 5, "out": 5 + MIN_CUT / 2},
            {"op": "add_clip", "source": "s1", "video_track": "a1"},
            {"op": "add_clip", "source": "s1", "audio_track": "v1"},
            {"op": "add_clip", "source": "s1", "with_audio": "yes"},
        ]
        for change in bad:
            with self.assertRaises(ChangeError, msg=change):
                self.p.apply(change)
        self.assertEqual(self.p.clips, [])

    def test_times_round_to_milliseconds(self):
        self.p.apply({"op": "add_clip", "source": "s1", "in": 1.23456, "out": 4.56789})
        self.assertEqual((self.p.clips[0]["in"], self.p.clips[0]["out"]), (1.235, 4.568))

    def test_trim_cascades_to_a_linked_sibling(self):
        r = self.p.apply({"op": "add_clip", "source": "s1"})
        r2 = self.p.apply({"op": "trim", "clip": r["clip"], "in": 1.5, "out": 4.0})
        self.assertEqual(r2, {"clip": r["clip"], "sibling": r["sibling"]})
        video = self.p.clip(r["clip"])
        audio = self.p.clip(r["sibling"])
        self.assertEqual((video["in"], video["out"]), (1.5, 4.0))
        self.assertEqual((audio["in"], audio["out"]), (1.5, 4.0))
        for change in ({"op": "trim", "clip": r["clip"], "in": 4.0}, {"op": "trim", "clip": r["clip"], "out": 11.0},
                       {"op": "trim", "clip": "c9"}):
            with self.assertRaises(ChangeError):
                self.p.apply(change)
        self.assertEqual((video["in"], video["out"]), (1.5, 4.0))

    def test_move_without_track_cascades_sibling_by_the_same_delta(self):
        r = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 2})
        self.p.apply({"op": "move", "clip": r["clip"], "start": 5.0})
        video = self.p.clip(r["clip"])
        audio = self.p.clip(r["sibling"])
        self.assertEqual(video["start"], 5.0)
        self.assertEqual(audio["start"], 5.0)  # same track kind different track ids, same delta (0 -> 5)

    def test_move_with_track_unlinks(self):
        self.p.apply({"op": "add_track", "kind": "video"})  # v2
        r = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 2})
        result = self.p.apply({"op": "move", "clip": r["clip"], "start": 3.0, "track": "v2"})
        self.assertEqual(result, {"clip": r["clip"], "sibling": r["sibling"], "unlinked": True})
        video = self.p.clip(r["clip"])
        audio = self.p.clip(r["sibling"])
        self.assertEqual(video["track"], "v2")
        self.assertEqual(video["start"], 3.0)
        self.assertIsNone(video["link"])
        self.assertIsNone(audio["link"])
        self.assertEqual(audio["start"], 0.0)  # sibling did not move

    def test_move_refuses_cross_kind_track(self):
        r = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 2})
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "move", "clip": r["clip"], "start": 0.0, "track": "a1"})

    def test_move_refuses_an_overlap(self):
        self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 2})
        r2 = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 1, "video_track": "v1",
                            "with_audio": False, "start": 5.0})
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "move", "clip": r2["clip"], "start": 1.0})  # would overlap [0,2)

    def test_unlink(self):
        r = self.p.apply({"op": "add_clip", "source": "s1"})
        result = self.p.apply({"op": "unlink", "clip": r["clip"]})
        self.assertEqual(result, {"clip": r["clip"], "sibling": r["sibling"]})
        self.assertIsNone(self.p.clip(r["clip"])["link"])
        self.assertIsNone(self.p.clip(r["sibling"])["link"])
        # a second unlink is a no-op, not an error
        result = self.p.apply({"op": "unlink", "clip": r["clip"]})
        self.assertEqual(result, {"clip": r["clip"], "sibling": None})

    def test_split_linked_pair_produces_two_new_linked_pairs(self):
        r = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 10})
        original_link = self.p.clip(r["clip"])["link"]
        result = self.p.apply({"op": "split", "clip": r["clip"], "at": 4})
        second_video = self.p.clip(result["clip"])
        second_audio = self.p.clip(result["sibling"])
        first_video = self.p.clip(r["clip"])
        first_audio = self.p.clip(r["sibling"])
        # first halves keep the original link and start; second halves share a NEW link
        self.assertEqual(first_video["link"], original_link)
        self.assertEqual(first_audio["link"], original_link)
        self.assertEqual(second_video["link"], second_audio["link"])
        self.assertNotEqual(second_video["link"], original_link)
        # contiguous placement: second half starts right where the first half now ends
        self.assertEqual((first_video["in"], first_video["out"], first_video["start"]), (0.0, 4.0, 0.0))
        self.assertEqual((second_video["in"], second_video["out"], second_video["start"]), (4.0, 10.0, 4.0))
        self.assertEqual((first_audio["in"], first_audio["out"]), (0.0, 4.0))
        self.assertEqual((second_audio["in"], second_audio["out"]), (4.0, 10.0))

    def test_split_refuses_at_outside_the_clip(self):
        r = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 10})
        for change in ({"op": "split", "clip": r["clip"]}, {"op": "split", "clip": r["clip"], "at": 0.0},
                       {"op": "split", "clip": r["clip"], "at": 10.0}):
            with self.assertRaises(ChangeError):
                self.p.apply(change)

    def test_split_exactly_partitions_the_original_span(self):
        # a clip always exclusively owns its [start, end) span, so split can never collide
        # with anything else on its track -- the two halves must exactly cover what the
        # original clip covered, no gap, no overlap, no extension past either edge.
        r = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 10, "with_audio": False})
        result = self.p.apply({"op": "split", "clip": r["clip"], "at": 4})
        first, second = self.p.clip(r["clip"]), self.p.clip(result["clip"])
        self.assertEqual(first["start"], 0.0)
        self.assertEqual(first["start"] + (first["out"] - first["in"]), second["start"])
        self.assertEqual(second["start"] + (second["out"] - second["in"]), 10.0)

    def test_remove_clip_leaves_the_sibling_unlinked(self):
        r = self.p.apply({"op": "add_clip", "source": "s1"})
        result = self.p.apply({"op": "remove_clip", "clip": r["clip"]})
        self.assertEqual(result, {"clip": r["clip"]})
        with self.assertRaises(ChangeError):
            self.p.clip(r["clip"])
        self.assertIsNone(self.p.clip(r["sibling"])["link"])  # audio-only now

    def test_remove_source_refused_while_a_clip_uses_it(self):
        self.p.apply({"op": "add_clip", "source": "s1"})
        with self.assertRaises(ChangeError) as ctx:
            self.p.apply({"op": "remove_source", "source": "s1"})
        self.assertIn("clip(s)", str(ctx.exception))

    def test_set_output(self):
        r = self.p.apply({"op": "set_output", "width": 1080, "height": 1920})
        self.assertEqual(r, {"output": {"width": 1080, "height": 1920, "fps": 30.0}})
        self.p.apply({"op": "set_output", "fps": 29.97})
        self.assertEqual(self.p.output["fps"], 29.97)
        for change in ({"op": "set_output", "width": 1081}, {"op": "set_output", "height": 8},
                       {"op": "set_output", "fps": 0.5}, {"op": "set_output", "width": "1920"}):
            with self.assertRaises(ChangeError):
                self.p.apply(change)
        fresh = Project()
        self.assertEqual(fresh.apply({"op": "set_output", "fps": 24})["output"], {"width": 1920, "height": 1080, "fps": 24.0})

    def test_rename(self):
        self.assertEqual(self.p.apply({"op": "rename", "name": " Teaser "}), {"name": "Teaser"})
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "rename", "name": ""})

    def test_unknown_op_names_the_ops(self):
        with self.assertRaises(ChangeError) as ctx:
            self.p.apply({"op": "explode"})
        self.assertIn("add_source", str(ctx.exception))
        with self.assertRaises(ChangeError):
            self.p.apply("trim")

    def test_batch_applies_whole_or_not_at_all(self):
        with self.assertRaises(ChangeError) as ctx:
            apply_changes(self.p, [{"op": "add_clip", "source": "s1"}, {"op": "trim", "clip": "c1", "out": 99}])
        self.assertTrue(str(ctx.exception).startswith("change 1 (trim): out 99"))
        self.assertEqual(self.p.clips, [])
        draft, results = apply_changes(self.p, [{"op": "add_clip", "source": "s1"}, {"op": "split", "clip": "c1", "at": 5}])
        self.assertEqual(len(draft.clips), 4)  # linked pair + split linked pair = 4 rows
        self.assertEqual(self.p.clips, [])
        for bad in ([], "x", None):
            with self.assertRaises(ChangeError):
                apply_changes(self.p, bad)

    def test_dict_roundtrip_and_version_check(self):
        self.p.apply({"op": "add_clip", "source": "s1"})
        data = json.loads(json.dumps(self.p.to_dict()))
        again = Project.from_dict(data)
        self.assertEqual(again.to_dict(), self.p.to_dict())
        with self.assertRaises(ValueError):
            Project.from_dict({"version": 99})


class VideoSegmentsAndDuration(unittest.TestCase):
    def setUp(self):
        self.p = Project(name="Test")
        self.probe = fake_probe(duration=20.0)
        self.p.apply({"op": "add_source", "path": "/clips/a.mov"}, self.probe)

    def test_empty_project(self):
        self.assertEqual(self.p.duration(), 0.0)
        self.assertEqual(self.p.video_segments(), [])
        self.assertIsNone(self.p.locate(0))

    def test_single_track_matches_a_sequential_edit(self):
        self.p.apply({"op": "add_clip", "source": "s1", "in": 1, "out": 4})
        self.p.apply({"op": "add_clip", "source": "s1", "in": 2, "out": 2.5, "with_audio": False})
        segs = self.p.video_segments()
        self.assertEqual([(s["start"], s["end"], s["in"], s["out"]) for s in segs],
                         [(0.0, 3.0, 1.0, 4.0), (3.0, 3.5, 2.0, 2.5)])
        self.assertEqual(self.p.duration(), 3.5)

    def test_higher_priority_video_track_wins_where_it_overlaps(self):
        self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 5, "with_audio": False})  # v1, [0,5)
        self.p.apply({"op": "add_track", "kind": "video"})  # v2, higher priority
        self.p.apply({"op": "add_clip", "source": "s1", "in": 10, "out": 12, "video_track": "v2",
                       "with_audio": False, "start": 2.0})  # v2, [2,4), covers the middle of v1's clip
        segs = self.p.video_segments()
        self.assertEqual([(s["kind"], s["start"], s["end"]) for s in segs],
                         [("clip", 0.0, 2.0), ("clip", 2.0, 4.0), ("clip", 4.0, 5.0)])
        # the middle segment's source timing comes from the v2 (winning) clip, not v1
        self.assertEqual((segs[1]["in"], segs[1]["out"]), (10.0, 12.0))
        self.assertEqual((segs[0]["in"], segs[0]["out"]), (0.0, 2.0))
        # v1's own clip keeps its own internal clock running underneath the overlay --
        # when v2's clip ends, v1 resumes at ITS OWN mapping for timeline 4 (source time
        # 4, since v1.in=0/v1.start=0), not from "where v1 left off" before being covered
        self.assertEqual((segs[2]["in"], segs[2]["out"]), (4.0, 5.0))

    def test_video_segments_carry_the_winning_clip_id(self):
        r1 = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 5, "with_audio": False})
        self.p.apply({"op": "add_track", "kind": "video"})
        r2 = self.p.apply({"op": "add_clip", "source": "s1", "in": 10, "out": 12, "video_track": "v2",
                            "with_audio": False, "start": 2.0})  # fully inside r1's [0,5) span
        segs = self.p.video_segments()
        self.assertEqual([s["clip"] for s in segs], [r1["clip"], r2["clip"], r1["clip"]])

    def test_uncovered_interval_is_filler(self):
        self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 1, "with_audio": False})
        self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 1, "with_audio": False, "start": 3.0})
        segs = self.p.video_segments()
        self.assertEqual([(s["kind"], s["start"], s["end"]) for s in segs],
                         [("clip", 0.0, 1.0), ("filler", 1.0, 3.0), ("clip", 3.0, 4.0)])

    def test_duration_extends_past_the_last_video_clip_when_audio_runs_longer(self):
        self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 1, "with_audio": False})  # v1: [0,1)
        self.p.apply({"op": "add_track", "kind": "video"})  # v2, a scratch track for a throwaway video half
        r = self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 5, "video_track": "v2", "start": 0.0})
        # every source has video, so an "audio-only" clip only exists by removing its video sibling --
        # the audio half (on a1, [0,5)) survives, unlinked, running well past v1's [0,1) clip
        self.p.apply({"op": "remove_clip", "clip": r["clip"]})
        self.assertEqual(self.p.duration(), 5.0)
        segs = self.p.video_segments()
        self.assertEqual(segs[-1]["end"], 5.0)
        self.assertEqual(segs[-1]["kind"], "filler")  # nothing on any video track covers [1, 5)

    def test_locate(self):
        self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 2, "with_audio": False})
        self.p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 1, "with_audio": False, "start": 3.0})
        self.assertEqual(self.p.locate(0)["start"], 0.0)
        self.assertEqual(self.p.locate(1.999)["kind"], "clip")
        self.assertEqual(self.p.locate(2.5)["kind"], "filler")
        self.assertEqual(self.p.locate(99)["end"], 4.0)
        self.assertIsNone(self.p.locate(-1))


class MigrationTests(unittest.TestCase):
    def test_v1_project_migrates_purely_and_deterministically(self):
        v1 = {
            "version": 1, "revision": 5, "name": "Old",
            "output": {"width": 1280, "height": 720, "fps": 30.0},
            "sources": [
                {"id": "s1", "path": "a.mov", "name": "a.mov", "duration": 10.0,
                 "width": 1280, "height": 720, "fps": 30.0, "has_audio": True},
                {"id": "s2", "path": "b.mov", "name": "b.mov", "duration": 5.0,
                 "width": 640, "height": 360, "fps": 25.0, "has_audio": False},
            ],
            "cuts": [
                {"id": "c1", "source": "s1", "in": 1.0, "out": 3.0},
                {"id": "c2", "source": "s2", "in": 0.0, "out": 2.0},
            ],
            "next": {"source": 3, "cut": 3},
        }
        p1 = Project.from_dict(v1)
        p2 = Project.from_dict(v1)  # a second, independent load of the same raw dict
        self.assertEqual(p1.to_dict(), p2.to_dict())  # pure: identical ids both times

        self.assertEqual([(t["id"], t["kind"]) for t in p1.tracks], [("v1", "video"), ("a1", "audio")])
        self.assertEqual(len(p1.clips), 3)  # c1 has audio (2 rows), c2 has none (1 row)
        video1, audio1 = p1.clip("c1"), p1.clip("c2")
        self.assertEqual((video1["track"], video1["start"], video1["in"], video1["out"]), ("v1", 0.0, 1.0, 3.0))
        self.assertEqual((audio1["track"], audio1["start"]), ("a1", 0.0))
        self.assertEqual(video1["link"], audio1["link"])
        video2 = p1.clip("c3")
        self.assertEqual((video2["track"], video2["start"], video2["in"], video2["out"]), ("v1", 2.0, 0.0, 2.0))
        self.assertIsNone(video2["link"])  # s2 has no audio -- no sibling, no link

        # next_ids leave room for fresh clip/link ids that can't collide with migrated ones
        self.assertEqual(p1.next_ids["clip"], 4)
        self.assertEqual(p1.next_ids["link"], 3)
        new_clip = p1.next_clip_id()
        self.assertNotIn(new_clip, ("c1", "c2", "c3"))

    def test_migrated_project_can_be_edited_with_v2_ops(self):
        v1 = {
            "version": 1, "name": "Old", "output": {"width": 1280, "height": 720, "fps": 30.0},
            "sources": [{"id": "s1", "path": "a.mov", "name": "a.mov", "duration": 10.0,
                        "width": 1280, "height": 720, "fps": 30.0, "has_audio": True}],
            "cuts": [{"id": "c1", "source": "s1", "in": 0.0, "out": 5.0}],
            "next": {"source": 2, "cut": 2},
        }
        p = Project.from_dict(v1)
        r = p.apply({"op": "add_clip", "source": "s1", "in": 0, "out": 1, "with_audio": False, "start": 5.0})
        self.assertNotEqual(r["clip"], "c1")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="studio-test-")
        self.store = Store(os.path.join(self.tmp, "project.json"))
        self.store.create("Test")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, name):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(b"\x00" * 16)
        return path

    def test_create_load_and_revision(self):
        self.assertTrue(self.store.exists())
        p = self.store.load()
        self.assertEqual((p.name, p.revision), ("Test", 1))
        self.assertEqual([(t["id"], t["kind"]) for t in p.tracks], [("v1", "video"), ("a1", "audio")])
        with self.assertRaises(FileExistsError):
            self.store.create("Again")
        self.assertEqual(self.store.describe()["version"], "1")

    def test_apply_saves_journals_and_relativises(self):
        clip = self.touch("a.mov")
        project, results = self.store.apply([{"op": "add_source", "path": clip}], by="cli", probe=fake_probe())
        self.assertEqual(results, [{"source": "s1"}])
        self.assertEqual(project.sources[0]["path"], "a.mov")
        self.assertEqual(project.revision, 2)
        self.assertEqual(self.store.load().sources[0]["path"], "a.mov")
        self.assertEqual(self.store.resolve("a.mov"), clip)
        with open(self.store.journal_path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["by"], "cli")
        self.assertEqual(lines[0]["changes"], [{"op": "add_source", "path": "a.mov"}])
        self.assertEqual(lines[0]["results"], results)
        self.assertIn("at", lines[0])
        _, results = self.store.apply([{"op": "add_source", "path": "a.mov"}], by="cli", probe=fake_probe())
        self.assertEqual(results, [{"source": "s1", "existing": True}])

    def test_a_path_outside_the_folder_stays_absolute(self):
        outside = os.path.join(os.path.dirname(self.tmp), "elsewhere.mp4")
        project, _ = self.store.apply([{"op": "add_source", "path": outside}], by="cli", probe=fake_probe())
        self.assertEqual(project.sources[0]["path"], outside)
        self.assertEqual(self.store.relativise(outside), outside)

    def test_a_failed_batch_changes_nothing(self):
        with open(self.store.path, encoding="utf-8") as fh:
            before = fh.read()
        with self.assertRaises(ChangeError):
            self.store.apply([{"op": "rename", "name": "New"}, {"op": "trim", "clip": "c1"}], by="cli")
        with open(self.store.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)
        self.assertFalse(os.path.exists(self.store.journal_path))
        self.assertEqual(self.store.load().revision, 1)

    def test_describe_carries_the_timeline_and_video_segments(self):
        self.touch("a.mov")
        self.store.apply([{"op": "add_source", "path": "a.mov"},
                          {"op": "add_clip", "source": "s1", "in": 1, "out": 3, "with_audio": False}],
                         by="person", probe=fake_probe())
        d = self.store.describe()
        self.assertEqual((d["version"], d["file"], d["dir"]), ("2", self.store.path, self.tmp))
        self.assertEqual(d["duration"], 2.0)
        self.assertEqual(d["timeline"][0]["start"], 0.0)
        self.assertEqual(d["video_segments"][0]["start"], 0.0)
        self.assertEqual(d["project"]["clips"][0]["id"], "c1")

    def test_media_files(self):
        self.touch("a.mov")
        self.touch("B.MP4")
        self.touch("notes.txt")
        self.touch(".hidden.mov")
        os.mkdir(os.path.join(self.tmp, "folder.mp4"))
        self.store.apply([{"op": "add_source", "path": "a.mov"}], by="cli", probe=fake_probe())
        listing = self.store.media_files()
        self.assertEqual(listing["dir"], self.tmp)
        self.assertEqual([(f["name"], f["source"], f["bytes"]) for f in listing["files"]],
                         [("a.mov", "s1", 16), ("B.MP4", None, 16)])
        self.assertEqual(self.store.media_files(os.path.join(self.tmp, "missing"))["files"], [])

    def test_v1_project_file_migrates_on_load_with_a_journal_marker(self):
        v1_path = os.path.join(self.tmp, "old.json")
        with open(v1_path, "w", encoding="utf-8") as fh:
            json.dump({
                "version": 1, "revision": 3, "name": "Old",
                "output": {"width": 1280, "height": 720, "fps": 30.0},
                "sources": [{"id": "s1", "path": "a.mov", "name": "a.mov", "duration": 10.0,
                            "width": 1280, "height": 720, "fps": 30.0, "has_audio": True}],
                "cuts": [{"id": "c1", "source": "s1", "in": 0.0, "out": 5.0}],
                "next": {"source": 2, "cut": 2},
            }, fh)
        store = Store(v1_path)
        project = store.load()
        self.assertEqual(project.to_dict()["version"], 2)  # migrated in memory
        with open(v1_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], 1)  # not written back until the next save

        store.apply([{"op": "rename", "name": "New"}], by="cli")
        with open(v1_path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"], 2)  # now saved as v2
        with open(store.journal_path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh]
        self.assertEqual(lines[0]["op"], "_migrate_v1_to_v2")
        self.assertEqual(lines[1]["by"], "cli")

        # loading it again (now genuinely v2 on disk) does not re-journal a migration
        store.apply([{"op": "rename", "name": "Newer"}], by="cli")
        with open(store.journal_path, encoding="utf-8") as fh:
            lines = [json.loads(line) for line in fh]
        self.assertEqual(len(lines), 3)
        self.assertNotIn("op", lines[2])


if __name__ == "__main__":
    unittest.main()
