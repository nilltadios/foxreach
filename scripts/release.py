#!/usr/bin/env python3
"""Prepare a release that installed copies of the add-on pick up by themselves.

Run:      uv run --python 3.15 scripts/release.py              prepare docs/ for the manifest's version
          uv run --python 3.15 scripts/release.py --verify     check what GitHub Pages serves now
Before:   raise "version" in extension/manifest.json and commit; the work tree must be clean.
Makes:    docs/foxreach-<version>.xpi and a new entry in docs/updates.json (older entries stay).
          The manifest's update_url points at docs/updates.json as served by GitHub Pages. Browsers
          check it about once a day and install a higher version in the background.
Publish:  this script changes no remote. It prints the commit, tag and push commands to run.
Expected: exit 0. --verify prints "published: <version> ... hash matches".
Undo:     before pushing, `git checkout docs/ && git clean -f docs/`.
"""
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_install import build, version  # noqa: E402

DOCS = ROOT / "docs"
UPDATES = DOCS / "updates.json"
ADDON_ID = "foxreach@foxreach.local"
# Offered only to browsers at least this new: the version tested on (README.md, "Browser updates").
MIN_BROWSER = "159.0"


def update_url():
    manifest = json.loads((ROOT / "extension/manifest.json").read_text())
    return manifest["browser_specific_settings"]["gecko"]["update_url"]


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def updates_entry(ver, link, xpi):
    """One entry of an updates.json, in the format Firefox-based browsers read."""
    return {
        "version": ver,
        "update_link": link,
        "update_hash": f"sha256:{sha256(xpi)}",
        "applications": {"gecko": {"strict_min_version": MIN_BROWSER}},
    }


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def prepare():
    ver = version()
    if git("status", "--porcelain"):
        raise SystemExit("The work tree has changes. Commit them first, so the release matches a commit.")
    data = json.loads(UPDATES.read_text()) if UPDATES.exists() else {"addons": {ADDON_ID: {"updates": []}}}
    updates = data["addons"][ADDON_ID]["updates"]
    if any(u["version"] == ver for u in updates):
        raise SystemExit(f"{ver} is already in {UPDATES.relative_to(ROOT)}. Raise the version in extension/manifest.json.")
    base = update_url().rsplit("/", 1)[0]
    DOCS.mkdir(exist_ok=True)
    xpi = DOCS / f"foxreach-{ver}.xpi"
    build(xpi, True)
    updates.append(updates_entry(ver, f"{base}/{xpi.name}", xpi))
    UPDATES.write_text(json.dumps(data, indent=2) + "\n")
    (DOCS / ".nojekyll").touch()
    shutil.copyfile(xpi, ROOT / "dist/foxreach.xpi")
    print(f"prepared {ver}: {xpi.relative_to(ROOT)} sha256 {sha256(xpi)}")
    print(f"updated {UPDATES.relative_to(ROOT)} ({len(updates)} entries)")
    print("\nTo publish (changes GitHub):")
    print(f"  git add docs && git commit -m 'Release {ver}' && git tag v{ver} && git push origin main v{ver}")
    print("Once per repo, serve docs/ with GitHub Pages:")
    print("  gh api -X POST repos/{owner}/{repo}/pages -f 'source[branch]=main' -f 'source[path]=/docs'")
    print("Then check it:  uv run --python 3.15 scripts/release.py --verify")


def verify():
    url = update_url()
    with urllib.request.urlopen(url, timeout=30) as r:
        data = json.load(r)
    updates = data["addons"][ADDON_ID]["updates"]
    newest = updates[-1]
    with urllib.request.urlopen(newest["update_link"], timeout=60) as r:
        body = r.read()
    got = "sha256:" + hashlib.sha256(body).hexdigest()
    ok = got == newest["update_hash"]
    print(f"published: {newest['version']} at {newest['update_link']}, {'hash matches' if ok else 'HASH DIFFERS: ' + got}")
    if newest["version"] != version():
        print(f"note: this checkout is {version()}")
    return 0 if ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    sys.exit(verify() if args.verify else prepare() or 0)
