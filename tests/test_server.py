"""The server on an ephemeral port: describe, a batch, a failing batch, Range."""

import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest

from studio.project import Store
from studio.server import make_server


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
        body = json.dumps({"by": "agent", "changes": [{"op": "add_cut", "source": "s1"}]})
        resp, data = self.request("POST", "/api/changes", body=body)
        self.assertEqual(resp.status, 200)
        payload = json.loads(data)
        self.assertEqual(payload["results"], [{"cut": "c1"}])
        self.assertEqual(payload["timeline"][0]["cut"], "c1")

        with open(self.project_path, encoding="utf-8") as fh:
            before = fh.read()
        resp, data = self.request("POST", "/api/changes", body=json.dumps({"changes": [{"op": "trim", "cut": "nope"}]}))
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


if __name__ == "__main__":
    unittest.main()
