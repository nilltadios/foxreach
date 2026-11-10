#!/usr/bin/env python3
"""What WebDriver BiDi would give foxreach on a browser that refuses the Experiment, and at what cost.

Run:      uv run --python 3.15 --with websockets spikes/bidi/probe.py [browser spec]
          (default: FOXREACH_BROWSER; a Flatpak such as flatpak:org.mozilla.firefox works too)
Expected: one line per question, "yes"/"no" with the evidence; exit 0. Results: FINDINGS.md.
Touches:  a throwaway profile only (inside the Flatpak's own folder for a Flatpak), removed at the end.

BiDi is the browser's own automation protocol. It is switched on by starting the browser with
--remote-debugging-port, and then listens on a local TCP port.
"""
import asyncio
import http.server
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

import websockets

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import browsers  # noqa: E402

PORT, BIDI = 47861, 47862
PAGE = b"""<!doctype html><title>Probe</title>
<button id=b style="position:fixed;left:20px;top:20px;width:120px;height:30px" onclick="window.trusted=event.isTrusted">Click</button>
<button id=a style="position:fixed;left:20px;top:60px;width:120px;height:30px" onclick="window.answer=confirm('sure?')">Ask</button>
<div id=src draggable=true style="position:fixed;left:20px;top:110px;width:80px;height:40px;background:#c33"
  ondragstart="event.dataTransfer.setData('text/plain','cargo');window.dragStarted=true">drag</div>
<div id=dst style="position:fixed;left:220px;top:110px;width:120px;height:60px;background:#39c"
  ondragover="event.preventDefault()" ondrop="event.preventDefault();window.dropped=event.dataTransfer.getData('text/plain')">drop</div>
<input type=file id=f style="position:fixed;left:20px;top:190px">
<script>document.cookie='probe=secret; path=/'; console.log('hello from the page')</script>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)


class Bidi:
    """One reader hands replies to their callers and keeps events, so a call may wait while a dialog is open."""

    def __init__(self, ws):
        self.ws, self.seq, self.events, self.waiting = ws, 0, [], {}
        self.reader = asyncio.ensure_future(self.read())

    async def read(self):
        async for raw in self.ws:
            message = json.loads(raw)
            if message.get("id") in self.waiting:
                self.waiting.pop(message["id"]).set_result(message)
            else:
                self.events.append(message)

    async def call(self, method, **params):
        self.seq += 1
        reply = self.waiting[self.seq] = asyncio.get_running_loop().create_future()
        await self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params}))
        message = await asyncio.wait_for(reply, 20)
        if message.get("type") == "error":
            raise RuntimeError(f"{method}: {message.get('error')}: {message.get('message')}")
        return message.get("result", {})

    async def event(self, name, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            for e in self.events:
                if e.get("method") == name:
                    self.events.remove(e)
                    return e["params"]
            await asyncio.sleep(0.1)
        return None

    async def js(self, context, code):
        result = await self.call("script.evaluate", expression=code, target={"context": context}, awaitPromise=True)
        return result.get("result", {}).get("value", result.get("result", {}).get("type"))


def say(question, yes, evidence=""):
    print(f"{'yes' if yes else 'no ':3}  {question}" + (f"  [{evidence}]" if evidence != "" else ""), flush=True)


async def probe(upload):
    async with websockets.connect(f"ws://127.0.0.1:{BIDI}/session", max_size=None) as ws:
        b = Bidi(ws)
        # Without this the browser dismisses a page's dialog by itself before the client can answer.
        await b.call("session.new", capabilities={"alwaysMatch": {"unhandledPromptBehavior": {"default": "ignore"}}})
        await b.call("session.subscribe", events=["log.entryAdded", "browsingContext.userPromptOpened"])
        first = (await b.call("browsingContext.getTree"))["contexts"][0]["context"]
        url = f"http://127.0.0.1:{PORT}/"
        await b.call("browsingContext.navigate", context=first, url=url, wait="complete")

        say("pages can tell the browser is automated (navigator.webdriver)", await b.js(first, "navigator.webdriver") is True)
        pointer = lambda *steps: b.call("input.performActions", context=first, actions=[  # noqa: E731
            {"type": "pointer", "id": "mouse", "parameters": {"pointerType": "mouse"}, "actions": list(steps)}])
        move = lambda x, y: {"type": "pointerMove", "x": x, "y": y, "duration": 60}  # noqa: E731
        down, up = {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0}
        await pointer(move(60, 35), down, up)
        say("a click is trusted (event.isTrusted)", await b.js(first, "window.trusted") is True)
        log = await b.event("log.entryAdded")
        say("console messages can be read", bool(log) and "hello from the page" in json.dumps(log), (log or {}).get("text"))
        await pointer(move(60, 130), down, move(120, 135), move(260, 140), move(270, 140), up)
        await asyncio.sleep(0.5)
        say("a drag moves data to a drop target (HTML drag and drop)", await b.js(first, "window.dropped") == "cargo",
            f"dragstart={await b.js(first, 'window.dragStarted')}, dropped={await b.js(first, 'window.dropped')}")
        clicking = asyncio.ensure_future(pointer(move(60, 75), down, up))  # does not return while the dialog is open
        prompt = await b.event("browsingContext.userPromptOpened")
        if prompt:
            await b.call("browsingContext.handleUserPrompt", context=first, accept=True)
            await asyncio.wait_for(clicking, 10)
        say("a page dialog can be seen and answered", bool(prompt) and await b.js(first, "window.answer") is True, (prompt or {}).get("message"))
        node = (await b.call("browsingContext.locateNodes", context=first, locator={"type": "css", "value": "#f"}))["nodes"][0]
        await b.call("input.setFiles", context=first, element={"sharedId": node["sharedId"]}, files=[str(upload)])
        say("a file can be attached to a file input", await b.js(first, "document.getElementById('f').files[0]?.name") == upload.name)

        # What the port lets its client reach: is it only the tabs it made, as with foxreach's shared tabs?
        other = (await b.call("browsingContext.create", type="tab"))["context"]
        tree = (await b.call("browsingContext.getTree"))["contexts"]
        say("the client sees every tab of the browser, not only chosen ones", len(tree) >= 2, f"{len(tree)} tabs listed")
        cookies = (await b.call("storage.getCookies"))["cookies"]
        say("the client can read cookies, the logins themselves", any(c["name"] == "probe" for c in cookies),
            ", ".join(f"{c['name']}={c['value'].get('value')}" for c in cookies)[:80])
        await b.call("browsingContext.close", context=other)
        await b.call("session.end")
        b.reader.cancel()


async def access():
    """Who can connect to the port."""
    listening = subprocess.run(["ss", "-ltnH", f"sport = :{BIDI}"], capture_output=True, text=True).stdout.split()
    say("the port listens on this machine only", bool(listening) and listening[3].startswith(("127.0.0.1", "[::1]")), listening[3] if listening else "")
    say("any local program can connect: there is no password or key", True, "this script supplied none")
    try:
        async with websockets.connect(f"ws://127.0.0.1:{BIDI}/session", origin="https://example.org"):
            say("a web page cannot connect (the browser checks where the request comes from)", False, "connection accepted")
    except Exception as e:  # noqa: BLE001
        say("a web page cannot connect (the browser checks where the request comes from)", True, str(e)[:60])


def main():
    browser = browsers.choose(sys.argv[1] if len(sys.argv) > 1 else None)
    base = browser.home / "foxreach" if browser.app_id else None
    if base:
        base.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="bidi-", dir=base))
    profile = work / "profile"
    profile.mkdir()
    (profile / "user.js").write_text(
        'user_pref("termsofuse.bypassNotification", true);\nuser_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);\n'
        'user_pref("browser.aboutwelcome.enabled", false);\nuser_pref("browser.shell.checkDefaultBrowser", false);\n')
    upload = work / "notes.txt"
    upload.write_text("x")
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proc = browser.popen(["--headless", "--no-remote", "--new-instance", "--profile", profile, "--remote-debugging-port", str(BIDI)],
                         stdout=open(work / "browser.log", "ab"), stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                socket.create_connection(("127.0.0.1", BIDI), timeout=1).close()
                break
            except OSError:
                time.sleep(0.3)
        version = subprocess.run(browser.command(["--version"]), capture_output=True, text=True).stdout.strip()
        print(f"{browser.name} ({browser.packaging}): {version}")
        say("this build offers BiDi when started with --remote-debugging-port", time.time() < deadline)
        asyncio.run(probe(upload))
        asyncio.run(access())
    finally:
        for d in pathlib.Path("/proc").iterdir():
            try:
                if d.name.isdigit() and str(profile).encode() in (d / "cmdline").read_bytes() \
                        and os.path.basename(os.readlink(d / "exe")) not in ("bwrap", "flatpak", "xdg-dbus-proxy", "uv") \
                        and not os.readlink(d / "exe").lower().endswith(".appimage") and int(d.name) != os.getpid() \
                        and not os.path.basename(os.readlink(d / "exe")).startswith("python"):
                    os.kill(int(d.name), signal.SIGTERM)
            except OSError:
                pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            print("warning: the browser was still running after 30 s")
        server.shutdown()
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
