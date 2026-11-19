#!/usr/bin/env python3
"""A real browser in the several-browsers mode: its host registers itself, an agent reaches it.

Run:      FOXREACH_BROWSER=<browser> uv run --python 3.15 test/multi_real.py
Expected: every line PASS, exit 0.
How:      a throwaway headless browser and profile (test/harness.py), started without FOXREACH_SOCKET
          and with a private XDG_RUNTIME_DIR, so its native host takes its own socket and info file
          as in daily use. Needs the host registered (scripts/build_install.py) for the HOME in use.
"""
import os
if os.name == "nt":
    raise SystemExit("This test runs on Linux and macOS only: Windows ignores its throwaway HOME, so it "
                     "would act on the real foxreach setup. The Windows test is test/windows_hosts.py.")

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness import ROOT, RUNTIME, Browser, McpClient  # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


run_dir = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-run-"))
run_dir.chmod(0o700)
os.environ["XDG_RUNTIME_DIR"] = str(run_dir)
os.environ.pop("FOXREACH_SOCKET", None)
folder = run_dir / "foxreach"
browser = Browser(ROOT / "dist/foxreach.xpi")
try:
    # An empty FOXREACH_SOCKET counts as unset: the host takes its own socket under XDG_RUNTIME_DIR.
    browser.start(marionette=True, env={"FOXREACH_SOCKET": "", "XDG_RUNTIME_DIR": str(run_dir)})
    browser.install()
    deadline = time.time() + 45
    while time.time() < deadline and not list(folder.glob("browser-*.json")):
        time.sleep(0.2)
    rows = json.loads(subprocess.run([RUNTIME, str(ROOT / "bridge/cli.mjs"), "browsers", "--json"], capture_output=True, text=True).stdout or "[]")
    row = rows[0] if rows else {}
    check("the host registers the browser it serves, with its profile", len(rows) == 1 and row.get("profile") == str(browser.profile)
          and row.get("name") not in (None, "Browser"), rows)
    want = "tor" if "/tor-browser" in str(browser.binary) else "appimage" if str(browser.binary).lower().endswith(".appimage") else "native"
    check("...and its packaging", row.get("packaging") == want, row)
    mcp = McpClient()
    out = mcp.call("tabs_list")
    check("an agent reaches the only browser without choosing", "tabs" in out.lower(), out)
    check("status names it", row.get("id", "?") in json.loads(mcp.call("status")).get("chosenBrowser", ""))
    mcp.close()
finally:
    browser.stop()
    browser.cleanup()
sys.exit(1 if fails else 0)
