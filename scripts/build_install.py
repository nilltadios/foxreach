#!/usr/bin/env python3
"""Build the add-on and register the native host for the current user.

Run: uv run --python 3.15 scripts/build_install.py
Makes:
  dist/foxreach.xpi            trusted input; needs the two prefs in README.md
  dist/foxreach-untrusted.xpi  no privileged code; for browsers that refuse Experiments
  bridge/foxreach-host         wrapper the browser starts, using Bun, or Node.js if Bun is missing
  ~/.mozilla/native-messaging-hosts/foxreach.json   read by LibreWolf and Firefox
Undo: delete those. This touches no browser profile.
"""
import json
import os
import pathlib
import shutil
import stat
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
DIST = ROOT / "dist"
ADDON_ID = "foxreach@foxreach.local"
HOST_NAME = "foxreach"
NATIVE_DIR = pathlib.Path.home() / ".mozilla/native-messaging-hosts"


def build(path, trusted):
    manifest = json.loads((EXT / "manifest.json").read_text())
    if not trusted:
        manifest.pop("experiment_apis")
        manifest["name"] = "foxreach (untrusted input)"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        for f in sorted(EXT.rglob("*")):
            rel = f.relative_to(EXT)
            if f.is_file() and rel.name != "manifest.json":
                if trusted or rel.parts[0] != "experiment":
                    z.write(f, rel)
    return path


def runtime():
    """The JavaScript runtime for the bridge: FOXREACH_RUNTIME, else Bun, else Node.js."""
    search = f"{pathlib.Path.home()}/.bun/bin:/usr/bin:/usr/local/bin:" + os.environ.get("PATH", "")
    for name in filter(None, [os.environ.get("FOXREACH_RUNTIME"), "bun", "node"]):
        found = shutil.which(name, path=search)
        if found:
            return found
    raise SystemExit("Neither bun nor node was found; the bridge needs one of them.")


def install_host():
    node = runtime()
    wrapper = ROOT / "bridge/foxreach-host"
    # An AppImage browser exports its own library paths, which must not reach the runtime.
    wrapper.write_text(
        "#!/bin/sh\n"
        "unset LD_LIBRARY_PATH LD_PRELOAD APPDIR APPIMAGE ARGV0 OWD\n"
        f'exec "{node}" "{ROOT}/bridge/host.mjs"\n'
    )
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    NATIVE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = NATIVE_DIR / f"{HOST_NAME}.json"
    manifest.write_text(
        json.dumps(
            {
                "name": HOST_NAME,
                "description": "foxreach bridge between the browser and local MCP agents",
                "path": str(wrapper),
                "type": "stdio",
                "allowed_extensions": [ADDON_ID],
            },
            indent=2,
        )
    )
    return manifest


if __name__ == "__main__":
    DIST.mkdir(exist_ok=True)
    print(build(DIST / "foxreach.xpi", True))
    print(build(DIST / "foxreach-untrusted.xpi", False))
    print(install_host())
    print(runtime())
