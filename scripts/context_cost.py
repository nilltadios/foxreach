#!/usr/bin/env python3
"""How much of an agent's context foxreach takes: its tool definitions, and what calls return.

Run:      uv run --python 3.15 scripts/context_cost.py [URL ...]
          (default URLs: a long Wikipedia article and a news front page; needs internet)
Browser:  FOXREACH_BROWSER, as for the tests. A throwaway headless instance on a temporary profile;
          no real profile is touched and no agent's quota is used.
Prints:   characters and an estimate of tokens (characters / 4 for text; width x height / 750 for
          images, after the downscale models apply to images over 1568 px on the long side). The
          estimate is rough: a tokenizer gives exact counts, but the order of magnitude is what
          shows bloat. Nothing is written.
"""
import io
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test"))
sys.path.insert(0, str(ROOT / "scripts"))
from build_install import build  # noqa: E402
from harness import Browser, McpClient  # noqa: E402

URLS = ["https://en.wikipedia.org/wiki/Firefox", "https://www.bbc.com/news"]


def tokens(chars):
    return chars // 4


def image_tokens(data):
    from PIL import Image
    w, h = Image.open(io.BytesIO(data)).size
    scale = min(1.0, 1568 / max(w, h))
    w, h = int(w * scale), int(h * scale)
    return w, h, w * h // 750


def row(name, chars, note=""):
    print(f"  {name:<34} {chars:>8} chars  ~{tokens(chars):>7} tokens  {note}")


def main():
    urls = sys.argv[1:] or URLS
    work = pathlib.Path("/tmp") / "foxreach-context-cost.xpi"
    build(work, True)
    browser = Browser(work)
    try:
        browser.install_and_restart()
        mcp = McpClient(browser.socket)
        init = mcp.init
        listed = mcp.request("tools/list")["tools"]
        defs = sum(len(json.dumps(t)) for t in listed)
        print("Loaded when the agent starts (unless the agent defers MCP tools, as Claude Code does):")
        row("server instructions", len(init.get("instructions", "")))
        row(f"tool definitions ({len(listed)} tools)", defs)
        for url in urls:
            print(f"\nPer call, on {url}:")
            text = mcp.call("tabs_create", url=url)
            tab = int(text.split(":")[0].split()[-1])
            for name, args in [
                ("read_page", {}),
                ("read_page filter=interactive", {"filter": "interactive"}),
                ("read_page filter=interactive scope=page", {"filter": "interactive", "scope": "page"}),
                ("get_page_text", {}),
                ("find 'search box'", {"query": "search box"}),
                ("read_console", {}),
                ("read_network", {}),
            ]:
                tool = name.split()[0]
                try:
                    out = mcp.call(tool, tabId=tab, **args)
                except Exception as e:  # noqa: BLE001
                    out = f"(error: {e})"
                note = "cut at the default max_chars" if "[truncated" in out or "truncated" in out[-200:] else ""
                row(name, len(out), note)
            data, mime = mcp.image(tabId=tab)
            w, h, t = image_tokens(data)
            print(f"  {'screenshot (default)':<34} {w}x{h} {mime:<10}         ~{t:>7} tokens")
            mcp.call("tabs_close", tabId=tab)
        print("\nCaps: read_page and get_page_text stop at max_chars (default 50000);")
        print("javascript replies stop at 500 kB unless save_to writes the result to a file instead.")
        mcp.close()
    finally:
        browser.cleanup()
        work.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
