#!/usr/bin/env python3
"""Prepare a release: what installed add-ons update from, and the files people download.

Run:      uv run --python 3.15 scripts/release.py              prepare the manifest's version
          uv run --python 3.15 scripts/release.py --verify     check what GitHub Pages serves now
Before:   raise "version" in extension/manifest.json and commit; the work tree must be clean.
          For release Firefox, sign that version first: scripts/sign.py (optional; needs an API key).
Makes:    docs/foxreach-<version>.xpi and an entry in docs/updates.json: the full add-on, which
            installed copies fetch by themselves (GitHub Pages serves docs/; older entries stay).
          docs/foxreach-<version>-signed.xpi and an entry in docs/updates-signed.json, if
            dist/signed/ holds the signed build of this version.
          dist/release/: the foxreach program for x64 and arm64, install.sh, the add-ons, SHA256SUMS
            and BUILDINFO.txt: the same files the release workflow builds and publishes.
Publish:  this script changes no remote. It prints the commit, tag and push commands; pushing the
          tag makes .github/workflows/release.yml build the same files and publish the release.
Expected: exit 0. --verify prints "published: <version> ... hash matches" for each list of updates.
Undo:     before pushing, `git checkout docs/ && git clean -f docs/`.
Check a published release against its source: scripts/verify_release.py.
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
import build_binary  # noqa: E402
from build_install import build, version  # noqa: E402
from sign import SIGNED_UPDATES, build_store, signed_path  # noqa: E402

DOCS = ROOT / "docs"
RELEASE = ROOT / "dist/release"
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


def add_update(listing, ver, xpi):
    """Append xpi to a list of updates in docs/. Returns how many entries it has now."""
    file = DOCS / listing
    data = json.loads(file.read_text()) if file.exists() else {"addons": {ADDON_ID: {"updates": []}}}
    updates = data["addons"][ADDON_ID]["updates"]
    if any(u["version"] == ver for u in updates):
        raise SystemExit(f"{ver} is already in {file.relative_to(ROOT)}. Raise the version in extension/manifest.json.")
    updates.append(updates_entry(ver, f"{update_url().rsplit('/', 1)[0]}/{xpi.name}", xpi))
    file.write_text(json.dumps(data, indent=2) + "\n")
    return len(updates)


def commit():
    try:
        return git("rev-parse", "HEAD")
    except (subprocess.CalledProcessError, OSError):
        return "unknown (not built in a git checkout)"


def release_files(ver, out):
    """Everything a release carries, built into out from this folder alone. The release workflow
    and scripts/verify_release.py call this too (--files-only), so all three make the same files."""
    out.mkdir(parents=True, exist_ok=True)
    full = build(out / f"foxreach-{ver}.xpi", True)
    published = DOCS / full.name
    if published.exists() and sha256(published) != sha256(full):
        raise SystemExit(f"{published.relative_to(ROOT)} is not what this source builds: {sha256(published)} there, {sha256(full)} built.")
    # What Mozilla signed, before signing: the signed file must hold exactly these files and a signature.
    build_store(out / f"foxreach-{ver}-unsigned.xpi", "unlisted")
    signed = DOCS / signed_path(ver).name
    if signed.exists():
        shutil.copyfile(signed, out / signed.name)
    for name, target in build_binary.TARGETS.items():
        build_binary.build(out / name, target)
    shutil.copyfile(ROOT / "install.sh", out / "install.sh")
    names = sorted(p.name for p in out.iterdir() if p.name not in ("SHA256SUMS", "BUILDINFO.txt"))
    (out / "SHA256SUMS").write_text("".join(f"{sha256(out / n)}  {n}\n" for n in names))
    (out / "BUILDINFO.txt").write_text(
        f"foxreach {ver}\ncommit {commit()}\nbun {build_binary.bun_version()}\n"
        "Rebuild and compare: uv run --python 3.15 scripts/verify_release.py " + ver + "\n")
    return names


def prepare():
    ver = version()
    if git("status", "--porcelain"):
        raise SystemExit("The work tree has changes. Commit them first, so the release matches a commit.")
    if build_binary.bun_version() != build_binary.pinned_bun():
        raise SystemExit(f"Releases are built with Bun {build_binary.pinned_bun()} (.bun-version); this is {build_binary.bun_version()}.")
    DOCS.mkdir(exist_ok=True)
    xpi = build(DOCS / f"foxreach-{ver}.xpi", True)
    count = add_update("updates.json", ver, xpi)
    (DOCS / ".nojekyll").touch()
    shutil.copyfile(xpi, ROOT / "dist/foxreach.xpi")
    print(f"prepared {ver}: {xpi.relative_to(ROOT)} sha256 {sha256(xpi)} ({count} entries in docs/updates.json)")
    signed = signed_path(ver)
    if signed.exists():
        shutil.copyfile(signed, DOCS / signed.name)
        add_update(pathlib.PurePosixPath(SIGNED_UPDATES).name, ver, DOCS / signed.name)
        print(f"signed build: docs/{signed.name}")
    else:
        print(f"no signed build of {ver} in dist/signed/: release Firefox gets none this time (scripts/sign.py makes it)")
    if RELEASE.exists():
        shutil.rmtree(RELEASE)
    names = release_files(ver, RELEASE)
    print(f"release files in {RELEASE.relative_to(ROOT)}: {', '.join(names)}")
    print("\nTo publish (changes GitHub):")
    print(f"  git add docs && git commit -m 'Release {ver}' && git tag v{ver} && git push origin main v{ver}")
    print("The tag starts the release workflow, which builds these files again and publishes them.")
    print("Once per repo, serve docs/ with GitHub Pages:")
    print("  gh api -X POST repos/{owner}/{repo}/pages -f 'source[branch]=main' -f 'source[path]=/docs'")
    print(f"Then check:  uv run --python 3.15 scripts/release.py --verify && uv run --python 3.15 scripts/verify_release.py {ver}")


def verify():
    failed = 0
    for url in (update_url(), SIGNED_UPDATES):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                data = json.load(r)
        except OSError as e:
            print(f"not published: {url} ({e})")
            failed += url == update_url()  # the signed list is optional
            continue
        newest = data["addons"][ADDON_ID]["updates"][-1]
        with urllib.request.urlopen(newest["update_link"], timeout=60) as r:
            got = "sha256:" + hashlib.sha256(r.read()).hexdigest()
        ok = got == newest["update_hash"]
        failed += not ok
        print(f"published: {newest['version']} at {newest['update_link']}, {'hash matches' if ok else 'HASH DIFFERS: ' + got}")
        if newest["version"] != version():
            print(f"note: this checkout is {version()}")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--files-only", metavar="DIR", help="only build the release's files into DIR (the release workflow)")
    args = parser.parse_args()
    if args.files_only:
        print(*release_files(version(), pathlib.Path(args.files_only).resolve()), sep="\n")
        sys.exit(0)
    sys.exit(verify() if args.verify else prepare() or 0)
