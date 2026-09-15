"""The MCP handler, driven in-process: initialize, tools/list, and the tools."""

import os
import shutil
import sys
import tempfile
import unittest

from studio import media
from studio.mcp import DEFAULT_PROTOCOL_VERSION, Handler
from studio.project import Store

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import ffmpeg_proofs  # noqa: E402


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="studio-mcp-test-")
        self.project_path = os.path.join(self.tmp, "project.json")
        Store(self.project_path).create("MCP Test")
        self.handler = Handler(Store(self.project_path))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_initialize(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(resp["result"]["capabilities"], {"tools": {}})
        self.assertEqual(resp["result"]["serverInfo"]["name"], "studio")

    def test_initialize_with_an_unknown_protocol_version_falls_back(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {"protocolVersion": "9999-01-01"}})
        self.assertEqual(resp["result"]["protocolVersion"], DEFAULT_PROTOCOL_VERSION)

    def test_notifications_initialized_has_no_reply(self):
        self.assertIsNone(self.handler.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_ping(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 2, "method": "ping"})
        self.assertEqual(resp["result"], {})

    def test_unknown_method_is_a_json_rpc_error(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 3, "method": "explode"})
        self.assertEqual(resp["error"]["code"], -32601)

    def test_tools_list(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
        names = {t["name"] for t in resp["result"]["tools"]}
        self.assertEqual(names, {
            "project_get", "sources_list", "media_list", "source_add", "source_probe",
            "source_frame", "source_sheet", "source_scenes", "source_silences",
            "changes_apply", "export",
        })
        for tool in resp["result"]["tools"]:
            self.assertIn("required", tool["inputSchema"])

    def test_project_get(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                     "params": {"name": "project_get", "arguments": {}}})
        self.assertNotIn("isError", resp["result"])
        self.assertIn('"name": "MCP Test"', resp["result"]["content"][0]["text"])

    def test_changes_apply(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
            "name": "changes_apply", "arguments": {"changes": [{"op": "rename", "name": "Renamed"}]}}})
        self.assertNotIn("isError", resp["result"])
        self.assertIn('"Renamed"', resp["result"]["content"][0]["text"])
        self.assertEqual(Store(self.project_path).load().name, "Renamed")

    def test_a_change_error_comes_back_as_is_error_not_a_json_rpc_error(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "changes_apply", "arguments": {"changes": [{"op": "trim", "cut": "nope"}]}}})
        self.assertNotIn("error", resp)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("no cut", resp["result"]["content"][0]["text"])

    def test_an_unknown_tool_is_also_is_error(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                                     "params": {"name": "nope", "arguments": {}}})
        self.assertTrue(resp["result"]["isError"])


@unittest.skipUnless(media.available(), "ffmpeg/ffprobe not on PATH")
class McpMediaToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="studio-mcp-media-test-")
        self.a, self.b, self.c = ffmpeg_proofs.make_clips(self.tmp)
        self.project_path = os.path.join(self.tmp, "project.json")
        store = Store(self.project_path)
        store.create("MCP Media")
        store.apply([{"op": "add_source", "path": self.a}], by="cli", probe=media.probe)
        self.handler = Handler(Store(self.project_path))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_source_frame_returns_an_image(self):
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "source_frame", "arguments": {"source": "s1", "t": 2.5}}})
        content = resp["result"]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertEqual(content[0]["mimeType"], "image/jpeg")
        self.assertTrue(content[0]["data"])
        self.assertIn("s1 at 2.500 s", content[1]["text"])

    def test_source_scenes(self):
        store = Store(self.project_path)
        store.apply([{"op": "add_source", "path": self.c}], by="cli", probe=media.probe)
        resp = self.handler.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "source_scenes", "arguments": {"source": "s2"}}})
        self.assertIn('"t": 2.0', resp["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
