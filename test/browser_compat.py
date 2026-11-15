#!/usr/bin/env python3
"""Which foxreach build runs in a browser: the full build, the untrusted build, the signed build.

Run:      uv run --python 3.15 test/browser_compat.py --browser PATH [--home DIR]
          (PATH: the browser's program, e.g. <tor-browser>/Browser/firefox)
Needs:    dist/foxreach.xpi and dist/foxreach-untrusted.xpi (scripts/build_install.py),
          dist/signed/foxreach-<version>-signed.xpi, and the native host registered in --home
          (default: a throwaway HOME is NOT made for you; pass the one build_install.py wrote to).
How:      for each build, a fresh throwaway profile with the add-on dropped into its extensions
          folder and the prefs that build needs, the browser started headless once with HOME set
          to --home, then: what extensions.json says (active, signature) and whether the add-on
          really ran, i.e. started the native host, which then opened its socket. No user profile
          or browser is touched.
Expected: a table, one row per build; exit 0. It reports, it does not judge.
"""
import os
if os.name == "nt":
    raise SystemExit("This test runs on Linux and macOS only: Windows ignores its throwaway HOME, so it "
                     "would act on the real foxreach setup. The Windows test is test/windows_hosts.py.")

import argparse
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADDON_ID = "foxreach@foxreach.local"
SIGNED_STATES = {-2: "broken", -1: "unknown", 0: "missing", 1: "preliminary", 2: "signed", 3: "system", 4: "privileged"}
BASE_PREFS = ['user_pref("extensions.autoDisableScopes", 0);', 'user_pref("extensions.startupScanScopes", 1);']
UNSIGNED_PREFS = ['user_pref("xpinstall.signatures.required", false);']
FULL_PREFS = UNSIGNED_PREFS + ['user_pref("extensions.experiments.enabled", true);']


def probe(browser, home, xpi, prefs, wait=45, private=False):
    work = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-compat-"))
    profile, sock = work / "profile", work / "s.sock"
    (profile / "extensions").mkdir(parents=True)
    shutil.copy2(xpi, profile / "extensions" / f"{ADDON_ID}.xpi")
    (profile / "user.js").write_text("\n".join(BASE_PREFS + prefs) + "\n")
    if private:
        # "Run in Private Windows": a browser that is always private (Tor Browser) runs no other add-on.
        (profile / "extension-preferences.json").write_text(json.dumps(
            {ADDON_ID: {"permissions": ["internal:privateBrowsingAllowed"], "origins": []}}))
    env = dict(os.environ, HOME=str(home), FOXREACH_SOCKET=str(sock), FOXREACH_HOST_LOG=str(work / "host.log"))
    proc = subprocess.Popen([browser, "--headless", "--no-remote", "--new-instance", "--profile", str(profile), "about:blank"],
                            env=env, stdout=open(work / "browser.log", "wb"), stderr=subprocess.STDOUT, start_new_session=True)
    entry, ran = None, False
    try:
        deadline = time.time() + wait
        while time.time() < deadline and not ran:
            time.sleep(1)
            ran = sock.exists()
        try:
            data = json.loads((profile / "extensions.json").read_text())
            entry = next((a for a in data["addons"] if a["id"] == ADDON_ID), None)
        except (OSError, ValueError, KeyError):
            pass
        log = (work / "browser.log").read_text(errors="replace")
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)  # our own throwaway instance and what it started
            proc.wait(timeout=20)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        shutil.rmtree(work, ignore_errors=True)
    hints = [l.strip() for l in log.splitlines() if any(k in l.lower() for k in ("experiment", "foxreach", "native messag", "signature"))][:3]
    return entry, ran, hints


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--browser", required=True)
    p.add_argument("--home", required=True, help="HOME with the native host registered in .mozilla")
    p.add_argument("--private", action="store_true", help="also allow the add-on in private windows")
    args = p.parse_args()
    version = json.loads((ROOT / "extension/manifest.json").read_text())["version"]
    signed = sorted((ROOT / "dist/signed").glob("foxreach-*-signed.xpi"))
    builds = [("full (experiments, trusted input)", ROOT / "dist/foxreach.xpi", FULL_PREFS),
              ("untrusted (no experiments)", ROOT / "dist/foxreach-untrusted.xpi", UNSIGNED_PREFS),
              (f"signed ({signed[-1].name})" if signed else "signed (missing)", signed[-1] if signed else None, [])]
    print(f"browser: {args.browser}  (checkout {version})")
    for name, xpi, prefs in builds:
        if not xpi or not xpi.exists():
            print(f"  {name:40} file missing")
            continue
        entry, ran, hints = probe(args.browser, pathlib.Path(args.home), xpi, prefs, private=args.private)
        if entry is None:
            verdict = "not installed"
        else:
            verdict = (f"active={entry.get('active')} appDisabled={entry.get('appDisabled')} "
                       f"signature={SIGNED_STATES.get(entry.get('signedState'), entry.get('signedState'))}")
        print(f"  {name:40} {verdict}  ran_host={ran}")
        for h in hints:
            print(f"      log: {h[:160]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
