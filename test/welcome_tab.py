#!/usr/bin/env python3
"""After installing from a release link, the tab left blank becomes foxreach's welcome page; no
other tab is ever taken, and an update opens nothing.

Run:      FOXREACH_BROWSER=<browser> uv run --python 3.15 test/welcome_tab.py [--shot FILE]
Expected: every line PASS, exit 0; --shot saves a PNG of the welcome page.
How:      throwaway headless browsers and profiles (test/harness.py).
          1. A loaded release page on github.com (needs the network), then the add-on is installed:
             the page stays as it was.
          2. A real install from the .xpi link, through a local proxy standing in for the release
             site: the blank tab it leaves becomes the welcome page, a loaded release page next to
             it does not.
          3. Updates in the background from released versions 0.4.1 (which recorded the version it
             welcomed) and 0.3.2 (older than the welcome page) to this checkout, with a loaded
             release page and a blank tab left by the .xpi link open: no tab changes.
"""
import os
if os.name == "nt":
    raise SystemExit("This test runs on Linux and macOS only: Windows ignores its throwaway HOME, so it "
                     "would act on the real foxreach setup. The Windows test is test/windows_hosts.py.")

import base64
import http.server
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness import ROOT, Browser, McpClient, Marionette  # noqa: E402
import build_install  # noqa: E402  (on sys.path through harness)
import importlib.util  # noqa: E402

# test/update.py, by path: scripts/update.py, also on sys.path, has the same name.
_spec = importlib.util.spec_from_file_location("update_test", pathlib.Path(__file__).resolve().parent / "update.py")
update_test = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update_test)
ADDON_ID, BACKGROUND_CHECK, BASE, serve = update_test.ADDON_ID, update_test.BACKGROUND_CHECK, update_test.BASE, update_test.serve

fails = 0
# The add-on looks for the tab for about 6 s after it starts; a tab still unchanged after this long
# was left alone.
SETTLE = 10


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


def chrome_run(m, script, *args):
    m.command("Marionette:SetContext", {"value": "chrome"})
    return m.command("WebDriver:ExecuteScript", {"script": script, "args": list(args)})["value"]


def tab_state(m):
    """[address, label] of every tab, read from the browser window: a page moved to the add-on's
    process is not followed by Marionette's own idea of the current page."""
    return chrome_run(m, "return gBrowser.tabs.map((t) => [t.linkedBrowser.currentURI.spec, t.label])")


def welcome_tabs(tabs):
    return [u for u, _ in tabs if u.startswith("moz-extension://") and "/welcome/" in u]


def watch(m, seconds=SETTLE):
    """Every distinct tab state seen over the next seconds, so a tab changed and then closed again
    is caught too."""
    seen = []
    for _ in range(int(seconds * 2)):
        tabs = tab_state(m)
        if tabs not in seen:
            seen.append(tabs)
        time.sleep(0.5)
    return seen


def welcome_url(m):
    uuid = chrome_run(m, 'return JSON.parse(Services.prefs.getStringPref("extensions.webextensions.uuids"))[arguments[0]];', ADDON_ID)
    return f"moz-extension://{uuid}/welcome/welcome.html"


shot = sys.argv[sys.argv.index("--shot") + 1] if "--shot" in sys.argv else None

# 1. A release page that loaded is the user's page, whatever its address.
RELEASE_PAGE = "https://github.com/nilltadios/foxreach/releases/tag/v0.3.2"
browser = Browser(ROOT / "dist/foxreach.xpi")
try:
    browser.start(marionette=True)
    m = Marionette()
    m.command("WebDriver:NewSession", {})
    m.command("WebDriver:Navigate", {"url": RELEASE_PAGE})
    before = tab_state(m)
    m.command("Addon:Install", {"path": str(browser.xpi), "temporary": False, "allowPrivateBrowsing": True})
    browser.wait_for_socket()  # the add-on runs: from here it looks for the tab
    seen = watch(m)
    check("installing leaves a loaded release page as it was",
          [u for u, _ in before] == [RELEASE_PAGE] and all([u for u, _ in tabs] == [RELEASE_PAGE] for tabs in seen),
          {"before install": before, f"seen over {SETTLE} s": seen})
    # Installed as a user would (no test helper answering for them): both builds start with a list.
    status = json.loads(McpClient(browser.socket).call("status"))
    check("the full build starts with an empty list of sites, like the signed build",
          status.get("siteAccess") == "listed", status.get("siteAccess"))
