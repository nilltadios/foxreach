#!/usr/bin/env python3
"""The no-privilege build: must load with no Experiment, and must say input is untrusted.

Run: uv run --python 3.15 test/untrusted.py    Expected: all PASS, exit 0.
Signature checks are off here only because unsigned add-ons need that outside AMO.
"""
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
    with zipfile.ZipFile(ROOT / "dist/foxreach-untrusted.xpi") as z:
        manifest = json.loads(z.read("manifest.json"))
    check("asks for no site at install", "<all_urls>" not in manifest["permissions"] and manifest["optional_permissions"] == ["<all_urls>"], manifest["permissions"])
    check("starts with a list of sites, which is empty", json.loads(m.call("status"))["siteAccess"] == "listed")
    check("a site not granted is refused", "not allowed on 127.0.0.1 yet" in refused(lambda: m.call("tabs_create", url=url)))
    run = b.open_popup(); time.sleep(2.5)
    b.click("#site-requests button")  # Allow, with a real click: the browser takes the request only from one
    time.sleep(1)
    held = run("browser.permissions.getAll().then((p) => arguments[0](p.origins))")
    check("allowing the request grants that site only", held == ["*://127.0.0.1/*", "*://*.127.0.0.1/*"], held)
    tab = int(m.call("tabs_create", url=url).split()[1].rstrip(":"))
    check("the granted site can be read", "Go" in m.call("read_page", tabId=tab))
    check("a picture explains that it needs every site", "Every site" in refused(lambda: m.image(tabId=tab)))
    check("a click still works without the picture", "Clicked" in m.call("click", tabId=tab, x=50, y=30))
    run("document.getElementById('site').value = 'example.org'; arguments[0](1)")
    b.click("#access-all"); time.sleep(1)
    check("choosing every site grants every site", "<all_urls>" in run("browser.permissions.getAll().then((p) => arguments[0](p.origins))"))
    b.click("#add-site button"); time.sleep(1)
    b.click("#access-listed"); time.sleep(1)
    held = run("browser.permissions.getAll().then((p) => arguments[0](p.origins))")
    check("back to the list, the browser's grant is narrowed to the list", "<all_urls>" not in held and "*://example.org/*" in held and "*://127.0.0.1/*" in held, held)
    b.click("#access-all"); time.sleep(1)  # the rest of this test is about input, with every site
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
    m.close()
finally:
    b.cleanup(); srv.shutdown()
print("failed:", fails); sys.exit(1 if fails else 0)
