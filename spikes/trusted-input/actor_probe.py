#!/usr/bin/env python3
"""Which JSWindowActor registrations can getActor() reach on a normal web page?

Run: FOXREACH_BROWSER=/path uv run --python 3.15 spikes/trusted-input/actor_probe.py
"""
import http.server, json, pathlib, sys, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "test"))
from harness import Browser
PORT = 47853
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
        self.wfile.write(b"<!doctype html><title>t</title><p>x")
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
b = Browser(None); 
try:
    b.start(marionette=True); from harness import Marionette
    m = b.marionette = Marionette(); m.command("WebDriver:NewSession", {})
    m.command("WebDriver:Navigate", {"url": f"http://127.0.0.1:{PORT}/"})
    m.command("Marionette:SetContext", {"value": "chrome"})
    js = """
    const child = {esModuleURI: "resource://gre/actors/AutoCompleteChild.sys.mjs"};
    const parent = {esModuleURI: "resource://gre/actors/AutoCompleteParent.sys.mjs"};
    const bc = gBrowser.selectedBrowser.browsingContext;
    const out = {uri: bc.currentWindowGlobal.documentURI.spec, remote: bc.currentRemoteType, group: bc.messageManagerGroup, isContent: bc.isContent, grp: gBrowser.selectedBrowser.getAttribute("messagemanagergroup")};
    const variants = {
      childOnly: {child},
      childAll: {child, allFrames: true},
      both: {parent, child, allFrames: true},
      group: {child, allFrames: true, messageManagerGroups: ["browsers"]},
      remote: {child, allFrames: true, remoteTypes: ["web", "webIsolated"]},
      matches: {child, allFrames: true, matches: ["*://*/*"]},
      chrome: {child, allFrames: true, includeChrome: true},
    };
    for (const [n, v] of Object.entries(variants)) {
      const name = "Probe" + n[0].toUpperCase() + n.slice(1);
      try {
        ChromeUtils.registerWindowActor(name, v);
        try { bc.currentWindowGlobal.getActor(name); out[n] = "ok"; } catch (e) { out[n] = e.message; }
        ChromeUtils.unregisterWindowActor(name);
      } catch (e) { out[n] = "register: " + e.message; }
    }
    return out;"""
    print(json.dumps(m.command("WebDriver:ExecuteScript", {"script": js, "args": []})["value"], indent=1))
finally:
    b.cleanup(); srv.shutdown()
