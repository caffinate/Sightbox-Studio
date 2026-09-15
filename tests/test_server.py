"""The server on an ephemeral port: describe, a batch, a failing batch, Range."""

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

from studio import media
from studio.project import Store
from studio.server import make_server

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import ffmpeg_proofs  # noqa: E402


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="studio-server-test-")
        self.project_path = os.path.join(self.tmp, "project.json")
        store = Store(self.project_path)
        store.create("Test")

        with open(os.path.join(self.tmp, "a.bin"), "wb") as fh:
            fh.write(bytes(range(256)) * 4)  # 1024 bytes, no ffmpeg needed for a Range test

        project = store.load()
        project.sources.append({"id": "s1", "path": "a.bin", "name": "a.bin", "duration": 1.0,
                                 "width": 10, "height": 10, "fps": 30.0, "has_audio": False})
        project.output = {"width": 10, "height": 10, "fps": 30.0}
        store.save(project)

        self.httpd = make_server(self.project_path, port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp, resp.read()
        finally:
            conn.close()

    def test_describe(self):
        resp, data = self.request("GET", "/api/project")
        self.assertEqual(resp.status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["project"]["name"], "Test")
        self.assertEqual(payload["project"]["sources"][0]["id"], "s1")

    def test_a_batch_and_a_failing_batch(self):
        body = json.dumps({"by": "agent", "changes": [{"op": "add_clip", "source": "s1", "with_audio": False}]})
        resp, data = self.request("POST", "/api/changes", body=body)
        self.assertEqual(resp.status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["results"], [{"clip": "c1", "sibling": None}])
        self.assertEqual(payload["timeline"][0]["clip"], "c1")
        self.assertEqual(payload["video_segments"][0]["source"], "s1")

        with open(self.project_path, encoding="utf-8") as fh:
            before = fh.read()
        resp, data = self.request("POST", "/api/changes", body=json.dumps({"changes": [{"op": "trim", "clip": "nope"}]}))
        self.assertEqual(resp.status, 400)
        self.assertIn("error", json.loads(data))
        with open(self.project_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)

    def test_media_list_is_video_files_only(self):
        resp, data = self.request("GET", "/api/media")
        self.assertEqual(resp.status, 200)
        self.assertEqual(json.loads(data)["files"], [])  # a.bin has no video extension

    def test_range_bytes(self):
        resp, data = self.request("GET", "/media/s1", headers={"Range": "bytes=10-19"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(len(data), 10)
        self.assertEqual(resp.getheader("Content-Range"), "bytes 10-19/1024")

    def test_range_suffix(self):
        resp, data = self.request("GET", "/media/s1", headers={"Range": "bytes=-5"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(len(data), 5)

    def test_range_open_ended(self):
        resp, data = self.request("GET", "/media/s1", headers={"Range": "bytes=990-"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(len(data), 34)

    def test_range_past_the_end_is_416(self):
        resp, data = self.request("GET", "/media/s1", headers={"Range": "bytes=2000-2010"})
        self.assertEqual(resp.status, 416)

    def test_head(self):
        resp, data = self.request("HEAD", "/media/s1")
        self.assertEqual(resp.status, 200)
        self.assertEqual(data, b"")
        self.assertEqual(resp.getheader("Content-Length"), "1024")

    def test_unknown_source_is_404(self):
        resp, _data = self.request("GET", "/media/nope")
        self.assertEqual(resp.status, 404)


@unittest.skipUnless(media.available(), "ffmpeg/ffprobe not on PATH")
class AgentEyesServerTests(unittest.TestCase):
    """/api/frame, /api/sheet, /api/scenes, /api/silences, on a real clip."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="studio-server-eyes-test-")
        self.a, self.b, self.c = ffmpeg_proofs.make_clips(self.tmp)
        self.project_path = os.path.join(self.tmp, "project.json")
        store = Store(self.project_path)
        store.create("Eyes")
        store.apply([{"op": "add_source", "path": self.a}], by="cli", probe=media.probe)

        self.httpd = make_server(self.project_path, port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp, resp.read()
        finally:
            conn.close()

    def test_frame(self):
        resp, data = self.request("GET", "/api/frame?source=s1&t=2.5")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"), "image/jpeg")
        self.assertEqual(data[:3], b"\xff\xd8\xff")

    def test_sheet(self):
        resp, data = self.request("GET", "/api/sheet?source=s1&cols=4&rows=2")
        self.assertEqual(resp.status, 200)
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        self.assertIn("tiles=8 cols=4", resp.getheader("X-Sheet"))

    def test_scenes(self):
        resp, data = self.request("GET", "/api/scenes?source=s1&threshold=0.3")
        self.assertEqual(resp.status, 200)
        payload = json.loads(data)
        self.assertEqual([s["t"] for s in payload["scenes"]], [2.0])

    def test_silences(self):
        resp, data = self.request("GET", "/api/silences?source=s1")
        self.assertEqual(resp.status, 200)
        payload = json.loads(data)
        self.assertEqual(len(payload["silences"]), 1)
        self.assertAlmostEqual(payload["silences"][0]["start"], 1.5, delta=0.05)

    def test_unknown_source_is_400(self):
        resp, data = self.request("GET", "/api/frame?source=nope&t=0")
        self.assertEqual(resp.status, 400)


if __name__ == "__main__":
    unittest.main()
