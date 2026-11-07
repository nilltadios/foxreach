#!/usr/bin/env python3
"""Start a browser on a profile without automation and report how far foxreach gets.

Run: uv run --python 3.15 spikes/trusted-input/profile_probe.py PROFILE [SECONDS]
Prints the host log, process state, and whether a status call is answered. Never use a live profile.
"""
import json, os, pathlib, signal, subprocess, sys, tempfile, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "test"))
from harness import APPIMAGE, McpClient
profile = pathlib.Path(sys.argv[1]).resolve(); wait = int(sys.argv[2]) if len(sys.argv) > 2 else 25
d = tempfile.mkdtemp(prefix="foxreach-probe-"); sock = f"{d}/s.sock"; hostlog = f"{d}/host.log"; log = f"{d}/browser.log"
env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"} | {"FOXREACH_SOCKET": sock, "FOXREACH_HOST_LOG": hostlog}
proc = subprocess.Popen([os.environ.get("FOXREACH_BROWSER") or APPIMAGE, "--headless", "--no-remote", "--new-instance", "--profile", profile],
                        env=env, stdout=open(log, "wb"), stderr=subprocess.STDOUT, start_new_session=True)
start = time.time()
while time.time() - start < wait and not os.path.exists(sock):
    time.sleep(0.2)
print(f"socket after {time.time() - start:.1f} s: {os.path.exists(sock)}")
print("host log:", open(hostlog).read() if os.path.exists(hostlog) else "(host never started)")
if os.path.exists(sock):
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError("status call timed out")))
    signal.alarm(15)
    try:
        c = McpClient(sock); print("status:", c.call("status")); c.close()
    except Exception as e:
        print("status failed:", repr(e))
    signal.alarm(0)
print("browser log tail:", open(log, errors="replace").read()[-500:])
for p in pathlib.Path("/proc").iterdir():
    if p.name.isdigit():
        try:
            if os.path.basename(os.readlink(p / "exe")).startswith("librewolf") and not os.readlink(p / "exe").endswith(".appimage") and str(profile).encode() in (p / "cmdline").read_bytes():
                os.kill(int(p.name), signal.SIGTERM)
        except OSError:
            pass
proc.wait(timeout=30)
