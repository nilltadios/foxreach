#!/usr/bin/env python3
"""After installing from a release link, the leftover tab becomes foxreach's welcome page.

Run:      FOXREACH_BROWSER=<browser> uv run --python 3.15 test/welcome_tab.py [--shot FILE]
Expected: every line PASS, exit 0; --shot saves a PNG of the welcome page.
How:      a throwaway headless browser and profile (test/harness.py). A tab on a foxreach release
          address stands in for the one the browser leaves after installing from the .xpi link;
          then the add-on is installed and the tab is read back.
"""
import os
if os.name == "nt":
    raise SystemExit("This test runs on Linux and macOS only: Windows ignores its throwaway HOME, so it "
                     "would act on the real foxreach setup. The Windows test is test/windows_hosts.py.")

import base64
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness import ROOT, Browser, McpClient, Marionette  # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


shot = sys.argv[sys.argv.index("--shot") + 1] if "--shot" in sys.argv else None
browser = Browser(ROOT / "dist/foxreach.xpi")
try:
    browser.start(marionette=True)
    m = Marionette()
    m.command("WebDriver:NewSession", {})
    # The release page's address (any foxreach release URL counts), loaded before the add-on exists.
    m.command("WebDriver:Navigate", {"url": "https://github.com/nilltadios/foxreach/releases/tag/v0.3.2"})
    m.command("Addon:Install", {"path": str(browser.xpi), "temporary": False, "allowPrivateBrowsing": True})
    # Read the tab from the browser window: the page moves to the add-on's process, which
    # Marionette's own idea of the current page does not follow.
    m.command("Marionette:SetContext", {"value": "chrome"})
    url = ""
    for _ in range(60):
        url = m.command("WebDriver:ExecuteScript", {"script": "return gBrowser.tabs.map((t) => t.linkedBrowser.currentURI.spec).join(' ')", "args": []})["value"]
        if "/welcome/welcome.html" in url:
            break
        time.sleep(0.25)
    url = next((u for u in url.split() if "/welcome/" in u), url)
    check("the release tab became the welcome page", url.startswith("moz-extension://") and url.endswith("/welcome/welcome.html"), url)
    time.sleep(1)
    m.command("Marionette:SetContext", {"value": "content"})
    m.command("WebDriver:Navigate", {"url": url})  # the welcome page itself, to read and photograph it
    text = m.command("WebDriver:ExecuteScript", {"script": "return document.body.innerText", "args": []})["value"]
    check("it says foxreach is installed and shows the prompt", "foxreach is installed" in text and "small foxreach window" in text and "Always allow" in text, text[:200])
    # Installed as a user would (no test helper answering for them): both builds start with a list.
    browser.wait_for_socket()
    status = json.loads(McpClient(browser.socket).call("status"))
    check("the full build starts with an empty list of sites, like the signed build",
          status.get("siteAccess") == "listed", status.get("siteAccess"))
    if shot:
        time.sleep(1.5)  # the arrival animation
        for scheme, value in (("light", 1), ("dark", 0)):
            m.command("Marionette:SetContext", {"value": "chrome"})
            m.command("WebDriver:ExecuteScript", {"script": f"Services.prefs.setIntPref('layout.css.prefers-color-scheme.content-override', {value})", "args": []})
            m.command("Marionette:SetContext", {"value": "content"})
            m.command("WebDriver:Navigate", {"url": url})
            time.sleep(1.8)
            png = m.command("WebDriver:TakeScreenshot", {"full": False})["value"]
            pathlib.Path(shot.replace(".png", f"-{scheme}.png")).write_bytes(base64.b64decode(png))
finally:
    browser.stop()
    browser.cleanup()


# A real install from the link, as the foxreach app and the README's link do it. The browser then
# leaves a tab that never loaded anything: its address is about:blank, and only its label (the
# add-on API's title) and the address bar still show the .xpi address. The release site is
# stood in for by a local proxy that answers every request with the add-on.
import http.server  # noqa: E402
import threading  # noqa: E402

XPI = (ROOT / "dist/foxreach.xpi").read_bytes()
RELEASE_LINK = "http://nilltadios.github.io/foxreach/foxreach-0.4.0-signed.xpi"


class Release(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-xpinstall")
        self.send_header("Content-Length", str(len(XPI)))
        self.end_headers()
        self.wfile.write(XPI)


proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Release)
threading.Thread(target=proxy.serve_forever, daemon=True).start()
browser = Browser(ROOT / "dist/foxreach.xpi")
try:
    with open(browser.profile / "user.js", "a") as prefs:
        prefs.write(
            'user_pref("network.proxy.type", 1);\n'
            'user_pref("network.proxy.http", "127.0.0.1");\n'
            f'user_pref("network.proxy.http_port", {proxy.server_address[1]});\n'
            'user_pref("network.proxy.no_proxies_on", "");\n'
            'user_pref("dom.security.https_only_mode", false);\n'
            'user_pref("dom.security.https_first", false);\n'
        )
    browser.start(marionette=True)
    m = Marionette()
    m.command("WebDriver:NewSession", {})
    m.command("Marionette:SetContext", {"value": "chrome"})
    run = lambda script: m.command("WebDriver:ExecuteScript", {"script": script, "args": []})["value"]  # noqa: E731
    # Opened as from outside the browser (the app runs `firefox <link>`): a trusted new tab.
    run(f"openTrustedLinkIn({RELEASE_LINK!r}, 'tab'); return 1;")
    added = False
    for _ in range(60):
        added = run('const n = document.getElementById("addon-webext-permissions-notification");'
                    "if (!n || n.hidden || !n.button) return false; n.button.click(); return true;")
        if added:
            break
        time.sleep(0.25)
    check("from the link, the browser asks to add foxreach, and Add is pressed", added)
    tab_state = lambda: run("return gBrowser.tabs.map((t) => [t.linkedBrowser.currentURI.spec, t.label])")  # noqa: E731
    left = tab_state()
    tabs = left
    for _ in range(60):
        tabs = tab_state()
        if any(u.endswith("/welcome/welcome.html") for u, _ in tabs):
            break
        time.sleep(0.25)
    check("the tab the install left behind became the welcome page",
          any(u.startswith("moz-extension://") and u.endswith("/welcome/welcome.html") for u, _ in tabs),
          {"just after Add": left, "after 15 s": tabs})
finally:
    browser.stop()
    browser.cleanup()
    proxy.shutdown()
sys.exit(1 if fails else 0)
