"""Throwaway browser and MCP client used by the tests. Touches no real profile."""
import base64
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import browsers  # noqa: E402
from build_install import runtime  # noqa: E402

RUNTIME = runtime()
MARIONETTE_PORT = 47851


class Browser:
    """A separate browser on its own profile and its own foxreach socket. Which one: binary, else
    FOXREACH_BROWSER, else the only one installed (scripts/browsers.py). Not a Flatpak: its sandbox
    sees neither the throwaway profile nor the socket in /tmp."""

    def __init__(self, xpi, binary=None, headless=True):
        self.browser = browsers.choose(binary)
        if self.browser.app_id:
            raise SystemExit("The tests cannot drive a Flatpak browser. Set FOXREACH_BROWSER to another build.")
        self.binary = pathlib.Path(self.browser.spec)
        self.xpi = xpi
        self.headless = headless
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-test-"))
        self.profile = self.dir / "profile"
        self.profile.mkdir()
        self.socket = str(self.dir / "s.sock")
        self.log = self.dir / "browser.log"
        self.proc = None
        self.marionette = None
        (self.profile / "user.js").write_text(
            f'user_pref("marionette.port", {MARIONETTE_PORT});\n'
            'user_pref("extensions.experiments.enabled", true);\n'
            'user_pref("xpinstall.signatures.required", false);\n'
            'user_pref("browser.sessionstore.resume_from_crash", false);\n'
            # Mozilla's own builds open first-run prompts in a new profile (terms of use, welcome,
            # default browser). While one is open the browser refuses to bring any tab to the front.
            'user_pref("termsofuse.bypassNotification", true);\n'
            'user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);\n'
            'user_pref("browser.aboutwelcome.enabled", false);\n'
            'user_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'
            # Grant what the add-on asks the browser for without the browser's own prompt: nobody is
            # there to answer it. The add-on's popup still has to ask.
            'user_pref("extensions.webextOptionalPermissionPrompts", false);\n'
        )

    def start(self, marionette, env=None):
        env = {"FOXREACH_SOCKET": self.socket, "FOXREACH_HOST_LOG": str(self.dir / "host.log")} | (env or {})
        args = ["--no-remote", "--new-instance", "--profile", self.profile]
        if self.headless:
            args.append("--headless")
        if marionette:
            args.append("--marionette")
            env["MOZ_REMOTE_ALLOW_SYSTEM_ACCESS"] = "1"
        self.proc = self.browser.popen(
            args, env, stdout=open(self.log, "ab"), stderr=subprocess.STDOUT, start_new_session=True
        )

    def install(self, ask_approval=False):
        """Install the add-on. Unless ask_approval, also answer for the user that agents need no
        approval, as the tests' own agents have nobody to click Allow for them."""
        self.marionette = Marionette()
        self.marionette.command("WebDriver:NewSession", {})
        addon = self.marionette.command("Addon:Install", {"path": str(self.xpi), "temporary": False})["value"]
        if not ask_approval:
            self.popup_message({"popup": "setAsk", "value": False})
            # Leave the add-on's page: it closes when the add-on is updated or reloaded.
            self.marionette.command("WebDriver:Navigate", {"url": "about:blank"})
        return addon

    def open_popup(self):
        return open_popup(self.marionette)

    def click(self, selector):
        click(self.marionette, selector)

    def popup_message(self, message):
        return popup_message(self.marionette, message)

    def install_and_restart(self):
        """Install, then restart with no automation attached: the daily-use state."""
        self.start(marionette=True)
        addon = self.install()
        self.wait_for_socket()
        # Killing the browser just after a first install can leave the add-on recorded as active but
        # not running. Finish only once a real call has been answered and the install has settled.
        client = McpClient(self.socket)
        client.call("status")
        client.close()
        time.sleep(3)
        self.stop()
        time.sleep(0.5)
        self.start(marionette=False)
        self.wait_for_socket()
        return addon

    def wait_for_socket(self, timeout=45):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(self.socket):
                return
            if self.proc.poll() is not None:
                raise RuntimeError(f"browser exited with {self.proc.returncode}")
            time.sleep(0.2)
        hook = os.environ.get("FOXREACH_ON_SOCKET_TIMEOUT")
        if hook:
            subprocess.run([hook, str(self.dir)], check=False)
        raise RuntimeError("the native host never opened its socket")

    def stop(self):
        """SIGTERM the browser only, found by profile path; the runtime exits by itself."""
        if self.marionette:
            self.marionette.close()
            self.marionette = None
        pids = browsers.mac_pids(self.profile) if browsers.MAC else []
        for d in [] if browsers.MAC else pathlib.Path("/proc").iterdir():
            if d.name.isdigit():
                try:
                    if str(self.profile).encode() in (d / "cmdline").read_bytes() and not os.readlink(
                        d / "exe"
                    ).lower().endswith(".appimage"):
                        pids.append(int(d.name))
                except OSError:
                    pass
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        if self.proc:
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                print("warning: browser still running after 30 s")
            self.proc = None

    def cleanup(self):
        self.stop()
        if not os.environ.get("FOXREACH_KEEP"):
            shutil.rmtree(self.dir, ignore_errors=True)
        else:
            print("kept", self.dir)


