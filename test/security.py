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
import threading
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


def with_home(home, fn):
    """fn's result with HOME set to home for the MCP servers it starts."""
    saved = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        return fn()
    finally:
        os.environ["HOME"] = saved


def mac_library_checks(work, mcp, visible):
    """~/Library on macOS: refused for writes too, whatever HOME says, except cloud documents."""
    real_home = pathlib.Path(os.path.expanduser("~"))
    settings = real_home / "Library/Application Support/foxreach-security-test/settings.json"
    out = error_of(lambda: mcp.call("javascript", tabId=1, code="1", save_to=str(settings)))
    check("save_to does not write into ~/Library", "refuses ~/Library" in out and not settings.exists(), out)
    link = work / "library-link"
    link.symlink_to(real_home / "Library")
    out = error_of(lambda: mcp.call("javascript", tabId=1, code="1", save_to=str(link / "x.json")))
    check("...nor through a link to it", "refuses ~/Library" in out, out)
    out = error_of(lambda: mcp.call("screenshot", tabId=1, save_to=str(link / "x.png")))
    check("...nor an image there", "refuses ~/Library" in out, out)

    home_link = work / "home-link"
    home_link.symlink_to(real_home)
    for label, home in (("HOME is a link to the home folder", home_link), ("HOME is another folder", work / "elsewhere")):
        def attempt():
            client = McpClient(str(work / "none.sock"))
            try:
                return error_of(lambda: client.call("file_upload", tabId=1, ref="r1", paths=[str(visible)]))
            finally:
                client.close()
        out = with_home(home, attempt)
        check(f"a file in ~/Library is refused when {label}", "refuses files in ~/Library" in out, out)

    # A made-up home: cloud documents under its Library pass, its settings do not.
    fake = work / "fake-home"
    cv = fake / "Library/CloudStorage/Dropbox/cv.pdf"
    icloud = fake / "Library/Mobile Documents/com~apple~CloudDocs/notes.txt"
    prefs = fake / "Library/Preferences/app.plist"
    for f in (cv, icloud, prefs):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")

    def uploads():
        client = McpClient(str(work / "none.sock"))
        try:
            return [error_of(lambda f=f: client.call("file_upload", tabId=1, ref="r1", paths=[str(f)])) for f in (cv, icloud, prefs)]
        finally:
            client.close()
    got = with_home(fake, uploads)
    check("documents in Dropbox, OneDrive or Google Drive (~/Library/CloudStorage) can be uploaded",
          "refuses" not in got[0] and "browser" in got[0].lower(), got[0])
    check("...and in iCloud Drive", "refuses" not in got[1] and "browser" in got[1].lower(), got[1])
    check("...while the settings beside them cannot", "refuses files in ~/Library" in got[2], got[2])


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
        if sys.platform == "darwin":
            # ~/Library holds on macOS what hidden folders hold on Linux: keychains, cookies, settings.
            library = pathlib.Path.home() / "Library/Preferences/.GlobalPreferences.plist"
            visible = next((p for p in (pathlib.Path.home() / "Library/Preferences").glob("[!.]*.plist")), library)
            check("a file in ~/Library is not uploaded", "refuses files in ~/Library" in upload(str(visible)), upload(str(visible)))
            check("...whatever the case of its path", "refuses files in ~/Library" in upload(str(visible).replace("/Library/", "/library/")))
            mac_library_checks(work, mcp, visible)
        else:
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

        # "mod" reaches the add-on as a key it knows, so an add-on from before "mod" does not drop it.
        seen = []
        fake = work / "fake"
        fake.mkdir(mode=0o700)
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(fake / "s.sock"))
        listener.listen(1)

        def answer():
            conn, _ = listener.accept()
            for line in conn.makefile():
                request = json.loads(line)
                seen.append(request)
                conn.sendall(json.dumps({"id": request["id"], "result": {"text": "ok"}}).encode() + b"\n")
        threading.Thread(target=answer, daemon=True).start()
        modded = McpClient(str(fake / "s.sock"))
        modded.call("press_key", tabId=1, keys="mod+a Delete")
        modded.call("click", tabId=1, x=1, y=1, modifiers=["mod", "shift"])
        modded.close()
        listener.close()
        key = "meta" if sys.platform == "darwin" else "ctrl"
        sent = {r["tool"]: r["args"] for r in seen}
        check(f"mod reaches the add-on as {key}, in keys and in modifiers",
              sent.get("press_key", {}).get("keys") == f"{key}+a Delete" and sent.get("click", {}).get("modifiers") == [key, "shift"], seen)

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
