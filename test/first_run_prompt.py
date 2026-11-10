#!/usr/bin/env python3
"""A browser prompt that covers the window: tabs_select must say the tab did not come to the front.

Run:      uv run --python 3.15 test/first_run_prompt.py
Expected: "3 passed, 0 failed", exit 0, on a browser that opens a first-run prompt in a new profile
          (Mozilla's own builds: Firefox Developer Edition, Nightly). Exit 0 with one SKIP line on a
          browser that opens none (LibreWolf).

While such a prompt is open the browser ignores requests to switch tabs. The other tests switch
these prompts off (test/harness.py); this one leaves them on.
"""
import re
import sys

from harness import ROOT, Browser, McpClient

PROMPT_PREFS = ("termsofuse.", "datareporting.policy.", "browser.aboutwelcome.", "browser.shell.", "homepage_override")


def main():
    browser = Browser(ROOT / "dist/foxreach.xpi")
    user_js = browser.profile / "user.js"
    user_js.write_text("".join(l for l in user_js.read_text().splitlines(keepends=True) if not any(p in l for p in PROMPT_PREFS)))
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"))

    try:
        browser.install_and_restart()
        mcp = McpClient(browser.socket)
        tab = int(re.search(r"Tab (\d+)", mcp.call("tabs_create", url="about:blank")).group(1))
        out = mcp.call("tabs_select", tabId=tab)
        if out == f"Selected tab {tab}.":
            print("SKIP this browser opened no prompt in a new profile")
            return 0
        check("tabs_select says the tab was not brought to the front", "not brought to the front" in out and "prompt" in out, out)
        check("tabs_list agrees: the tab is not the active one", f"{tab} (active)" not in mcp.call("tabs_list"))
        check("tools still work on the tab", mcp.call("javascript", tabId=tab, code="1 + 1").strip() == "2")
        mcp.close()
    finally:
        browser.cleanup()
    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
