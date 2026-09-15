"""The local server: describe, a batch, a failing batch, Range media. Skipped
without ffmpeg on PATH (adding a source needs a probe)."""

import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest

import _clips

from studio import media, server
from studio.project import Store


@unittest.skipUnless(media.available(), "ffmpeg/ffprobe not on PATH")
class Server(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="studio-server-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.a, self.b = _clips.make(self.dir)
        self.project_path = os.path.join(self.dir, "project.json")
        store = Store(self.project_path)
        store.create("Server test")
        store.apply([{"op": "add_source", "path": self.a}], "cli", probe=media.probe)
        store.apply([{"op": "add_cut", "source": "s1", "in": 0.0, "out": 2.0}], "cli", probe=media.probe)

        self.httpd = server.make_server(self.project_path, port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def _conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)

    def _get(self, path, headers=None):
        conn = self._conn()
        conn.request("GET", path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp, body

    def _head(self, path, headers=None):
        conn = self._conn()
        conn.request("HEAD", path, headers=headers or {})
        resp = conn.getresponse()
        resp.read()
        conn.close()
        return resp

    def _post(self, path, payload):
        conn = self._conn()
        body = json.dumps(payload).encode("utf-8")
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, json.loads(data)

    # ---- describe -----------------------------------------------------

    def test_describe(self):
        resp, body = self._get("/api/project")
        self.assertEqual(resp.status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["project"]["name"], "Server test")
        self.assertEqual(len(payload["timeline"]), 1)
        self.assertEqual(payload["duration"], 2.0)

    # ---- a batch, and a failing batch ----------------------------------

    def test_a_batch_applies_and_is_visible_on_the_next_describe(self):
        resp, payload = self._post("/api/changes", {"by": "person", "changes": [
            {"op": "add_cut", "source": "s1", "in": 2.0, "out": 4.0},
        ]})
        self.assertEqual(resp.status, 200)
        self.assertEqual(payload["results"], [{"cut": "c2"}])
        _, describe = self._get("/api/project")
        describe = json.loads(describe)
        self.assertEqual(len(describe["project"]["cuts"]), 2)

    def test_a_failing_batch_gives_400_and_leaves_the_file_unchanged(self):
        _, before = self._get("/api/project")
        before = json.loads(before)
        resp, payload = self._post("/api/changes", {"by": "person", "changes": [
            {"op": "trim", "cut": "nope"},
        ]})
        self.assertEqual(resp.status, 400)
        self.assertIn("error", payload)
        _, after = self._get("/api/project")
        after = json.loads(after)
        self.assertEqual(before["project"]["revision"], after["project"]["revision"])

    # ---- media, Range ---------------------------------------------------

    def test_media_range_bytes_s_e(self):
        size = os.path.getsize(self.a)
        with open(self.a, "rb") as fh:
            fh.seek(10)
            expected = fh.read(10)
        resp, body = self._get("/media/s1", headers={"Range": "bytes=10-19"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(resp.getheader("Content-Range"), f"bytes 10-19/{size}")
        self.assertEqual(body, expected)

    def test_media_range_last_n_bytes(self):
        size = os.path.getsize(self.a)
        with open(self.a, "rb") as fh:
            fh.seek(size - 5)
            expected = fh.read(5)
        resp, body = self._get("/media/s1", headers={"Range": "bytes=-5"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(resp.getheader("Content-Range"), f"bytes {size - 5}-{size - 1}/{size}")
        self.assertEqual(body, expected)

    def test_media_range_open_ended(self):
        size = os.path.getsize(self.a)
        start = size - 100
        with open(self.a, "rb") as fh:
            fh.seek(start)
            expected = fh.read()
        resp, body = self._get("/media/s1", headers={"Range": f"bytes={start}-"})
        self.assertEqual(resp.status, 206)
        self.assertEqual(resp.getheader("Content-Range"), f"bytes {start}-{size - 1}/{size}")
        self.assertEqual(body, expected)

    def test_media_range_past_the_end_is_416(self):
        size = os.path.getsize(self.a)
        resp, _ = self._get("/media/s1", headers={"Range": f"bytes={size + 100}-"})
        self.assertEqual(resp.status, 416)

    def test_media_head(self):
        size = os.path.getsize(self.a)
        resp = self._head("/media/s1")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Length"), str(size))

    def test_media_unknown_id_is_404(self):
        resp, _ = self._get("/media/s99")
        self.assertEqual(resp.status, 404)

    def test_media_whole_file_without_range(self):
        size = os.path.getsize(self.a)
        resp, body = self._get("/media/s1")
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(body), size)
        self.assertEqual(resp.getheader("Accept-Ranges"), "bytes")


if __name__ == "__main__":
    unittest.main()
