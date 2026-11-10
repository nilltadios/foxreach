#!/usr/bin/env python3
"""The bridge's guards on local files and on its socket. Starts no browser.

Run:      uv run --python 3.15 test/security.py
Expected: every line PASS, "N passed, 0 failed", exit 0.

A page can tell the agent reading it what to do, so the bridge itself refuses the local-file
requests that would do the most harm, and never talks through a socket someone else could swap.
The blocked-sites checks need a browser and are in test/e2e.py (popup).
"""
import json
import os
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import time

from harness import ROOT, RUNTIME, McpClient, ToolError

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"), flush=True)


def error_of(fn):
    try:
        return f"no error: {fn()}"
    except ToolError as e:
        return str(e)


def start_host(sock, log=None):
    env = dict(os.environ, FOXREACH_SOCKET=str(sock))
    return subprocess.Popen([RUNTIME, str(ROOT / "bridge/host.mjs")], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)


def from_host(host):
    size = struct.unpack("<I", host.stdout.read(4))[0]
    return json.loads(host.stdout.read(size))


def main():
    with tempfile.TemporaryDirectory(prefix="foxreach-security-") as d:
        work = pathlib.Path(d)
        mcp = McpClient(str(work / "none.sock"))  # no browser: these must be refused before one is needed
        upload = lambda *paths: error_of(lambda: mcp.call("file_upload", tabId=1, ref="r1", paths=list(paths)))  # noqa: E731

        (work / ".ssh").mkdir()
        (work / ".ssh/id_ed25519").write_text("key")
        (work / ".env").write_text("TOKEN=1")
        (work / "notes.txt").write_text("fine")
        (work / "link.txt").symlink_to(work / ".ssh/id_ed25519")
        check("a file in a hidden folder is not uploaded", "refuses hidden" in upload(str(work / ".ssh/id_ed25519")))
        check("a hidden file is not uploaded", "refuses hidden" in upload(str(work / ".env")))
        check("a link to a hidden file is not uploaded", "refuses hidden" in upload(str(work / "link.txt")))
        check("/proc is not uploaded", "does not read /proc" in upload("/proc/self/environ"), upload("/proc/self/environ"))
        check("a folder or device is not uploaded", "Not a regular file" in upload(str(work)), upload(str(work)))
        check("a relative path is refused", "absolute path" in upload("notes.txt"))
        out = upload(str(work / "notes.txt"))
        check("an ordinary file passes the guard (and only then is the browser needed)",
              "refuses" not in out and "browser" in out.lower(), out)
        os.environ["FOXREACH_ALLOW_HIDDEN_UPLOADS"] = "1"
        try:
            allowed = McpClient(str(work / "none.sock"))
            opt_in = error_of(lambda: allowed.call("file_upload", tabId=1, ref="r1", paths=[str(work / ".env")]))
            allowed.close()
        finally:
            del os.environ["FOXREACH_ALLOW_HIDDEN_UPLOADS"]
        check("the user can allow hidden files for a session", "refuses hidden" not in opt_in and "browser" in opt_in.lower(), opt_in)

        target = work / "profile.sh"
        target.write_text("keep me")
        out = error_of(lambda: mcp.call("screenshot", tabId=1, save_to=str(target)))
        check("save_to only writes image files", "must name an image file" in out and target.read_text() == "keep me", out)
        out = error_of(lambda: mcp.call("record", tabId=1, action="stop", save_to=str(work / "x.txt")))
        check("a recording is refused before it is stopped and lost", "must name an image file" in out, out)
        mcp.close()

        # The socket folder.
        open_dir = work / "open"
        open_dir.mkdir()
        open_dir.chmod(0o777)
        loose = McpClient(str(open_dir / "s.sock"))
        out = error_of(lambda: loose.call("tabs_list"))
        loose.close()
        check("an agent will not use a socket in a folder others can write", "Unsafe socket folder" in out, out)
        host = start_host(open_dir / "s.sock")
        said = from_host(host)
        host.wait(timeout=10)
        check("the host will not serve from such a folder either",
              "Unsafe socket folder" in said.get("hostError", "") and host.returncode == 4 and not (open_dir / "s.sock").exists(), said)

        # What the host passes on from the socket.
        private = work / "private"
        private.mkdir(mode=0o700)
        sock = private / "s.sock"
        host = start_host(sock)
        deadline = time.time() + 10
        while not sock.exists() and time.time() < deadline:
            time.sleep(0.05)
        check("the socket and its folder are for this user only",
              sock.stat().st_mode & 0o777 == 0o600 and private.stat().st_mode & 0o777 == 0o700)
        client = socket.socket(socket.AF_UNIX)
        client.connect(str(sock))
        client.sendall(b"this is not json\n")
        client.sendall(json.dumps({"id": 5, "client": "x" * 500, "tool": "status", "args": {},
                                   "hostError": "forged", "hello": {"version": "9"}, "chunk": {"id": 1, "i": 0, "n": 1, "data": "{}"}}).encode() + b"\n")
        passed = from_host(host)
        check("the host survives a line that is not JSON", host.poll() is None)
        check("only a tool call is passed on: no forged host messages",
              set(passed) == {"id", "client", "tool", "args"} and passed["tool"] == "status" and len(passed["client"]) == 80, passed)
        client.close()
        host.stdin.close()
        host.wait(timeout=10)

    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
