#!/usr/bin/env python3
"""Does a WebExtension Experiment produce isTrusted=true input in LibreWolf?

Starts a separate headless LibreWolf on a throwaway profile and prints what a test
page observed. The user's running browser and profiles are not touched.

Run:      uv run --python 3.15 run_spike.py [--permanent]
Default:  temporary install, Marionette attached.
--permanent: permanent install of an unsigned .xpi, then a restart WITHOUT Marionette;
          the checks apply to the run after the restart (the normal daily-use case).
Expected: content_script_dispatch shows trusted=false; experiment_* steps show
          trusted=true and final_input_value is "abc". Exit 0 only in that case.
"""
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
import zipfile

HERE = pathlib.Path(__file__).resolve().parent
APPIMAGE = pathlib.Path.home() / "AppImages/librewolf.appimage"
PORT = 47831
MARIONETTE_PORT = 47832
TIMEOUT_S = int(os.environ.get("FOXREACH_TIMEOUT", 60))

PAGE = b"""<!doctype html><meta charset=utf-8><title>spike</title>
<style>body{margin:0}
#btn{position:fixed;left:50px;top:50px;width:200px;height:40px}
#inp{position:fixed;left:50px;top:150px;width:200px;height:40px}</style>
<button id=btn>button</button><input id=inp>
<script>
document.body.dataset.events = "[]";
for (const t of ["mousedown","mouseup","click","keydown","keypress","keyup","input"])
  addEventListener(t, e => {
    const a = JSON.parse(document.body.dataset.events);
    a.push({type: e.type, trusted: e.isTrusted, target: e.target.id});
    document.body.dataset.events = JSON.stringify(a);
  }, true);
</script>"""

reports = []
finished = threading.Event()


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        reports.append(body)
        self.send_response(204)
        self.end_headers()
        if body["step"] in ("done", "fatal"):
            finished.set()


def kill_instance(profile):
    """Stop only browser processes whose command line names this throwaway profile."""
    pids = []
    for d in pathlib.Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                # Signal the browser only. Killing the AppImage runtime unmounts the
                # browser's files under it, and its helpers then die with SIGBUS.
                if str(profile).encode() in (d / "cmdline").read_bytes() and not os.readlink(
                    d / "exe"
                ).endswith(".appimage"):
                    pids.append(int(d.name))
            except OSError:
                pass
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    for _ in range(80):
        alive = []
        for pid in pids:
            try:
                state = pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
                if state != "Z":  # a zombie has exited and only awaits proc.wait()
                    alive.append(pid)
            except OSError:
                pass
        if not alive:
            return
        time.sleep(0.25)
    print(f"warning: instance still running: {pids}")


def install_addon(path, temporary):
    """Minimal Marionette client: length-prefixed JSON. Test harness only.

    librewolf.cfg forces devtools.debugger.remote-enabled off and profile drop-ins are
    removed at startup, so this is the available route for a throwaway test profile.
    """
    deadline = time.time() + 30
    while True:
        try:
            sock = socket.create_connection(("127.0.0.1", MARIONETTE_PORT), timeout=30)
            break
        except OSError:
            if time.time() > deadline:
                raise
            time.sleep(0.25)
    stream = sock.makefile("rb")

    def recv():
        size = b""
        while (c := stream.read(1)) != b":":
            if not c:
                raise RuntimeError("Marionette closed the connection")
            size += c
        return json.loads(stream.read(int(size)))

    seq = 0

    def command(name, params):
        nonlocal seq
        seq += 1
        data = json.dumps([0, seq, name, params]).encode()
        sock.sendall(str(len(data)).encode() + b":" + data)
        _, _, error, result = recv()
        if error:
            raise RuntimeError(f"{name}: {error}")
        return result

    recv()  # greeting
    command("WebDriver:NewSession", {})
    result = command("Addon:Install", {"path": str(path), "temporary": temporary})
    return result["value"], sock  # keep the socket so the session stays open


def start_browser(profile, marionette, log):
    env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"}
    args = [APPIMAGE, "--headless", "--no-remote", "--new-instance", "--profile", profile]
    return subprocess.Popen(
        args + (["--marionette"] if marionette else []),
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,  # detached from this script's terminal signals
    )


def main():
    permanent = "--permanent" in sys.argv
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    debug_dir = os.environ.get("FOXREACH_DEBUG_DIR")  # keep profile and browser log here
    profile = pathlib.Path(debug_dir or tempfile.mkdtemp(prefix="foxreach-spike-"))
    (profile / "user.js").write_text(
        f'user_pref("marionette.port", {MARIONETTE_PORT});\n'
        'user_pref("extensions.experiments.enabled", true);\n'
        'user_pref("xpinstall.signatures.required", false);\n'
    )
    log = open(profile / "browser.log", "wb") if debug_dir else subprocess.DEVNULL
    target = HERE / "ext"
    if permanent:
        target = profile / "spike.xpi"
        with zipfile.ZipFile(target, "w") as z:
            for f in (HERE / "ext").iterdir():
                z.write(f, f.name)

    proc = start_browser(profile, True, log)
    got_result = False
    try:
        addon_id, _session = install_addon(target, temporary=not permanent)
        print("installed:", addon_id, "(permanent)" if permanent else "(temporary)")
        got_result = finished.wait(TIMEOUT_S)
        if permanent:
            kill_instance(profile)
            proc.wait(timeout=20)
            print("first run reports:", len(reports), "- restarting without Marionette")
            reports.clear()
            finished.clear()
            proc = start_browser(profile, False, log)
            got_result = finished.wait(TIMEOUT_S)
    finally:
        kill_instance(profile)
        proc.wait(timeout=20)
        if not debug_dir:
            shutil.rmtree(profile, ignore_errors=True)
        server.shutdown()

    for r in reports:
        print(json.dumps(r))
    if not got_result:
        print(f"FAIL: no result within {TIMEOUT_S}s")
        return 1

    by_step = {r["step"]: r for r in reports}
    events = lambda s: json.loads(by_step.get(s, {}).get("events") or "[]")
    checks = {
        "control is untrusted": any(not e["trusted"] for e in events("content_script_dispatch")),
        "click is trusted": any(
            e["type"] == "click" and e["trusted"] and e["target"] == "btn"
            for e in events("experiment_click_button")
        ),
        "keys are trusted": any(
            e["type"] == "keydown" and e["trusted"] for e in events("experiment_type")
        ),
        "text reached the input": by_step.get("final_input_value", {}).get("value") == "abc",
    }
    for name, ok in checks.items():
        print(("PASS" if ok else "FAIL"), name)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
