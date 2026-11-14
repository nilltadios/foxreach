#!/usr/bin/env python3
"""Browser detection and native-host placement, on a made-up home folder. Starts no browser.

Run:      uv run --python 3.15 test/browsers.py
Expected: every line PASS, "N passed, 0 failed", exit 0.

The made-up home holds a distro-style command, an AppImage, a Flatpak (through a stand-in `flatpak`
command) and files that are not browsers. Each check runs scripts/ in a fresh process with that
home, since the scripts read the home folder once, at import.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLATPAK = "io.gitlab.librewolf-community"
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f" -- {detail}"))


def run(home, code, path=None, env=None):
    """Run code with scripts/ importable, in the made-up home. Returns what it printed, as JSON."""
    full = {"HOME": str(home), "PATH": path or f"{home}/bin:/usr/bin", "XDG_RUNTIME_DIR": f"{home}/run",
            "FOXREACH_PLATFORM": "linux"} | (env or {})
    out = subprocess.run(
        [sys.executable, "-c", f"import sys, json, pathlib; sys.path.insert(0, {str(ROOT / 'scripts')!r})\n{code}"],
        env=full, capture_output=True, text=True,
    )
    if out.returncode:
        return {"exit": out.stderr.strip() or out.returncode}
    return json.loads(out.stdout)


def executable(path, text="#!/bin/sh\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def mac_checks(home):
    """macOS: apps in /Applications and ~/Applications, profiles and manifests in Application Support."""
    mac = {"FOXREACH_PLATFORM": "darwin"}
    app = executable(home / "Applications/LibreWolf.app/Contents/MacOS/librewolf")
    executable(home / "Applications/Zenity.app/Contents/MacOS/zenity")  # not a browser
    found = run(home, "import browsers; print(json.dumps([[b.name, b.packaging, b.spec] for b in browsers.find()]))", env=mac)
    mine = [f for f in found if f[2].startswith(str(home))] if isinstance(found, list) else found
    check("macOS: finds an app in ~/Applications by the executable inside it, and no AppImage or Flatpak",
          mine == [["Firefox", "native", f"{home}/bin/firefox-developer-edition"], ["LibreWolf", "app", str(app)]], found)
    out = run(home, f"import browsers; b = browsers.resolve({str(home / 'Applications/LibreWolf.app')!r}); print(json.dumps([b.name, b.packaging, b.spec]))", env=mac)
    check("macOS: an app's folder is accepted as a spec", out == ["LibreWolf", "app", str(app)], out)
    out = run(home, "import build_install; print(json.dumps([str(p) for p in build_install.host_dirs()]))", env=mac)
    check("macOS: the manifest goes to Mozilla's folder in Application Support",
          out == [f"{home}/Library/Application Support/Mozilla/NativeMessagingHosts"], out)
    out = run(home, f"import browsers; print(json.dumps([str(p) for p in browsers.resolve({str(app)!r}).profile_roots()]))", env=mac)
    check("macOS: LibreWolf's profiles are looked for in Application Support",
          out == [f"{home}/Library/Application Support/librewolf"], out)


def mac_pids_checks(home):
    """mac_pids reads ps; a profile path that begins another one must not match it."""
    code = (
        "import browsers, subprocess, types\n"
        "out = ('101 /Applications/LibreWolf.app/Contents/MacOS/librewolf HOME=/h XRE_PROFILE_PATH=/p/a b MOZ_X=1\\n'\n"
        "       '102 /Applications/LibreWolf.app/Contents/MacOS/librewolf -profile /p/a -new-instance\\n'\n"
        "       '103 /Applications/LibreWolf.app/Contents/MacOS/librewolf HOME=/h XRE_PROFILE_PATH=/p/a\\n'\n"
        "       '104 /usr/bin/python3 setup.py --profile /p/a\\n')\n"
        "browsers.subprocess.run = lambda *a, **k: types.SimpleNamespace(stdout=out)\n"
        "print(json.dumps([browsers.mac_pids('/p/a'), browsers.mac_pids('/p/a b')]))"
    )
    out = run(home, code, env={"FOXREACH_PLATFORM": "darwin"})
    check("macOS: a browser is matched by its whole profile path, not one that begins another",
          out == [[102, 103], [101]], out)


def main():
    with tempfile.TemporaryDirectory(prefix="foxreach-browsers-") as d:
        home = pathlib.Path(d)
        (home / "run").mkdir()
        executable(home / "bin/firefox-developer-edition")
        executable(home / "bin/zenity")                       # not a browser, though it starts with "zen"
        executable(home / "AppImages/LibreWolf-159.0.1.x86_64.AppImage")
        executable(home / "AppImages/vscodium.appimage")      # not a browser
        executable(home / "bin/flatpak", f'#!/bin/sh\necho "$@" >> {home}/flatpak.log\n[ "$1" = list ] && echo {FLATPAK}\nexit 0\n')

        found = run(home, "import browsers; print(json.dumps([[b.name, b.packaging, b.spec] for b in browsers.find()]))")
        check("finds a command on PATH, an AppImage and a Flatpak, and nothing else", found == [
            ["Firefox", "native", f"{home}/bin/firefox-developer-edition"],
            ["LibreWolf", "appimage", f"{home}/AppImages/LibreWolf-159.0.1.x86_64.AppImage"],
            ["LibreWolf", "flatpak", f"flatpak:{FLATPAK}"],
        ], found)

        choose = "import browsers; b = browsers.choose({spec!r}, {profile}); print(json.dumps([b.name, b.packaging]))"
        native = f"pathlib.Path({str(home / '.config/librewolf/librewolf/abc.default')!r})"
        boxed = f"pathlib.Path({str(home / '.var/app' / FLATPAK / '.librewolf/abc.default')!r})"
        check("a profile in LibreWolf's folder picks the LibreWolf outside a sandbox",
              run(home, choose.format(spec=None, profile=native)) == ["LibreWolf", "appimage"])
        check("a profile in the Flatpak's folder picks the Flatpak",
              run(home, choose.format(spec=None, profile=boxed)) == ["LibreWolf", "flatpak"])
        out = run(home, choose.format(spec=None, profile=None))
        check("several browsers and no hint: asks for --browser", "More than one" in str(out) or "--browser" in str(out), out)
        check("a command name is accepted as a spec",
              run(home, choose.format(spec="firefox-developer-edition", profile=None)) == ["Firefox", "native"])
        check("FOXREACH_BROWSER decides when nothing is named",
              run(home, choose.format(spec=None, profile=None), env={"FOXREACH_BROWSER": f"flatpak:{FLATPAK}"}) == ["LibreWolf", "flatpak"])
        out = run(home, choose.format(spec="/nowhere/browser", profile=None))
        check("a spec that names nothing is refused", "No browser at" in str(out), out)
        out = run(home, choose.format(spec="flatpak:org.example.Missing", profile=None))
        check("a Flatpak that is not installed is refused", "No Flatpak" in str(out), out)

        command = run(home, f"import browsers; b = browsers.resolve('flatpak:{FLATPAK}'); "
                            "print(json.dumps([b.command(['--profile', '/p'], {'FOXREACH_SOCKET': '/s'}), str(b.default_profile()), str(b.socket_dir())]))")
        check("a Flatpak is started through flatpak run, its environment passed in", command[0] == [
            "flatpak", "run", "--env=FOXREACH_SOCKET=/s", FLATPAK, "--profile", "/p"], command)
        check("a Flatpak's dedicated profile and socket folder are ones its sandbox can reach",
              command[1:] == [f"{home}/.var/app/{FLATPAK}/foxreach/profile", f"{home}/run/foxreach"], command)

        # Native host manifests.
        hosts = "import build_install; print(json.dumps([str(p) for p in build_install.host_dirs()]))"
        check("outside a sandbox the manifest goes to ~/.mozilla",
              run(home, hosts) == [f"{home}/.mozilla/native-messaging-hosts"], run(home, hosts))
        (home / ".librewolf").mkdir()
        check("and to ~/.librewolf once that folder exists", run(home, hosts) == [
            f"{home}/.mozilla/native-messaging-hosts", f"{home}/.librewolf/native-messaging-hosts"], run(home, hosts))

        bun = shutil.which("bun", path=f"{pathlib.Path.home()}/.bun/bin:{os.environ.get('PATH', '')}")
        if not bun:
            print("SKIP the Flatpak host checks need Bun")
        else:
            path = f"{home}/bin:{os.path.dirname(bun)}:/usr/bin"
            out = run(home, f"import browsers, build_install; print(json.dumps([str(p) for p in "
                            f"build_install.install_flatpak_host(browsers.resolve('flatpak:{FLATPAK}'))]))", path=path)
            base = home / ".var/app" / FLATPAK
            check("a Flatpak gets the manifest in its own .mozilla and .librewolf", out == [
                f"{base}/.mozilla/native-messaging-hosts/foxreach.json",
                f"{base}/.librewolf/native-messaging-hosts/foxreach.json"], out)
            manifest = json.loads((base / ".mozilla/native-messaging-hosts/foxreach.json").read_text())
            wrapper = pathlib.Path(manifest["path"])
            check("its manifest names a wrapper inside the Flatpak's folder",
                  wrapper == base / "foxreach/foxreach-host" and os.access(wrapper, os.X_OK), manifest)
            text = wrapper.read_text()
            check("the wrapper runs the copied Bun on the copied bridge",
                  f'"{base}/foxreach/bun" "{base}/foxreach/bridge/host.mjs"' in text
                  and (base / "foxreach/bun").stat().st_size == os.path.getsize(os.path.realpath(bun))
                  and (base / "foxreach/bridge/socket.mjs").exists(), text)
            copied = subprocess.run([str(base / "foxreach/bun"), "-e",
                                     f'console.log((await import("{base}/foxreach/bridge/version.mjs")).VERSION)'],
                                    capture_output=True, text=True).stdout.strip()
            here = json.loads((ROOT / "extension/manifest.json").read_text())["version"]
            check("the copied bridge reports this folder's version", copied == here, f"{copied} != {here}")
            shutil.rmtree(base / ".mozilla")
            out = run(home, f"import browsers, build_install; print(json.dumps([str(p) for p in "
                            f"build_install.install_flatpak_host(browsers.resolve('flatpak:{FLATPAK}'))]))", path=path)
            check("once the Flatpak has its own folder, the manifest goes only there",
                  out == [f"{base}/.librewolf/native-messaging-hosts/foxreach.json"] and not (base / ".mozilla").exists(), out)
            log = (home / "flatpak.log").read_text()
            check("the Flatpak is allowed the socket folder and nothing more",
                  f"override --user --filesystem=xdg-run/foxreach {FLATPAK}" in log and set(l for l in log.splitlines() if "override" in l) == {f"override --user --filesystem=xdg-run/foxreach {FLATPAK}"}, log)
            check("the socket folder exists, for this user only",
                  (home / "run/foxreach").is_dir() and (home / "run/foxreach").stat().st_mode & 0o777 == 0o700)

        # scripts/foxreach-browser opens the profile with the browser setup recorded for it.
        profile = home / ".local/share/foxreach/profile"
        profile.mkdir(parents=True)
        shown = executable(home / "opt/some browser/firefox", '#!/bin/sh\nprintf "%s\\n" "$@"\n')
        (home / ".local/state/foxreach").mkdir(parents=True)
        (home / ".local/state/foxreach/profiles").write_text(f"/other/profile\t0.2.0\t/wrong\n{profile}\t0.2.0\t{shown}\n")
        mac_checks(home)
        mac_pids_checks(home)

        launch = lambda env: subprocess.run([str(ROOT / "scripts/foxreach-browser"), "https://example.com/"],  # noqa: E731
                                            env={"HOME": str(home), "PATH": f"{home}/bin:/usr/bin"} | env, capture_output=True, text=True)
        out = launch({})
        check("foxreach-browser starts the recorded browser on the dedicated profile",
              out.stdout.split("\n")[:4] == ["--no-remote", "--profile", str(profile), "https://example.com/"], out.stdout + out.stderr)
        out = launch({"FOXREACH_BROWSER": f"flatpak:{FLATPAK}"})
        check("foxreach-browser starts a Flatpak through flatpak run",
              f"run {FLATPAK} --no-remote --profile {profile} https://example.com/" in (home / "flatpak.log").read_text(), out.stderr)
        (home / ".local/state/foxreach/profiles").write_text(f"{profile}\t0.2.0\n")
        out = launch({})
        check("foxreach-browser says so when no browser is recorded", out.returncode == 1 and "No browser is recorded" in out.stderr, out.stderr)

    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
