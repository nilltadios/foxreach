#!/usr/bin/env python3
"""What read_page's replies are made of: content, or noise (link addresses, repeated text, IDs).

Run:      uv run --python 3.15 scripts/page_noise.py [URL ...]
          (default: a long Wikipedia article and a news front page; needs internet)
Browser:  FOXREACH_BROWSER, as for the tests. A throwaway headless instance on a temporary profile;
          no real profile is touched.
Prints:   per page, the share of read_page's reply taken by quoted names and text, link addresses
          (href=...), refs ([rNN]) and indentation; structure-only lines; duplicate lines; styling
          words; how many of the page's visible words read_page carries; and a sample of lines.
          Run it before and after a change to read_page to see the difference. Nothing is written.
Measured 2026-11-11 (0.3.1): link addresses were 32% of Wikipedia's reply and 21% of BBC News';
          no styling noise; container names repeated their children's text.
"""
import collections
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test"))
sys.path.insert(0, str(ROOT / "scripts"))
from build_install import build  # noqa: E402
from harness import Browser, McpClient  # noqa: E402

URLS = ["https://en.wikipedia.org/wiki/Firefox", "https://www.bbc.com/news"]
LINE_ROLE = re.compile(r'\s*-?\s*(\w[\w ]*?)(?: "|\s*\[|:|$)')


def share(label, chars, total):
    print(f"  {label:<28} {chars:>7} chars  {100 * chars / max(1, total):5.1f}%")


def main():
    urls = sys.argv[1:] or URLS
    xpi = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-noise-")) / "foxreach.xpi"
    build(xpi, True)
    browser = Browser(xpi)
    try:
        browser.install_and_restart()
        mcp = McpClient(browser.socket)
        for url in urls:
            tab = int(mcp.call("tabs_create", url=url).split(":")[0].split()[-1])
            rp = mcp.call("read_page", tabId=tab, max_chars=200000)
            txt = mcp.call("get_page_text", tabId=tab, max_chars=200000)
            lines = rp.splitlines()
            structure_only = [l for l in lines if not re.search(r'"[^"]+"|: \S', l)]
            roles = collections.Counter((LINE_ROLE.match(l).group(1) if LINE_ROLE.match(l) else "?") for l in structure_only)
            visible = set(re.findall(r"\w{4,}", txt.lower()))
            carried = set(re.findall(r"\w{4,}", rp.lower()))
            print(f"\n=== {url}")
            print(f"read_page {len(rp)} chars, {len(lines)} lines | get_page_text {len(txt)} chars")
            share("names/text in quotes", sum(len(x) for x in re.findall(r'"([^"]*)"', rp)), len(rp))
            share("link addresses (href=...)", sum(len(x) for x in re.findall(r"\(href=[^)]*\)", rp)), len(rp))
            share("refs [rNN]", sum(len(x) for x in re.findall(r"\[(?:f\d+:)?r\d+\]", rp)), len(rp))
            share("indentation", sum(len(l) - len(l.lstrip()) for l in lines), len(rp))
            print(f"  structure-only lines: {len(structure_only)} of {len(lines)}; most common: {roles.most_common(6)}")
            print(f"  exact duplicate lines: {len(lines) - len(set(l.strip() for l in lines))}; "
                  f"styling words (class=/style=/css): {len(re.findall(r'class=|style=|css|font-|color:', rp))}")
            print(f"  visible words also in read_page: {100 * len(visible & carried) / max(1, len(visible)):.0f}%")
            print("  sample (lines 40-62):")
            for l in lines[40:62]:
                print("   |", l[:150])
            mcp.call("tabs_close", tabId=tab)
        mcp.close()
    finally:
        browser.cleanup()
        xpi.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
