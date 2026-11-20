#!/usr/bin/env python3
"""The request window, on a real screen: the card and the browser's own permission prompt both fit,
and the window keeps the size the user gives it. Both builds ask in this window.

Run: FOXREACH_BROWSER=/usr/bin/librewolf uv run --python 3.15 test/request_window.py [--full] [--rfp] [OUT_DIR]
The build without privileged code (the one Mozilla signs) unless --full. The full build holds every
site already, so Allow site asks the browser nothing there; every other check is the same.
Expected: all PASS, exit 0. privacy.resistFingerprinting is off unless --rfp (LibreWolf turns it on
by default; it gives new windows a standard size and spoofs window.outerHeight). Needs a desktop session (the prompt is never drawn headless). With
OUT_DIR, also saves the measurements (request-window.json) and, where the linux-computer-use `lcu`
command is found (LCU or PATH), a picture of the window with the browser's prompt open.

Unlike the other tests, the browser's permission prompt stays on here (harness.py turns it off), so
the add-on asks the browser exactly as for the user. A throwaway profile; no real one is touched.
"""
import os
if os.name == "nt":
    raise SystemExit("This test runs on Linux and macOS only (it uses a throwaway HOME, as test/untrusted.py).")

import http.server, json, pathlib, shutil, subprocess, sys, threading, time
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, Browser, McpClient, ToolError

if sys.platform.startswith("linux") and not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
    print("SKIP no desktop session: the browser draws no permission prompt without one")
    sys.exit(0)

PORT = 47856
PAGE = b"<!doctype html><title>R</title><p>request window test page"
RFP = "--rfp" in sys.argv[1:]
FULL = "--full" in sys.argv[1:]
_paths = [a for a in sys.argv[1:] if a not in ("--rfp", "--full")]
OUT = pathlib.Path(_paths[0]) if _paths else None


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(PAGE)


