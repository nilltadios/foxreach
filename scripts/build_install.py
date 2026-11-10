#!/usr/bin/env python3
"""Build the add-on and register the native host for the current user.

Run: uv run --python 3.15 scripts/build_install.py [--browser flatpak:<application id> ...]
Makes:
  dist/foxreach.xpi            trusted input; needs the two prefs in README.md
  dist/foxreach-untrusted.xpi  no privileged code; for browsers that refuse Experiments
  bridge/foxreach-host         wrapper the browser starts, using Bun, or Node.js if Bun is missing
  ~/.mozilla/native-messaging-hosts/foxreach.json   read by Firefox and every browser built on it,
                               whether a distro package, a tarball or an AppImage; also written to
                               ~/.librewolf/native-messaging-hosts when ~/.librewolf exists
For each Flatpak browser named, whose sandbox can see neither this folder nor Bun:
  ~/.var/app/<id>/foxreach/    a copy of the bridge and of Bun, with its own wrapper
  foxreach.json in the native-messaging-hosts folder the Flatpak keeps under ~/.var/app/<id>
  (.librewolf for LibreWolf's, .mozilla for Firefox's)
  a permission for that Flatpak to use $XDG_RUNTIME_DIR/foxreach, where the socket for agents is:
  flatpak override --user --filesystem=xdg-run/foxreach <id>
Undo: delete those; `flatpak override --user --nofilesystem=xdg-run/foxreach <id>` withdraws the
permission. This touches no browser profile.
"""
import argparse
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXT = ROOT / "extension"
DIST = ROOT / "dist"
ADDON_ID = "foxreach@foxreach.local"
HOST_NAME = "foxreach"
sys.path.insert(0, str(ROOT / "scripts"))
import browsers  # noqa: E402


def version():
    return json.loads((EXT / "manifest.json").read_text())["version"]


def build(path, trusted, version=None, update_url=None):
    """version and update_url replace the manifest's, for tests of the update path."""
    manifest = json.loads((EXT / "manifest.json").read_text())
    gecko = manifest["browser_specific_settings"]["gecko"]
    if version:
        manifest["version"] = version
    if update_url:
        gecko["update_url"] = update_url
    if not trusted:
        manifest.pop("experiment_apis")
        manifest["name"] = "foxreach (untrusted input)"
        # The published updates are the full build; they must not replace this one.
        gecko.pop("update_url", None)
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


# What an AppImage browser exports for itself; none of it may reach the runtime.
UNSET = "unset LD_LIBRARY_PATH LD_PRELOAD APPDIR APPIMAGE ARGV0 OWD\n"


def write_wrapper(wrapper, node, host):
    wrapper.write_text(f'#!/bin/sh\n{UNSET}exec "{node}" "{host}"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)


def write_manifests(folders, wrapper):
    written = []
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        manifest = folder / f"{HOST_NAME}.json"
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
        written.append(manifest)
    return written


def host_dirs():
    """Where builds outside a sandbox look: ~/.mozilla, and a fork's own folder if the fork is used."""
    home = pathlib.Path.home()
    extra = {d for k in browsers.KINDS for d in k.host_dirs if (home / d).is_dir()}
    return [home / d / "native-messaging-hosts" for d in (".mozilla", *sorted(extra))]


def install_host():
    wrapper = ROOT / "bridge/foxreach-host"
    write_wrapper(wrapper, runtime(), ROOT / "bridge/host.mjs")
    return write_manifests(host_dirs(), wrapper)


def copy_bridge(base):
    """Put the bridge and Bun in base, a folder a sandbox sees under the same path. Returns the wrapper."""
    node = pathlib.Path(runtime())
    if node.name != "bun":
        raise SystemExit("A Flatpak browser needs Bun: it is one file that runs inside the sandbox; Node.js is not.")
    (base / "bridge").mkdir(parents=True, exist_ok=True)
    (base / "extension").mkdir(exist_ok=True)
    for f in (ROOT / "bridge").glob("*.mjs"):
        shutil.copy2(f, base / "bridge" / f.name)
    shutil.copy2(EXT / "manifest.json", base / "extension/manifest.json")  # bridge/version.mjs reads it
    if not (base / "bun").exists() or (base / "bun").stat().st_size != node.stat().st_size:
        shutil.copy2(node.resolve(), base / "bun")
    wrapper = base / "foxreach-host"
    write_wrapper(wrapper, base / "bun", base / "bridge/host.mjs")
    return wrapper


def install_flatpak_host(browser):
    """A Flatpak browser starts the host inside its sandbox, so everything the host needs goes into
    the Flatpak's own folder, the one place both sides see under the same path."""
    wrapper = copy_bridge(browser.home / "foxreach")
    browser.socket_dir().mkdir(mode=0o700, exist_ok=True)
    subprocess.run(["flatpak", "override", "--user", "--filesystem=xdg-run/foxreach", browser.app_id], check=True)
    # A Flatpak keeps only the folders its manifest names (.librewolf for LibreWolf's); the rest of
    # its home is thrown away at exit. Once it has run, those exist: write only there.
    kept = [d for d in browser.host_dirs() if d.parent.is_dir()]
    return write_manifests(kept or browser.host_dirs(), wrapper)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--browser", action="append", default=[], help="a Flatpak browser to prepare as well")
    args = parser.parse_args()
    DIST.mkdir(exist_ok=True)
    print(build(DIST / "foxreach.xpi", True))
    print(build(DIST / "foxreach-untrusted.xpi", False))
    print(*install_host(), sep="\n")
    for spec in args.browser:
        browser = browsers.resolve(spec)
        if browser.app_id:
            print(*install_flatpak_host(browser), sep="\n")
    print(runtime())
