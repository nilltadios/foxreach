#!/usr/bin/env python3
"""`foxreach update` replaces the program with the latest release, safely.

Run:      uv run --python 3.15 test/update_cli.py
Expected: every line PASS, exit 0.
How:      a real single-file foxreach (built from this checkout) is installed into a throwaway home;
          a local web server plays the release (FOXREACH_RELEASE_URL), serving BUILDINFO.txt,
          SHA256SUMS and a stand-in "new" program: a small script that answers `version` with
          9.9.9 and records any `install` call. Nothing is downloaded from the internet and no
          real file outside the throwaway folder is touched.
"""
import hashlib
import http.server
import json
import os
import socket
import pathlib
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_binary  # noqa: E402
from build_install import version  # noqa: E402
sys.path.insert(0, str(ROOT / "test"))
from harness import McpClient  # noqa: E402

ASSET = {"x86_64": "foxreach-linux-x64", "aarch64": "foxreach-linux-arm64"}[platform.machine()]
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


def sha(p):
    return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()


def release(folder, ver, program, sums_ok=True):
    """Lay out a release the way the release workflow does (the files update reads)."""
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / ASSET).write_bytes(program)
    (folder / "BUILDINFO.txt").write_text(f"foxreach {ver}\ncommit test\n")
    digest = hashlib.sha256(program).hexdigest() if sums_ok else "0" * 64
    (folder / "SHA256SUMS").write_text(f"{digest}  {ASSET}\n{'1' * 64}  install.sh\n")


def stand_in(reports):
    return (f'#!/bin/sh\ncase "$1" in\n  version) echo {reports} ;;\n'
            '  install) echo "install $*" >> "$HOME/install-calls.log" ;;\nesac\n').encode()


def fake_browser(path):
    """Answers every call the way the add-on answers status, with an old add-on version."""
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(path)
    srv.listen()
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve_one, args=(conn,), daemon=True).start()


def serve_one(conn):
    with conn, conn.makefile("rw") as f:
        for line in f:
            req = json.loads(line)
            text = json.dumps({"version": "0.0.1", "trustedInput": False, "paused": False})
            f.write(json.dumps({"id": req["id"], "result": {"text": text}}) + "\n")
            f.flush()


def main():
    work = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-update-cli-"))
    served = work / "release"
    served.mkdir()
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(served), **k)

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        home = work / "home"
        bindir = home / ".local/bin"
        bindir.mkdir(parents=True)
        program = bindir / "foxreach"
        build_binary.build(work / "built", None)
        original = (work / "built").read_bytes()

        def reset():
            program.write_bytes(original)
            program.chmod(0o755)
            for p in (bindir / "foxreach.previous", home / "install-calls.log"):
                p.unlink(missing_ok=True)

        def run(*args):
            env = {"HOME": str(home), "PATH": f"{bindir}:/usr/bin:/bin", "FOXREACH_RELEASE_URL": url,
                   "FOXREACH_AGENT_CLI": "0", "XDG_RUNTIME_DIR": str(home / "run")}
            r = subprocess.run([str(program), *args], env=env, capture_output=True, text=True, timeout=120)
            return r.returncode, r.stdout + r.stderr

        cur = version()
        reset()
        release(served, cur, original)
        code, out = run("update")
        check("same version: says up to date and changes nothing",
              code == 0 and "up to date" in out and sha(program) == hashlib.sha256(original).hexdigest(), out)

        new = stand_in("9.9.9")
        release(served, "9.9.9", new)
        code, out = run("update", "--check")
        check("--check: names the new version and changes nothing",
              code == 0 and "9.9.9 is available" in out and program.read_bytes() == original, out)

        release(served, "9.9.9", new, sums_ok=False)
        code, out = run("update")
        check("checksum mismatch: refused, program unchanged",
              code != 0 and "Checksum mismatch" in out and program.read_bytes() == original, out)
        check("checksum mismatch: no temporary file left", not list(bindir.glob(".foxreach-update-*")), list(bindir.iterdir()))

        release(served, "9.9.9", stand_in("1.2.3"))
        code, out = run("update")
        check("a download that reports another version: refused, program unchanged",
              code != 0 and "does not run as foxreach 9.9.9" in out and program.read_bytes() == original, out)
        check("...and no temporary file left", not list(bindir.glob(".foxreach-update-*")))

        release(served, "9.9.9", new)
        code, out = run("update")
        check("update exits 0", code == 0, out)
        check("the program is now the new release", program.read_bytes() == new, out)
        check("the old program is kept as .previous", (bindir / "foxreach.previous").read_bytes() == original)
        check("the new program refreshed what install wrote, without touching agents",
              (home / "install-calls.log").read_text().strip() == "install install --agents none",
              (home / "install-calls.log").read_text() if (home / "install-calls.log").exists() else out)
        check("update says how to undo", "Undo: mv" in out and "foxreach.previous" in out, out)

        reset()
        release(served, "0.0.1", new)
        code, out = run("update")
        check("an older release is not installed", code == 0 and "up to date" in out and program.read_bytes() == original, out)

        # The version warning tells a release user to run foxreach update, a checkout to run update.py:
        # each MCP server is pointed at a stand-in browser whose add-on is older.
        sock_dir = work / "sock"
        sock_dir.mkdir(mode=0o700)
        sock_path = str(sock_dir / "s.sock")
        threading.Thread(target=fake_browser, args=(sock_path,), daemon=True).start()
        time.sleep(0.3)
        for label, command, advice in (("release program", [str(program), "mcp"], "foxreach update"),
                                       ("checkout", ["bun", str(ROOT / "bridge/mcp.mjs")], "scripts/update.py")):
            client = McpClient(sock_path, command=command)
            try:
                status = client.call("status")
            finally:
                client.close()
            check(f"version warning for a {label} says: {advice}", "versions differ" in status and advice in status, status)
    finally:
        server.shutdown()
        shutil.rmtree(work, ignore_errors=True)
    print("failed:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
