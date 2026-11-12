#!/usr/bin/env python3
"""End-to-end test: a real MCP client, through the bridge, to a real Firefox-based browser.

Run:      uv run --python 3.15 test/e2e.py [--visible] [--only NAME]
Browser:  FOXREACH_BROWSER (a path or a command), default the only Firefox-based browser installed
Expected: every line PASS, exit 0. The browser is a throwaway instance on a temporary profile;
          it is restarted without Marionette before the tool tests, as in daily use.
"""
import http.server
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import pathlib
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, RUNTIME, Browser, McpClient, ToolError  # noqa: E402

PORT = 47852
MAIN = f"http://127.0.0.1:{PORT}"
OTHER_SITE = f"http://localhost:{PORT}"  # another site, so Fission gives it its own process

RECORDER = """<script>
window.events = [];
for (const t of ["mousedown","mouseup","click","dblclick","contextmenu","mousemove","keydown","keyup","input","wheel"])
  addEventListener(t, e => events.push({type: e.type, trusted: e.isTrusted, target: e.target.id, button: e.button,
                                        ctrl: e.ctrlKey, key: e.key, detail: e.detail}), true);
</script>"""

PAGES = {
    "/main": f"""<!doctype html><meta charset=utf-8><title>Main page</title>{RECORDER}
<style>body{{margin:0}} #btn{{position:fixed;left:40px;top:40px;width:160px;height:40px}}
#cover{{position:fixed;left:40px;top:90px;width:160px;height:40px;background:#ccc}}
#under{{position:fixed;left:40px;top:90px;width:160px;height:40px}}</style>
<h1>Heading one</h1>
<button id=btn onclick="this.dataset.n=(+this.dataset.n||0)+1">Press me</button>
<button id=under>Hidden under</button><div id=cover></div>
<div style="margin-top:160px">
<label for=name>Your name</label> <input id=name placeholder=Name>
<input id=pw type=password value=secret123>
<textarea id=area></textarea>
<select id=sel><option value=a>Apple</option><option value=b>Banana</option></select>
<input id=chk type=checkbox aria-label="Agree">
<input id=file type=file multiple>
<div id=ce contenteditable=true>editable</div>
<a id=lnk href="/second">Go to second</a>
<a id=pop href="/second" target=_blank>Open popup</a>
<div id=host></div>
<input id=range type=range min=0 max=100 value=0 style="width:200px">
<p id=visible>Some visible paragraph text.</p>
<div style="display:none"><button id=hidden>Hidden button</button></div>
<input id=locked aria-label="Locked field" onkeydown="event.preventDefault()">
</div>
<script>
document.getElementById('host').attachShadow({{mode:'closed'}}).innerHTML='<button id=inshadow>Shadow button</button>';
console.log('hello from page', {{a:1}});
console.error('page console error');
setTimeout(()=>{{ throw new Error('uncaught boom'); }},0);
fetch('/api/data?x=1');
</script>""",
    "/second": "<!doctype html><title>Second page</title><h1>Second</h1><p>Arrived.</p>",
    "/frames": f"""<!doctype html><meta charset=utf-8><title>Frames</title><style>body{{margin:0}}
iframe{{position:absolute;left:0;top:0;width:500px;height:300px;border:0}}</style>
<iframe id=f src="{OTHER_SITE}/inner"></iframe>""",
    "/inner": f"""<!doctype html><meta charset=utf-8><title>Inner</title>{RECORDER}<style>body{{margin:0}}
#ib{{position:absolute;left:50px;top:50px;width:150px;height:40px}}
#ii{{position:absolute;left:50px;top:120px;width:150px;height:30px}}</style>
<button id=ib>Inner button</button><input id=ii aria-label="Inner input">
<script>console.log('inner frame says hi')</script>""",
    "/scroll": f"""<!doctype html><title>Scroll</title>{RECORDER}<body style="margin:0;height:5000px">
<div style="position:absolute;top:3000px"><button id=far>Far button</button></div>
<div id=box style="position:fixed;left:400px;top:0;width:200px;height:200px;background:#0a0"></div>""",
    "/wheelapp": f"""<!doctype html><title>Wheel app</title>{RECORDER}<body style="margin:0;height:5000px">
<script>window.zoom=0;addEventListener('wheel',e=>{{e.preventDefault();window.zoom++}},{{passive:false}});</script>""",
    "/dnd": """<!doctype html><title>DnD</title><body style="margin:0">
<div id=src draggable=true style="position:absolute;left:20px;top:20px;width:80px;height:80px;background:#c60">drag me</div>
<div id=dst style="position:absolute;left:300px;top:20px;width:150px;height:150px;background:#ddd">target</div>
<script>window.log=[];
src.addEventListener('dragstart',e=>{log.push('dragstart:'+e.isTrusted);e.dataTransfer.setData('text/plain','payload')});
dst.addEventListener('dragover',e=>e.preventDefault());
dst.addEventListener('drop',e=>{e.preventDefault();log.push('drop:'+e.isTrusted+':'+e.dataTransfer.getData('text/plain'));dst.textContent='dropped'});</script>""",
    "/drop": """<!doctype html><title>Drop zone</title><body style="margin:0">
<div id=zone style="position:absolute;left:20px;top:20px;width:300px;height:200px;background:#eef">Drop files here</div>
<script>window.got=[];zone.addEventListener('dragover',e=>e.preventDefault());
zone.addEventListener('drop',async e=>{e.preventDefault();for(const f of e.dataTransfer.files)got.push([f.name,f.type,f.size])});</script>""",
    "/slowstyle": """<!doctype html><title>Slow style</title><link rel=stylesheet href="/slow.css">
<body style="margin:0"><p id=ready>Text arrives first</p>""",
    "/color": """<!doctype html><title>Color</title><body style="margin:0;background:#ff0000">
<div style="position:absolute;left:100px;top:100px;width:100px;height:100px;background:#00ff00"></div>""",
    "/dialogs": f"""<!doctype html><title>Dialogs</title>{RECORDER}<style>button{{display:block;height:30px;width:120px}}</style>
<button id=a onclick="alert('alert text')">alert</button>
<button id=c onclick="window.r=confirm('confirm text')">confirm</button>
<button id=p onclick="window.r=prompt('prompt text','d')">prompt</button>""",
    "/late": """<!doctype html><title>Late</title><body><script>
setTimeout(()=>{const d=document.createElement('div');d.id='late';d.textContent='now here';document.body.append(d)},700);</script>""",
    "/drag": f"""<!doctype html><title>Drag</title>{RECORDER}<body style="margin:0">
<div id=handle style="position:absolute;left:20px;top:20px;width:50px;height:50px;background:#06c"></div>
<script>let on=false;handle.onmousedown=()=>on=true;
addEventListener('mousemove',e=>{{if(on){{handle.style.left=(e.clientX-25)+'px';handle.style.top=(e.clientY-25)+'px'}}}});
addEventListener('mouseup',()=>on=false);</script>""",
    "/csp": """<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'">
<title>CSP</title><p>locked</p>""",
    "/filedrop": """<!doctype html><title>File</title><input id=f type=file>
<script nonce=x></script>""",
}

results = []
requests_seen = []


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        requests_seen.append(self.path)
        if path == "/slow.css":
            time.sleep(1.2)
            body, kind = b"html,body{background:#0000ff!important;height:100%}", "text/css"
        elif path == "/api/data":
            body, kind = b'{"ok":true}', "application/json"
        elif path in PAGES:
            body, kind = PAGES[path].encode(), "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS" if ok else "FAIL"), name, ("" if ok else f"-- {detail}"), flush=True)


def tab_id(text):
    return int(re.search(r"Tab (\d+)", text).group(1))


def ref_of(tree, needle):
    for line in tree.splitlines():
        if needle in line:
            m = re.search(r"\[((?:f\d+:)?r\d+)\]", line)
            if m:
                return m.group(1)
    raise AssertionError(f"{needle!r} not in tree:\n{tree[:1500]}")


