#!/usr/bin/env python3
# /// script
# dependencies = ["pillow"]
# ///
"""What foxreach can do with another add-on's prompt inside a page (a password manager's PIN box).

pinbox/ is a stand-in for such an add-on: its content script puts its own moz-extension page, in an
iframe inside a closed shadow root, over the site; the page has six one-digit boxes that move focus
to the next box 120 ms after each digit. The probe installs foxreach and pinbox in a throwaway
profile and tries: screenshot, read_page/find, click by point, type_text fast and with a delay.

Run:      FOXREACH_BROWSER=~/AppImages/librewolf.appimage uv run --python 3.15 spikes/other-addon-overlay/probe.py
Output:   one line per question; FINDINGS.md records a run.
"""
import http.server
import io
import json
import pathlib
import re
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "test"))
from harness import ROOT, Browser, McpClient, ToolError  # noqa: E402
from PIL import Image  # noqa: E402

PORT = 47856
PAGE = b"""<!doctype html><meta charset=utf-8><title>Sign in</title><body style="margin:0;background:#fff">
<h1>Sign in</h1><label>Email <input id=email></label>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)


def say(question, answer):
    print(f"{question}: {answer}", flush=True)


def main():
    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    b = Browser(ROOT / "dist/foxreach.xpi")
    try:
        b.start(marionette=True)
        b.install()
        b.marionette.command("Addon:Install", {"path": str(HERE / "pinbox"), "temporary": True})
        b.wait_for_socket()
        mcp = McpClient(b.socket)
        tab = int(re.search(r"\d+", mcp.call("tabs_create", url=f"http://127.0.0.1:{PORT}/")).group())
        mcp.call("tabs_select", tabId=tab)  # in front, so its frames paint at full rate
        time.sleep(1.5)
        pin = lambda: json.loads(mcp.call("javascript", tabId=tab, code="document.documentElement.dataset.pin ?? null"))  # noqa: E731

        shot, _ = mcp.image(tabId=tab, format="png")
        img = Image.open(io.BytesIO(shot)).convert("RGB")
        sx = img.width / json.loads(mcp.call("javascript", tabId=tab, code="innerWidth"))
        px = img.getpixel((int(380 * sx), int(310 * sx)))  # inside the box, right of the inputs
        say("screenshot shows the other add-on's iframe", f"{'yes' if px[0] > 180 and px[1] < 60 else 'no'} (pixel {px})")

        tree = mcp.call("read_page", tabId=tab, filter="all")
        say("read_page lists its inputs", "no" if tree.count("textbox") <= 1 else "yes")
        say("read_page mentions the frame at all", "yes" if "moz-extension" in tree else "no")
        found = mcp.call("find", tabId=tab, query="text box")
        say("find returns", found.replace("\n", " | ")[:200])

        x, y = 40 + 20 + 15, 300 + 20 + 14  # the first box
        out = mcp.call("click", tabId=tab, x=x, y=y)
        say("click by point into the first box", out)
        try:
            out = mcp.call("type_text", tabId=tab, text="123456")
            say("type_text, all at once", out)
        except ToolError as e:
            say("type_text, all at once (refused)", str(e)[:200])
        try:
            out = mcp.call("type_text", tabId=tab, text="123456", allow_non_text=True)
            say("type_text allow_non_text", out)
        except ToolError as e:
            say("type_text allow_non_text (refused)", str(e)[:200])
        time.sleep(0.5)
        say("...the boxes then hold", pin())

        mcp.call("navigate", tabId=tab, url="reload")
        time.sleep(1.5)
        mcp.call("click", tabId=tab, x=x, y=y)
        try:
            out = mcp.call("type_text", tabId=tab, text="123456", allow_non_text=True, delay_ms=250)
            say("type_text delay_ms=250", out)
        except ToolError as e:
            say("type_text delay_ms=250 (refused)", str(e)[:200])
        time.sleep(0.5)
        say("...the boxes then hold", pin())

        # The same in a background tab, where the page's own timers wait at least a second.
        other = int(re.search(r"\d+", mcp.call("tabs_create", url=f"http://127.0.0.1:{PORT}/?other")).group())
        mcp.call("tabs_select", tabId=other)
        mcp.call("navigate", tabId=tab, url="reload")
        time.sleep(1.5)
        mcp.call("click", tabId=tab, x=x, y=y)
        for delay in (250, 1200):
            mcp.call("javascript", tabId=tab, code="delete document.documentElement.dataset.pin; 1")
            mcp.call("click", tabId=tab, x=x, y=y)
            mcp.call("press_key", tabId=tab, keys="mod+a", allow_non_text=True)
            mcp.call("type_text", tabId=tab, text="123456", allow_non_text=True, delay_ms=delay)
            time.sleep(1.5)
            say(f"background tab, delay_ms={delay}: the boxes hold", pin())
            mcp.call("navigate", tabId=tab, url="reload")
            time.sleep(1.5)
        mcp.close()
    finally:
        b.cleanup()
        server.shutdown()


if __name__ == "__main__":
    main()
