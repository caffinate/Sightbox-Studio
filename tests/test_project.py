"""The cut list and the write path, with ffprobe stubbed out."""

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

    def test_add_cut_defaults_to_the_whole_source_at_the_end(self):
        r = self.p.apply({"op": "add_cut", "source": "s1"})
        self.assertEqual(r, {"cut": "c1"})
        self.assertEqual(self.p.cuts, [{"id": "c1", "source": "s1", "in": 0.0, "out": 10.0}])
        self.p.apply({"op": "add_cut", "source": "s1", "in": 2, "out": 3, "at": 0})
        self.assertEqual([c["id"] for c in self.p.cuts], ["c2", "c1"])

    def test_add_cut_validation(self):
        bad = [
            {"op": "add_cut", "source": "nope"},
            {"op": "add_cut", "source": "s1", "in": -1},
            {"op": "add_cut", "source": "s1", "out": 10 + SLACK + 0.01},
            {"op": "add_cut", "source": "s1", "in": 5, "out": 5 + MIN_CUT / 2},
            {"op": "add_cut", "source": "s1", "at": 1},
            {"op": "add_cut", "source": "s1", "at": "0"},
            {"op": "add_cut", "source": "s1", "in": "1"},
        ]
        for change in bad:
            with self.assertRaises(ChangeError, msg=change):
                self.p.apply(change)
        self.assertEqual(self.p.cuts, [])
        self.p.apply({"op": "add_cut", "source": "s1", "out": 10 + SLACK})

    def test_times_round_to_milliseconds(self):
        self.p.apply({"op": "add_cut", "source": "s1", "in": 1.23456, "out": 4.56789})
        self.assertEqual((self.p.cuts[0]["in"], self.p.cuts[0]["out"]), (1.235, 4.568))

    def test_trim(self):
        self.p.apply({"op": "add_cut", "source": "s1"})
        self.assertEqual(self.p.apply({"op": "trim", "cut": "c1", "in": 1.5}), {"cut": "c1"})
        self.p.apply({"op": "trim", "cut": "c1", "out": 4.0})
        self.assertEqual((self.p.cuts[0]["in"], self.p.cuts[0]["out"]), (1.5, 4.0))
        for change in ({"op": "trim", "cut": "c1", "in": 4.0}, {"op": "trim", "cut": "c1", "out": 11.0},
                       {"op": "trim", "cut": "c9"}):
            with self.assertRaises(ChangeError):
                self.p.apply(change)
        self.assertEqual((self.p.cuts[0]["in"], self.p.cuts[0]["out"]), (1.5, 4.0))

    def test_split(self):
        self.p.apply({"op": "add_cut", "source": "s1"})
        self.p.apply({"op": "add_cut", "source": "s1", "in": 0, "out": 1})
        r = self.p.apply({"op": "split", "cut": "c1", "at": 4})
        self.assertEqual(r, {"cut": "c3"})
        self.assertEqual(self.p.cuts, [
            {"id": "c1", "source": "s1", "in": 0.0, "out": 4.0},
            {"id": "c3", "source": "s1", "in": 4.0, "out": 10.0},
            {"id": "c2", "source": "s1", "in": 0.0, "out": 1.0},
        ])
        for change in ({"op": "split", "cut": "c1"}, {"op": "split", "cut": "c1", "at": 0.0},
                       {"op": "split", "cut": "c1", "at": 4.0}, {"op": "split", "cut": "c1", "at": 4 - MIN_CUT / 2}):
            with self.assertRaises(ChangeError):
                self.p.apply(change)

    def test_move(self):
        for _ in range(3):
            self.p.apply({"op": "add_cut", "source": "s1"})
        self.assertEqual(self.p.apply({"op": "move", "cut": "c3", "to": 0}), {"cut": "c3", "to": 0})
        self.assertEqual([c["id"] for c in self.p.cuts], ["c3", "c1", "c2"])
        self.p.apply({"op": "move", "cut": "c3", "to": 2})
        self.assertEqual([c["id"] for c in self.p.cuts], ["c1", "c2", "c3"])
        for change in ({"op": "move", "cut": "c1", "to": 3}, {"op": "move", "cut": "c1"}, {"op": "move", "cut": "c1", "to": -1}):
            with self.assertRaises(ChangeError):
                self.p.apply(change)

    def test_remove_cut_and_remove_source(self):
        self.p.apply({"op": "add_cut", "source": "s1"})
        self.p.apply({"op": "add_cut", "source": "s1"})
        with self.assertRaises(ChangeError) as ctx:
            self.p.apply({"op": "remove_source", "source": "s1"})
        self.assertIn("c1, c2", str(ctx.exception))
        self.assertEqual(self.p.apply({"op": "remove_cut", "cut": "c1"}), {"cut": "c1"})
        self.p.apply({"op": "remove_cut", "cut": "c2"})
        self.assertEqual(self.p.apply({"op": "remove_source", "source": "s1"}), {"source": "s1"})
        self.assertEqual(self.p.sources, [])
        with self.assertRaises(ChangeError):
            self.p.apply({"op": "remove_cut", "cut": "c1"})

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

    def test_timeline_duration_and_locate(self):
        self.assertIsNone(self.p.locate(0))
        self.p.apply({"op": "add_cut", "source": "s1", "in": 1, "out": 4})
        self.p.apply({"op": "add_cut", "source": "s1", "in": 2, "out": 2.5})
        tl = self.p.timeline()
        self.assertEqual([(e["cut"], e["start"], e["end"], e["duration"]) for e in tl],
                         [("c1", 0.0, 3.0, 3.0), ("c2", 3.0, 3.5, 0.5)])
        self.assertEqual(self.p.duration(), 3.5)
        self.assertEqual(self.p.locate(0)["cut"], "c1")
        self.assertEqual(self.p.locate(2.999)["cut"], "c1")
        self.assertEqual(self.p.locate(3.0)["cut"], "c2")
        self.assertEqual(self.p.locate(3.5)["cut"], "c2")
        self.assertEqual(self.p.locate(99)["cut"], "c2")
        self.assertIsNone(self.p.locate(-1))

    def test_batch_applies_whole_or_not_at_all(self):
        with self.assertRaises(ChangeError) as ctx:
            apply_changes(self.p, [{"op": "add_cut", "source": "s1"}, {"op": "trim", "cut": "c1", "out": 99}])
        self.assertTrue(str(ctx.exception).startswith("change 1 (trim): out 99"))
        self.assertEqual(self.p.cuts, [])
        draft, results = apply_changes(self.p, [{"op": "add_cut", "source": "s1"}, {"op": "split", "cut": "c1", "at": 5}])
        self.assertEqual(results, [{"cut": "c1"}, {"cut": "c2"}])
        self.assertEqual(len(draft.cuts), 2)
        self.assertEqual(self.p.cuts, [])
        for bad in ([], "x", None):
            with self.assertRaises(ChangeError):
                apply_changes(self.p, bad)

    def test_dict_roundtrip_and_version_check(self):
        self.p.apply({"op": "add_cut", "source": "s1"})
        data = json.loads(json.dumps(self.p.to_dict()))
        again = Project.from_dict(data)
        self.assertEqual(again.to_dict(), self.p.to_dict())
        self.assertEqual(data["next"], {"source": 2, "cut": 2})
        with self.assertRaises(ValueError):
            Project.from_dict({"version": 2})


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
        # the same file, given relative to the project folder this time, is the same source
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
            self.store.apply([{"op": "rename", "name": "New"}, {"op": "trim", "cut": "c1"}], by="cli")
        with open(self.store.path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)
        self.assertFalse(os.path.exists(self.store.journal_path))
        self.assertEqual(self.store.load().revision, 1)

    def test_describe_carries_the_timeline(self):
        self.touch("a.mov")
        self.store.apply([{"op": "add_source", "path": "a.mov"}, {"op": "add_cut", "source": "s1", "in": 1, "out": 3}],
                         by="person", probe=fake_probe())
        d = self.store.describe()
        self.assertEqual((d["version"], d["file"], d["dir"]), ("2", self.store.path, self.tmp))
        self.assertEqual(d["duration"], 2.0)
        self.assertEqual(d["timeline"][0]["start"], 0.0)
        self.assertEqual(d["project"]["cuts"][0]["id"], "c1")

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


if __name__ == "__main__":
    unittest.main()
