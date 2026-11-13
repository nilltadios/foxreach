#!/usr/bin/env python3
"""`foxreach addon remove` against a real release Firefox: refused while the profile is open, and
after removal Firefox drops the add-on from its list at the next start.

Run:      uv run --python 3.15 test/addon_firefox.py [--browser flatpak:org.mozilla.firefox] [--program dist/foxreach]
Needs:    the Firefox Flatpak (or another release Firefox), docs/foxreach-<version>-signed.xpi, and a
          foxreach program (default: bun bridge/cli.mjs).
How:      a throwaway profile inside the Flatpak's own folder, holding the signed add-on, started
          headless (no window) with add-ons from the profile folder enabled; then the real
          `foxreach addon remove`, once while Firefox runs and once after it stopped; then one more
          headless start, and extensions.json read. No user profile is touched; the throwaway
          profile is deleted at the end.
Expected: every line PASS, exit 0.
"""
import argparse
import json
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADDON_ID = "foxreach@foxreach.local"
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


def entry(profile):
    try:
        data = json.loads((profile / "extensions.json").read_text())
        return next((a for a in data["addons"] if a["id"] == ADDON_ID), None)
    except (OSError, ValueError, KeyError):
        return None


def start(browser, profile):
    cmd = ["flatpak", "run", browser[8:]] if browser.startswith("flatpak:") else [browser]
    return subprocess.Popen(cmd + ["--headless", "--no-remote", "--profile", str(profile), "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def stop(proc, browser):
    """Stop our own instance. For a Flatpak, SIGTERM to `flatpak run` leaves Firefox running in its
    sandbox, so the app is stopped with `flatpak kill`: main() refused to start if it was running
    already, so the only instance is this test's."""
    try:
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
    if browser.startswith("flatpak:"):
        app = browser[8:]
        for _ in range(20):
            if app not in subprocess.run(["flatpak", "ps", "--columns=application"], capture_output=True, text=True).stdout.split():
                break
            subprocess.run(["flatpak", "kill", app], capture_output=True)
            time.sleep(1)
    time.sleep(2)


def wait_for(cond, seconds=60):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(1)
    return cond()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--browser", default="flatpak:org.mozilla.firefox")
    parser.add_argument("--program", help="a foxreach program (default: bun bridge/cli.mjs)")
    args = parser.parse_args()
    program = [args.program] if args.program else [shutil.which("bun") or str(pathlib.Path.home() / ".bun/bin/bun"), str(ROOT / "bridge/cli.mjs")]
    xpi = max(ROOT.glob("docs/foxreach-*-signed.xpi"), key=lambda p: [int(x) for x in p.name.split("-")[1].split(".")])
    if args.browser.startswith("flatpak:"):
        app = args.browser[8:]
        running = subprocess.run(["flatpak", "ps", "--columns=application"], capture_output=True, text=True).stdout.split()
        if app in running:
            raise SystemExit(f"{app} is running; close it first (this test tells running from not running by the app).")
        base = pathlib.Path.home() / ".var/app" / app
    else:
        base = pathlib.Path(tempfile.gettempdir())
    profile = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-addontest-", dir=base))
    try:
        (profile / "extensions").mkdir()
        shutil.copy2(xpi, profile / "extensions" / f"{ADDON_ID}.xpi")
        (profile / "user.js").write_text('user_pref("extensions.autoDisableScopes", 0);\n'
                                         'user_pref("extensions.startupScanScopes", 1);\n')
        proc = start(args.browser, profile)
        try:
            installed = wait_for(lambda: (entry(profile) or {}).get("active") is True)
            check(f"Firefox installed the signed add-on ({xpi.name})", installed, entry(profile))
            r = subprocess.run([*program, "addon", "remove", "--profile", str(profile), "--browser", args.browser],
                               capture_output=True, text=True, timeout=60)
            check("addon remove refuses while Firefox has the profile open", r.returncode == 1 and "in use" in r.stderr
                  and (profile / "extensions" / f"{ADDON_ID}.xpi").exists(), r.stdout + r.stderr)
        finally:
            stop(proc, args.browser)
        r = subprocess.run([*program, "addon", "remove", "--profile", str(profile), "--browser", args.browser],
                           capture_output=True, text=True, timeout=60)
        check("addon remove deletes the file once Firefox is closed", r.returncode == 0
              and not (profile / "extensions" / f"{ADDON_ID}.xpi").exists(), r.stdout + r.stderr)
        check("Firefox's list still names it before the next start", entry(profile) is not None)
        proc = start(args.browser, profile)
        try:
            gone = wait_for(lambda: entry(profile) is None, 45)
            check("after one start Firefox has dropped it from extensions.json", gone, entry(profile))
        finally:
            stop(proc, args.browser)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
