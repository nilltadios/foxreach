#!/usr/bin/env python3
"""The no-privilege build: must load with no Experiment, and must say input is untrusted.

Run: uv run --python 3.15 test/untrusted.py    Expected: all PASS, exit 0.
Signature checks are off here only because unsigned add-ons need that outside AMO.
"""
import os
if os.name == "nt":
    raise SystemExit("This test runs on Linux and macOS only: Windows ignores its throwaway HOME, so it "
                     "would act on the real foxreach setup. The Windows test is test/windows_hosts.py.")

import http.server, json, pathlib, subprocess, sys, threading, time, zipfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, Browser, McpClient, ToolError
PORT = 47854
PAGE = b"""<!doctype html><title>U</title><script>window.events=[];addEventListener('click',e=>events.push(e.isTrusted),true)</script>
<button id=b style="position:fixed;left:20px;top:20px;width:100px;height:30px">Go</button><input id=i style="position:fixed;left:20px;top:80px">"""
seen = []
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        seen.append(self.path); self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(PAGE)
subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
b = Browser(ROOT / "dist/foxreach-untrusted.xpi"); fails = 0
def check(name, ok, detail=""):
    global fails; fails += not ok; print("PASS" if ok else "FAIL", name, "" if ok else detail)
try:
    (b.profile / "user.js").write_text(open(b.profile / "user.js").read().replace('user_pref("extensions.experiments.enabled", true);\n', ''))
    b.start(marionette=True); b.install(); b.wait_for_socket()
    m = McpClient(b.socket)
    check("reports untrusted input", json.loads(m.call("status"))["trustedInput"] is False)
    with zipfile.ZipFile(ROOT / "dist/foxreach-untrusted.xpi") as z:
        gecko = json.loads(z.read("manifest.json"))["browser_specific_settings"]["gecko"]
    check("has no update_url, so the full build never replaces it", "update_url" not in gecko, gecko)
    # This build holds no site until the user grants one: nothing to read, and no pictures.
    url = f"http://127.0.0.1:{PORT}/"
    def refused(call):
        try:
            return f"no error: {call()}"
        except ToolError as e:
            return str(e)
    mar = b.marionette
    held_now = lambda: mar.command("WebDriver:ExecuteAsyncScript", {  # noqa: E731
        "script": "browser.permissions.getAll().then((p) => arguments[0](p.origins))", "args": [], "scriptTimeout": 8000})["value"]
    handles = lambda: mar.command("WebDriver:GetWindowHandles", {})  # noqa: E731  (a bare list, not {"value": ...})
    def chrome(script):
        mar.command("Marionette:SetContext", {"value": "chrome"})
        try:
            return mar.command("WebDriver:ExecuteScript", {"script": script, "args": []})["value"]
        finally:
            mar.command("Marionette:SetContext", {"value": "content"})
    def real_prompts(on):
        """The browser's own permission prompt, which the profile turns off (harness.py). With it off
        the browser stores every grant asked for; with it on, not one it already covers (issue #12)."""
        chrome(f'Services.prefs.setBoolPref("extensions.webextOptionalPermissionPrompts", {str(on).lower()}); return 1;')
    def allow_browser_prompt(label):
        """Press Allow in the browser's permission prompt, as the user would."""
        deadline = time.time() + 10
        while time.time() < deadline:
            done = chrome('const n = document.getElementById("addon-webext-permissions-notification");'
                          'if (!n || n.hidden || !n.button) return false; n.button.click(); return true;')
            if done:
                break
            time.sleep(0.3)
        check(f"{label}: the browser asks, and Allow is pressed", done)
    def grant_in_tab(host, label, cancel=False, prompt=False):
        """The toolbar panel does not ask the browser itself: it opens a tab that does (popup.js,
        allowSite, listedOnly). Find that tab, press Continue (or Cancel) with a real click, and come
        back to the panel. host: one site, several joined by commas, or "*"."""
        import urllib.parse
        panel = mar.command("WebDriver:GetWindowHandle", {})["value"]
        want = f"popup.html?grant={urllib.parse.quote(host, safe="!'()*")}"  # as encodeURIComponent"
        found, deadline = None, time.time() + 10
        while not found and time.time() < deadline:
            for h in handles():
                mar.command("WebDriver:SwitchToWindow", {"handle": h, "focus": True})
                if mar.command("WebDriver:GetCurrentURL", {})["value"].endswith(want):
                    found = h
                    break
            if not found:
                mar.command("WebDriver:SwitchToWindow", {"handle": panel, "focus": True}); time.sleep(0.3)
        check(f"{label}: the panel opens a tab to ask the browser", found is not None, want)
        if not found:
            return
        time.sleep(0.5)  # the tab draws its card after it loads
        before = held_now()
        check(f"{label}: nothing is granted before Continue", "<all_urls>" not in before if host == "*"
              else not any(f"//{h}/" in o for h in host.split(",") for o in before), before)
        # Continue, with a real click: the browser takes the request only from one.
        b.click("#grant-card .action.ghost" if cancel else "#grant-card .action.primary")
        if prompt:
            allow_browser_prompt(label)
        deadline = time.time() + 10
        while found in handles() and time.time() < deadline:
            time.sleep(0.3)
        check(f"{label}: the tab closes itself once {'cancelled' if cancel else 'granted'}", found not in handles())
        mar.command("WebDriver:SwitchToWindow", {"handle": panel, "focus": True})
    with zipfile.ZipFile(ROOT / "dist/foxreach-untrusted.xpi") as z:
        manifest = json.loads(z.read("manifest.json"))
    check("asks for no site at install", "<all_urls>" not in manifest["permissions"] and manifest["optional_permissions"] == ["<all_urls>"], manifest["permissions"])
    check("starts with a list of sites, which is empty", json.loads(m.call("status"))["siteAccess"] == "listed")
    check("a site not granted is refused", "not allowed on 127.0.0.1 yet" in refused(lambda: m.call("tabs_create", url=url)))
    run = b.open_popup(); time.sleep(2.5)
    b.click("#site-requests button")  # Allow
    grant_in_tab("127.0.0.1", "allowing the request")
    held = run("browser.permissions.getAll().then((p) => arguments[0](p.origins))")
    check("allowing the request grants that site only", held == ["*://127.0.0.1/*", "*://*.127.0.0.1/*"], held)
    tab = int(m.call("tabs_create", url=url).split()[1].rstrip(":"))
    check("the granted site can be read", "Go" in m.call("read_page", tabId=tab))
    check("a picture explains that it needs every site", "Every site" in refused(lambda: m.image(tabId=tab)))
    check("a click still works without the picture", "Clicked" in m.call("click", tabId=tab, x=50, y=30))
    run("document.getElementById('site').value = 'example.org'; arguments[0](1)")
    b.click("#access-all"); grant_in_tab("*", "choosing every site")
    check("choosing every site grants every site", "<all_urls>" in run("browser.permissions.getAll().then((p) => arguments[0](p.origins))"))
    b.click("#add-site button"); time.sleep(1)  # every site is granted: no tab, only the list changes
    held_all = lambda: run("browser.permissions.getAll().then((p) => arguments[0](p.origins))")  # noqa: E731
    listed = lambda: run("browser.runtime.sendMessage({popup: 'state'}).then((s) => arguments[0](s.allowedHosts))")  # noqa: E731
    # A site added while every site was granted is covered by that grant and holds none of its own,
    # so "Only these sites" asks the browser for it once every site is withdrawn (issue #12).
    # Cancel first: the site stays listed, waiting for the browser, with a Grant button.
    b.click("#access-listed"); grant_in_tab("example.org", "back to the list", cancel=True)
    held = held_all()
    check("back to the list, every site is withdrawn and a site granted on its own keeps its grant",
          "<all_urls>" not in held and "*://127.0.0.1/*" in held, held)
    check("a site cancelled there stays listed, waiting for the browser's grant", {"host": "example.org", "granted": False} in listed(), listed())
    check("...with a Grant button for it", run(
        "const g = [...document.querySelectorAll('#sites button')].filter((x) => x.textContent === 'Grant');"
        "if (g.length === 1) g[0].id = 'grant-listed'; arguments[0](g.length)") == 1)
    b.click("#grant-listed"); grant_in_tab("example.org", "granting a listed site")
    held = held_all()
    check("granting a listed site grants it, and only it", "*://example.org/*" in held and "<all_urls>" not in held, held)
    # Two sites added under every site, this time with the browser's real prompts, which is how the
    # grant got lost: the browser stores no grant for a site it already covers. One tab asks for
    # both; example.org has its own grant now and is not asked again.
    b.click("#access-all"); grant_in_tab("*", "every site, a second time")
    real_prompts(True)
    for site in ("example.net", "example.com"):
        run(f"document.getElementById('site').value = '{site}'; arguments[0](1)")
        b.click("#add-site button"); time.sleep(1)
    held = held_all()
    check("with real prompts, a site added under every site gets no grant of its own", not any("example.net" in o for o in held), held)
    b.click("#access-listed"); grant_in_tab("example.net,example.com", "back to the list with two such sites", prompt=True)
    real_prompts(False)
    held = held_all()
    check("both are granted, every site is withdrawn, and the others keep their grant",
          all(f"*://{h}/*" in held for h in ("example.net", "example.com", "example.org", "127.0.0.1")) and "<all_urls>" not in held, held)
    check("every listed site is usable", all(s["granted"] for s in listed()), listed())
    b.click("#access-all"); grant_in_tab("*", "every site again")  # the rest of this test is about input, with every site
    m.call("javascript", tabId=tab, code="window.events.length = 0")
    check("read_page works", "Go" in m.call("read_page", tabId=tab))
    out = m.call("click", tabId=tab, x=50, y=30)
    check("click warns it is untrusted", "untrusted" in out, out)
    check("the page sees isTrusted=false", m.call("javascript", tabId=tab, code="window.events.length") == "1", m.call("javascript", tabId=tab, code="window.events.length"))
    check("agent scripts run in the page's world, not the add-on's",
          m.call("javascript", tabId=tab, code="typeof browser + ' ' + typeof wrappedJSObject") == '"undefined undefined"',
          m.call("javascript", tabId=tab, code="typeof browser + ' ' + typeof wrappedJSObject"))
    check("a script's last value comes back, promises awaited",
          m.call("javascript", tabId=tab, code="const a = 20; Promise.resolve(a + 1)") == "21")
    check("screenshot works", m.image(tabId=tab)[1] == "image/jpeg")
    try:
        m.call("read_console", tabId=tab); check("read_console explains itself", False)
    except ToolError as e:
        check("read_console explains itself", "trusted" in str(e), str(e))
    # Agent scripts must not carry the add-on's own right to read any site, and a blocked site must
    # not even be asked.
    mar = b.marionette
    mar.command("Marionette:SetContext", {"value": "chrome"})
    uuid = mar.command("WebDriver:ExecuteScript", {"script": 'return JSON.parse(Services.prefs.getStringPref("extensions.webextensions.uuids"))["foxreach@foxreach.local"];', "args": []})["value"]
    mar.command("Marionette:SetContext", {"value": "content"})
    mar.command("WebDriver:Navigate", {"url": f"moz-extension://{uuid}/popup/popup.html"})
    probe = f"fetch('http://localhost:{PORT}/?PROBE=1').then((r) => r.text()).then((t) => 'read ' + t.length, (e) => 'refused: ' + e)"
    before = m.call("javascript", tabId=tab, code=probe.replace("PROBE", "openprobe"))
    check("an agent script reads another site only as the page could: here, not at all",
          before.startswith('"refused') and any("openprobe" in p for p in seen), before)
    mar.command("WebDriver:ExecuteAsyncScript", {"script": "browser.runtime.sendMessage({popup: 'setBlocked', value: 'localhost'}).then(() => arguments[0](1))", "args": [], "scriptTimeout": 8000})
    after = m.call("javascript", tabId=tab, code=probe.replace("PROBE", "blockprobe"))
    check("a request to a blocked site is never sent", after.startswith('"refused') and not any("blockprobe" in p for p in seen), after)
    # This build cannot read a tab's history beforehand, so it checks where back led and steps out
    # again: here to a site that left the list after the tab was on it.
    say = lambda msg: mar.command("WebDriver:ExecuteAsyncScript", {"script": f"browser.runtime.sendMessage({json.dumps(msg)}).then(() => arguments[0](1))", "args": [], "scriptTimeout": 8000})  # noqa: E731
    say({"popup": "setBlocked", "value": ""})
    m.call("navigate", tabId=tab, url=f"http://localhost:{PORT}/")
    m.call("navigate", tabId=tab, url=url)
    say({"popup": "listedOnly"})
    check("back into a site no longer on the list is refused", "not allowed on localhost yet" in refused(lambda: m.call("navigate", tabId=tab, url="back")))
    listed_tabs = m.call("tabs_list")
    check("...and the tab is back where it was", f"<{url}>" in listed_tabs, listed_tabs)
    m.close()
finally:
    b.cleanup(); srv.shutdown()
print("failed:", fails); sys.exit(1 if fails else 0)
