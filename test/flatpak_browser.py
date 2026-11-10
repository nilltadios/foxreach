#!/usr/bin/env python3
"""A real Flatpak browser: it finds the native host, starts it in its sandbox, and agents reach it.

Run:      uv run --python 3.15 test/flatpak_browser.py flatpak:<application id> [--program dist/foxreach]
          (default: FOXREACH_BROWSER, which must name a Flatpak)
          --program: install and serve with the single-file foxreach program (scripts/build_binary.py)
          instead of the checkout's bridge and Bun
Expected: every line PASS, "N passed, 0 failed", exit 0.
Changes:  what scripts/build_install.py does for a Flatpak browser (the bridge copy in
          ~/.var/app/<id>/foxreach, the manifest, the xdg-run/foxreach permission). The profile is a
          throwaway inside ~/.var/app/<id>/foxreach, removed at the end; no real profile is touched.

A build that accepts unsigned add-ons (the LibreWolf Flatpak) gets the full add-on. One that does
not (the Firefox Flatpak, an official release) gets the untrusted build, loaded for this run only
the way about:debugging loads one; the test says which it used. Either way the path under test is
the same: manifest, host in the sandbox, socket outside.
"""
import http.server
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import browsers  # noqa: E402
import build_install  # noqa: E402
from harness import MARIONETTE_PORT, Marionette, McpClient, click, open_popup, popup_message  # noqa: E402

PORT = 47853
PAGE = b"<!doctype html><title>Sandbox page</title><h1 id=h>reached from a Flatpak</h1><button onclick=\"h.textContent='clicked'\">Press</button>"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"), flush=True)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


def browser_pids(profile):
    """The browser's processes on this profile, not the sandbox helpers that carry it."""
    pids = []
    for d in pathlib.Path("/proc").iterdir():
        if d.name.isdigit() and int(d.name) != os.getpid():
            try:
                if str(profile).encode() in (d / "cmdline").read_bytes() \
                        and os.path.basename(os.readlink(d / "exe")) not in ("bwrap", "flatpak", "xdg-dbus-proxy", "uv") \
                        and not os.path.basename(os.readlink(d / "exe")).startswith("python"):
                    pids.append(int(d.name))
            except OSError:
                pass
    return pids


def main():
    argv = sys.argv[1:]
    program = None
    if "--program" in argv:
        program = str(pathlib.Path(argv.pop(argv.index("--program") + 1)).resolve())
        argv.remove("--program")
    spec = argv[0] if argv else os.environ.get("FOXREACH_BROWSER", "")
    browser = browsers.resolve(spec) if spec.startswith("flatpak:") else None
    if not browser:
        raise SystemExit("Name a Flatpak browser: test/flatpak_browser.py flatpak:<application id>")
    DIST = ROOT / "dist"
    DIST.mkdir(exist_ok=True)
    build_install.build(DIST / "foxreach.xpi", True)
    build_install.build(DIST / "foxreach-untrusted.xpi", False)
    if program:
        done = subprocess.run([program, "install", "--browser", spec], check=True, capture_output=True, text=True).stdout
        print(f"{browser.name} {browser.app_id}; installed by {program}:\n{done.split('The browser can now')[0]}")
    else:
        manifests = build_install.install_flatpak_host(browser)
        print(f"{browser.name} {browser.app_id}; manifests:", *manifests)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    work = pathlib.Path(tempfile.mkdtemp(prefix="test-", dir=browser.home / "foxreach"))
    sockets = pathlib.Path(tempfile.mkdtemp(prefix="test-", dir=browser.socket_dir()))
    profile, sock = work / "profile", sockets / "s.sock"
    profile.mkdir()
    for name in ("foxreach.xpi", "foxreach-untrusted.xpi"):
        shutil.copy2(DIST / name, work / name)  # the sandbox cannot read dist/
    (profile / "user.js").write_text(
        f'user_pref("marionette.port", {MARIONETTE_PORT});\n'
        'user_pref("extensions.experiments.enabled", true);\n'
        'user_pref("xpinstall.signatures.required", false);\n'
        'user_pref("termsofuse.bypassNotification", true);\n'
        'user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);\n'
        'user_pref("browser.aboutwelcome.enabled", false);\n'
        'user_pref("browser.shell.checkDefaultBrowser", false);\n'
        'user_pref("extensions.webextOptionalPermissionPrompts", false);\n'  # nobody is there to answer the browser
    )
    env = {"FOXREACH_SOCKET": str(sock), "FOXREACH_HOST_LOG": str(work / "host.log"),
           "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}  # to find the popup's address
    proc = browser.popen(["--headless", "--no-remote", "--new-instance", "--profile", profile, "--marionette"], env,
                         stdout=open(work / "browser.log", "ab"), stderr=subprocess.STDOUT, start_new_session=True)
    try:
        marionette = Marionette()
        marionette.command("WebDriver:NewSession", {})
        try:
            marionette.command("Addon:Install", {"path": str(work / "foxreach.xpi"), "temporary": False})
            build = "full"
        except RuntimeError as e:
            print("the full add-on was refused:", str(e)[:150])
            marionette.command("Addon:Install", {"path": str(work / "foxreach-untrusted.xpi"), "temporary": True})
            build = "untrusted, for this run only"
        print("add-on:", build)
        deadline = time.time() + 45
        while not sock.exists() and time.time() < deadline:
            time.sleep(0.2)
        log = (work / "host.log").read_text() if (work / "host.log").exists() else "the host never started"
        check("the Flatpak browser found the manifest and started the host in its sandbox", "start, socket" in log, log)
        check("the host's socket appears outside the sandbox", sock.exists(), log)
        if not sock.exists():
            return 1
        # Answer for the user: agents need no approval, and (for the build that holds no site) every site.
        popup_message(marionette, {"popup": "setAsk", "value": False})
        if build != "full":
            open_popup(marionette)
            click(marionette, "#access-all")
            time.sleep(1)
        mcp = McpClient(str(sock), command=[program, "mcp"] if program else None)
        status = json.loads(mcp.call("status"))
        check("an agent outside gets an answer from the add-on", status.get("version") == build_install.version(), status)
        check("the bridge inside the sandbox and the add-on agree on the version",
              status.get("bridgeVersion") == status.get("version") and "warning" not in status, status)
        check("trusted input matches the build used", status.get("trustedInput") is (build == "full"), status)
        tab = int(re.search(r"Tab (\d+)", mcp.call("tabs_create", url=f"http://127.0.0.1:{PORT}/")).group(1))
        text = mcp.call("get_page_text", tabId=tab)
        check("a page opened by the agent can be read", "reached from a Flatpak" in text, text)
        button = re.search(r"\[(ref_\d+|[a-z]*\d+)\][^\n]*Press|Press[^\n]*\[(ref_\d+|[a-z]*\d+)\]", mcp.call("read_page", tabId=tab))
        mcp.call("click", tabId=tab, ref=next(g for g in button.groups() if g))
        text = mcp.call("get_page_text", tabId=tab)
        check("a click from the agent lands", "clicked" in text, text)
        data, kind = mcp.image(tabId=tab, format="png")
        check("a screenshot comes back", data[:4] == b"\x89PNG", kind)
        mcp.close()
        marionette.close()
    finally:
        for pid in browser_pids(profile):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            print("warning: the browser was still running after 30 s")
        server.shutdown()
        if os.environ.get("FOXREACH_KEEP"):
            print("kept", work)
        else:
            shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(sockets, ignore_errors=True)
    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
