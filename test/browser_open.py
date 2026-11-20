#!/usr/bin/env python3
"""browser_open: an agent starts a profile's browser and waits for foxreach to connect. The
profiles it can start are the recorded ones and the ones `foxreach status` finds with the add-on.

Run:      uv run --python 3.15 test/browser_open.py
Browser:  FOXREACH_BROWSER (a path or a command), as for test/e2e.py
Expected: every line PASS, exit 0. Only throwaway profiles: a record file of the test's own
          (FOXREACH_PROFILES) lists them, and the MCP server runs with a HOME and PATH of the
          test's own, so detection finds only the profiles and browsers the test puts there; the
          real record and profiles are never read or opened. The browser starts headless
          (MOZ_HEADLESS=1).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import RUNTIME, ROOT, Browser, McpClient, ToolError  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else f' -- {detail}'}", flush=True)


def call(mcp, tool, **args):
    try:
        return True, mcp.call(tool, **args)
    except ToolError as e:
        return False, str(e)


def main():
    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    b = Browser(ROOT / "dist/foxreach.xpi")
    work = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-open-"))
    record = work / "profiles"
    saved = dict(os.environ)
    try:
        b.start(marionette=True)
        b.install()
        b.wait_for_socket()
        McpClient(b.socket).call("status")
        time.sleep(3)
        b.stop()

        # The server (and the browser it starts) gets a home of its own: detection scans HOME and
        # PATH, which must hold only what this test puts there. The browser finds the native host
        # through the manifests build_install.py wrote, copied in.
        home = work / "home"
        home.mkdir()
        for d in (".mozilla/native-messaging-hosts", ".librewolf/native-messaging-hosts"):
            if (pathlib.Path.home() / d).is_dir():
                shutil.copytree(pathlib.Path.home() / d, home / d)
        (home / "bin").mkdir()
        os.environ.update({"FOXREACH_PROFILES": str(record), "FOXREACH_SOCKET": b.socket, "MOZ_HEADLESS": "1",
                           "FOXREACH_HOST_LOG": str(pathlib.Path(b.dir) / "host.log"),
                           "HOME": str(home), "PATH": f"{home}/bin:/usr/bin:/bin"})
        for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
            os.environ.pop(k, None)
        record.write_text(f"{b.profile}\t0\t{b.browser.spec}\n")
        mcp = McpClient(b.socket)
        ok, text = call(mcp, "status")
        check("before: not connected, and the error names the profile", not ok and str(b.profile) in text, text)

        started = time.monotonic()
        ok, text = call(mcp, "browser_open")
        check("browser_open with one recorded profile starts it and waits for foxreach", ok and "connected" in text, text)
        print(f"     (took {time.monotonic() - started:.1f} s)")
        ok, text = call(mcp, "tabs_list")
        check("...and tools work afterwards", ok, text)
        ok, text = call(mcp, "browser_open")
        check("browser_open when connected starts nothing", ok and "already connected" in text, text)
        mcp.close()
        b.stop()
        time.sleep(1)

        # Fake profiles, recorded with programs that cannot start a browser.
        busy = work / "busy-profile"
        busy.mkdir()
        os.symlink(f"127.0.0.1:+{os.getpid()}", busy / "lock")  # held by a live process: this one
        missing = work / "missing-program"
        missing.mkdir()
        record.write_text(f"{busy}\t0\t/bin/false\n{missing}\t0\t{work}/no-such-browser\n")
        mcp = McpClient(b.socket)
        ok, text = call(mcp, "browser_open")
        check("several recorded profiles and no default: asks which", not ok and "several profiles" in text and str(busy) in text, text)
        ok, text = call(mcp, "browser_open", profile=str(work / "elsewhere"))
        check("a profile that is not recorded is refused", not ok and "No single profile" in text, text)
        ok, text = call(mcp, "browser_open", profile="busy-profile")
        check("a profile a running browser holds is refused, by folder name too", not ok and "already has" in text, text)
        ok, text = call(mcp, "browser_open", profile=str(missing))
        check("a recorded program that does not exist is reported", not ok and "Could not start" in text, text)
        mcp.close()

        # No record: the profile is found the way `foxreach status` finds it, as after an install
        # from addons.mozilla.org. The browser is a command on PATH, the profile is in its
        # profiles.ini, and the add-on (installed above) is in the profile.
        record.unlink()
        kind = b.browser.kind
        command = home / "bin" / kind.commands[0]
        command.symlink_to(b.browser.spec)
        root = home / kind.profile_roots[0]
        root.mkdir(parents=True)
        ini = [f"[Profile0]\nName=harness\nIsRelative=0\nPath={b.profile}\n"]
        (root / "profiles.ini").write_text("\n".join(ini))
        check("the add-on is installed in the harness profile", (b.profile / "extensions/foxreach@foxreach.local.xpi").is_file())
        mcp = McpClient(b.socket)
        ok, text = call(mcp, "status")
        # (Its path is in the error anyway, in the note on how the last connection ended: look for the hint.)
        check("no record: the not-connected error offers the detected profile, as browser_open does",
              not ok and f"{b.profile} (open it with: {command} --profile {b.profile})" in text, text)
        started = time.monotonic()
        ok, text = call(mcp, "browser_open")
        check("no record, one detected profile with the add-on: browser_open starts it with the detected browser",
              ok and "connected" in text and f"{command} --profile {b.profile}" in text and not record.exists(), text)
        print(f"     (took {time.monotonic() - started:.1f} s)")
        ok, text = call(mcp, "tabs_list")
        check("...and tools work afterwards", ok, text)
        mcp.close()
        b.stop()
        time.sleep(1)

        # More profiles in the same profiles.ini, and a Tor Browser install with the add-on.
        def fake(name, addon=True, active=True, stopped=False, parent=work):
            p = parent / name
            (p / "extensions").mkdir(parents=True)
            if addon:
                (p / "extensions/foxreach@foxreach.local.xpi").write_text("x")
            if active is not None:
                (p / "extensions.json").write_text(json.dumps({"addons": [
                    {"id": "foxreach@foxreach.local", "version": "0.4.1", "location": "app-profile", "active": active}]}))
            if stopped:
                (p / "foxreach-agents-stopped").write_text("x")
            return p

        second = fake("second")
        unlisted = fake("unlisted", active=None)  # the browser has not listed the add-on yet: still offered
        without = fake("without", addon=False)
        disabled = fake("disabled", active=False)
        stopped = fake("stopped", stopped=True)
        for i, p in enumerate([second, unlisted, without, disabled, stopped], start=1):
            ini.append(f"[Profile{i}]\nName={p.name}\nIsRelative=0\nPath={p}\n")
        (root / "profiles.ini").write_text("\n".join(ini))
        tor = home / "tor-browser/Browser"
        (tor / "TorBrowser/Data/Browser").mkdir(parents=True)
        tor_ran = work / "tor-ran"
        (tor / "start-tor-browser").write_text(f"#!/bin/sh\ntouch {tor_ran}\n")
        (tor / "start-tor-browser").chmod(0o755)
        tor_profile = fake("tor.default", parent=tor / "TorBrowser/Data/Browser")
        (tor / "TorBrowser/Data/Browser/profiles.ini").write_text("[Profile0]\nName=default\nIsRelative=1\nPath=tor.default\n")

        def entry(p, spec):
            return f"- {p} ({spec})"

        # Detection does see every one of them, so what is left out below is left out on purpose.
        r = subprocess.run([RUNTIME, str(ROOT / "bridge/cli.mjs"), "status", "--json"], env=os.environ | {"FOXREACH_AGENT_CLI": "0"},
                           capture_output=True, text=True, timeout=60)
        rows = json.loads(r.stdout)["browsers"] if r.returncode == 0 else []
        listed = {p["path"]: (row["packaging"], p["addon"]) for row in rows for p in row["profiles"]}
        check("status finds the browser and all the profiles, Tor Browser's with the add-on",
              {str(p) for p in (b.profile, second, unlisted, without, disabled, stopped, tor_profile)} <= listed.keys()
              and listed[str(tor_profile)][0] == "tor" and listed[str(tor_profile)][1], f"{r.stderr} {listed}")
        mcp = McpClient(b.socket)
        ok, text = call(mcp, "browser_open")
        offered = [b.profile, second, unlisted]
        check("several detected profiles and no default: asks which, listing each with its browser",
              not ok and "several profiles" in text and all(entry(p, command) in text for p in offered), text)
        check("...each once", all(text.count(f"- {p} (") == 1 for p in offered), text)
        check("...not a profile without the add-on, with it disabled, or with agents stopped",
              not any(str(p) in text for p in (without, disabled, stopped)), text)
        check("...not a Tor Browser profile", str(tor_profile) not in text and "tor-browser" not in text, text)
        ok, text = call(mcp, "browser_open", profile=str(without))
        check("a detected profile without the add-on is refused", not ok and "No single" in text and "Could not start" not in text, text)
        ok, text = call(mcp, "browser_open", profile=str(tor_profile))
        check("a Tor Browser profile with the add-on is refused, and Tor Browser is not started",
              not ok and "No single" in text and not tor_ran.exists(), text)
        mcp.close()

        # The same profile recorded and detected: listed once, with the recorded browser.
        record.write_text(f"{second}\t0\t{work}/no-such-browser\n")
        mcp = McpClient(b.socket)
        ok, text = call(mcp, "browser_open")
        check("a recorded profile that is also detected is listed once, with the recorded browser",
              not ok and text.count(f"- {second} (") == 1 and entry(second, f"{work}/no-such-browser") in text
              and entry(b.profile, command) in text, text)
        ok, text = call(mcp, "browser_open", profile="second")
        check("...and opened with the recorded browser", not ok and f"Could not start {work}/no-such-browser" in text, text)
        mcp.close()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        b.cleanup()
        subprocess.run(["rm", "-rf", str(work)], check=False)
    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