finally:
    browser.stop()
    browser.cleanup()


# A local proxy stands in for the release site: an .xpi address gets XPI_BYTES, any other address
# a small page, so a "loaded release page" needs no network. github.com is on the browser's HSTS
# preload list, which would turn http into https past the proxy, hence the pref.
# The proxy is also given the browser's https, and refuses it. LibreWolf's policy fetches uBlock
# Origin from addons.mozilla.org into every new profile, and while that loads its filter lists
# from the internet it holds page requests: the .xpi link then waits about 7 s, and now and then
# longer than this test does. With https refused it is never installed, and these cases use no
# network at all.
XPI_BYTES = (ROOT / "dist/foxreach.xpi").read_bytes()
RELEASE_LINK = "http://nilltadios.github.io/foxreach/foxreach-0.4.0-signed.xpi"
LOCAL_RELEASE_PAGE = "http://github.com/nilltadios/foxreach/releases/tag/v0.4.1"


proxied = []  # what the browser asked the stand-in release site for, shown when a check fails


class Release(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_CONNECT(self):  # https
        self.send_error(403)

    def do_GET(self):
        proxied.append(self.path)
        xpi = self.path.split("?")[0].endswith(".xpi")
        body = XPI_BYTES if xpi else b"<!doctype html><title>Release v0.4.1</title><p>foxreach v0.4.1"
        self.send_response(200)
        self.send_header("Content-Type", "application/x-xpinstall" if xpi else "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def use_proxy(browser, port):
    with open(browser.profile / "user.js", "a") as prefs:
        prefs.write(
            'user_pref("network.proxy.type", 1);\n'
            'user_pref("network.proxy.http", "127.0.0.1");\n'
            f'user_pref("network.proxy.http_port", {port});\n'
            'user_pref("network.proxy.ssl", "127.0.0.1");\n'
            f'user_pref("network.proxy.ssl_port", {port});\n'
            'user_pref("network.proxy.no_proxies_on", "");\n'
            'user_pref("dom.security.https_only_mode", false);\n'
            'user_pref("dom.security.https_first", false);\n'
            'user_pref("network.stricttransportsecurity.preloadlist", false);\n'
        )


def wait_tabs(m, ok, seconds=15):
    tabs = tab_state(m)
    for _ in range(int(seconds * 4)):
        if ok(tabs):
            break
        time.sleep(0.25)
        tabs = tab_state(m)
    return tabs


proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Release)
threading.Thread(target=proxy.serve_forever, daemon=True).start()


# 2. A real install from the link, as the foxreach app and the README's link do it. The browser then
# leaves a tab that never loaded anything: its address is about:blank, and only its label (the
# add-on API's title) and the address bar still show the .xpi address. Once as is, once with "Allow
# extension to run in private windows" ticked in the install prompt (Tor Browser needs it), which
# makes the browser restart the add-on just after it is installed.
for private in (False, True):
    how = "with private windows ticked: " if private else ""
    browser = Browser(ROOT / "dist/foxreach.xpi")
    try:
        use_proxy(browser, proxy.server_address[1])
        browser.start(marionette=True)
        m = Marionette()
        m.command("WebDriver:NewSession", {})
        # Opened as from outside the browser (the app runs `firefox <link>`): trusted new tabs, in
        # front, where the browser shows its install prompt.
        chrome_run(m, f"openTrustedLinkIn({LOCAL_RELEASE_PAGE!r}, 'tab', {{ inBackground: false }}); return 1;")
        wait_tabs(m, lambda tabs: any(u == LOCAL_RELEASE_PAGE for u, _ in tabs))
        proxied.clear()
        chrome_run(m, f"openTrustedLinkIn({RELEASE_LINK!r}, 'tab', {{ inBackground: false }}); return 1;")
        added = False
        for _ in range(120):
            added = chrome_run(m, 'const n = document.getElementById("addon-webext-permissions-notification");'
                               "if (!n || n.hidden || !n.button) return false;"
                               "const box = [...n.querySelectorAll('moz-checkbox, checkbox')].find((c) => /private/i.test(c.getAttribute('label') ?? ''));"
                               "if (arguments[0] && !box) return 'no private-windows box';"
                               "if (arguments[0] && !box.checked) box.click();"
                               "n.button.click(); return true;", private)
            if added:
                break
            time.sleep(0.25)
        check(f"{how}from the link, the browser asks to add foxreach, and Add is pressed", added is True,
              added or chrome_run(m, "return (async () => { const { AddonManager } = ChromeUtils.importESModule('resource://gre/modules/AddonManager.sys.mjs');"
                                  "return [gBrowser.selectedTab.label, PopupNotifications._currentNotifications.map((n) => n.id),"
                                  "(await AddonManager.getAllInstalls()).map((i) => [i.state, i.error, i.sourceURI?.spec])]; })();")
              + [list(proxied)])
        label = RELEASE_LINK.split("://")[1]
        left = wait_tabs(m, lambda tabs: ["about:blank", label] in tabs, 5)
        blank = left.index(["about:blank", label]) if ["about:blank", label] in left else -1
        tabs = wait_tabs(m, lambda tabs: welcome_tabs(tabs), 20)
        check(f"{how}the tab the install left behind became the welcome page",
              blank >= 0 and len(tabs) == len(left) and welcome_tabs(tabs[blank:blank + 1]),
              {"just after Add": left, "after 20 s": tabs})
        seen = watch(m)
        check(f"{how}the loaded release page next to it is left as it was",
              all(LOCAL_RELEASE_PAGE in [u for u, _ in tabs] and len(welcome_tabs(tabs)) <= 1 for tabs in seen), seen)
        if not private and added is True:  # the page itself, once
            url = welcome_url(m)
            m.command("Marionette:SetContext", {"value": "content"})
            m.command("WebDriver:Navigate", {"url": url})  # the welcome page itself, to read and photograph it
            text = m.command("WebDriver:ExecuteScript", {"script": "return document.body.innerText", "args": []})["value"]
            check("it says foxreach is installed and shows the prompt", "foxreach is installed" in text and "small foxreach window" in text and "Always allow" in text, text[:200])
            if shot:
                time.sleep(1.5)  # the arrival animation
                for scheme, value in (("light", 1), ("dark", 0)):
                    chrome_run(m, f"Services.prefs.setIntPref('layout.css.prefers-color-scheme.content-override', {value})")
                    m.command("Marionette:SetContext", {"value": "content"})
                    m.command("WebDriver:Navigate", {"url": url})
                    time.sleep(1.8)
                    png = m.command("WebDriver:TakeScreenshot", {"full": False})["value"]
                    pathlib.Path(shot.replace(".png", f"-{scheme}.png")).write_bytes(base64.b64decode(png))
    finally:
        browser.stop()
        browser.cleanup()


# 3. Updates, from released versions built from their git tags with a local update_url, to this
# checkout as a higher version. The update arrives through the browser's own background check, as
# it does for users while they work.
ADDON_VERSION = """
return (async () => {
const { AddonManager } = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
return (await AddonManager.getAddonByID(arguments[0]))?.version ?? null;
})();
"""
# What the add-on keeps in storage.local under a key, read from the browser side.
STORED = """
const [id, key] = arguments;
return (async () => {
const { ExtensionStorageIDB } = ChromeUtils.importESModule("resource://gre/modules/ExtensionStorageIDB.sys.mjs");
const db = await ExtensionStorageIDB.open(ExtensionStorageIDB.getStoragePrincipal(WebExtensionPolicy.getByID(id).extension));
return JSON.stringify((await db.get([key]))[key] ?? null);
})();
"""
work = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-welcome-update-"))
newer = f"{build_install.version()}.1"
new_xpi = build_install.build(work / f"foxreach-{newer}.xpi", True, version=newer, update_url=f"{BASE}/updates.json")
from release import updates_entry  # noqa: E402
(work / "updates.json").write_text(json.dumps({"addons": {ADDON_ID: {"updates": [
    updates_entry(newer, f"{BASE}/{new_xpi.name}", new_xpi),
]}}}))
server = serve(work)
# What the link gives here is not an add-on, so the browser installs nothing from it: the version
# changes only through the update, and the tab is left as a click on the link leaves it.
XPI_BYTES = b"not an add-on"
try:
    for tag in ("v0.4.1", "v0.3.2"):
        src = work / tag
        src.mkdir()
        archive = subprocess.run(["git", "-C", str(ROOT), "archive", tag, "extension"], check=True, capture_output=True).stdout
        subprocess.run(["tar", "-x", "-C", str(src)], input=archive, check=True)
        here = build_install.EXT
        build_install.EXT = src / "extension"
        try:
            old_xpi = build_install.build(work / f"old-{tag}.xpi", True, update_url=f"{BASE}/updates.json")
        finally:
            build_install.EXT = here
        browser = Browser(old_xpi)
        try:
            use_proxy(browser, proxy.server_address[1])
            with open(browser.profile / "user.js", "a") as f:
                f.write('user_pref("extensions.checkUpdateSecurity", false);\n')
            browser.start(marionette=True)
            m = Marionette()
            m.command("WebDriver:NewSession", {})
            m.command("WebDriver:SetTimeouts", {"script": 90000})
            m.command("Addon:Install", {"path": str(old_xpi), "temporary": False, "allowPrivateBrowsing": True})
            # The old version's own welcome on install must be over first: it starts once the
            # add-on runs, which in a new profile can be well after the install returns, and 0.4.1
            # records the version it welcomed once it is done.
            browser.wait_for_socket()
            time.sleep(SETTLE)
            welcomed = None
            for _ in range(60 if tag == "v0.4.1" else 1):
                welcomed = json.loads(chrome_run(m, STORED, ADDON_ID, "welcomed"))
                if welcomed:
                    break
                time.sleep(0.5)
            check(f"{tag}: the old version has {'recorded' if tag == 'v0.4.1' else 'no record of'} the version it welcomed",
                  welcomed == (tag[1:] if tag == "v0.4.1" else None), welcomed)
            chrome_run(m, f"openTrustedLinkIn({LOCAL_RELEASE_PAGE!r}, 'tab', {{ inBackground: false }}); return 1;")
            wait_tabs(m, lambda tabs: any(u == LOCAL_RELEASE_PAGE for u, _ in tabs))
            # The tab a click on the .xpi link leaves: blank, labelled with the link.
            chrome_run(m, f"openTrustedLinkIn({RELEASE_LINK!r}, 'tab', {{ inBackground: false }}); return 1;")
            label = RELEASE_LINK.split("://")[1]
            wait_tabs(m, lambda tabs: [u for u, _ in tabs if u == LOCAL_RELEASE_PAGE]
                      and ["about:blank", label] in tabs, 10)
            time.sleep(2)
            before = tab_state(m)
            installed = chrome_run(m, ADDON_VERSION, ADDON_ID)
            check(f"{tag}: before the update, a loaded release page and a blank tab labelled with the .xpi link are open",
                  installed == tag[1:] and LOCAL_RELEASE_PAGE in [u for u, _ in before] and ["about:blank", label] in before
                  and not welcome_tabs(before), {"installed": installed, "tabs": before})
            after = chrome_run(m, BACKGROUND_CHECK, ADDON_ID, newer)
            check(f"{tag}: the background check installs {newer} and it runs", after and after["version"] == newer and after["active"], after)
            seen = watch(m)
            check(f"{tag}: after the update the loaded release page is unchanged",
                  all(LOCAL_RELEASE_PAGE in [u for u, _ in tabs] for tabs in seen), seen)
            check(f"{tag}: after the update the blank .xpi tab is still blank",
                  all(["about:blank", label] in tabs for tabs in seen), seen)
            check(f"{tag}: the update opened no welcome page",
                  all(not welcome_tabs(tabs) and len(tabs) == len(before) for tabs in seen),
                  {"before": before, f"seen over {SETTLE} s": seen})
        finally:
            browser.stop()
            browser.cleanup()
finally:
    server.shutdown()
    proxy.shutdown()
sys.exit(1 if fails else 0)