def open_popup(m):
    """Show the toolbar popup's page in the Marionette tab; returns a function that runs a script
    there and gives back what the script passes to arguments[0]."""
    m.command("Marionette:SetContext", {"value": "chrome"})
    uuid = m.command("WebDriver:ExecuteScript", {"args": [], "script":
        'return JSON.parse(Services.prefs.getStringPref("extensions.webextensions.uuids"))["foxreach@foxreach.local"];'})["value"]
    m.command("Marionette:SetContext", {"value": "content"})
    m.command("WebDriver:Navigate", {"url": f"moz-extension://{uuid}/popup/popup.html"})
    return lambda code: m.command("WebDriver:ExecuteAsyncScript", {"script": code, "args": [], "scriptTimeout": 8000})["value"]


def click(m, selector):
    """A real click in the Marionette tab, as the browser wants for a permission request."""
    element = m.command("WebDriver:FindElement", {"using": "css selector", "value": selector})["value"]
    m.command("WebDriver:ElementClick", {"id": next(iter(element.values()))})


def popup_message(m, message):
    """Send what the popup sends for a click, and return the add-on's state after it."""
    run = open_popup(m)
    deadline = time.time() + 20
    while True:  # the background page may still be starting
        state = run(f"browser.runtime.sendMessage({json.dumps(message)}).then(arguments[0], () => arguments[0](null))")
        if state or time.time() > deadline:
            return state
        time.sleep(0.3)


class Marionette:
    """Test-only client, for the steps a tool call cannot do: install and the toolbar popup."""

    def __init__(self):
        deadline = time.time() + 45
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", MARIONETTE_PORT), timeout=60)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.25)
        self.stream = self.sock.makefile("rb")
        self.seq = 0
        self._recv()

    def _recv(self):
        size = b""
        while (c := self.stream.read(1)) != b":":
            if not c:
                raise RuntimeError("Marionette closed the connection")
            size += c
        return json.loads(self.stream.read(int(size)))

    def command(self, name, params):
        self.seq += 1
        data = json.dumps([0, self.seq, name, params]).encode()
        self.sock.sendall(str(len(data)).encode() + b":" + data)
        _, _, error, result = self._recv()
        if error:
            raise RuntimeError(f"{name}: {error['error']}: {error['message']}")
        return result

    def close(self):
        self.sock.close()


class McpClient:
    """A plain MCP client: the same stdio conversation any agent has with the server."""

    def __init__(self, socket_path=None, name="foxreach-test", command=None):
        env = dict(os.environ)
        if socket_path:
            env["FOXREACH_SOCKET"] = socket_path
        self.proc = subprocess.Popen(
            command or [RUNTIME, str(ROOT / "bridge/mcp.mjs")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env,
        )
        self.seq = 0
        self.init = self.request(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": name, "version": "1"}},
        )
        self.notify("notifications/initialized")

    def notify(self, method):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params=None):
        self.seq += 1
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.seq, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("the MCP server exited")
        reply = json.loads(line)
        assert reply["id"] == self.seq, reply
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply["result"]

    def call_raw(self, tool, **args):
        return self.request("tools/call", {"name": tool, "arguments": args})

    def call(self, tool, **args):
        """Text of a successful call. A tool error raises."""
        result = self.call_raw(tool, **args)
        text = "\n".join(c["text"] for c in result["content"] if c["type"] == "text")
        if result.get("isError"):
            raise ToolError(text)
        return text

    def image(self, tool="screenshot", **args):
        result = self.call_raw(tool, **args)
        if result.get("isError"):
            raise ToolError(result["content"][0]["text"])
        image = next(c for c in result["content"] if c["type"] == "image")
        return base64.b64decode(image["data"]), image["mimeType"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)


class ToolError(Exception):
    pass