fails = 0
report = {}
def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
b = Browser(ROOT / ("dist/foxreach.xpi" if FULL else "dist/foxreach-untrusted.xpi"), headless=False)
try:
    prefs = (b.profile / "user.js").read_text()
    if not FULL:
        prefs = prefs.replace('user_pref("extensions.experiments.enabled", true);\n', "")
    prefs = prefs.replace('user_pref("extensions.webextOptionalPermissionPrompts", false);',
                          'user_pref("extensions.webextOptionalPermissionPrompts", true);')
    prefs += f'user_pref("privacy.resistFingerprinting", {str(RFP).lower()});\n'
    (b.profile / "user.js").write_text(prefs)
    report["resistFingerprinting"] = RFP
    report["build"] = "full" if FULL else "signed"
    b.start(marionette=True); b.install(); b.wait_for_socket()
    mar = b.marionette
    if FULL:  # it starts with every site, as it holds them all; the checks below are about asking
        b.popup_message({"popup": "listedOnly"})
        mar.command("WebDriver:Navigate", {"url": "about:blank"})
    m = McpClient(b.socket)
    main = mar.command("WebDriver:GetWindowHandle", {})["value"]  # chrome scripts run from here

    def chrome(script, *args):
        mar.command("Marionette:SetContext", {"value": "chrome"})
        try:
            return mar.command("WebDriver:ExecuteScript", {"script": script, "args": list(args)})["value"]
        finally:
            mar.command("Marionette:SetContext", {"value": "content"})

    FIND = ('const w = [...Services.wm.getEnumerator(null)].find((w) => w.gBrowser && '
            'w.gBrowser.currentURI.spec.endsWith("popup.html?requests"));')

    def window_state():
        return chrome(FIND + 'if (!w) return null; return {state: w.windowState, outerWidth: w.outerWidth, '
                      'outerHeight: w.outerHeight, innerWidth: w.innerWidth, innerHeight: w.innerHeight};')

    def wait(fn, timeout=10, step=0.3):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if (v := fn()):
                return v
            time.sleep(step)
        return None

    def ask_for(host):
        """An agent asks for a site the user has not allowed: the request window opens."""
        try:
            m.call("tabs_create", url=f"http://{host}:{PORT}/")
            return "no error"
        except ToolError as e:
            return str(e)

    def request_handle():
        for h in mar.command("WebDriver:GetWindowHandles", {}):
            mar.command("WebDriver:SwitchToWindow", {"handle": h, "focus": False})
            if mar.command("WebDriver:GetCurrentURL", {})["value"].endswith("popup.html?requests"):
                return h
        return None

    def content_fit():
        """What the window's page measures: its content against the room it has, and each button."""
        return mar.command("WebDriver:ExecuteScript", {"args": [], "script":
            "const v = document.getElementById('request-view');"
            "return {scrollHeight: document.documentElement.scrollHeight, contentHeight: v.scrollHeight,"
            " innerWidth, innerHeight, buttons: [...document.querySelectorAll('.action')].map((b) => {"
            " const r = b.getBoundingClientRect(); return {label: b.textContent, right: r.right, bottom: r.bottom}; })};"})["value"]

    # 1. The window opens, and its card fits it whole.
    check("an agent asking for a site is refused", "not allowed on 127.0.0.1 yet" in ask_for("127.0.0.1"))
    check("the request window opens", wait(window_state) is not None)
    handle = wait(request_handle)
    check("its page is found", handle is not None)
    time.sleep(1.5)  # drawn, then fitted to the card
    fit = content_fit()
    report["card"] = fit
    check("the card fits without scrolling", fit["scrollHeight"] <= fit["innerHeight"], fit)
    check("every button of the card is inside the window",
          all(x["right"] <= fit["innerWidth"] and x["bottom"] <= fit["innerHeight"] for x in fit["buttons"]), fit)

    # 2. Allow site: the browser's own prompt opens over the window, and both its buttons fit.
    for attempt in range(5):  # the page redraws its cards every 2 s, which can make the element stale
        try:
            element = mar.command("WebDriver:FindElement", {"using": "css selector", "value": ".card .action.primary"})["value"]
            mar.command("WebDriver:ElementClick", {"id": next(iter(element.values()))})  # a real click, as the browser requires
            break
        except RuntimeError as e:
            if "stale element" not in str(e) or attempt == 4:
                raise
    mar.command("WebDriver:SwitchToWindow", {"handle": main, "focus": False})  # the request window closes later
    if FULL:  # every site is the add-on's already: nothing to ask the browser
        check("the window closes once the site is allowed", wait(lambda: window_state() is None))
        check("the site is then allowed", ask_for("127.0.0.1") == "no error")
    prompt = None if FULL else wait(lambda: chrome(FIND + 'if (!w) return null;'
        'const n = w.document.getElementById("addon-webext-permissions-notification");'
        'const p = w.PopupNotifications.panel;'
        'if (!n || n.hidden || !n.button || p.state !== "open") return null;'
        'const r = (e) => { const b = e.getBoundingClientRect(); return {left: b.left, top: b.top, right: b.right, bottom: b.bottom}; };'
        'const s = p.getOuterScreenRect();'
        'return {panel: {left: s.left - w.mozInnerScreenX, top: s.top - w.mozInnerScreenY, width: s.width, height: s.height},'
        ' allow: r(n.button), deny: r(n.secondaryButton), innerWidth: w.innerWidth, innerHeight: w.innerHeight};'))
    report["prompt"] = prompt
    if not FULL:
        check("the browser asks in the request window", prompt is not None)
    if prompt:
        time.sleep(0.8)  # let it finish drawing for the picture
        lcu = os.environ.get("LCU") or shutil.which("lcu")
        if OUT and lcu:
            OUT.mkdir(parents=True, exist_ok=True)
            subprocess.run([lcu, "capture", "shot", str(OUT / "request-window-prompt.png"),
                            "--window", "title:foxreach: a request needs your answer", "--no-raise"], check=False)
        # The panel's own rectangle includes a few pixels of shadow; its buttons are what must show.
        panel = prompt["panel"]
        for name in ("allow", "deny"):
            right, bottom = panel["left"] + prompt[name]["right"], panel["top"] + prompt[name]["bottom"]
            check(f"the browser's {name.title()} button is inside the window",
                  right <= prompt["innerWidth"] and bottom <= prompt["innerHeight"], prompt)
        # Allow: the site is granted, the request answered, and the window closes by itself.
        chrome(FIND + 'w.document.getElementById("addon-webext-permissions-notification").button.click(); return 1;')
        check("the window closes once the site is granted", wait(lambda: window_state() is None))
        check("the site is then allowed", ask_for("127.0.0.1") == "no error")

    # 3. Maximized by the user, the window stays maximized, through several of the page's refreshes.
    check("an agent asking for another site is refused", "not allowed on localhost yet" in ask_for("localhost"))
    check("the request window opens again", wait(window_state) is not None)
    time.sleep(1.5)
    before = window_state()
    chrome(FIND + "w.maximize(); return 1;")
    maximized = wait(lambda: (s := window_state()) and s["state"] == 1 and s)  # STATE_MAXIMIZED
    time.sleep(5)  # the page refreshes every 2 s
    after = window_state()
    report["maximize"] = {"before": before, "maximized": maximized, "after_5s": after}
    check("the window maximizes", bool(maximized), before)
    check("it is still maximized 5 s later", after and after["state"] == 1, after)
    # Restored, the window may be fitted again; a bigger size the user drags to is kept.
    chrome(FIND + "w.restore(); return 1;")
    time.sleep(2.5)
    chrome(FIND + "w.resizeTo(w.outerWidth + 120, w.outerHeight + 120); return 1;")
    time.sleep(0.8)
    grown = window_state()
    time.sleep(5)
    kept = window_state()
    report["resize"] = {"grown": grown, "after_5s": kept}
    check("a size the user gives it is kept",
          grown and kept and abs(kept["outerWidth"] - grown["outerWidth"]) < 4 and abs(kept["outerHeight"] - grown["outerHeight"]) < 4,
          report["resize"])
    m.close()
    chrome(FIND + "if (w) w.close(); return 1;")  # the localhost request is left unanswered
    wait(lambda: window_state() is None)

    # 4. A new agent waiting for approval: its card (three buttons) fits the window too.
    b.popup_message({"popup": "dismissSite", "host": "localhost"})
    b.popup_message({"popup": "setAsk", "value": True})
    mar.command("WebDriver:Navigate", {"url": "about:blank"})
    main = mar.command("WebDriver:GetWindowHandle", {})["value"]
    answer = {}
    def new_agent():
        c = McpClient(b.socket, name="request-window-test-agent")
        try:
            answer["tabs"] = c.call("tabs_list")  # waits for the user's answer (status would not)
        except Exception as e:  # noqa: BLE001
            answer["error"] = str(e)
        c.close()
    threading.Thread(target=new_agent, daemon=True).start()
    check("an agent waiting for approval opens the request window", wait(window_state, timeout=15) is not None)
    handle = wait(request_handle)
    # The card is drawn on the page's next refresh (every 2 s), then the window is fitted to it.
    fit = (handle and wait(lambda: (f := content_fit()) and len(f["buttons"]) == 3 and f)) or {}
    time.sleep(1)
    fit = content_fit() if fit else fit
    report["agent_card"] = fit
    check("the agent card fits without scrolling", fit and fit["scrollHeight"] <= fit["innerHeight"], fit)
    check("its three buttons are inside the window", fit and len(fit["buttons"]) == 3 and
          all(x["right"] <= fit["innerWidth"] and x["bottom"] <= fit["innerHeight"] for x in fit["buttons"]), fit)
    for attempt in range(5):
        try:
            element = mar.command("WebDriver:FindElement", {"using": "css selector", "value": ".card .action.primary"})["value"]
            mar.command("WebDriver:ElementClick", {"id": next(iter(element.values()))})
            break
        except RuntimeError as e:
            if "stale element" not in str(e) or attempt == 4:
                raise
    mar.command("WebDriver:SwitchToWindow", {"handle": main, "focus": False})
    check("Allow lets the agent in", wait(lambda: answer, timeout=10) and "tabs" in answer, answer)
finally:
    if OUT:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "request-window.json").write_text(json.dumps(report, indent=2))
    b.cleanup()
    srv.shutdown()
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
