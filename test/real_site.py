#!/usr/bin/env python3
"""Use a real website through foxreach, in a visible or headless throwaway browser.

Types into Wikipedia's search box with real input, submits, and checks the article that loads.
Run:      uv run --python 3.15 test/real_site.py [--visible] [--hold SECONDS] [--out DIR]
Expected: all PASS, exit 0. Needs internet. Read-only: no account, no posting.
"""
import pathlib
import re
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, Browser, McpClient


def main():
    visible = "--visible" in sys.argv
    hold = int(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 0
    out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else None
    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    browser = Browser(ROOT / "dist/foxreach.xpi", headless=not visible)
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        fails += not ok
        print("PASS" if ok else "FAIL", name, "" if ok else f"-- {str(detail)[:900]}", flush=True)

    try:
        browser.install_and_restart()
        m = McpClient(browser.socket)
        started = time.time()
        opened = m.call("tabs_create", url="https://en.wikipedia.org/wiki/Main_Page")
        tab = int(re.search(r"Tab (\d+)", opened).group(1))
        check("opens a real site", "Wikipedia" in opened, opened)
        tree = m.call("read_page", tabId=tab, filter="interactive")
        check("reads a large real page", len(tree) > 500 and "[r" in tree, tree[:300])
        found = m.call("find", tabId=tab, query="search")
        box = next((l for l in found.splitlines() if "searchbox" in l or "textbox" in l), None)
        check("finds the search box", box, found)
        ref = re.search(r"\[(r\d+)\]", box).group(1)
        m.call("type_text", tabId=tab, ref=ref, text="Red fox")
        value = m.call("javascript", tabId=tab, code="document.activeElement.value")
        check("typing reaches the real search box", "Red fox" in value, value)
        m.call("press_key", tabId=tab, keys="Enter")
        m.call("wait_for", tabId=tab, selector="#firstHeading", timeout=20)
        m.call("wait_for", tabId=tab, text="Vulpes vulpes", timeout=20)
        text = m.call("get_page_text", tabId=tab)
        check("Enter submits and the article loads", "Vulpes vulpes" in text, text[:400])
        m.call("hover", tabId=tab, x=500, y=300)
        data, mime = m.image(tabId=tab, format="png")
        check("screenshot of the real page", len(data) > 50000 and mime == "image/png", len(data))
        if out:
            out.mkdir(parents=True, exist_ok=True)
            (out / "agent-view.png").write_bytes(data)
        net = m.call("read_network", tabId=tab, url_pattern="wikipedia")
        check("network capture on a real site", "GET 200" in net, net[:400])
        print(f"took {time.time() - started:.1f} s", flush=True)
        m.call("scroll", tabId=tab, dy=500)
        print("READY", flush=True)
        time.sleep(hold)
        m.close()
    finally:
        browser.cleanup()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
