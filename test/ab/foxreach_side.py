#!/usr/bin/env python3
"""The foxreach half of the comparison: same pages and tasks as were run in Claude in Chrome.

Needs test/ab/serve_pages.py running, and internet for the real sites.
Run: uv run --python 3.15 test/ab/foxreach_side.py
"""
import json, pathlib, re, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from harness import Browser, McpClient, ToolError, ROOT
import subprocess
MAIN = "http://127.0.0.1:47852"
subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
b = Browser(ROOT / "dist/foxreach.xpi")
def tab_id(t): return int(re.search(r"Tab (\d+)", t).group(1))
def show(name, value): print(f"{name}: {str(value)[:500]}", flush=True)
try:
    b.install_and_restart(); m = McpClient(b.socket)
    t = tab_id(m.call("tabs_create", url=f"{MAIN}/main"))
    tree = m.call("read_page", tabId=t)
    show("read_page chars, /main", len(tree))
    for q in ["the field for typing your name", "name"]:
        show(f"find {q!r}", m.call("find", tabId=t, query=q))
    show("console", m.call("read_console", tabId=t).replace("\n", " | "))
    show("js last expression", m.call("javascript", tabId=t, code="const a = 2; a * 3"))
    show("js top-level await", m.call("javascript", tabId=t, code="const r = await Promise.resolve(2); r * 3"))
    m.call("navigate", tabId=t, url=f"{MAIN}/dnd")
    m.call("drag", tabId=t, from_x=60, from_y=60, to_x=370, to_y=90); time.sleep(0.3)
    show("native drag and drop", m.call("javascript", tabId=t, code="window.log.join()"))
    for site in ["https://en.wikipedia.org/wiki/Red_fox", "https://news.ycombinator.com/"]:
        s = time.time(); out = m.call("navigate", tabId=t, url=site); nav = time.time() - s
        s = time.time(); full = m.call("read_page", tabId=t, filter="interactive", max_chars=10_000_000); rd = time.time() - s
        s = time.time(); shot, _ = m.image(tabId=t); sh = time.time() - s
        show(site, f"nav {nav:.2f}s, read {rd:.2f}s {len(full)} chars, shot {sh:.2f}s {len(shot)} bytes")
        show("  find search bar", m.call("find", tabId=t, query="search bar").splitlines()[:2])
    m.close()
finally:
    b.cleanup()
