#!/usr/bin/env python3
"""The Flatpak arrangement, in a real Flatpak sandbox, without a Flatpak browser.

Run:      uv run --python 3.15 test/flatpak_sandbox.py [application id]
Expected: every line PASS, "N passed, 0 failed", exit 0. Exit 0 with one SKIP line where Flatpak,
          Bun or an installed application to borrow a sandbox from is missing.

scripts/build_install.py gives a Flatpak browser a copy of the bridge and of Bun in a folder its
sandbox sees, and shares one folder of $XDG_RUNTIME_DIR for the socket. This runs that copy as the
native host inside the sandbox of an installed application (only its sandbox is used; the
application itself is not started), with a folder shared the same way, and plays both ends:
an agent connecting to the socket from outside, and the browser on the host's stdin and stdout.
It does not show that a Flatpak browser finds the manifest and starts the host: that needs one.
"""
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_install  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"))


def borrowed_app():
    if len(sys.argv) > 1:
        return sys.argv[1]
    out = subprocess.run(["flatpak", "list", "--app", "--columns=application,runtime"], capture_output=True, text=True)
    rows = [line.split() for line in out.stdout.splitlines()]
    return next((r[0] for r in rows if len(r) == 2 and r[1].startswith("org.freedesktop.Platform/")), None)


def main():
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not shutil.which("flatpak") or not runtime_dir or pathlib.Path(build_install.runtime()).name != "bun":
        print("SKIP needs flatpak, XDG_RUNTIME_DIR and Bun")
        return 0
    app = borrowed_app()
    if not app:
        print("SKIP no installed Flatpak application on org.freedesktop.Platform to borrow a sandbox from")
        return 0
    print("sandbox of", app)
    with tempfile.TemporaryDirectory(prefix="foxreach-sandbox-test-", dir=runtime_dir) as d:
        shared = pathlib.Path(d)
        wrapper = build_install.copy_bridge(shared / "copy")
        sock = shared / "s.sock"
        host = subprocess.Popen(
            ["flatpak", "run", f"--filesystem=xdg-run/{shared.name}", f"--env=FOXREACH_SOCKET={sock}",
             f"--command={wrapper}", app],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.time() + 30
            while not sock.exists() and time.time() < deadline and host.poll() is None:
                time.sleep(0.1)
            check("the host starts inside the sandbox and its socket appears outside", sock.exists(),
                  host.stderr.read().decode() if host.poll() is not None else "no socket after 30 s")
            if not sock.exists():
                return 1
            check("the socket is for this user only", sock.stat().st_mode & 0o777 == 0o600, oct(sock.stat().st_mode))
            agent = socket.socket(socket.AF_UNIX)
            agent.settimeout(15)
            agent.connect(str(sock))
            agent.sendall(b'{"id": 7, "tool": "status"}\n')
            size = struct.unpack("<I", host.stdout.read(4))[0]
            request = json.loads(host.stdout.read(size))
            check("a request from outside reaches the browser's side of the host",
                  request.get("tool") == "status" and str(request.get("id")).endswith(":7"), request)
            body = json.dumps({"id": request["id"], "result": {"text": "from the sandbox"}}).encode()
            host.stdin.write(struct.pack("<I", len(body)) + body)
            host.stdin.flush()
            reply = json.loads(agent.makefile().readline())
            check("the answer comes back out to the agent", reply == {"id": 7, "result": {"text": "from the sandbox"}}, reply)
            agent.close()
        finally:
            host.stdin.close()  # the browser going away is what ends a native host
            try:
                host.wait(timeout=15)
            except subprocess.TimeoutExpired:
                host.kill()
        check("the host ends when the browser's side closes, and removes its socket",
              host.returncode == 0 and not sock.exists(), f"exit {host.returncode}")
    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
