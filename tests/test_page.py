"""A Playwright smoke test for app/studio.html, skipped when Playwright is absent.

Uses a VP9/WebM synthetic clip with real audio: this cloud environment's Chromium may
lack H.264 decoding (per BRIEF.md), and this test only needs the DOM to react, not
playback -- but the clip needs audio so adding it exercises v2's linked video+audio
clip pair, not just a lone video clip.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import unittest

from studio import media
from studio.project import Store
from studio.server import make_server

try:
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:
    HAVE_PLAYWRIGHT = False


@unittest.skipUnless(HAVE_PLAYWRIGHT, "Playwright is not installed")
@unittest.skipUnless(media.available(), "ffmpeg/ffprobe not on PATH")
class PageSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="studio-page-test-")
        cls.clip = os.path.join(cls.tmp, "clip.webm")
        subprocess.run(
            [media.FFMPEG, "-v", "error", "-y",
             "-f", "lavfi", "-i", "color=c=blue:s=640x360:r=30:d=3",
             "-f", "lavfi", "-i", "sine=f=440:r=44100:d=3",
             "-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p", "-c:a", "libopus", cls.clip],
            check=True,
        )
        cls.project_path = os.path.join(cls.tmp, "project.json")
        store = Store(cls.project_path)
        store.create("Page Test")
        store.apply([{"op": "add_source", "path": cls.clip}], by="cli", probe=media.probe)

        cls.httpd = make_server(cls.project_path, port=0)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

        cls.pw = sync_playwright().start()
        chromium_path = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        launch_args = {"executable_path": chromium_path} if os.path.exists(chromium_path) else {}
        cls.browser = cls.pw.chromium.launch(**launch_args)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_add_split_unlink_remove_with_no_console_errors(self):
        # Console errors from a blocked network resource (Google Fonts behind this
        # sandbox's proxy, the browser's automatic favicon request) are not script
        # bugs; the page is specced to work offline on its font fallback stacks.
        errors = []
        page = self.browser.new_page()
        page.on("console", lambda m: None if m.type != "error" or "Failed to load resource" in m.text
                else errors.append(m.text))
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        page.goto(f"http://127.0.0.1:{self.port}/studio.html")
        page.wait_for_selector("#sourceList li")

        # a source with audio: adding it makes a linked video+audio pair -- two blocks
        page.click("#sourceList li")
        page.wait_for_function("document.querySelectorAll('.cut-block').length === 2")
        page.wait_for_function("document.querySelectorAll('.cut-block.linked').length === 2")

        # splitting the video clip cascades to its linked audio sibling -- four blocks
        page.locator("#ruler").click(position={"x": 90, "y": 5})
        page.keyboard.press("s")
        page.wait_for_function("document.querySelectorAll('.cut-block').length === 4")

        # select a clip and unlink it: it loses its "linked" styling, its sibling too
        page.click(".track-row:not(.audio) .cut-block")
        page.keyboard.press("u")
        page.wait_for_function("document.querySelectorAll('.cut-block.linked').length === 2")

        # remove the (now unlinked) selected clip: its sibling survives -- three blocks
        page.keyboard.press("Delete")
        page.wait_for_function("document.querySelectorAll('.cut-block').length === 3")

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
