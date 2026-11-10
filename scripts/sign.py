#!/usr/bin/env python3
"""Have Mozilla sign the build without privileged code, so release Firefox installs it for good.

Run:      uv run --python 3.15 scripts/sign.py --dry-run            build and check only; sends nothing
          uv run --python 3.15 scripts/sign.py                      sign, not listed on addons.mozilla.org
          uv run --python 3.15 scripts/sign.py --channel listed     submit for listing (Mozilla reviews it)
Needs:    Node.js (for Mozilla's web-ext, fetched by npx at the version pinned below), and an API key
          from https://addons.mozilla.org/developers/addon/api/key/ in the environment, or in
          ~/.config/foxreach/amo.env (mode 600), as two lines:
              WEB_EXT_API_KEY=user:12345:67
              WEB_EXT_API_SECRET=...
Sends:    the add-on's files to addons.mozilla.org, under that account. Nothing else leaves.
Makes:    dist/signed/foxreach-<version>-signed.xpi (unlisted: within minutes; listed: once approved,
          from the add-on's page there). scripts/release.py then publishes the unlisted one.
Expected: exit 0 and the path of the signed file; --dry-run ends with "0 errors".

Only this build can be signed: Mozilla signs privileged code (the Experiment in the full build) for
its own add-ons only. Each version can be submitted once; raise "version" in extension/manifest.json
for the next. A listed add-on may not carry an update_url, so it is built without one.
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_install import build, version  # noqa: E402

WEB_EXT = "web-ext@10.8.0"
KEY_FILE = pathlib.Path.home() / ".config/foxreach/amo.env"
KEYS = ("WEB_EXT_API_KEY", "WEB_EXT_API_SECRET")
SIGNED = ROOT / "dist/signed"
# Installed copies of the unlisted build look here for newer ones (docs/, served by GitHub Pages).
SIGNED_UPDATES = "https://nilltadios.github.io/foxreach/updates-signed.json"


def signed_path(ver=None):
    return SIGNED / f"foxreach-{ver or version()}-signed.xpi"


def build_store(path, channel):
    """The build Mozilla signs. Unlisted copies update from our own file; listed ones from Mozilla."""
    return build(path, False, update_url=SIGNED_UPDATES if channel == "unlisted" else None)


def credentials():
    found = {k: os.environ[k] for k in KEYS if os.environ.get(k)}
    if len(found) < 2 and KEY_FILE.exists():
        if KEY_FILE.stat().st_mode & 0o077:
            raise SystemExit(f"{KEY_FILE} can be read by others. Run: chmod 600 {KEY_FILE}")
        for line in KEY_FILE.read_text().splitlines():
            name, _, value = line.strip().partition("=")
            if name in KEYS and value:
                found.setdefault(name, value.strip().strip("'\""))
    if len(found) < 2:
        raise SystemExit(f"No API key. Set {' and '.join(KEYS)}, or put them in {KEY_FILE} (see --help).")
    return found


def web_ext(*args, env=None):
    npx = shutil.which("npx")
    if not npx:
        raise SystemExit("npx was not found; Mozilla's web-ext needs Node.js.")
    return subprocess.run([npx, "--yes", WEB_EXT, *args], env={**os.environ, **(env or {})})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--channel", choices=("unlisted", "listed"), default="unlisted")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="foxreach-sign-") as d:
        work = pathlib.Path(d)
        source = work / "source"
        with zipfile.ZipFile(build_store(work / "store.xpi", args.channel)) as z:
            z.extractall(source)
        # Two warnings are expected: the Experiment call the full build uses (absent here, never
        # reached), and eval, which is what the javascript tool is.
        if web_ext("lint", "--source-dir", str(source)).returncode:
            raise SystemExit("The add-on does not pass Mozilla's checks; nothing was sent.")
        if args.dry_run:
            print(f"dry run: {version()} ({args.channel}) passes the checks; nothing was sent.")
            return 0
        command = ["sign", "--source-dir", str(source), "--artifacts-dir", str(work / "out"), "--channel", args.channel]
        if args.channel == "listed":
            # Listing needs a licence, a summary and a category; review can take days, so do not wait.
            command += ["--amo-metadata", str(ROOT / "amo/metadata-listed.json"), "--approval-timeout", "0"]
        if web_ext(*command, env=credentials()).returncode:
            raise SystemExit("Signing failed; see the message above. A version can be submitted only once.")
        made = sorted((work / "out").glob("*.xpi"))
        if not made:
            print("Submitted. Mozilla has not approved it yet; the signed file will be on the add-on's page.")
            return 0
        SIGNED.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(made[0], signed_path())
        print(signed_path())
    return 0


if __name__ == "__main__":
    sys.exit(main())
