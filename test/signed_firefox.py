#!/usr/bin/env python3
"""Does release Firefox accept the Mozilla-signed build (and refuse an unsigned one)?

Run:      uv run --python 3.15 test/signed_firefox.py [--browser flatpak:org.mozilla.firefox]
Needs:    dist/signed/foxreach-<version>-signed.xpi (scripts/sign.py) and a release Firefox, which
          enforces signatures. Default: the Firefox Flatpak.
How:      two fresh throwaway profiles (inside the Flatpak's own folder, the only place it can read),
          each with one add-on dropped into its extensions folder, Firefox started headless once,
          then extensions.json read: what Firefox decided about the signature. No Marionette, no
          automation, no user profile touched.
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
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_install import build, version  # noqa: E402

ADDON_ID = "foxreach@foxreach.local"
SIGNED_STATES = {-2: "broken", -1: "unknown", 0: "missing", 1: "preliminary", 2: "signed", 3: "system", 4: "privileged"}
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


def run_once(browser, base, xpi):
    """Start Firefox on a fresh profile holding xpi; return its entry in extensions.json."""
    profile = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-signcheck-", dir=base))
    (profile / "extensions").mkdir()
    shutil.copy2(xpi, profile / "extensions" / f"{ADDON_ID}.xpi")
    # Enable add-ons found in the profile's own folder at startup (they are disabled by default).
    (profile / "user.js").write_text('user_pref("extensions.autoDisableScopes", 0);\n'
                                     'user_pref("extensions.startupScanScopes", 1);\n')
    if browser.startswith("flatpak:"):
        cmd = ["flatpak", "run", browser[8:]]
    else:
        cmd = [browser]
    proc = subprocess.Popen(cmd + ["--headless", "--no-remote", "--profile", str(profile), "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    entry = None
    try:
        deadline = time.time() + 60
        while time.time() < deadline and entry is None:
            time.sleep(1)
            try:
                data = json.loads((profile / "extensions.json").read_text())
                entry = next((a for a in data["addons"] if a["id"] == ADDON_ID), None)
            except (OSError, ValueError, KeyError):
                pass
        time.sleep(2)  # let the decision settle, then read once more
        try:
            data = json.loads((profile / "extensions.json").read_text())
            entry = next((a for a in data["addons"] if a["id"] == ADDON_ID), entry)
        except (OSError, ValueError, KeyError):
            pass
    finally:
        try:
            proc.send_signal(signal.SIGTERM)  # our own throwaway instance, not the user's browser
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(profile, ignore_errors=True)
    return entry


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--browser", default="flatpak:org.mozilla.firefox")
    args = p.parse_args()
    signed = ROOT / f"dist/signed/foxreach-{version()}-signed.xpi"
    if not signed.exists():
        raise SystemExit(f"{signed} is missing; run scripts/sign.py first.")
    base = (pathlib.Path.home() / ".var/app" / args.browser[8:] if args.browser.startswith("flatpak:")
            else pathlib.Path(tempfile.gettempdir()))
    base.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(signed) as z:
        check("signed file carries Mozilla's signature files",
              {"META-INF/cose.sig", "META-INF/mozilla.rsa"} <= set(z.namelist()), z.namelist())

    entry = run_once(args.browser, base, signed)
    check("release Firefox installs the signed build", entry is not None, "no entry in extensions.json")
    if entry:
        state = SIGNED_STATES.get(entry.get("signedState"), entry.get("signedState"))
        check("Firefox recognizes Mozilla's signature", state == "signed", f"signedState={state}")
        check("and runs it (not disabled for signing)", entry.get("active") and not entry.get("appDisabled"),
              {k: entry.get(k) for k in ("active", "appDisabled", "userDisabled")})

    with tempfile.TemporaryDirectory() as d:
        unsigned = pathlib.Path(d) / "unsigned.xpi"
        build(unsigned, False)
        entry = run_once(args.browser, base, unsigned)
    # Control: the same build without Mozilla's signature must be refused, or the check proves nothing.
    check("control: an unsigned build is refused",
          entry is None or entry.get("appDisabled") or not entry.get("active"),
          {k: entry.get(k) for k in ("active", "appDisabled", "signedState")} if entry else None)

    print("failed:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
