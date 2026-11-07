#!/usr/bin/env python3
"""Print where this browser looks for user native-messaging manifests.

Run: uv run --python 3.15 native_manifest_dir.py /path/to/browser-or-appimage
"""
import json, os, pathlib, shutil, signal, socket, subprocess, sys, tempfile, time
PORT = 47834
profile = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-diag-"))
(profile / "user.js").write_text(f'user_pref("marionette.port", {PORT});\n')
env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"} | {"MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}
proc = subprocess.Popen([sys.argv[1], "--headless", "--no-remote", "--new-instance", "--profile", profile, "--marionette"],
                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
try:
    for _ in range(120):
        try:
            sock = socket.create_connection(("127.0.0.1", PORT), timeout=60); break
        except OSError:
            time.sleep(0.25)
    stream = sock.makefile("rb"); seq = 0
    def recv():
        n = b""
        while (c := stream.read(1)) != b":":
            n += c
        return json.loads(stream.read(int(n)))
    def cmd(name, params):
        global seq; seq += 1
        d = json.dumps([0, seq, name, params]).encode(); sock.sendall(str(len(d)).encode() + b":" + d)
        _, _, err, res = recv()
        if err: raise RuntimeError(err)
        return res
    recv(); cmd("WebDriver:NewSession", {}); cmd("Marionette:SetContext", {"value": "chrome"})
    print(cmd("WebDriver:ExecuteScript", {"script": 'return Services.dirsvc.get("XREUserNativeManifests", Ci.nsIFile).path + " | sys: " + Services.dirsvc.get("XRESysNativeManifests", Ci.nsIFile).path + " | fission:" + Services.appinfo.fissionAutostart + " | rfp:" + Services.prefs.getBoolPref("privacy.resistFingerprinting") + " | fpp:" + Services.prefs.getBoolPref("privacy.fingerprintingProtection",false)', "args": []})["value"])
finally:
    proc.send_signal(signal.SIGTERM); proc.wait(timeout=20); shutil.rmtree(profile, ignore_errors=True)
