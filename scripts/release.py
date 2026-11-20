#!/usr/bin/env python3
"""Prepare a release: what installed add-ons update from, and the files people download.

Run:      uv run --python 3.15 scripts/release.py              prepare the manifest's version
          uv run --python 3.15 scripts/release.py --verify     check what GitHub Pages serves now
          uv run --python 3.15 scripts/release.py --signed     add the signed build of a version already
                                                               released (Mozilla approved it later)
Before:   raise "version" in extension/manifest.json, commit; the work tree must be clean. The manager app
            (manager/) has its own version and its own release (README.md, "Releasing").
          For release Firefox, sign that version first: scripts/sign.py (optional; needs an API key).
Makes:    docs/foxreach-<version>.xpi and an entry in docs/updates.json: the full add-on, which
            installed copies fetch by themselves (GitHub Pages serves docs/; older entries stay).
          docs/foxreach-<version>-signed.xpi and an entry in docs/updates-signed.json, if
            dist/signed/ holds the signed build of this version.
          --signed: only docs/foxreach-<version>-signed.xpi and its entry in docs/updates-signed.json,
            for a version already in docs/updates.json (a listed build approved after the release).
          dist/release/: the foxreach program for x64 and arm64, install.sh, install.ps1, the add-ons, SHA256SUMS
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
import tempfile
import urllib.request
import zipfile

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
        raise SystemExit(f"{ver} is already in {file.relative_to(ROOT)}. Raise the version in extension/manifest.json "
                         "(or, for its signed build only, run release.py --signed).")
    updates.append(updates_entry(ver, f"{update_url().rsplit('/', 1)[0]}/{xpi.name}", xpi))
    file.write_text(json.dumps(data, indent=2) + "\n")
    return len(updates)


def commit():
    try:
        return git("rev-parse", "HEAD")
    except (subprocess.CalledProcessError, OSError):
        return "unknown (not built in a git checkout)"


def entries(xpi):
    """An add-on's files and their hashes, whatever the compression."""
    with zipfile.ZipFile(xpi) as z:
        return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if not n.endswith("/")}


def release_files(ver, out):
    """Everything a release carries, built into out from this folder alone. The release workflow
    and scripts/verify_release.py call this too (--files-only), so all three make the same files."""
    out.mkdir(parents=True, exist_ok=True)
    full = build(out / f"foxreach-{ver}.xpi", True)
    published = DOCS / full.name
    if published.exists() and sha256(published) != sha256(full):
        # Another zlib (Python on Windows packs with zlib-ng) compresses the same files differently:
        # the same entries are the same add-on, and the release carries the published bytes.
        if entries(published) != entries(full):
            raise SystemExit(f"{published.relative_to(ROOT)} is not what this source builds: {sha256(published)} there, {sha256(full)} built.")
        shutil.copyfile(published, full)
    # What Mozilla signed, before signing: the signed file must hold exactly these files and a signature.
    build_store(out / f"foxreach-{ver}-unsigned.xpi", "unlisted")
    signed = DOCS / signed_path(ver).name
    if signed.exists():
        shutil.copyfile(signed, out / signed.name)
    for name, target in build_binary.TARGETS.items():
        build_binary.build(out / name, target)
    shutil.copyfile(ROOT / "install.sh", out / "install.sh")
    shutil.copyfile(ROOT / "install.ps1", out / "install.ps1")
    names = sorted(p.name for p in out.iterdir() if p.name not in ("SHA256SUMS", "BUILDINFO.txt"))
    # newline="\n": Windows would write CRLF, which sha256sum -c reads as part of each name.
    (out / "SHA256SUMS").write_text("".join(f"{sha256(out / n)}  {n}\n" for n in names), newline="\n")
    (out / "BUILDINFO.txt").write_text(
        f"foxreach {ver}\ncommit {commit()}\nbun {build_binary.bun_version()}\n"
        "Rebuild and compare: uv run --python 3.15 scripts/verify_release.py " + ver + "\n", newline="\n")
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


def add_signed():
    """Publish the Mozilla-signed build of a version that is already released. A listed build is
    approved days after the release, when prepare() refuses the version. Writes only the signed file
    in docs/ and its entry in docs/updates-signed.json."""
    ver = version()
    signed = signed_path(ver)
    listing = DOCS / pathlib.PurePosixPath(SIGNED_UPDATES).name
    listed = lambda file: [u["version"] for u in json.loads(file.read_text())["addons"][ADDON_ID]["updates"]] if file.exists() else []  # noqa: E731
    if ver not in listed(DOCS / "updates.json"):
        raise SystemExit(f"{ver} is not released (not in docs/updates.json): release it first, with release.py alone.")
    if ver in listed(listing):
        raise SystemExit(f"{ver} is already in {listing.relative_to(ROOT)}: nothing to add.")
    if not signed.exists():
        raise SystemExit(f"No {signed.relative_to(ROOT)}: download the signed file from addons.mozilla.org to there.")
    files = entries(signed)
    unsigned = {n: h for n, h in files.items() if not n.startswith("META-INF/")}
    if unsigned == files:
        raise SystemExit(f"{signed.relative_to(ROOT)} has no signature (META-INF/).")
    with tempfile.TemporaryDirectory(prefix="foxreach-signed-") as d:
        # What scripts/sign.py sends, for either channel: the signed file must hold exactly that.
        if not any(entries(build_store(pathlib.Path(d) / f"{c}.xpi", c)) == unsigned for c in ("listed", "unlisted")):
            raise SystemExit(f"{signed.relative_to(ROOT)} does not hold the files this source builds for Mozilla (scripts/sign.py).")
    shutil.copyfile(signed, DOCS / signed.name)
    count = add_update(listing.name, ver, DOCS / signed.name)
    print(f"signed build of {ver}: docs/{signed.name} sha256 {sha256(signed)} ({count} entries in docs/{listing.name})")
    print("\nTo publish (changes GitHub):")
    print(f"  git add docs && git commit -m 'Signed build of {ver}' && git push origin main")
    print("Then check:  uv run --python 3.15 scripts/release.py --verify")


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
    parser.add_argument("--signed", action="store_true", help="only add the signed build of a version already released")
    parser.add_argument("--files-only", metavar="DIR", help="only build the release's files into DIR (the release workflow)")
    args = parser.parse_args()
    if args.files_only:
        print(*release_files(version(), pathlib.Path(args.files_only).resolve()), sep="\n")
        sys.exit(0)
    if args.verify:
        sys.exit(verify())
    add_signed() if args.signed else prepare()
