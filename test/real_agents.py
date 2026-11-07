#!/usr/bin/env python3
"""Do unrelated agents really work through foxreach, using only its MCP server?

Each agent is told to open a local page and click a button. The page reveals a random token only
for a trusted click, so the token in the agent's answer proves the whole path worked.

Run:      uv run --python 3.15 test/real_agents.py [claude] [codex]
Expected: PASS per agent that is installed and signed in, exit 0. Uses a little of each agent's quota.
"""
import http.server
import json
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, RUNTIME, Browser

PORT = 47855
TIMEOUT_S = 240


def page(token):
    return f"""<!doctype html><title>Agent check</title>
<button id=go>Reveal the word</button><p id=out>Nothing yet.</p>
<script>go.addEventListener('click', e => {{
  out.textContent = e.isTrusted ? 'The word is {token}' : 'Rejected: that click was not real input.';
}});</script>""".encode()


def prompt(url):
    return (
        f"Use the foxreach browser tools. Open {url} in a new tab, click the 'Reveal the word' button, "
        "read the page again, close the tab, and reply with only the word shown after 'The word is'. "
        "If the page rejects the click, reply REJECTED."
    )


def run_claude(socket, url, workdir):
    config = workdir / "mcp.json"
    config.write_text(json.dumps({"mcpServers": {"foxreach": {
        "command": RUNTIME, "args": [str(ROOT / "bridge/mcp.mjs")], "env": {"FOXREACH_SOCKET": socket},
    }}}))
    return ["claude", "-p", prompt(url), "--model", "claude-haiku-5-5", "--strict-mcp-config",
            "--mcp-config", str(config), "--allowedTools", "mcp__foxreach"]


def run_codex(socket, url, workdir):
    return ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
            "-c", f'mcp_servers.foxreach.command="{RUNTIME}"',
            "-c", f'mcp_servers.foxreach.args=["{ROOT / "bridge/mcp.mjs"}"]',
            "-c", f'mcp_servers.foxreach.env={{FOXREACH_SOCKET="{socket}"}}',
            "-c", 'mcp_servers.foxreach.default_tools_approval_mode="approve"',
            prompt(url)]


AGENTS = {"claude": run_claude, "codex": run_codex}


def main():
    wanted = [a for a in sys.argv[1:] if a in AGENTS] or list(AGENTS)
    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    token = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page(token["value"]))

    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    browser = Browser(ROOT / "dist/foxreach.xpi")
    failed = 0
    try:
        browser.install_and_restart()
        with tempfile.TemporaryDirectory() as d:
            for name in wanted:
                if not shutil.which(name):
                    print(f"SKIP {name}: not installed")
                    continue
                token["value"] = f"fox{secrets.token_hex(4)}"
                url = f"http://127.0.0.1:{PORT}/?agent={name}"
                try:
                    out = subprocess.run(AGENTS[name](browser.socket, url, pathlib.Path(d)), cwd=d,
                                         capture_output=True, text=True, timeout=TIMEOUT_S, stdin=subprocess.DEVNULL)
                    ok = token["value"] in out.stdout
                    detail = (out.stdout + out.stderr)[-1200:]
                except subprocess.TimeoutExpired as e:
                    ok, detail = False, f"timed out: {e.stdout!r}"
                failed += not ok
                print("PASS" if ok else "FAIL", name, "" if ok else f"-- {detail}", flush=True)
    finally:
        browser.cleanup()
        server.shutdown()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
