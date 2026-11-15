#!/usr/bin/env python3
"""Several browsers connected at once: each native host on its own socket, agents choosing one.

Run:      uv run --python 3.15 test/multi_browser.py
Expected: every line PASS, exit 0.
How:      two real native hosts (bridge/host.mjs) in a private XDG_RUNTIME_DIR, each with a stand-in
          browser on its stdin/stdout that answers every tool call with its own name; real MCP
          servers (bridge/mcp.mjs) as agents. No browser is started and no real setting is touched
          (XDG_CONFIG_HOME is a throwaway folder too).
"""
import json
import os
import pathlib
import signal
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness import ROOT, RUNTIME, McpClient, ToolError  # noqa: E402

fails = 0
VERSION = json.loads((ROOT / "extension/manifest.json").read_text())["version"]  # no version-mismatch notes


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


class FakeBrowser:
    """A native host started as a browser would, answering each call with this browser's name."""

    def __init__(self, label, profile):
        self.label = label
        env = dict(os.environ, FOXREACH_HOST_PROFILE=str(profile))
        self.proc = subprocess.Popen([RUNTIME, str(ROOT / "bridge/host.mjs")], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.send({"hello": {"version": VERSION, "trustedInput": True}})
        threading.Thread(target=self.answer, daemon=True).start()

    def send(self, message):
        body = json.dumps(message).encode()
        self.proc.stdin.write(struct.pack("<I", len(body)) + body)
        self.proc.stdin.flush()

    def answer(self):
        while True:
            head = self.proc.stdout.read(4)
            if len(head) < 4:
                return
            message = json.loads(self.proc.stdout.read(struct.unpack("<I", head)[0]))
            if "tool" in message:
                text = json.dumps({"version": VERSION, "browser": {"name": self.label}}) if message["tool"] == "status" else f"from {self.label}"
                self.send({"id": message["id"], "result": {"text": text}})

    def stop(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def foxreach(*args):
    r = subprocess.run([RUNTIME, str(ROOT / "bridge/cli.mjs"), *args], capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


def result_of(fn):
    try:
        return fn()
    except (ToolError, RuntimeError) as e:
        return f"ERROR {e}"


def wait_for(cond, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


with tempfile.TemporaryDirectory(prefix="foxreach-multi-") as tmp:
    work = pathlib.Path(tmp)
    run_dir = work / "run"
    run_dir.mkdir(mode=0o700)
    os.environ.pop("FOXREACH_SOCKET", None)
    os.environ["XDG_RUNTIME_DIR"] = str(run_dir)
    os.environ["XDG_CONFIG_HOME"] = str(work / "config")
    folder = run_dir / "foxreach"
    infos = lambda: sorted(folder.glob("browser-*.json")) if folder.exists() else []  # noqa: E731
    prof_a, prof_b = work / "aaaaaaaa.default", work / "bbbbbbbb.work"
    prof_a.mkdir()
    prof_b.mkdir()

    a = FakeBrowser("A", prof_a)
    wait_for(lambda: len(infos()) == 1 and json.loads(infos()[0].read_text()).get("addon"))
    rows = json.loads(foxreach("browsers", "--json")[1] or "[]")
    check("one browser: listed with its profile and add-on version", len(rows) == 1 and rows[0]["profile"] == str(prof_a)
          and rows[0]["addon"] == VERSION and rows[0]["profileName"] == "default", rows)
    check("the first host also serves browser.sock for older agents", (folder / "browser.sock").exists()
          and json.loads(infos()[0].read_text())["legacy"] is True)
    one = McpClient()
    check("one browser: an agent uses it without choosing", result_of(lambda: one.call("tabs_list")) == "from A")

    b = FakeBrowser("B", prof_b)
    wait_for(lambda: len(infos()) == 2)
    check("a second browser does not quit: both connected", b.proc.poll() is None and len(infos()) == 2)
    rows = json.loads(foxreach("browsers", "--json")[1] or "[]")
    ids = [r["id"] for r in rows]
    check("two browsers of one name get ids by profile", ids == ["browser-default", "browser-work"], ids)

    two = McpClient()
    said = result_of(lambda: two.call("tabs_list"))
    check("two browsers, no default: the agent is told to choose, with the list",
          said.startswith("ERROR") and "Several browsers" in said and "browser-default" in said and "browser-work" in said
          and "browser_select" in said, said)
    status = json.loads(result_of(lambda: two.call("status")))
    check("status then lists them instead of failing", [x["id"] for x in status.get("browsers", [])] == ids and "Several" in status.get("note", ""), status)
    listed = json.loads(two.call("browsers_list"))
    check("browsers_list: both, none chosen yet", [x["id"] for x in listed["browsers"]] == ids
          and not any(x["chosen"] for x in listed["browsers"]), listed)
    check("browser_select chooses for this session", "browser-work" in two.call("browser_select", browser="browser-work")
          and result_of(lambda: two.call("tabs_list")) == "from B")
    check("...and status names the chosen browser", "browser-work" in json.loads(two.call("status")).get("chosenBrowser", ""))
    check("another agent keeps the browser it was using", result_of(lambda: one.call("tabs_list")) == "from A")
    check("browser_select refuses an unknown id, listing the real ones",
          "browser-default" in result_of(lambda: two.call("browser_select", browser="nope")))

    code, out, err = foxreach("browsers", "default", "--profile", str(prof_a))
    three = McpClient()
    check("with a default set, an agent that has not chosen uses it", code == 0 and result_of(lambda: three.call("tabs_list")) == "from A", out + err)
    check("browsers_list marks the default", [x["id"] for x in json.loads(three.call("browsers_list"))["browsers"] if x["default"]] == ["browser-default"])
    foxreach("browsers", "default", "--clear")

    b.stop()
    check("a browser that closes takes its socket and file with it", wait_for(lambda: len(infos()) == 1))
    said = result_of(lambda: two.call("tabs_list"))
    check("the agent that chose it is told it is gone", "no longer connected" in said, said)
    check("...and, one browser left, its next call goes there", result_of(lambda: two.call("tabs_list")) == "from A")

    c = FakeBrowser("C", prof_b)
    wait_for(lambda: len(infos()) == 2)
    os.kill(c.proc.pid, signal.SIGKILL)
    c.proc.wait(timeout=10)
    rows = json.loads(foxreach("browsers", "--json")[1] or "[]")
    check("a host that died is dropped and its files removed", [r["id"] for r in rows] == ["browser"] and len(infos()) == 1, (rows, infos()))

    for client in (one, two, three):
        client.close()
    a.stop()
    check("the last browser leaves nothing behind", wait_for(lambda: not infos() and not (folder / "browser.sock").exists()))

sys.exit(1 if fails else 0)
