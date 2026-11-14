#!/usr/bin/env python3
"""Check that a published release is what its source builds.

Run:      uv run --python 3.15 scripts/verify_release.py [version]            the release on GitHub
          uv run --python 3.15 scripts/verify_release.py [version] --dir DIR  files already downloaded
          (version: default, the one in extension/manifest.json; its tag v<version> must be fetched)
Does:     takes the source at the tag, builds the release's files from it in a temporary folder
          (the tag's own scripts/release.py --files-only), and compares each published file:
            the add-on and the foxreach programs must be the same bytes;
            the signed add-on must hold the same files as the unsigned build, plus Mozilla's
            signature (META-INF/), which cannot be rebuilt.
Needs:    git, uv, and Bun at the version in the release's BUILDINFO.txt (the programs are Bun's
          runtime plus the bridge; another Bun gives other bytes, and they are then not compared).
Expected: one line per file, "N files match the source at v<version>", exit 0. Exit 1 if any differs.

Who built a file is a separate question: `gh attestation verify <file> --repo nilltadios/foxreach`
shows that GitHub's release workflow built it from this repository.
"""
import argparse
import hashlib
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPO_URL = "https://github.com/nilltadios/foxreach"


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def entries(xpi, skip=()):
    with zipfile.ZipFile(xpi) as z:
        return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if not n.startswith(skip) and not n.endswith("/")}


def download(ver, out):
    out.mkdir(parents=True)
    base = f"{REPO_URL}/releases/download/v{ver}"
    fetch = lambda name: (out / name).write_bytes(urllib.request.urlopen(f"{base}/{name}", timeout=300).read())  # noqa: E731
    fetch("SHA256SUMS")
    fetch("BUILDINFO.txt")
    for line in (out / "SHA256SUMS").read_text().splitlines():
        fetch(line.split()[1])
    return out


def rebuild(ver, work):
    """The release's files, built from the tag with the tag's own scripts."""
    source = work / "source"
    source.mkdir()
    archive = subprocess.run(["git", "archive", f"v{ver}"], cwd=ROOT, capture_output=True)
    if archive.returncode:
        raise SystemExit(f"No tag v{ver} here ({archive.stderr.decode().strip()}). Run: git fetch --tags")
    tarfile.open(fileobj=io.BytesIO(archive.stdout)).extractall(source, filter="data")
    built = subprocess.run(["uv", "run", "--python", "3.15", str(source / "scripts/release.py"), "--files-only", str(work / "rebuilt")],
                           capture_output=True, text=True)
    if built.returncode:
        raise SystemExit(f"The source at v{ver} does not build:\n{built.stdout}{built.stderr}")
    return work / "rebuilt"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", default=json.loads((ROOT / "extension/manifest.json").read_text())["version"])
    parser.add_argument("--dir", type=pathlib.Path, help="the release's files, already downloaded")
    args = parser.parse_args()
    ver = args.version.removeprefix("v")
    with tempfile.TemporaryDirectory(prefix="foxreach-verify-") as d:
        work = pathlib.Path(d)
        published = args.dir.resolve() if args.dir else download(ver, work / "published")
        rebuilt = rebuild(ver, work)
        info = dict(line.split(" ", 1) for line in (published / "BUILDINFO.txt").read_text().splitlines() if " " in line)
        ours = dict(line.split(" ", 1) for line in (rebuilt / "BUILDINFO.txt").read_text().splitlines() if " " in line)
        same_bun = info.get("bun") == ours.get("bun")
        failed = matched = 0
        for line in (published / "SHA256SUMS").read_text().splitlines():
            listed, name = line.split()
            file = published / name
            if sha256(file) != listed:
                verdict, ok = f"DIFFERS from its line in SHA256SUMS ({sha256(file)})", False
            elif name.endswith("-signed.xpi"):
                unsigned = rebuilt / name.replace("-signed.xpi", "-unsigned.xpi")
                theirs, mine = entries(file, skip=("META-INF/",)), entries(unsigned)
                odd = sorted(n for n in theirs.keys() | mine.keys() if theirs.get(n) != mine.get(n))
                signature = any(n.startswith("META-INF/") for n in zipfile.ZipFile(file).namelist())
                ok = not odd and signature
                verdict = "same files as the source builds, plus Mozilla's signature" if ok else f"DIFFERS in {odd or 'having no signature'}"
            elif not (rebuilt / name).exists():
                verdict, ok = "DIFFERS: the source does not build this file", False
            elif sha256(rebuilt / name) == listed:
                verdict, ok = "same bytes as the source builds", True
            elif name.endswith(".xpi") and entries(file) == entries(rebuilt / name):
                verdict, ok = "same files as the source builds (packed by another zlib)", True
            elif name.startswith(("foxreach-linux-", "foxreach-darwin-")) and not same_bun:
                print(f"skipped  {name}: built with Bun {info.get('bun')}, this machine has {ours.get('bun')}")
                continue
            else:
                verdict, ok = f"DIFFERS from what the source builds ({sha256(rebuilt / name)})", False
            print(f"{'ok      ' if ok else 'FAILED  '} {name}: {verdict}")
            matched += ok
            failed += not ok
        print(f"\n{matched} files match the source at v{ver}" + (f", {failed} DO NOT" if failed else "") + f" (published commit: {info.get('commit')})")
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
