#!/usr/bin/env python3
"""Does foxreach input reach focus-tracking widgets in a page without the browser's focus, without
touching anything else?

Bug (2026-11-12, GradForms faculty picker): Firefox sends no focus/blur events to a page that lacks
the browser's focus (the browser is behind another app, or the tab is in the background). MUI's
Autocomplete keeps typed text only after its own onFocus, so everything foxreach typed was cleared.
foxreach now sends focus and focusin itself, to the element the agent focused, only in the agent's
document and only while that document lacks focus.

The page here behaves like that widget: it clears typed text unless it has seen a focus event. It
also logs every focus event its window sees, so a tab the agent does not act on can show it was
left alone. Cases:
  front       agent tab in front of its window, browser behind another app
  background  agent tab in the background; the front tab (standing in for the user's) must see no
              focus event and must not gain focus

Headless browsers count as the active app, so e2e.py covers only the background-tab case
("background tab: a focus-tracking widget keeps typed text"). This needs a headful browser with
another app in front:
    setsid -f flatpak run --env=FOXREACH_SOCKET=$XDG_RUNTIME_DIR/foxreach/flatpak-test.sock \
        io.gitlab.librewolf-community --no-remote \
        --profile ~/.var/app/io.gitlab.librewolf-community/foxreach/profile
    # put another app in front, then:
    FOXREACH_SOCKET=$XDG_RUNTIME_DIR/foxreach/flatpak-test.sock \
        uv run --python 3.15 test/background_focus.py
Expected: PASS on every line and exit 0. Before the fix: value "" and no focus event.
"""
import http.server
import json
import pathlib
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test"))
from harness import McpClient  # noqa: E402

PAGE = b"""<!doctype html><meta charset=utf-8><title>focus-tracking input</title>
<input id=box autocomplete=off style="font-size:24px;margin:40px;width:300px">
<script>
const box = document.getElementById('box');
window.log = []; window.seen = []; window.focused = false;
for (const t of ['focus', 'blur']) addEventListener(t, e => seen.push(t + ':' + (e.target.id || e.target.nodeName)), true);
box.addEventListener('focus', e => { focused = true; log.push('focus:' + e.isTrusted); });
box.addEventListener('blur', e => { focused = false; log.push('blur:' + e.isTrusted); });
// Like MUI's Autocomplete: without its own focus state, every redraw resets the text.
box.addEventListener('input', () => { if (!focused) setTimeout(() => { box.value = ''; log.push('reset'); }); });
</script>"""

fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'} {name} {'' if ok else detail}")


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *a):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{server.server_port}/"
c = McpClient(name="background-focus-test", command=[
    str(pathlib.Path.home() / ".bun/bin/bun"), str(ROOT / "bridge/mcp.mjs")])
js = lambda tab, code: json.loads(c.call("javascript", tabId=tab, code=code))  # noqa: E731
tab_of = lambda text: int(text.split(":")[0].split()[-1])  # noqa: E731
STATE = "await new Promise(r => setTimeout(r, 300)); ({value: box.value, log, seen, hasFocus: document.hasFocus()})"


def type_into(tab):
    c.call("click", tabId=tab, x=150, y=60)
    c.call("type_text", tabId=tab, text="Kim")
    return js(tab, STATE)


try:
    front = tab_of(c.call("tabs_create", url=url, active=True))
    check("front: the browser is behind another app", js(front, "document.hasFocus()") is False,
          "put another app in front first")
    s = type_into(front)
    check("front: the widget keeps the typed text", s["value"] == "Kim", s)
    check("front: it saw one trusted focus event", s["log"] == ["focus:true"], s)
    check("front: the document still lacks real focus (the window was not activated)", s["hasFocus"] is False, s)

    back = tab_of(c.call("tabs_create", url=url))
    js(front, "seen.length = 0; 1")
    s = type_into(back)
    check("background: the widget keeps the typed text", s["value"] == "Kim", s)
    check("background: it saw one trusted focus event", s["log"] == ["focus:true"], s)
    f = js(front, STATE)
    check("background: the tab in front saw no focus or blur event", f["seen"] == [], f)
    check("background: the tab in front did not gain focus", f["hasFocus"] is False, f)

    # Moving the agent's focus elsewhere tells the old element it lost it.
    js(back, "document.body.insertAdjacentHTML('beforeend', '<input id=other style=\"margin:40px\">'); 1")
    ref = c.call("find", tabId=back, query="other").split("[")[1].split("]")[0]
    c.call("click", tabId=back, ref=ref)
    s = js(back, STATE)
    check("background: focus moving on sends blur to the old element", s["log"] == ["focus:true", "blur:true"], s)
    for t in (front, back):
        c.call("tabs_close", tabId=t)
finally:
    c.proc.terminate()
    server.shutdown()

print("failed:", fails)
sys.exit(1 if fails else 0)
