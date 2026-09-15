"""A Playwright smoke test for `app/studio.html`. Skipped when Playwright, or
ffmpeg (needed to add a source), is absent."""

import os
import shutil
import tempfile
import threading
import unittest

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False

import _clips

from studio import media, server
from studio.project import Store

CHROMIUM_PATH = "/opt/pw-browsers/chromium"


@unittest.skipUnless(HAVE_PLAYWRIGHT, "playwright not installed")
@unittest.skipUnless(media.available(), "ffmpeg/ffprobe not on PATH")
class PageSmoke(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="studio-page-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.a, self.b = _clips.make(self.dir)
        project_path = os.path.join(self.dir, "project.json")
        store = Store(project_path)
        store.create("Page smoke")
        store.apply([{"op": "add_source", "path": self.a}], "cli", probe=media.probe)
        store.apply([{"op": "add_source", "path": self.b}], "cli", probe=media.probe)

        self.httpd = server.make_server(project_path, port=0)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def test_page_loads_adds_splits_and_removes_a_cut(self):
        errors = []
        with sync_playwright() as p:
            launch_kwargs = {}
            if os.path.exists(CHROMIUM_PATH):
                launch_kwargs["executable_path"] = CHROMIUM_PATH
            browser = p.chromium.launch(**launch_kwargs)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: errors.append(str(exc)))

            page.goto(f"http://127.0.0.1:{self.port}/studio.html")
            page.wait_for_selector("#sources-list .bin-row")
            self.assertEqual(errors, [], f"console errors on load: {errors}")

            # A source click adds a cut.
            self.assertEqual(page.locator("#track .cut").count(), 0)
            page.locator("#sources-list .bin-row").first.click()
            page.wait_for_selector("#track .cut")
            self.assertEqual(page.locator("#track .cut").count(), 1)

            # Move the playhead inside the cut (via the ruler, so the cut stays
            # selected rather than starting a reorder drag), then split with "S".
            box = page.locator("#track .cut").first.bounding_box()
            ruler_box = page.locator("#ruler").bounding_box()
            page.mouse.click(box["x"] + box["width"] / 2, ruler_box["y"] + ruler_box["height"] / 2)
            page.keyboard.press("s")
            page.wait_for_function("document.querySelectorAll('#track .cut').length === 2")
            self.assertEqual(page.locator("#track .cut").count(), 2)

            # Delete removes the selected cut (the second half, auto-selected by the split).
            page.keyboard.press("Delete")
            page.wait_for_function("document.querySelectorAll('#track .cut').length === 1")
            self.assertEqual(page.locator("#track .cut").count(), 1)

            self.assertEqual(errors, [], f"console errors during interaction: {errors}")
            browser.close()


if __name__ == "__main__":
    unittest.main()