def js(mcp, tab, code, **kw):
    text = mcp.call("javascript", tabId=tab, code=code, **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise AssertionError(f"not JSON from {code[:60]!r}: {text[:300]}") from None


def events(mcp, tab, frame=None):
    kw = {"frame_id": frame} if frame else {}
    return js(mcp, tab, "window.events", **kw)


def clear(mcp, tab, frame=None):
    kw = {"frame_id": frame} if frame else {}
    mcp.call("javascript", tabId=tab, code="window.events.length = 0; 1", **kw)


def expect_error(fn, pattern):
    try:
        fn()
    except ToolError as e:
        return re.search(pattern, str(e)) is not None
    return False


def png_size(data):
    return struct.unpack(">II", data[16:24])


# Tests. Each takes (mcp) and opens what it needs.

def t_protocol(mcp):
    names = {t["name"] for t in mcp.request("tools/list")["tools"]}
    check("tools/list has all tools", len(names) >= 25 and {"click", "read_page", "screenshot"} <= names, names)
    status = json.loads(mcp.call("status"))
    check("trusted input is active", status["trustedInput"] is True, status)
    # LibreWolf reports itself as Firefox here (librewolf.getBrowserInfo.setToFirefoxDefaults).
    check("browser reports a version", status["browser"]["version"], status)
    check("status reports matching add-on and bridge versions",
          status["version"] == status.get("bridgeVersion") and "warning" not in status, status)


def t_click_trusted(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    # Clicked immediately after load, where the spike saw presses dropped.
    out = mcp.call("click", tabId=tab, x=100, y=60)
    ev = [e for e in events(mcp, tab) if e["type"] in ("mousedown", "mouseup", "click")]
    check("click by point is trusted", ev and all(e["trusted"] for e in ev) and len(ev) == 3, f"{out} {ev}")
    check("one click means one click", js(mcp, tab, "document.getElementById('btn').dataset.n") == "1", ev)

    webdriver = js(mcp, tab, "navigator.webdriver")
    check("navigator.webdriver stays false", webdriver is False, webdriver)

    clear(mcp, tab)
    tree = mcp.call("read_page", tabId=tab)
    mcp.call("click", tabId=tab, ref=ref_of(tree, "Press me"))
    check("click by ref", js(mcp, tab, "document.getElementById('btn').dataset.n") == "2", tree[:400])

    clear(mcp, tab)
    mcp.call("click", tabId=tab, x=100, y=60, count=2)
    ev = events(mcp, tab)
    check("double click fires dblclick", any(e["type"] == "dblclick" and e["trusted"] for e in ev), ev)

    clear(mcp, tab)
    mcp.call("click", tabId=tab, x=100, y=60, button="right")
    ev = events(mcp, tab)
    check("right click fires contextmenu", any(e["type"] == "contextmenu" and e["trusted"] for e in ev), ev)
    mcp.call("press_key", tabId=tab, keys="Escape")

    clear(mcp, tab)
    mcp.call("click", tabId=tab, x=100, y=60, modifiers=["ctrl"])
    ev = [e for e in events(mcp, tab) if e["type"] == "click"]
    check("modifiers reach the event", ev and ev[0]["ctrl"], ev)

    clear(mcp, tab)
    mcp.call("hover", tabId=tab, x=120, y=60)
    ev = events(mcp, tab)
    check("hover moves the pointer", any(e["type"] == "mousemove" and e["trusted"] for e in ev), ev)

    under = mcp.call("find", tabId=tab, query="Hidden under")
    out = mcp.call("click", tabId=tab, ref=ref_of(under, "Hidden under"))
    check("covered element gives a warning", "covered by div#cover" in out, out)
    mcp.call("tabs_close", tabId=tab)


def t_keyboard(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    tree = mcp.call("read_page", tabId=tab)
    name = ref_of(tree, "Your name")
    clear(mcp, tab)
    mcp.call("type_text", tabId=tab, ref=name, text="Hi There 42!")
    ev = [e for e in events(mcp, tab) if e["type"] in ("keydown", "input")]
    check("typing is trusted", ev and all(e["trusted"] for e in ev), ev[:4])
    check("typed text arrives", js(mcp, tab, "document.getElementById('name').value") == "Hi There 42!")

    mcp.call("press_key", tabId=tab, keys="ctrl+a Delete")
    check("ctrl+a Delete clears a field", js(mcp, tab, "document.getElementById('name').value") == "")

    mcp.call("click", tabId=tab, ref=name)
    mcp.call("press_key", tabId=tab, keys="ctrl+a Delete")
    mcp.call("type_text", tabId=tab, text="héllo 日本語 🦊")
    check("non-Latin text and emoji", js(mcp, tab, "document.getElementById('name').value") == "héllo 日本語 🦊",
          js(mcp, tab, "document.getElementById('name').value"))

    mcp.call("type_text", tabId=tab, ref=ref_of(tree, "#area"), text="one\ntwo")
    check("newline in a textarea", js(mcp, tab, "document.getElementById('area').value") == "one\ntwo",
          js(mcp, tab, "document.getElementById('area').value"))

    mcp.call("click", tabId=tab, ref=ref_of(tree, "#ce"))
    mcp.call("press_key", tabId=tab, keys="ctrl+a")
    mcp.call("type_text", tabId=tab, text="rich")
    check("typing into contenteditable", js(mcp, tab, "document.getElementById('ce').textContent").strip() == "rich",
          js(mcp, tab, "document.getElementById('ce').textContent"))

    mcp.call("click", tabId=tab, ref=name)
    mcp.call("press_key", tabId=tab, keys="Tab")
    check("Tab moves focus", js(mcp, tab, "document.activeElement.id") == "pw", js(mcp, tab, "document.activeElement.id"))
    check("unknown key is an error", expect_error(lambda: mcp.call("press_key", tabId=tab, keys="ctrl+Nope"), "Unknown key"))

    # Focus reporting and the guard against keys landing outside a text field.
    out = mcp.call("type_text", tabId=tab, ref=name, text="x")
    check("type_text names the field it typed into", 'textbox "Your name"' in out, out)
    out = mcp.call("press_key", tabId=tab, keys="End")
    check("press_key names the focused element", 'textbox "Your name"' in out, out)
    # The report describes the field after the keys: it once showed the value from before them.
    mcp.call("type_text", tabId=tab, ref=name, text="stale")
    out = mcp.call("press_key", tabId=tab, keys="ctrl+a Backspace")
    check("press_key reports the value after the keys", "stale" not in out, out)
    out = mcp.call("type_text", tabId=tab, text="fresh")
    check("type_text does not call a value change a focus move", "Focus has moved" not in out, out)
    out = mcp.call("press_key", tabId=tab, keys="Tab")
    check("press_key says where focus went", "Focus is now on" in out and "#pw" in out, out)
    mcp.call("click", tabId=tab, ref=ref_of(tree, "Press me"))
    clear(mcp, tab)
    check("Delete outside a text field is refused",
          expect_error(lambda: mcp.call("press_key", tabId=tab, keys="ctrl+a Delete"), "not a text field"))
    check("typing outside a text field is refused",
          expect_error(lambda: mcp.call("type_text", tabId=tab, text="abc"), "Nothing was typed"))
    check("refused keys never reach the page", not [e for e in events(mcp, tab) if e["type"] == "keydown"],
          events(mcp, tab)[:4])
    out = mcp.call("press_key", tabId=tab, keys="Delete", allow_non_text=True)
    check("allow_non_text lets Delete through", "Pressed Delete" in out and 'button "Press me"' in out, out)
    check("Escape outside a text field still works", "Pressed Escape" in mcp.call("press_key", tabId=tab, keys="Escape"))
    out = mcp.call("type_text", tabId=tab, ref=ref_of(tree, "Locked field"), text="abc")
    check("a field that ignores keys gets a warning", "did not change" in out, out)
    mcp.call("tabs_close", tabId=tab)


def focus_guard(mcp, tab, tree, where):
    """The keyboard guard, wherever the tab is: keys land in the field and are named; outside a
    text field, Delete and typing are refused and nothing reaches the page."""
    name = ref_of(tree, "Your name")
    js(mcp, tab, "document.getElementById('name').value = ''; 1")
    out = mcp.call("type_text", tabId=tab, ref=name, text="ok")
    check(f"{where}: type_text names the field", 'textbox "Your name"' in out, out)
    check(f"{where}: the text arrives in the field", js(mcp, tab, "document.getElementById('name').value") == "ok")
    mcp.call("click", tabId=tab, ref=ref_of(tree, "Press me"))
    clear(mcp, tab)
    check(f"{where}: Delete outside a text field is refused",
          expect_error(lambda: mcp.call("press_key", tabId=tab, keys="Delete"), "not a text field"))
    check(f"{where}: typing outside a text field is refused",
          expect_error(lambda: mcp.call("type_text", tabId=tab, text="abc"), "Nothing was typed"))
    check(f"{where}: refused keys never reach the page", not [e for e in events(mcp, tab) if e["type"] == "keydown"],
          events(mcp, tab)[:4])


def t_focus_switch(mcp):
    """The user brings the agent's tab to the front (clicks it) and leaves it again: the guard
    works the same in every state, with focus in the page and in a frame."""
    other = tab_id(mcp.call("tabs_create", url=f"{MAIN}/second", active=True))  # what the user reads
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    tree = mcp.call("read_page", tabId=tab)
    check("agent tab starts in the background", js(mcp, tab, "document.hasFocus()") is False)
    focus_guard(mcp, tab, tree, "background")
    mcp.call("tabs_select", tabId=tab)  # as when the user clicks the tab
    check("in front: the tab has the browser's focus (so the in-front path is tested)",
          js(mcp, tab, "document.hasFocus()") is True)
    focus_guard(mcp, tab, tree, "in front")
    mcp.call("tabs_select", tabId=other)  # the user goes back to their own tab
    check("back in the background", js(mcp, tab, "document.hasFocus()") is False)
    focus_guard(mcp, tab, tree, "background again")
    mcp.call("tabs_close", tabId=tab)

    # Focus inside a frame from another site, in the background and in front.
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/frames"))
    time.sleep(0.5)
    for where in ("frame, background", "frame, in front"):
        if where.endswith("in front"):
            mcp.call("tabs_select", tabId=tab)
        tree = mcp.call("read_page", tabId=tab)
        out = mcp.call("type_text", tabId=tab, ref=ref_of(tree, "Inner input"), text="z")
        check(f"{where}: type_text names the field in the frame", 'textbox "Inner input"' in out and "in frame" in out, out)
        mcp.call("click", tabId=tab, ref=ref_of(tree, "Inner button"))
        check(f"{where}: Delete on a button in the frame is refused",
              expect_error(lambda: mcp.call("press_key", tabId=tab, keys="Delete"), "not a text field"))
    mcp.call("tabs_close", tabId=tab)
    mcp.call("tabs_close", tabId=other)


def t_read(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    tree = mcp.call("read_page", tabId=tab)
    check("heading with level", 'heading "Heading one"' in tree and "level=1" in tree, tree[:500])
    check("label names an input", 'textbox "Your name"' in tree, tree[:800])
    check("closed shadow DOM is read", "Shadow button" in tree, tree)
    check("display:none is skipped", "Hidden button" not in tree)
    check("password value is not exposed", "secret123" not in tree)
    inter = mcp.call("read_page", tabId=tab, filter="interactive")
    check("interactive filter drops text", "Some visible paragraph" not in inter and "Press me" in inter, inter[:400])
    far = tab_id(mcp.call("tabs_create", url=f"{MAIN}/scroll"))
    near = mcp.call("read_page", tabId=far, filter="interactive")
    check("interactive read keeps to the viewport", "Far button" not in near and "off screen" in near, near)
    check("scope page reads everything", "Far button" in mcp.call("read_page", tabId=far, filter="interactive", scope="page"))
    check("find still reaches off-screen elements", "Far button" in mcp.call("find", tabId=far, query="far button"))
    mcp.call("tabs_close", tabId=far)
    check("find matches", "Press me" in mcp.call("find", tabId=tab, query="press"))
    plain = mcp.call("find", tabId=tab, query="the field for typing your name").splitlines()
    check("find understands a plain description", plain and "#name" in plain[0], plain)
    check("find ranks the password field by word", "#pw" in mcp.call("find", tabId=tab, query="password box").splitlines()[0])
    check("find by kind of element", "#btn" in mcp.call("find", tabId=tab, query="the press me button").splitlines()[0])
    check("read_page states the viewport", "Viewport:" in tree)
    short = mcp.call("read_page", tabId=tab, max_chars=120)
    check("truncation is at a line and states the size", "[truncated at" in short and "characters" in short, short)
    check("find reports no match", "Nothing matches" in mcp.call("find", tabId=tab, query="zzzznope"))
    check("get_page_text", "Some visible paragraph text." in mcp.call("get_page_text", tabId=tab))

    shadow = ref_of(tree, "Shadow button")
    clear(mcp, tab)
    mcp.call("click", tabId=tab, ref=shadow)
    check("click inside closed shadow DOM", any(e["type"] == "click" and e["trusted"] for e in events(mcp, tab)))
    check("bad ref is a clear error", expect_error(lambda: mcp.call("click", tabId=tab, ref="r99999"), "stale"))
    mcp.call("tabs_close", tabId=tab)


def t_forms(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    tree = mcp.call("read_page", tabId=tab)
    mcp.call("form_input", tabId=tab, ref=ref_of(tree, "Your name"), value="Filled")
    check("form_input text", js(mcp, tab, "document.getElementById('name').value") == "Filled")
    mcp.call("form_input", tabId=tab, ref=ref_of(tree, "combobox"), value="Banana")
    check("form_input select by text", js(mcp, tab, "document.getElementById('sel').value") == "b")
    mcp.call("form_input", tabId=tab, ref=ref_of(tree, '"Agree"'), value=True)
    check("form_input checkbox", js(mcp, tab, "document.getElementById('chk').checked") is True)

    with tempfile.TemporaryDirectory() as d:
        small = pathlib.Path(d, "note.txt")
        small.write_text("file body")
        big = pathlib.Path(d, "big.bin")
        big.write_bytes(bytes(range(256)) * 4096 * 3)  # 3 MiB: beyond one 1 MB native message
        file_ref = ref_of(mcp.call("find", tabId=tab, query="file"), "file input")
        mcp.call("file_upload", tabId=tab, ref=file_ref, paths=[str(small), str(big)])
        info = js(mcp, tab, "(async () => { const f = document.getElementById('file').files; return [f.length, f[0].name, await f[0].text(), f[1].size]; })()")
    check("file_upload, including over 1 MB", info == [2, "note.txt", "file body", 256 * 4096 * 3], info)
    mcp.call("tabs_close", tabId=tab)


def t_frames(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/frames"))
    tree = mcp.call("read_page", tabId=tab)
    check("cross-site frame is read", "Inner button" in tree, tree)
    btn = ref_of(tree, "Inner button")
    frame = int(re.match(r"f(\d+):", btn).group(1))

    mcp.call("click", tabId=tab, x=100, y=70)
    ev = events(mcp, tab, frame)
    check("point click reaches a cross-process frame", any(e["type"] == "click" and e["trusted"] and e["target"] == "ib" for e in ev), ev)

    clear(mcp, tab, frame)
    mcp.call("click", tabId=tab, ref=btn)
    ev = events(mcp, tab, frame)
    check("ref click reaches a cross-process frame", any(e["type"] == "click" and e["trusted"] for e in ev), ev)

    mcp.call("type_text", tabId=tab, ref=ref_of(tree, "Inner input"), text="in frame")
    check("typing reaches a cross-process frame", js(mcp, tab, "document.getElementById('ii').value", frame_id=frame) == "in frame",
          js(mcp, tab, "document.getElementById('ii').value", frame_id=frame))
    check("console includes frames", "inner frame says hi" in mcp.call("read_console", tabId=tab))
    mcp.call("tabs_close", tabId=tab)


def t_scroll_and_shots(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/scroll"))
    mcp.call("scroll", tabId=tab, dy=600)
    check("scroll moves the page", js(mcp, tab, "window.scrollY") == 600, js(mcp, tab, "window.scrollY"))
    ref = ref_of(mcp.call("find", tabId=tab, query="Far button"), "Far button")
    mcp.call("scroll_to", tabId=tab, ref=ref)
    y = js(mcp, tab, "window.scrollY")
    check("scroll_to reaches a far element", y > 2000, y)
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/wheelapp"))
    out = mcp.call("scroll", tabId=tab, dy=300)
    state = js(mcp, tab, "[window.scrollY, window.zoom]")
    check("a page handling the wheel itself is not scrolled", state == [0, 1] and "handled" in out, f"{state} {out}")
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/scroll"))
    other = tab_id(mcp.call("tabs_create", url=f"{MAIN}/second"))
    mcp.call("scroll", tabId=tab, dy=400)
    check("scroll works in a background tab", js(mcp, tab, "window.scrollY") == 400, js(mcp, tab, "window.scrollY"))
    mcp.call("tabs_close", tabId=other)
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/color"))
    data, mime = mcp.image(tabId=tab, format="png")
    check("screenshot is a PNG", mime == "image/png" and data[:4] == b"\x89PNG", mime)
    w, h = png_size(data)
    vw, vh = js(mcp, tab, "[innerWidth, innerHeight]")
    check("image pixels equal viewport coordinates", (w, h) == (vw, vh), f"{w}x{h} vs {vw}x{vh}")
    pixel = colour_at(data, 150, 150)
    corner = colour_at(data, 20, 20)
    check("screenshot shows real content", pixel == (0, 255, 0) and corner == (255, 0, 0), f"{pixel} {corner}")

    region, _ = mcp.image(tabId=tab, format="png", region={"x": 120, "y": 120, "width": 40, "height": 40})
    check("region captures that region", png_size(region) == (40, 40) and colour_at(region, 20, 20) == (0, 255, 0), png_size(region))
    jpeg, mime = mcp.image(tabId=tab)
    check("default screenshot is JPEG", mime == "image/jpeg" and jpeg[:2] == b"\xff\xd8", mime)
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/scroll"))
    mcp.call("scroll", tabId=tab, dy=300)
    region, _ = mcp.image(tabId=tab, format="png", region={"x": 450, "y": 50, "width": 20, "height": 20})
    check("region is viewport-relative after scrolling", colour_at(region, 10, 10) == (0, 170, 0), colour_at(region, 10, 10))
    mcp.call("tabs_close", tabId=tab)


def colour_at(png, x, y):
    code = (
        "import io,sys,PIL.Image as I;"
        "im=I.open(io.BytesIO(sys.stdin.buffer.read())).convert('RGB');"
        f"print(*im.getpixel(({x},{y})))"
    )
    out = subprocess.run(
        ["uv", "run", "--python", "3.15", "--with", "pillow", "python", "-c", code],
        input=png, capture_output=True, check=True,
    )
    return tuple(int(v) for v in out.stdout.split())


def t_devtools(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    # The page throws from setTimeout(0); in a background tab the browser delays timers (about 1 s).
    deadline = time.time() + 3
    con = mcp.call("read_console", tabId=tab)
    while "uncaught boom" not in con and time.time() < deadline:
        time.sleep(0.2)
        con = mcp.call("read_console", tabId=tab)
    check("console.log with values", 'hello from page {"a":1}' in con, con)
    check("console.error", "page console error" in con, con)
    check("uncaught exceptions", "uncaught boom" in con, con)
    check("only_errors filter", "hello from page" not in mcp.call("read_console", tabId=tab, only_errors=True))
    check("console pattern is a regular expression", "boom" in mcp.call("read_console", tabId=tab, pattern="^Error: unc.*m$"))
    mcp.call("read_console", tabId=tab, clear=True)
    check("console clear hides what was read", "No console" in mcp.call("read_console", tabId=tab))
    net = mcp.call("read_network", tabId=tab)
    check("network capture", "GET 200" in net and "/api/data?x=1" in net and "/main" in net, net)
    check("network filter", "/main" not in mcp.call("read_network", tabId=tab, url_pattern="api/data"))

    check("js returns JSON", js(mcp, tab, "({a: [1, 2]})") == {"a": [1, 2]})
    check("js awaits", js(mcp, tab, "new Promise(r => setTimeout(() => r(7), 50))") == 7)
    check("js multi-statement with return", js(mcp, tab, "const a = 2; const b = 3; return a * b;") == 6)
    check("js returns the last expression", js(mcp, tab, "const a = 2; a * 3") == 6)
    check("js top-level await", js(mcp, tab, "const r = await Promise.resolve(2); r * 3") == 6)
    check("js semicolon in a string", js(mcp, tab, "const s = 'a;b'; await 0; s.length") == 3)
    check("js runs only once", js(mcp, tab, "window.ran = (window.ran || 0) + 1; window.ran") == 1)
    check("js for loop then value", js(mcp, tab, "let n = 0;\nfor (let i = 0; i < 3; i++) { n += i; }\nn") == 3)
    # A regex literal holding a quote or an unpaired bracket once hid the last expression: the
    # call returned undefined instead of the value.
    check("js regex with a quote, then await",
          js(mcp, tab, "const m = 'say \"hi\"'.match(/\"([^\"]+)\"/); await 0;\nm[1]") == "hi")
    check("js regex with an unpaired bracket, then await",
          js(mcp, tab, "const t = 'a (feat. b)'.replace(/\\s*\\((feat)[^)]*\\)/g, ''); await 0; t") == "a")
    check("js division is not a regex", js(mcp, tab, "const a = 6, b = 2; await 0; a / b / 1") == 3)
    check("js template substitution with braces",
          js(mcp, tab, "const x = `a${ {k: 1}.k }b;`; await 0; x") == "a1b;")
    check("js comment with a semicolon", js(mcp, tab, "let n = 4; // a ; comment\nawait 0\nn") == 4)
    check("js sees page globals", js(mcp, tab, "typeof window.events") == "object")
    err = mcp.call("javascript", tabId=tab, code="throw new Error('nope')")
    check("js errors are reported", "nope" in err, err)
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/csp"))
    check("js works under a strict CSP", js(mcp, tab, "document.title") == "CSP")
    check("read_page works under a strict CSP", "locked" in mcp.call("read_page", tabId=tab))
    mcp.call("tabs_close", tabId=tab)


def t_script(mcp):
    """javascript's page-world semantics, file, args, save_to and timeout."""
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    # A bare assignment or a var lands on the page's window, as in a page script; once it stayed
    # in a per-call sandbox, so a background loop's progress counter never moved.
    js(mcp, tab, "bareGlobal = 5; 1")
    check("js bare assignment sets a page global", js(mcp, tab, "window.bareGlobal") == 5)
    js(mcp, tab, "var varGlobal = 6; varGlobal")
    check("js var sets a page global", js(mcp, tab, "window.varGlobal") == 6)
    js(mcp, tab, "window.progress = 0; (async () => { for (let i = 0; i < 3; i++) { await 0; progress++; } doneFlag = true; })(); 1")
    time.sleep(0.3)
    check("js background assignments reach the page", js(mcp, tab, "[window.progress, window.doneFlag]") == [3, True])
    check("js the page's own scripts see agent globals", js(mcp, tab, "document.defaultView.eval('bareGlobal')") == 5)
    check("js let/const can be declared again in the next call",
          js(mcp, tab, "const reuse = 1; reuse") == 1 and js(mcp, tab, "const reuse = 2; reuse") == 2)
    # A plain script (no await) whose last value is an object: its value is handed from one
    # evaluation to the next, which once failed with "belongs to a different Debugger".
    check("js plain script returning an object",
          js(mcp, tab, "objGlobal = 41 + 1; ({ title: document.title, onWindow: window.objGlobal })") == {"title": "Main page", "onWindow": 42})
    check("js plain script returning an array and a DOM node",
          js(mcp, tab, "const a = [1, 2]; a") == [1, 2] and "<h1>" in js(mcp, tab, "const h = document.querySelector('h1'); JSON.stringify(h.outerHTML)"))
    # With await, the last value comes after a loop's "}" on the same line; it once came back
    # undefined because only ";" and newlines split statements.
    check("js await code ending in a value right after a loop's block",
          js(mcp, tab, "const xs = []; for (const n of [1, 2]) { xs.push(await n); } ({ xs })") == {"xs": [1, 2]})
    check("js ...and after if/else and try/catch blocks",
          js(mcp, tab, "let v = await 1; if (v) { v++ } else { v-- } try { v *= 10 } catch (e) {} v") == 20)
    check("js an object literal's } does not end a statement",
          js(mcp, tab, "const o = await { k: 3 }; ({ k: o.k })") == {"k": 3})
    check("js a plain script's const stays local", js(mcp, tab, "const localOnly = 1; localOnly") == 1
          and js(mcp, tab, "typeof window.localOnly") == "undefined")

    check("js args are a constant", js(mcp, tab, "args.a + args.b.length", args={"a": 2, "b": [1, 2, 3]}) == 5)
    check("js args with quotes and newlines", js(mcp, tab, "args", args="it's \"q\"\nline") == "it's \"q\"\nline")

    work = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-script-"))
    lib = work / "lib.js"
    lib.write_text("window.helper = (x) => x * 2;\nfunction annexB() { return 'kept'; }\n")
    check("js file runs before code", js(mcp, tab, "helper(21)", file=str(lib)) == 42)
    check("js file alone", js(mcp, tab, "1", file=str(lib)) == 1)
    check("js a file's helper stays on the page", js(mcp, tab, "helper(5)") == 10)
    hidden = work / ".secret" / "x.js"
    hidden.parent.mkdir()
    hidden.write_text("1")
    check("js file refuses hidden files", expect_error(lambda: mcp.call("javascript", tabId=tab, file=str(hidden)), "hidden"))
    check("js file must be a script", expect_error(lambda: mcp.call("javascript", tabId=tab, file=str(work / "lib.txt")), "script"))
    check("js needs code or file", expect_error(lambda: mcp.call("javascript", tabId=tab), "code, file"))

    out = work / "big.json"
    reply = mcp.call("javascript", tabId=tab, code="Array.from({length: 100000}, (_, i) => ({i, s: 'x'.repeat(10)}))", save_to=str(out))
    saved = json.loads(out.read_text())
    check("js save_to writes the full result", len(saved) == 100000 and saved[-1]["i"] == 99999, reply[:200])
    check("js save_to replies with a summary only", reply.startswith("Saved ") and len(reply) < 600, reply[:200])
    check("js save_to refuses other extensions",
          expect_error(lambda: mcp.call("javascript", tabId=tab, code="1", save_to=str(work / "x.sh")), "data or text file"))
    check("js save_to refuses hidden folders",
          expect_error(lambda: mcp.call("javascript", tabId=tab, code="1", save_to=str(work / ".h" / "x.json")), "hidden"))

    check("js timeout cuts a slow script",
          expect_error(lambda: mcp.call("javascript", tabId=tab, code="await new Promise(r => setTimeout(r, 4000)); 1", timeout=1), "Timed out|did not answer"))
    started = time.monotonic()
    check("js timeout allows a script longer than the default 30 s",
          js(mcp, tab, "await new Promise(r => setTimeout(r, 33000)); 'late'", timeout=60) == "late")
    check("js long script took its time", time.monotonic() - started > 32)
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/csp"))
    js(mcp, tab, "cspGlobal = 'yes'; 1")
    check("js page globals under a strict CSP", js(mcp, tab, "window.cspGlobal") == "yes")
    mcp.call("tabs_close", tabId=tab)


def t_navigation(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    check("navigate waits for load", "Second page" in mcp.call("navigate", tabId=tab, url=f"{MAIN}/second"))
    check("back", "Main page" in mcp.call("navigate", tabId=tab, url="back"))
    check("forward", "Second page" in mcp.call("navigate", tabId=tab, url="forward"))
    check("reload", "Second page" in mcp.call("navigate", tabId=tab, url="reload"))
    check("about:config is refused", expect_error(lambda: mcp.call("navigate", tabId=tab, url="about:config"), "only open"))
    check("file: is refused", expect_error(lambda: mcp.call("navigate", tabId=tab, url="file:///etc/passwd"), "only open"))
    check("javascript: is refused", expect_error(lambda: mcp.call("navigate", tabId=tab, url="javascript:alert(1)"), "only open"))

    mcp.call("navigate", tabId=tab, url=f"{MAIN}/late")
    check("wait_for finds late content", "Found" in mcp.call("wait_for", tabId=tab, selector="#late"))
    check("wait_for times out clearly", expect_error(lambda: mcp.call("wait_for", tabId=tab, text="never", timeout=1), "Not found"))
    mcp.call("tabs_close", tabId=tab)


def t_dialogs(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/dialogs"))
    btn = lambda name: ref_of(mcp.call("find", tabId=tab, query=name), name)
    a, c, p = btn("alert"), btn("confirm"), btn("prompt")

    out = mcp.call("click", tabId=tab, ref=a)
    check("click reports the dialog it opened", "alert" in out and "alert text" in out, out)
    check("page tools explain a blocking dialog", expect_error(lambda: mcp.call("read_page", tabId=tab), "dialog is open"))
    check("handle_dialog status", "alert text" in mcp.call("handle_dialog", tabId=tab))
    mcp.call("handle_dialog", tabId=tab, action="accept")
    time.sleep(0.2)
    check("page works after an alert", js(mcp, tab, "1 + 1") == 2)

    mcp.call("click", tabId=tab, ref=c)
    mcp.call("handle_dialog", tabId=tab, action="dismiss")
    time.sleep(0.2)
    check("confirm dismissed returns false", js(mcp, tab, "window.r") is False, js(mcp, tab, "String(window.r)"))
    mcp.call("click", tabId=tab, ref=c)
    mcp.call("handle_dialog", tabId=tab, action="accept")
    time.sleep(0.2)
    check("confirm accepted returns true", js(mcp, tab, "window.r") is True)
    mcp.call("click", tabId=tab, ref=p)
    mcp.call("handle_dialog", tabId=tab, action="accept", text="answer")
    time.sleep(0.2)
    check("prompt receives text", js(mcp, tab, "window.r") == "answer", js(mcp, tab, "String(window.r)"))
    mcp.call("tabs_close", tabId=tab)


def t_pointer(mcp):
    red = (255, 0, 0)
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/color"))
    shot = lambda **kw: mcp.image(tabId=tab, format="png", **kw)[0]
    check("no pointer is drawn before any pointer action", colour_at(shot(), 302, 309) == red)
    mcp.call("hover", tabId=tab, x=300, y=300)
    check("the pointer is drawn at the last position", colour_at(shot(), 302, 309) != red, colour_at(shot(), 302, 309))
    check("the pointer is not drawn elsewhere", colour_at(shot(), 340, 340) == red)
    check("show_pointer false leaves it out", colour_at(shot(show_pointer=False), 302, 309) == red)
    cropped = shot(region={"x": 290, "y": 290, "width": 40, "height": 40})
    check("the pointer follows a region", colour_at(cropped, 12, 19) != red, colour_at(cropped, 12, 19))
    outside = shot(region={"x": 400, "y": 400, "width": 40, "height": 40})
    check("a region without the pointer stays clean", colour_at(outside, 2, 9) == red)
    half = shot(scale=0.5)
    check("the pointer follows image scale", colour_at(half, 152, 159) != red, colour_at(half, 152, 159))
    mcp.call("click", tabId=tab, x=500, y=200)
    moved = shot()
    check("the pointer moves with a click", colour_at(moved, 502, 209) != red and colour_at(moved, 302, 309) == red)
    mcp.call("drag", tabId=tab, from_x=500, from_y=200, to_x=420, to_y=350)
    check("the pointer moves with a drag", colour_at(shot(), 422, 359) != red)
    jpeg, mime = mcp.image(tabId=tab)
    check("a pointer works with the default format", mime == "image/jpeg" and jpeg[:2] == b"\xff\xd8", mime)
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/frames"))
    tree = mcp.call("read_page", tabId=tab)
    mcp.call("click", tabId=tab, ref=ref_of(tree, "Inner button"))
    # The button's centre is (125, 70) in the top page; a frame-local point would be the same here,
    # so move the frame first to prove the conversion.
    mcp.call("javascript", tabId=tab, code="const f = document.getElementById('f'); f.style.left = '200px'; f.style.top = '100px'; 1")
    tree = mcp.call("read_page", tabId=tab)
    mcp.call("click", tabId=tab, ref=ref_of(tree, "Inner button"))
    with_p = mcp.image(tabId=tab, format="png")[0]
    without = mcp.image(tabId=tab, format="png", show_pointer=False)[0]
    check("a click in a cross-site frame draws at its place on the page",
          colour_at(with_p, 327, 179) != colour_at(without, 327, 179), (colour_at(with_p, 327, 179), colour_at(without, 327, 179)))
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/scroll"))
    content = mcp.call_raw("scroll", tabId=tab, dy=200)["content"]
    check("scroll returns a screenshot", [c["type"] for c in content] == ["image", "text"] and "Scrolled to (0, 200)" in content[1]["text"], content[-1])
    content = mcp.call_raw("scroll", tabId=tab, dy=100, screenshot=False)["content"]
    check("scroll can leave the screenshot out", [c["type"] for c in content] == ["text"], content[-1])
    image = next(c for c in mcp.call_raw("scroll", tabId=tab, dy=0)["content"] if c["type"] == "image")
    check("the scroll screenshot shows the new position", js(mcp, tab, "window.scrollY") == 300 and len(image["data"]) > 100)
    result = mcp.call_raw("batch", actions=[{"tool": "scroll", "args": {"tabId": tab, "dy": 50}}])
    check("scroll's screenshot works in a batch", "image" in [c["type"] for c in result["content"]], result["content"][:2])
    mcp.call("tabs_close", tabId=tab)


def t_agent_speed(mcp):
    # Tools used straight after an input action must see the result of that action.
    result = mcp.call("navigate", url=f"{MAIN}/main")
    tab = tab_id(result)
    check("navigate without a tab opens one", "Main page" in result, result)
    out = mcp.call("click", tabId=tab, ref=ref_of(mcp.call("find", tabId=tab, query="Go to second"), "Go to second"))
    check("click reports the page it led to", "Second page" in out, out)
    check("the next read sees the new page", "Arrived." in mcp.call("get_page_text", tabId=tab))
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/color"))
    mcp.call("javascript", tabId=tab, code=f"location.href = '{MAIN}/slowstyle'; 1")
    mcp.call("wait_for", tabId=tab, selector="#ready")
    data, _ = mcp.image(tabId=tab, format="png")
    check("a screenshot waits for the page's styles", colour_at(data, 400, 400) == (0, 0, 255), colour_at(data, 400, 400))
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    result = mcp.call_raw("batch", actions=[
        {"tool": "click", "args": {"tabId": tab, "x": 100, "y": 60}},
        {"tool": "javascript", "args": {"tabId": tab, "code": "document.getElementById('btn').dataset.n"}},
        {"tool": "screenshot", "args": {"tabId": tab, "scale": 0.5}},
    ])
    kinds = [c["type"] for c in result["content"]]
    text = " ".join(c.get("text", "") for c in result["content"])
    check("batch runs steps in order and returns images", "image" in kinds and '"1"' in text and not result.get("isError"), result["content"][:3])
    result = mcp.call_raw("batch", actions=[
        {"tool": "click", "args": {"tabId": 999999, "x": 1, "y": 1}},
        {"tool": "click", "args": {"tabId": tab, "x": 100, "y": 60}},
    ])
    check("batch stops at the first error", result.get("isError") and js(mcp, tab, "document.getElementById('btn').dataset.n") == "1", result)

    with tempfile.TemporaryDirectory() as d:
        gif = pathlib.Path(d, "run.gif")
        mcp.call("record", tabId=tab, action="start")
        mcp.call("click", tabId=tab, x=100, y=60)
        mcp.call("scroll", tabId=tab, dy=50)
        out = mcp.call("record", tabId=tab, action="stop", save_to=str(gif))
        code = (
            "import sys,PIL.Image as I;im=I.open(sys.argv[1]);n=im.n_frames;im.seek(1);"
            "print(n,im.size[0],im.convert('RGB').getpixel((100,60))!=(0,0,0))"
        )
        info = subprocess.run(["uv", "run", "--python", "3.15", "--with", "pillow", "python", "-c", code, str(gif)],
                              capture_output=True, text=True).stdout.split()
        check("record saves a real animated GIF", info[:1] == ["4"] and int(info[1]) > 100, f"{out} {info}")
        check("recording stops", expect_error(lambda: mcp.call("record", tabId=tab, action="stop", save_to=str(gif)), "not recording"))
    mcp.call("tabs_close", tabId=tab)


def t_drag(mcp):
    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/drag"))
    mcp.call("drag", tabId=tab, from_x=45, from_y=45, to_x=300, to_y=200)
    pos = js(mcp, tab, "[handle.style.left, handle.style.top]")
    check("drag moves a pointer-driven element", pos == ["275px", "175px"], pos)
    mcp.call("tabs_close", tabId=tab)

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/dnd"))
    out = mcp.call("drag", tabId=tab, from_x=60, from_y=60, to_x=370, to_y=90)
    log = js(mcp, tab, "window.log")
    check("HTML5 drag-and-drop completes", log == ["dragstart:true", "drop:true:payload"] and "accepted" in out, f"{log} {out}")
    mcp.call("click", tabId=tab, x=500, y=400)
    check("the page still takes clicks after a drag", js(mcp, tab, "1 + 1") == 2)
    mcp.call("navigate", tabId=tab, url="reload")
    out = mcp.call("drag", tabId=tab, from_x=60, from_y=60, to_x=700, to_y=400)
    log = js(mcp, tab, "window.log")
    check("a drag to a place that refuses it does not drop", log == ["dragstart:true"] and "did not accept" in out, f"{log} {out}")
    mcp.call("tabs_close", tabId=tab)

    with tempfile.TemporaryDirectory() as d:
        tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/drop"))
        shot = pathlib.Path(d, "shot.png")
        mcp.image(tabId=tab, format="png", save_to=str(shot))
        check("screenshot save_to writes the file", shot.read_bytes()[:4] == b"\x89PNG")
        out = mcp.call("file_upload", tabId=tab, x=100, y=100, paths=[str(shot)])
        got = js(mcp, tab, "window.got")
        check("a file can be dropped on a drop zone", got and got[0][:2] == ["shot.png", "image/png"] and got[0][2] == shot.stat().st_size, f"{got} {out}")
        check("a place that refuses drops is an error", expect_error(lambda: mcp.call("file_upload", tabId=tab, x=500, y=100, paths=[str(shot)]), "does not accept"))
        mcp.call("tabs_close", tabId=tab)


def t_background_tab(mcp):
    front = tab_id(mcp.call("tabs_create", url=f"{MAIN}/second", active=True))
    check("active=True brings a new tab to the front", f"{front} (active)" in mcp.call("tabs_list"))
    # New tabs open behind the one the user is reading, unless asked otherwise.
    back = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    check("a new tab opens in the background by default", f"{front} (active)" in mcp.call("tabs_list"), mcp.call("tabs_list"))
    via_navigate = tab_id(mcp.call("navigate", url=f"{MAIN}/second"))
    check("navigate without a tab also opens it in the background", f"{front} (active)" in mcp.call("tabs_list"))
    mcp.call("tabs_close", tabId=via_navigate)
    mcp.call("click", tabId=back, x=100, y=60)
    ev = events(mcp, back)
    check("click works in a background tab", any(e["type"] == "click" and e["trusted"] for e in ev), ev)
    name = ref_of(mcp.call("read_page", tabId=back), "Your name")
    mcp.call("type_text", tabId=back, ref=name, text="bg")
    val = js(mcp, back, "document.getElementById('name').value")
    check("typing works in a background tab", val == "bg", val)
    # Firefox sends a background tab no focus events; widgets that keep text only after their own
    # onFocus (MUI's Autocomplete) then dropped everything typed. foxreach sends them, to the agent's
    # element only: the tab in front must see none.
    js(mcp, front, "window.seen = []; for (const t of ['focus', 'blur']) addEventListener(t, (e) => seen.push(t), true); 1")
    js(mcp, back, """document.body.insertAdjacentHTML('afterbegin', '<input id=picker style="margin:20px">');
        window.pickerLog = []; window.pickerFocused = false;
        picker.addEventListener('focus', (e) => { pickerFocused = true; pickerLog.push('focus:' + e.isTrusted); });
        picker.addEventListener('blur', (e) => { pickerFocused = false; pickerLog.push('blur:' + e.isTrusted); });
        picker.addEventListener('input', () => { if (!pickerFocused) setTimeout(() => { picker.value = ''; }); }); 1""")
    mcp.call("type_text", tabId=back, ref=ref_of(mcp.call("read_page", tabId=back), "#picker"), text="Kim")
    got = js(mcp, back, "await new Promise((r) => setTimeout(r, 200)); [picker.value, pickerLog, document.hasFocus()]")
    check("background tab: a focus-tracking widget keeps typed text", got[0] == "Kim", got)
    check("...after one trusted focus event, with no real focus taken", got[1] == ["focus:true"] and got[2] is False, got)
    # The user brings the tab forward and leaves again: the browser blurs the field for real, and
    # the next agent input must tell the widget it has focus again (it once kept quiet and lost text).
    mcp.call("tabs_select", tabId=back)
    mcp.call("tabs_select", tabId=front)
    js(mcp, front, "seen.length = 0; 1")  # those switches were real; only what follows counts
    js(mcp, back, "picker.value = ''; pickerLog.length = 0; 1")
    mcp.call("type_text", tabId=back, text="Riley")
    got = js(mcp, back, "await new Promise((r) => setTimeout(r, 200)); [picker.value, pickerLog, pickerFocused]")
    check("background tab: after the user visits and leaves the tab, typing still sticks", got[0] == "Riley" and got[2] is True, got)
    js(mcp, back, "picker.remove(); 1")
    seen = js(mcp, front, "seen")
    check("...and the tab in front saw no focus or blur event", seen == [], seen)
    data, _ = mcp.image(tabId=back, format="png")
    check("screenshot works in a background tab", data[:4] == b"\x89PNG")
    active = mcp.call("tabs_list")
    check("the user's front tab did not change", f"{front} (active)" in active, active)
    mcp.call("tabs_close", tabId=back)
    mcp.call("tabs_close", tabId=front)


def t_policy(mcp):
    listed = mcp.call("tabs_list")
    check("other tabs are hidden", "other tabs are not available" in listed, listed)
    all_tabs = js_probe_private(mcp)
    check("a tab not shared is refused", expect_error(lambda: mcp.call("read_page", tabId=all_tabs), "not available to agents"))
    check("unknown tab is refused", expect_error(lambda: mcp.call("click", tabId=999999, x=1, y=1), "not available"))

    tab = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    ref = ref_of(mcp.call("find", tabId=tab, query="Open popup"), "Open popup")
    clicked = mcp.call("click", tabId=tab, ref=ref)
    new = re.search(r"opened new tab (\d+)", clicked)
    check("click reports the tab it opened", new, clicked)
    mcp.call("wait_for", tabId=int(new.group(1)), text="Arrived.")
    after = mcp.call("tabs_list")
    check("a page's popup stays with the agent", "Second page" in after and f"{tab}" in after, after)
    for m in re.findall(r"^(\d+)", after, re.M):
        mcp.call("tabs_close", tabId=int(m))


def js_probe_private(mcp):
    # Tab ids are small integers. The session's first tab is never adopted, so 1 stays private.
    return 1


def t_multi_agent(mcp):
    """Two agents at once, each with its own MCP server process."""
    second = McpClient(mcp_socket, name="second-agent")
    a = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    b = tab_id(second.call("tabs_create", url=f"{MAIN}/second"))
    errs = []

    def hammer(client, tab, n):
        try:
            for _ in range(n):
                client.call("get_page_text", tabId=tab)
        except Exception as e:
            errs.append(repr(e))

    threads = [threading.Thread(target=hammer, args=(c, t, 15)) for c, t in ((mcp, a), (second, b))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    check("two agents run concurrently without crossed replies", not errs, errs)
    second.call("tabs_close", tabId=b)
    mcp.call("tabs_close", tabId=a)
    second.close()

    # A second browser's host must not steal the socket from the running one.
    env = dict(os.environ, FOXREACH_SOCKET=mcp_socket)
    # Stdin stays open here, as it does when a browser starts the host.
    rival = subprocess.Popen([RUNTIME, str(ROOT / "bridge/host.mjs")], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    code = rival.wait(timeout=10)
    out = rival.stdout.read()
    rival.stdin.close()
    check("a second host refuses a live socket", code == 3 and b"Another browser" in out, (code, out))
    # A host whose browser vanishes at once must not take the working socket with it.
    subprocess.run([RUNTIME, str(ROOT / "bridge/host.mjs")], env=env, input=b"", capture_output=True, timeout=10)
    check("a host that ends early leaves the live socket alone", os.path.exists(mcp_socket))
    check("the first host still serves", json.loads(mcp.call("status"))["version"])
    mode = os.stat(mcp_socket).st_mode & 0o777
    check("the socket is private to this user", mode == 0o600, oct(mode))


def popup_tests(browser, mcp):
    """The user's controls, driven through the real popup page in a tab."""
    m = browser.marionette
    uuid_js = """const u = JSON.parse(Services.prefs.getStringPref("extensions.webextensions.uuids"));return u["foxreach@foxreach.local"];"""
    m.command("Marionette:SetContext", {"value": "chrome"})
    uuid = m.command("WebDriver:ExecuteScript", {"script": uuid_js, "args": []})["value"]
    m.command("Marionette:SetContext", {"value": "content"})
    popup = f"moz-extension://{uuid}/popup/popup.html"
    m.command("WebDriver:Navigate", {"url": popup})
    run = lambda code: m.command("WebDriver:ExecuteAsyncScript", {"script": code, "args": [], "scriptTimeout": 8000})["value"]
    time.sleep(0.5)
    status = run("arguments[0](document.getElementById('status').textContent)")
    check("popup shows connected and trusted", "Connected" in status and "trusted input" in status, status)

    page = tab_id(mcp.call("tabs_create", url=f"{MAIN}/main"))
    run("document.getElementById('paused').click(); setTimeout(() => arguments[0](1), 200)")
    check("pause stops agents", expect_error(lambda: mcp.call("read_page", tabId=page), "paused"))
    run("document.getElementById('paused').click(); setTimeout(() => arguments[0](1), 200)")
    check("resume restores agents", "Press me" in mcp.call("read_page", tabId=page))

    run("document.getElementById('blocked').value = '127.0.0.1'; document.getElementById('save-blocked').click(); setTimeout(() => arguments[0](1), 200)")
    check("blocked site refuses tools", expect_error(lambda: mcp.call("read_page", tabId=page), "blocked agents"))
    check("blocked site refuses navigation", expect_error(lambda: mcp.call("tabs_create", url=f"{MAIN}/second"), "blocked agents"))
    listed = mcp.call("tabs_list")
    check("a blocked site's title and address are not listed", "blocked for agents" in listed and "Main page" not in listed, listed)

    # A blocked site must stay out of reach from a page that is allowed, too.
    set_blocked = lambda value: run(f"document.getElementById('blocked').value = {value!r}; document.getElementById('save-blocked').click(); setTimeout(() => arguments[0](1), 200)")  # noqa: E731
    set_blocked(f"HTTP://LocalHost:{PORT}/some/path, *.Example.org, example.org.")
    saved = run("browser.runtime.sendMessage({popup: 'state'}).then((s) => arguments[0](s.blockedHosts))")
    check("blocked entries are stored as plain host names", saved == ["localhost", "example.org"], saved)
    fetched = mcp.call("javascript", tabId=page, code=f"fetch('{OTHER_SITE}/api/data?blockprobe=1').then((r) => r.text())")
    check("a script in an allowed page cannot fetch a blocked site",
          fetched.startswith("Error") and not any("blockprobe" in r for r in requests_seen), fetched)
    mcp.call("javascript", tabId=page, code=f"location.href = '{OTHER_SITE}/second?blocknav=1'; 1")
    time.sleep(1)
    listed = mcp.call("tabs_list")
    check("a page cannot take its tab to a blocked site",
          "Main page" in listed and not any("blocknav" in r for r in requests_seen), listed)
    mcp.call("navigate", tabId=page, url=f"{MAIN}/frames")
    tree = mcp.call("read_page", tabId=page)
    check("a blocked site in a frame is not loaded or read", "Frames" in tree and "Inner" not in tree, tree)
    mcp.call("navigate", tabId=page, url=f"{MAIN}/main")
    log = run("setTimeout(() => arguments[0](document.getElementById('activity').textContent), 2200)")
    check("the activity log shows the blocked page load", "blocked a page load" in log and "localhost" in log, log[:400])
    set_blocked("127.0.0.1")
    run("document.getElementById('blocked').value = ''; document.getElementById('save-blocked').click(); setTimeout(() => arguments[0](1), 200)")
    check("unblocking restores access", "Press me" in mcp.call("read_page", tabId=page))

    log = run("setTimeout(() => arguments[0](document.getElementById('activity').textContent), 2200)")
    check("activity log names the agent", "foxreach-test: read_page" in log, log[:300])

    # This popup's tab is the Marionette tab; a second page shows as an unshared entry.
    m.command("WebDriver:ExecuteScript", {"script": "window.open('" + MAIN + "/second')", "args": []})
    time.sleep(0.8)
    share = run("""const done = arguments[0]; setTimeout(async () => {
        const boxes = [...document.querySelectorAll('#tabs input')];
        const row = boxes.find(b => !b.checked && b.parentElement.textContent.includes('Second page'));
        if (!row) return done(null);
        row.click(); setTimeout(() => done(+row.dataset.tabId), 300);
      }, 2200)""")
    check("user can share a tab", share and "Second" in mcp.call("get_page_text", tabId=share), share)
    run(f"""const done = arguments[0];
        const row = document.querySelector('#tabs input[data-tab-id="{share}"]');
        row.click(); setTimeout(() => done(1), 300);""")
    check("user can stop sharing", expect_error(lambda: mcp.call("read_page", tabId=share), "not available to agents"))

    # Only listed sites. In this build the browser grants every site, so the list alone decides.
    text_of = lambda element: run(f"setTimeout(() => arguments[0](document.getElementById('{element}').textContent), 300)")  # noqa: E731
    redrawn = lambda: time.sleep(2.3)  # the popup asks for news every 2 s; its own clicks show at once  # noqa: E731
    press = lambda element, label: run(  # noqa: E731
        f"""[...document.querySelectorAll('#{element} button')].find((b) => b.textContent === {label!r}).click();
        setTimeout(() => arguments[0](1), 300)""")
    run("document.getElementById('access-listed').click(); setTimeout(() => arguments[0](1), 300)")
    check("with an empty list, a site is refused", expect_error(lambda: mcp.call("read_page", tabId=page), "not allowed on 127.0.0.1 yet"))
    listed = mcp.call("tabs_list")
    check("its title and path are not listed, its host is", "not allowed on yet (127.0.0.1)" in listed and "Main page" not in listed, listed)
    check("...and so is opening it", expect_error(lambda: mcp.call("tabs_create", url=f"{MAIN}/second"), "not allowed on 127.0.0.1 yet"))
    redrawn()
    check("the refused site shows as a request", "An agent asks for 127.0.0.1" in text_of("site-requests"), text_of("site-requests"))
    press("site-requests", "Allow")
    check("allowing the request lets the agent in", "Press me" in mcp.call("read_page", tabId=page))
    check("the site is on the list", "127.0.0.1" in text_of("sites") and not text_of("site-requests"), text_of("sites"))
    mcp.call("navigate", tabId=page, url=f"{MAIN}/frames")
    tree = mcp.call("read_page", tabId=page)
    check("a frame from a site not on the list is not read", "Frames" in tree and "Inner button" not in tree and "not allowed on this frame" in tree, tree)
    check("navigating to a site not on the list is refused", expect_error(lambda: mcp.call("navigate", tabId=page, url=f"{OTHER_SITE}/second"), "not allowed on localhost yet"))
    run("document.getElementById('site').value = 'HTTP://LocalHost:1/x'; document.querySelector('#add-site button').click(); setTimeout(() => arguments[0](1), 300)")
    check("a site typed into the popup is added as a host name", "Inner button" in mcp.call("read_page", tabId=page) and "localhost" in text_of("sites"), text_of("sites"))
    mcp.call("navigate", tabId=page, url=f"{MAIN}/main")
    run("""[...document.querySelectorAll('#sites li')].find((li) => li.textContent.startsWith('127.0.0.1')).querySelector('button').click();
        setTimeout(() => arguments[0](1), 300)""")
    check("removing a site refuses it again", expect_error(lambda: mcp.call("read_page", tabId=page), "not allowed on 127.0.0.1 yet"))
    run("document.getElementById('access-all').click(); setTimeout(() => arguments[0](1), 300)")
    check("every site again", "Press me" in mcp.call("read_page", tabId=page) and json.loads(mcp.call("status"))["siteAccess"] == "all")

    # A new agent waits for the user.
    run("document.getElementById('ask').click(); setTimeout(() => arguments[0](1), 300)")
    answers = {}

    def attempt(key, client, tool="tabs_list"):
        try:
            answers[key] = client.call(tool)
        except (ToolError, RuntimeError) as e:  # RuntimeError: the test ended this agent itself
            answers[key] = f"ERROR {e}"

    def asking(name, key):
        client = McpClient(mcp_socket, name=name)
        thread = threading.Thread(target=attempt, args=(key, client))
        thread.start()
        redrawn()
        return client, thread

    one, thread = asking("newcomer", "one")
    check("a new agent's call waits", "one" not in answers and "newcomer asks to use the browser" in text_of("agent-requests"), answers)
    request_windows = lambda: run("browser.windows.getAll({ populate: true }).then((all) => arguments[0](all.filter((w) => w.tabs.some((t) => t.url.endsWith('?requests'))).length))")  # noqa: E731

    def chrome(script):
        m.command("Marionette:SetContext", {"value": "chrome"})
        try:
            return m.command("WebDriver:ExecuteScript", {"script": script, "args": []})["value"]
        finally:
            m.command("Marionette:SetContext", {"value": "content"})

    # The browser's own permission prompts that foxreach shows, on the tab in front.
    prompts_js = "return PopupNotifications._currentNotifications.filter((n) => n.id.startsWith('foxreach-') && n.browser === gBrowser.selectedBrowser)"
    prompts = lambda: chrome(f"{prompts_js.replace('return ', 'return (')}).map((n) => n.id + ' ' + n.options.name)")  # noqa: E731

    def answer_prompt(action, remember=False):
        chrome(prompts_js.replace("return ", "const n = (") + f")[0]; "
               + ("n.mainAction.callback({ checkboxChecked: %s, source: 'button' });" % ("true" if remember else "false")
                  if action == "allow" else "n.secondaryActions[0].callback({ source: 'button' });"))

    shown = prompts()
    check("the request is the browser's own prompt, in the browser window", len(shown) == 1 and "newcomer" in shown[0], shown)
    check("...and opens no window of its own", request_windows() == 0, request_windows())
    check("status needs no approval", json.loads(one.__class__(mcp_socket, name="looker").call("status"))["version"])
    press("agent-requests", "Allow")
    thread.join(10)
    check("Allow lets the waiting call through", "other tabs" in answers.get("one", ""), answers)
    time.sleep(0.5)
    check("answering in the toolbar popup removes the prompt", prompts() == [], prompts())
    check("...and the agent's later calls", "other tabs" in one.call("tabs_list"))
    one.close()

    viaprompt, thread = asking("prompted", "five")
    answer_prompt("allow")
    thread.join(10)
    check("Allow in the browser prompt lets the call through", "other tabs" in answers.get("five", ""), answers)
    redrawn()
    check("...and the popup no longer lists the request", "prompted" not in text_of("agent-requests"), text_of("agent-requests"))
    viaprompt.close()
    viaprompt, thread = asking("prompted", "six")
    answer_prompt("deny")
    thread.join(10)
    check("Refuse in the browser prompt fails the call", "refused" in answers.get("six", ""), answers)
    viaprompt.close()
    viaprompt, thread = asking("remembered", "seven")
    answer_prompt("allow", remember=True)
    thread.join(10)
    redrawn()
    check("the prompt's checkbox means Always", "remembered: always allowed" in text_of("agents"), text_of("agents"))
    press("agents", "Remove")
    viaprompt.close()
    two, thread = asking("newcomer", "two")
    check("the same name on a new connection asks again", "two" not in answers, answers)
    press("agent-requests", "Refuse")
    thread.join(10)
    check("Refuse fails the call", "refused" in answers.get("two", ""), answers)
    check("...and the agent's later calls, without asking", expect_error(lambda: two.call("tabs_list"), "refused") and not text_of("agent-requests"))
    two.close()
    three, thread = asking("regular", "three")
    press("agent-requests", "Always")
    thread.join(10)
    three.close()
    again = McpClient(mcp_socket, name="regular")
    check("Always lets that name in on later connections", "other tabs" in again.call("tabs_list") and "regular: always allowed" in text_of("agents"), text_of("agents"))
    press("agents", "Remove")
    again.close()
    four, thread = asking("regular", "four")
    check("removing the name makes it ask again", "four" not in answers and "regular asks" in text_of("agent-requests"), answers)
    four.proc.kill()
    redrawn()
    check("a request goes away when its agent does", not text_of("agent-requests"), text_of("agent-requests"))
    thread.join(10)
    log = run("setTimeout(() => arguments[0](document.getElementById('activity').textContent), 2200)")
    check("the activity log shows who was refused", "newcomer: tabs_list" in log, log[:300])
    run("document.getElementById('ask').click(); setTimeout(() => arguments[0](1), 300)")
    check("with asking off, the test's own agent works again", "Press me" in mcp.call("read_page", tabId=page))
    mcp.call("tabs_close", tabId=page)


TESTS = {
    "protocol": t_protocol, "click": t_click_trusted, "keyboard": t_keyboard, "focus": t_focus_switch, "read": t_read,
    "forms": t_forms, "frames": t_frames, "scroll": t_scroll_and_shots, "devtools": t_devtools, "script": t_script,
    "navigation": t_navigation, "dialogs": t_dialogs, "drag": t_drag, "background": t_background_tab,
    "policy": t_policy, "multi": t_multi_agent, "speed": t_agent_speed, "pointer": t_pointer,
}

mcp_socket = None


def main():
    global mcp_socket
    visible = "--visible" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    browser = Browser(ROOT / "dist/foxreach.xpi", headless=not visible)
    mcp_socket = browser.socket
    try:
        browser.start(marionette=True)
        print("installed", browser.install())
        browser.wait_for_socket()
        mcp = McpClient(browser.socket)
        if not only or "popup" in only:
            popup_tests(browser, mcp)
        # Stopping the browser just after a first install can leave the add-on recorded as active
        # but not running at the next start. Stop only once it has answered and the install settled.
        mcp.call("status")
        mcp.close()
        time.sleep(3)
        browser.stop()
        time.sleep(1)

        browser.start(marionette=False)
        browser.wait_for_socket()
        mcp = McpClient(browser.socket)
        for name, test in TESTS.items():
            if only and name not in only:
                continue
            print(f"--- {name}", flush=True)
            try:
                test(mcp)
            except Exception as e:
                check(f"{name}: finished without an exception", False, f"{type(e).__name__}: {e}")
        mcp.close()
    finally:
        browser.cleanup()
        server.shutdown()

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

