#!/usr/bin/env python3
"""Build foxreach as one program that needs neither Bun nor Node.js on the user's machine.

Run:      uv run --python 3.15 scripts/build_binary.py            dist/foxreach, for this machine
          uv run --python 3.15 scripts/build_binary.py --release  dist/release/foxreach-linux-x64 and -arm64
Needs:    Bun (the build machine only). --release downloads Bun's runtime for the other target once.
Expected: the paths written, each with its SHA-256, exit 0.

The program is Bun's runtime with bridge/ packed in: `foxreach mcp` is the MCP server, `foxreach
install` registers the native host. Two builds of the same commit with the same Bun version give
the same bytes (test/binary.py checks it), so a published file can be rebuilt and compared.
"""
import argparse
import hashlib
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
# "baseline" runs on x86-64 processors older than 2013 too.
TARGETS = {"foxreach-linux-x64": "bun-linux-x64-baseline", "foxreach-linux-arm64": "bun-linux-arm64"}


def bun():
    found = shutil.which("bun", path=f"{pathlib.Path.home()}/.bun/bin:/usr/bin:/usr/local/bin")
    if not found:
        raise SystemExit("Bun was not found; it is needed to build the program (not to run it).")
    return found


def bun_version():
    return subprocess.run([bun(), "--version"], check=True, capture_output=True, text=True).stdout.strip()


def pinned_bun():
    """The Bun version releases are built with (.bun-version); the same bytes need the same Bun."""
    return (ROOT / ".bun-version").read_text().strip()


def build(out, target=None):
    out.parent.mkdir(parents=True, exist_ok=True)
    command = [bun(), "build", "--compile", str(ROOT / "bridge/cli.mjs"), "--outfile", str(out)]
    if target:
        command.insert(3, f"--target={target}")
    # From the same folder every time: the folder's name is not part of what Bun packs.
    subprocess.run(command, check=True, capture_output=True, cwd=ROOT)
    return out


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--release", action="store_true", help="build every published target into dist/release")
    args = parser.parse_args()
    built = (
        [build(DIST / "release" / name, target) for name, target in TARGETS.items()]
        if args.release else [build(DIST / "foxreach")]
    )
    for path in built:
        print(f"{sha256(path)}  {path.relative_to(ROOT)}")
    print(f"built with Bun {bun_version()}", file=sys.stderr)
    if bun_version() != pinned_bun():
        print(f"warning: releases are built with Bun {pinned_bun()} (.bun-version); these files will differ from them", file=sys.stderr)
