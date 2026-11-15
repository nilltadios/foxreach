#!/usr/bin/env python3
"""After installing from a release link, the leftover tab becomes foxreach's welcome page.

Run:      FOXREACH_BROWSER=<browser> uv run --python 3.15 test/welcome_tab.py [--shot FILE]
Expected: every line PASS, exit 0; --shot saves a PNG of the welcome page.
How:      a throwaway headless browser and profile (test/harness.py). A tab on a foxreach release
          address stands in for the one the browser leaves after installing from the .xpi link;
          then the add-on is installed and the tab is read back.
"""
import base64
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness import ROOT, Browser, Marionette  # noqa: E402

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
    check("it says foxreach is installed and shows the prompt", "foxreach is installed" in text and "Always allow agents with this name" in text, text[:200])
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
sys.exit(1 if fails else 0)
