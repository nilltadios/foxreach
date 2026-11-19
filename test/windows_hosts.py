#!/usr/bin/env python3
"""Windows: native hosts on named pipes, agents choosing one, and a pipe that is not foxreach's.

Run:      uv run --python 3.15 test/windows_hosts.py      (on Windows)
Expected: every line PASS, exit 0.
How:      real native hosts (bridge/host.mjs), each with a stand-in browser on its stdin/stdout that
          answers every tool call with its own name, and real MCP servers (bridge/mcp.mjs) as agents.
          LOCALAPPDATA and APPDATA point at a throwaway folder, so the hosts' info files and the
          default-browser choice stay there. No browser is started and no real setting is touched.
          A third "host" is a plain pipe listed with a made-up token: agents must send it nothing.
"""
import json
import os
import pathlib
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from harness import ROOT, RUNTIME, McpClient, ToolError  # noqa: E402

if os.name != "nt":
    raise SystemExit("This test is for Windows; test/multi_browser.py covers Linux and macOS.")

fails = 0
VERSION = json.loads((ROOT / "extension/manifest.json").read_text())["version"]
PIPE = re.compile(r"^\\\\\.\\pipe\\foxreach-[0-9a-f]{16}$")


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


class FakeBrowser:
    """A native host started as a browser would, answering each call with this browser's name."""

    def __init__(self, label, profile):
        self.label = label
        env = dict(os.environ, FOXREACH_HOST_PROFILE=str(profile))
        self.proc = subprocess.Popen([RUNTIME, str(ROOT / "bridge/cli.mjs"), "host"], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.calls = []
        self.send({"hello": {"version": VERSION, "trustedInput": False}})
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
                self.calls.append(message)
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


def wait_for(cond, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


# A pipe server that answers anything with a wrong proof and records what it was sent.
SQUATTER = r"""
const net = require("node:net");
const fs = require("node:fs");
const [pipe, log] = process.argv.slice(-2);
net.createServer((c) => {
  c.setEncoding("utf8");
  c.on("data", (d) => {
    fs.appendFileSync(log, d);
    c.write(JSON.stringify({ proof: "0".repeat(64) }) + "\n");
    c.write(JSON.stringify({ id: 1, result: { text: "from the squatter" } }) + "\n");
  });
}).listen(pipe, () => console.log("ready"));
"""

with tempfile.TemporaryDirectory(prefix="foxreach-win-") as tmp:
    work = pathlib.Path(tmp)
    os.environ.pop("FOXREACH_SOCKET", None)
    os.environ["LOCALAPPDATA"] = str(work / "local")
    os.environ["APPDATA"] = str(work / "roaming")
    os.environ.pop("XDG_CONFIG_HOME", None)
    folder = work / "local" / "foxreach" / "run"
    infos = lambda: sorted(folder.glob("browser-*.json")) if folder.exists() else []  # noqa: E731
    prof_a, prof_b = work / "aaaaaaaa.default", work / "bbbbbbbb.work"
    prof_a.mkdir()
    prof_b.mkdir()

    code, out, err = foxreach("browsers")
    check("no browser: browsers says so", code == 0 and "No browser" in out, out + err)
    none = McpClient()
    said = result_of(lambda: none.call("tabs_list"))
    check("no browser: an agent is told to open one, with no fixed pipe tried", said.startswith("ERROR") and "no browser listed" in said, said)
    none.close()

    a = FakeBrowser("A", prof_a)
    wait_for(lambda: len(infos()) == 1 and json.loads(infos()[0].read_text()).get("addon"))
    info = json.loads(infos()[0].read_text()) if infos() else {}
    check("a host listens on a pipe of its own, with a token in its info file",
          bool(PIPE.match(info.get("socket", ""))) and len(info.get("token", "")) == 64 and info.get("legacy") is False, info)
    rows = json.loads(foxreach("browsers", "--json")[1] or "[]")
    check("one browser: listed with its profile and add-on version, no token shown",
          len(rows) == 1 and rows[0]["profile"] == str(prof_a) and rows[0]["addon"] == VERSION and "token" not in json.dumps(rows), rows)
    status = json.loads(foxreach("status", "--json")[1] or "{}")
    check("status --json lists it and does not show the token", len(status.get("connected", [])) == 1 and info.get("token", "x") not in json.dumps(status))
    one = McpClient()
    check("one browser: an agent uses it without choosing", result_of(lambda: one.call("tabs_list")) == "from A")

    b = FakeBrowser("B", prof_b)
    wait_for(lambda: len(infos()) == 2)
    rows = json.loads(foxreach("browsers", "--json")[1] or "[]")
    ids = [r["id"] for r in rows]
    check("two browsers of one name get ids by profile", ids == ["browser-default", "browser-work"], ids)
    two = McpClient()
    said = result_of(lambda: two.call("tabs_list"))
    check("two browsers, no default: the agent is told to choose", said.startswith("ERROR") and "Several browsers" in said, said)
    check("browser_select chooses for this session", "browser-work" in two.call("browser_select", browser="browser-work")
          and result_of(lambda: two.call("tabs_list")) == "from B")
    check("another agent keeps the browser it was using", result_of(lambda: one.call("tabs_list")) == "from A")
    code, out, err = foxreach("browsers", "default", "--profile", str(prof_a))
    three = McpClient()
    check("with a default set, an agent that has not chosen uses it", code == 0 and result_of(lambda: three.call("tabs_list")) == "from A", out + err)
    check("the default is kept in APPDATA", (work / "roaming" / "foxreach" / "default-browser").exists())
    # Windows paths: another case or forward slashes name the same profile.
    other = str(prof_b).swapcase().replace("\\", "/")
    code, out, err = foxreach("browsers", "default", "--profile", other)
    rows = json.loads(foxreach("browsers", "--json")[1] or "[]")
    five = McpClient()
    check("a default named in another case or with / is still that browser",
          [r["id"] for r in rows if r["default"]] == ["browser-work"] and result_of(lambda: five.call("tabs_list")) == "from B", (rows, out + err))
    five.close()
    foxreach("browsers", "default", "--clear")

    # Two hosts on one profile (or none known): each gets an id of its own, and either can be chosen.
    c = FakeBrowser("C", prof_b)
    wait_for(lambda: len(infos()) == 3)
    ids = [r["id"] for r in json.loads(foxreach("browsers", "--json")[1] or "[]")]
    six = McpClient()
    check("two on one profile get -2", ids == ["browser-default", "browser-work", "browser-work-2"], ids)
    check("...and the second can be chosen", "browser-work-2" in six.call("browser_select", browser="browser-work-2")
          and result_of(lambda: six.call("tabs_list")) == "from C")
    six.close()
    c.stop()
    wait_for(lambda: len(infos()) == 2)

    b.stop()
    check("a browser that closes takes its info file with it", wait_for(lambda: len(infos()) == 1))
    said = result_of(lambda: two.call("tabs_list"))
    check("the agent that chose it is told it is gone", "no longer connected" in said, said)

    # A pipe that came free, taken by someone else, still listed (a host that crashed): the agent must
    # send it nothing but the challenge.
    a.stop()
    wait_for(lambda: not infos())
    pipe = r"\\.\pipe\foxreach-00112233445566ff"
    log = work / "squatter.log"
    squatter = subprocess.Popen([RUNTIME, "-e", SQUATTER, pipe, str(log)], stdout=subprocess.PIPE, text=True)
    check("the squatter listens", squatter.stdout.readline().strip() == "ready")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "browser-00112233445566ff.json").write_text(json.dumps({
        "key": "00112233445566ff", "socket": pipe, "token": "11" * 32, "name": "Firefox", "packaging": "native",
        "appId": None, "profile": str(prof_a), "addon": VERSION, "legacy": False, "started": 1}))
    listed = json.loads(foxreach("browsers", "--json")[1] or "[]")
    check("a pipe that cannot prove itself is not listed, and its stale info file is removed",
          listed == [] and not (folder / "browser-00112233445566ff.json").exists(), (listed, infos()))
    (folder / "browser-00112233445566ff.json").write_text(json.dumps({
        "key": "00112233445566ff", "socket": pipe, "token": "11" * 32, "name": "Firefox", "packaging": "native",
        "appId": None, "profile": str(prof_a), "addon": VERSION, "legacy": False, "started": 1}))
    four = McpClient()
    said = result_of(lambda: four.call("tabs_list"))
    squatter.kill()  # first: Windows lets nobody read the log while the squatter has it open
    squatter.wait(timeout=10)
    sent = log.read_text() if log.exists() else ""
    check("an agent is not sent to it either", said.startswith("ERROR") and "no browser listed" in said, said)
    check("...and it was sent only challenges, no tool call", "challenge" in sent and "tabs_list" not in sent and "tool" not in sent, sent)

    for client in (one, two, three, four):
        client.close()
    rows = json.loads(foxreach("browsers", "--json")[1] or "[]")
    check("the last browser gone, nothing is listed", rows == [] and not infos(), (rows, infos()))

sys.exit(1 if fails else 0)
