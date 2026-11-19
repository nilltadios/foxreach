#!/usr/bin/env python3
"""How browser connections ended: history.jsonl beside the socket, status's `history`, and the
not-connected error's "Last connection" note.

Run:      uv run --python 3.15 test/history.py
Browser:  FOXREACH_BROWSER (a path or a command), as for test/e2e.py
Expected: every line PASS, exit 0. Throwaway profile only; the browser is quit, killed and
          stopped several times.
"""
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, Browser, McpClient, ToolError  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else f' -- {detail}'}", flush=True)


def records(b):
    f = pathlib.Path(b.dir) / "history.jsonl"
    return [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []


def host_pid(b):
    """The native host that started last."""
    return [r for r in records(b) if r["event"] == "start"][-1]["hostPid"]


def end_of(b, pid, timeout=8):
    """The end record of host `pid`, waiting for the host to write it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ends = [r for r in records(b) if r["event"] == "end" and r["hostPid"] == pid]
        if ends:
            return ends[0]
        time.sleep(0.2)
    return None


def new_host(b, old, timeout=30):
    """Wait for a host other than `old` to start and open its socket."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if host_pid(b) != old and os.path.exists(b.socket):
            return host_pid(b)
        time.sleep(0.2)
    raise RuntimeError("no new native host started")


def host_parent(b):
    """The pid of the browser process that started the current native host."""
    stat = pathlib.Path(f"/proc/{host_pid(b)}/stat").read_text()
    return int(stat.rsplit(")", 1)[1].split()[1])


def wait_exit(b, timeout=30):
    try:
        b.proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    b.proc = None


def main():
    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    b = Browser(ROOT / "dist/foxreach.xpi")
    try:
        b.start(marionette=True)
        b.install()
        b.wait_for_socket()
        McpClient(b.socket).call("status")
        time.sleep(3)  # the first install restarts the add-on once; let it settle
        start = [r for r in records(b) if r["event"] == "start"]
        check("the host records its start", start and start[-1]["profile"] == str(b.profile), records(b))

        # Installing the add-on again restarts it while the browser stays open.
        pid = host_pid(b)
        b.marionette.command("Addon:Install", {"path": str(b.xpi), "temporary": False, "allowPrivateBrowsing": True})
        end = end_of(b, pid)
        check("an add-on restart with the browser open is 'addon'", end and end["how"] == "addon", end)
        pid = new_host(b, pid)
        McpClient(b.socket).call("status")
        time.sleep(2)

        try:
            b.marionette.command("Marionette:Quit", {"flags": ["eAttemptQuit"]})
        except RuntimeError:
            pass  # the connection may close before the reply
        b.marionette = None
        wait_exit(b)
        end = end_of(b, pid)
        check("a normal quit is 'quit'", end and end["how"] == "quit", end)

        b.start(marionette=False)
        b.wait_for_socket()
        mcp = McpClient(b.socket)
        status = json.loads(mcp.call("status"))
        hist = status.get("history", [])
        check("status lists earlier sessions, newest first, the current one open",
              len(hist) >= 3 and hist[0]["ended"] is None and hist[1]["how"] == "quit" and "normally" in hist[1]["detail"], hist)
        mcp.close()

        pid = host_pid(b)
        os.kill(host_parent(b), signal.SIGKILL)
        end = end_of(b, pid)
        check("a killed browser is 'lost'", end and end["how"] == "lost", end)
        wait_exit(b, 10)
        mcp = McpClient(b.socket)
        try:
            mcp.call("status")
            check("status with no browser fails", False)
        except ToolError as e:
            check("the not-connected error says how the last connection ended",
                  "Last connection" in str(e) and "without quitting normally" in str(e), str(e))
        mcp.close()

        # SIGTERM, as a session logout or `kill` sends: recorded, whichever way the browser takes it.
        b.start(marionette=False)
        b.wait_for_socket()
        time.sleep(2)
        pid = host_pid(b)
        os.kill(host_parent(b), signal.SIGTERM)
        end = end_of(b, pid, timeout=30)
        check("SIGTERM to the browser is recorded", end is not None and end["how"] in ("quit", "lost"), end)
        print(f"     (SIGTERM was recorded as {end and end['how']!r})")
        wait_exit(b, 30)
    finally:
        b.cleanup()
    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
