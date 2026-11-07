#!/usr/bin/env python3
"""The no-privilege build: must load with no Experiment, and must say input is untrusted.

Run: uv run --python 3.15 test/untrusted.py    Expected: all PASS, exit 0.
Signature checks are off here only because unsigned add-ons need that outside AMO.
"""
import http.server, json, pathlib, subprocess, sys, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, Browser, McpClient, ToolError
PORT = 47854
PAGE = b"""<!doctype html><title>U</title><script>window.events=[];addEventListener('click',e=>events.push(e.isTrusted),true)</script>
<button id=b style="position:fixed;left:20px;top:20px;width:100px;height:30px">Go</button><input id=i style="position:fixed;left:20px;top:80px">"""
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(PAGE)
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
    tab = int(m.call("tabs_create", url=f"http://127.0.0.1:{PORT}/").split()[1].rstrip(":"))
    check("read_page works", "Go" in m.call("read_page", tabId=tab))
    out = m.call("click", tabId=tab, x=50, y=30)
    check("click warns it is untrusted", "untrusted" in out, out)
    check("the page sees isTrusted=false", m.call("javascript", tabId=tab, code="window.wrappedJSObject.events.length") == "1", m.call("javascript", tabId=tab, code="window.wrappedJSObject.events.length"))
    check("screenshot works", m.image(tabId=tab)[1] == "image/jpeg")
    try:
        m.call("read_console", tabId=tab); check("read_console explains itself", False)
    except ToolError as e:
        check("read_console explains itself", "trusted" in str(e), str(e))
    m.close()
finally:
    b.cleanup(); srv.shutdown()
print("failed:", fails); sys.exit(1 if fails else 0)
