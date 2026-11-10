#!/usr/bin/env python3
"""The add-on updates itself from its update_url, and the bridge then warns that versions differ.

Run:      uv run --python 3.15 test/update.py [--visible]
Expected: every line PASS, exit 0.

A throwaway profile gets the checkout's version built with a local update_url. A local updates.json
offers a higher version; the browser's own background update check (the daily timer, run now) installs
it. The served file has the same layout scripts/release.py writes to docs/updates.json. Only
extensions.checkUpdateSecurity is off here, because the local server is http, not https.
"""
import hashlib
import http.server
import json
import pathlib
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, Browser, McpClient, ToolError  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts"))
from build_install import build, install_host, version  # noqa: E402
from release import updates_entry  # noqa: E402

PORT = 47855
BASE = f"http://127.0.0.1:{PORT}"
ADDON_ID = "foxreach@foxreach.local"
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


def serve(folder):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(folder), **kw)

        def log_message(self, *_):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# Runs in the browser's chrome context: the same check the daily add-on update timer makes.
# Marionette turns add-on updates off for automation; the browser itself has them on by default,
# so put that default back first.
BACKGROUND_CHECK = """
const [id, want] = arguments;
return (async () => {
Services.prefs.setBoolPref("extensions.update.enabled", true);
const { AddonManager, AddonManagerPrivate } = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
// Its promise can outlast the update itself (it also waits on metadata requests), so poll instead.
AddonManagerPrivate.backgroundUpdateCheck();
const deadline = Date.now() + 45000;
let addon;
while (Date.now() < deadline) {
  addon = await AddonManager.getAddonByID(id);
  if (addon && addon.version === want) break;
  await new Promise(r => setTimeout(r, 250));
}
return addon ? { version: addon.version, active: addon.isActive, autoUpdate: addon.applyBackgroundUpdates } : null;
})();
"""


def main():
    visible = "--visible" in sys.argv
    current = version()
    newer = f"{current}.1"
    work = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-update-"))
    old_xpi = build(work / "old.xpi", True, update_url=f"{BASE}/updates.json")
    new_xpi = build(work / f"foxreach-{newer}.xpi", True, version=newer, update_url=f"{BASE}/updates.json")
    (work / "updates.json").write_text(json.dumps({"addons": {ADDON_ID: {"updates": [
        updates_entry(newer, f"{BASE}/{new_xpi.name}", new_xpi),
    ]}}}))
    install_host()
    server = serve(work)
    browser = Browser(old_xpi, headless=not visible)
    with open(browser.profile / "user.js", "a") as f:
        f.write('user_pref("extensions.checkUpdateSecurity", false);\n')
    try:
        browser.start(marionette=True)
        browser.install()
        browser.wait_for_socket()
        mcp = McpClient(browser.socket)
        status = json.loads(mcp.call("status"))
        check("status reports the add-on version", status["version"] == current, status)
        check("status reports the bridge version", status.get("bridgeVersion") == current, status)
        check("no warning when they match", "warning" not in status, status)
        mcp.close()

        m = browser.marionette
        m.command("Marionette:SetContext", {"value": "chrome"})
        m.command("WebDriver:SetTimeouts", {"script": 90000})
        after = m.command("WebDriver:ExecuteScript", {"script": BACKGROUND_CHECK, "args": [ADDON_ID, newer]})["value"]
        check("updates apply in the background by default", after and after["autoUpdate"] in (1, 2), after)
        check("the background check installs the newer version", after and after["version"] == newer, after)
        check("the updated add-on is active", after and after["active"], after)

        # The updated add-on restarts its background page, which starts a new native host.
        deadline = time.time() + 30
        status = None
        while time.time() < deadline:
            try:
                mcp = McpClient(browser.socket)
                status = json.loads(mcp.call("status"))
                if status["version"] == newer:
                    break
                mcp.close()
            except (ToolError, RuntimeError, OSError):
                pass
            time.sleep(0.5)
        check("the updated add-on answers agents", status and status["version"] == newer, status)
        check("status warns that versions differ", status and "versions differ" in status.get("warning", ""), status)
        check("the warning says how to update", status and "scripts/update.py" in status.get("warning", ""), status)
        mcp.close()

        mcp = McpClient(browser.socket)
        out = mcp.call("tabs_list")
        check("the first other call of a session warns once", "versions differ" in out, out)
        out = mcp.call("tabs_list")
        check("later calls do not repeat it", "versions differ" not in out, out)
        mcp.close()
    finally:
        browser.cleanup()
        server.shutdown()
    print("failed:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
