#!/usr/bin/env python3
"""The single-file foxreach program: builds the same twice, installs, and serves a real browser.

Run:      uv run --python 3.15 test/binary.py
Expected: every line PASS, "N passed, 0 failed", exit 0. Takes about a minute.
Needs:    FOXREACH_BROWSER if more than one browser is installed (not a Flatpak).

Everything is installed under a throwaway home folder, which the test browser is given as its
own: the real native messaging manifest and the real browser are not touched.
"""
import http.server
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))
from build_binary import build, sha256  # noqa: E402
from harness import ROOT, Browser, McpClient  # noqa: E402

PORT = 47856
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"), flush=True)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><title>Binary</title><button>Served by one file</button>")


def main():
    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    with tempfile.TemporaryDirectory(prefix="foxreach-binary-") as d:
        work = pathlib.Path(d)
        program = build(work / "bin/foxreach")
        check("two builds of the same source are the same file", sha256(program) == sha256(build(work / "again/foxreach")))
        version = json.loads((ROOT / "extension/manifest.json").read_text())["version"]
        run = lambda *args, **kw: subprocess.run([program, *args], capture_output=True, text=True, **kw)  # noqa: E731
        check("carries the add-on's version", run("version").stdout.strip() == version)
        check("explains itself", "foxreach install" in run().stdout and run("nonsense").returncode == 2)

        home = work / "home"
        (home / ".librewolf").mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k != "XDG_DATA_HOME"} | {"HOME": str(home)}
        out = run("install", "--no-flatpak", env=env)
        if sys.platform == "darwin":  # every build reads the one folder in Application Support
            manifests = [home / "Library/Application Support/Mozilla/NativeMessagingHosts/foxreach.json"]
        else:
            manifests = [home / d / "native-messaging-hosts/foxreach.json" for d in (".mozilla", ".librewolf")]
        check("install writes a manifest for Firefox and for a fork that is in use", out.returncode == 0 and all(m.exists() for m in manifests), out.stderr)
        manifest = json.loads(manifests[0].read_text())
        wrapper = pathlib.Path(manifest["path"])
        check("the manifest names the add-on and a wrapper that runs this program as the host",
              manifest["allowed_extensions"] == ["foxreach@foxreach.local"] and f'exec "{program.resolve()}" host' in wrapper.read_text()
              and os.access(wrapper, os.X_OK), wrapper.read_text())
        check("install says how to add it to an agent", f"{program} mcp" in out.stdout, out.stdout)
        refused = run("install", "--browser", "flatpak:org.example.none", env=env | {"PATH": "/nonexistent"})
        check("a failed step is reported, not a stack trace", refused.returncode == 1 and refused.stderr.startswith("foxreach:"), refused.stderr)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        browser = Browser(ROOT / "dist/foxreach.xpi")
        try:
            # The browser reads native messaging manifests from its home folder: give it the throwaway one.
            # macOS apps find Application Support from CFFIXED_USER_HOME, not HOME.
            browser.start(marionette=True, env={"HOME": str(home), "CFFIXED_USER_HOME": str(home)})
            browser.install()
            browser.wait_for_socket()
            mcp = McpClient(browser.socket, command=[str(program), "mcp"])
            status = json.loads(mcp.call("status"))
            check("a real browser starts the program as its host, and the program serves MCP",
                  status["version"] == version and status["bridgeVersion"] == version, status)
            tab = int(mcp.call("tabs_create", url=f"http://127.0.0.1:{PORT}/").split()[1].rstrip(":"))
            check("a page is read through it", "Served by one file" in mcp.call("read_page", tabId=tab))
            mcp.close()
        finally:
            browser.cleanup()
            server.shutdown()

        out = run("uninstall", env=env)
        check("uninstall removes what install wrote", not any(m.exists() for m in manifests) and not wrapper.exists(), out.stdout)
        if sys.platform == "darwin":
            # macOS gives a Downloads folder (the browser made one in the throwaway home) a rule that
            # forbids deleting it; drop the rules so the folder can be cleaned up.
            subprocess.run(["chmod", "-RN", str(home)], check=False)

    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
