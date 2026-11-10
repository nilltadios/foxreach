#!/usr/bin/env python3
"""Install foxreach into a LibreWolf profile, and prove it works after a restart.

Run:   uv run --python 3.15 scripts/setup_profile.py [--profile DIR] [--browser PATH]
Default profile: ~/.local/share/foxreach/profile, a dedicated one with no logins or history.
Refuses a profile that is in use. An existing user.js and prefs.js are backed up beside themselves
as <name>.bak-<date>.
It also puts back the profile's "restore previous session" data and startup-crash counters, which its
own headless starts would otherwise disturb.
Undo:  delete the dedicated profile. For another profile, close the browser, remove the add-on in
       about:addons, and remove the two prefs below from user.js and prefs.js.
Records the profile in ~/.local/state/foxreach/profiles, the list scripts/update.py updates.
Expected: "verified" and exit 0.

No automation is used on the profile. Marionette must not run on a profile someone uses: it applies
"recommended" prefs and clears them on exit, which deletes the user's own values for those prefs.

The two prefs set here make the profile accept unsigned add-ons and let this add-on run privileged
code. See "Security" in README.md before using a profile you browse with.
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test"))
from harness import APPIMAGE, McpClient  # noqa: E402

PREFS = {
    "extensions.experiments.enabled": "true",
    "xpinstall.signatures.required": "false",
}
SETTLE_S = 3
ADDON_ID = "foxreach@foxreach.local"
SESSION_FILES = ("sessionstore.jsonlz4", "sessionstore-backups")
LAST_SUCCESS = "toolkit.startup.last_success"
RECENT_CRASHES = "toolkit.startup.recent_crashes"
DETECT_TIMEOUT_S = 25
START_TIMEOUT_S = 60
START_ATTEMPTS = 3
# Only for the one start that detects the add-on, so a folder drop elsewhere never self-enables.
ALLOW_ONCE = "extensions.autoDisableScopes"


# Profiles foxreach was installed into, one path per line, for scripts/update.py.
RECORD = pathlib.Path.home() / ".local/state/foxreach/profiles"


def recorded_profiles():
    if not RECORD.exists():
        return []
    return [pathlib.Path(l) for l in dict.fromkeys(RECORD.read_text().splitlines()) if l.strip()]


def record_profile(profile, keep=True):
    others = [p for p in recorded_profiles() if p != profile]
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text("".join(f"{p}\n" for p in others + ([profile] if keep else [])))


def in_use(profile):
    lock = profile / "lock"
    if not lock.is_symlink():
        return False
    pid = os.readlink(lock).rpartition("+")[2]
    return pid.isdigit() and pathlib.Path(f"/proc/{pid}").exists()


def refuse_copy(profile):
    """A copy of a profile keeps its profile-group ID. Starting one makes the browser list the copy as
    the user's default in the real profiles.ini; delete the copy and the browser won't open. Refuse a
    profile that shares a group ID with a registered profile but is not itself registered."""
    prefs = profile / "prefs.js"
    if not prefs.exists():
        return
    found = re.search(r'"toolkit\.profiles\.storeID",\s*"([^"]+)"', prefs.read_text())
    ini = pathlib.Path.home() / ".config/librewolf/librewolf/profiles.ini"
    if not found or not ini.exists():
        return
    lines = ini.read_text().splitlines()
    if f"StoreID={found.group(1)}" not in lines:
        return
    if not any(line in (f"Path={profile.name}", f"Path={profile}") for line in lines) or profile.parent != ini.parent:
        raise SystemExit(
            f"{profile} is a copy of a profile in your profile group. Starting it would change your real "
            "profiles.ini. Remove toolkit.profiles.storeID from its prefs.js first."
        )


def write_prefs(profile, extra=()):
    """Set our prefs in user.js, keeping everything else in it."""
    path = profile / "user.js"
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [l for l in lines if not any(f'"{n}"' in l for n in [*PREFS, ALLOW_ONCE])]
    ours = [f'user_pref("{k}", {v});' for k, v in PREFS.items()] + list(extra)
    path.write_text("\n".join(kept + ours) + "\n")


def forget_pref(profile, name):
    """user.js only adds prefs; the browser also saved this one to prefs.js, which outlives it."""
    path = profile / "prefs.js"
    if path.exists():
        lines = path.read_text().splitlines(keepends=True)
        path.write_text("".join(l for l in lines if f'"{name}"' not in l))


def save_session(profile, folder):
    """Headless starts would replace what "restore previous session" brings back."""
    for name in SESSION_FILES:
        source = profile / name
        if source.is_dir():
            shutil.copytree(source, folder / name, symlinks=True)
        elif source.exists():
            shutil.copy2(source, folder / name)


def restore_session(profile, folder):
    for name in SESSION_FILES:
        target = profile / name
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink(missing_ok=True)
        saved = folder / name
        if saved.is_dir():
            shutil.copytree(saved, target, symlinks=True)
        elif saved.exists():
            shutil.copy2(saved, target)


def clear_startup_tracking(profile):
    """Headless starts never record a finished startup, so each looks like a failed one. Enough of
    those make the next normal start offer Troubleshoot Mode. The start was fine: say so."""
    path = profile / "prefs.js"
    if not path.exists():
        return
    lines = path.read_text().splitlines(keepends=True)
    tracked = any(f'"{LAST_SUCCESS}"' in l for l in lines)
    kept = [l for l in lines if f'"{LAST_SUCCESS}"' not in l and f'"{RECENT_CRASHES}"' not in l]
    if tracked:
        kept.append(f'user_pref("{LAST_SUCCESS}", {int(time.time()) + 1});\n')
    path.write_text("".join(kept))


def stop(profile, proc):
    for d in pathlib.Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                # The executable matters too: this script's own command line can name the profile.
                if os.path.basename(os.readlink(d / "exe")).startswith("librewolf") and not os.readlink(d / "exe").endswith(".appimage") \
                        and str(profile).encode() in (d / "cmdline").read_bytes():
                    os.kill(int(d.name), signal.SIGTERM)
            except OSError:
                pass
    proc.wait(timeout=30)


def start(browser, profile, socket_path):
    env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"} | {"FOXREACH_SOCKET": socket_path}
    args = [browser, "--headless", "--no-remote", "--new-instance", "--profile", profile]
    return subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def answer_status(socket_path, timeout):
    """Ready means a real tool call was answered; the socket existing is not enough."""
    def too_long(*_):
        raise TimeoutError(f"The add-on did not answer within {timeout} s.")

    signal.signal(signal.SIGALRM, too_long)
    signal.alarm(timeout)
    try:
        while not os.path.exists(socket_path):
            time.sleep(0.2)
        client = McpClient(socket_path)
        status = json.loads(client.call("status"))
        client.close()
        return status
    finally:
        signal.alarm(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", type=pathlib.Path, default=pathlib.Path.home() / ".local/share/foxreach/profile")
    parser.add_argument("--browser", type=pathlib.Path, default=pathlib.Path(os.environ.get("FOXREACH_BROWSER") or APPIMAGE))
    args = parser.parse_args()
    profile = args.profile.expanduser().resolve()
    if in_use(profile):
        raise SystemExit(f"{profile} is in use. Close that browser first.")
    refuse_copy(profile)

    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    profile.mkdir(parents=True, exist_ok=True)
    stamp = datetime.date.today().isoformat()
    for name in ("user.js", "prefs.js"):
        backup = profile / f"{name}.bak-{stamp}"
        if (profile / name).exists() and not backup.exists():
            shutil.copy2(profile / name, backup)
            print("backed up", backup)

    # Setup uses its own socket, so it cannot disturb a foxreach browser that is already open.
    with tempfile.TemporaryDirectory(prefix="foxreach-setup-") as d:
        socket_path = f"{d}/s.sock"
        session = pathlib.Path(d, "session")
        session.mkdir()
        save_session(profile, session)
        (profile / "extensions").mkdir(exist_ok=True)
        write_prefs(profile, [f'user_pref("{ALLOW_ONCE}", 0);'])
        shutil.copyfile(ROOT / "dist/foxreach.xpi", profile / "extensions" / f"{ADDON_ID}.xpi")
        try:
            # First start: the browser detects the add-on. It may record it as active without running it
            # until the next start, so no answer here is not a failure; the starts below decide.
            proc = start(args.browser, profile, socket_path)
            try:
                answer_status(socket_path, DETECT_TIMEOUT_S)
                print("detected and running on the first start")
            except TimeoutError:
                print("detected; it runs from the next start")
            finally:
                time.sleep(SETTLE_S)
                stop(profile, proc)
                write_prefs(profile)
                forget_pref(profile, ALLOW_ONCE)

            # Normal settings from here. It must answer, then answer again after one more restart.
            status = None
            answered = 0
            for attempt in range(1, START_ATTEMPTS + 2):
                time.sleep(1)
                proc = start(args.browser, profile, socket_path)
                try:
                    status = answer_status(socket_path, START_TIMEOUT_S)
                    answered += 1
                    print(f"start {attempt}: answered")
                    time.sleep(SETTLE_S)
                except TimeoutError as e:
                    print(f"start {attempt}: {e}")
                    answered = 0
                finally:
                    stop(profile, proc)
                if answered == 2:
                    break
            if answered < 2:
                raise SystemExit("Installed, but it did not answer on two starts in a row. Nothing further was changed.")
        finally:
            # Runs even if a start failed, so a failed setup cannot cause the prompt either.
            restore_session(profile, session)
            clear_startup_tracking(profile)
    if not status["trustedInput"]:
        raise SystemExit(f"Installed, but trusted input is off: {status}")
    record_profile(profile)
    print(f"verified: {profile} v{status['version']} trusted input on, after a restart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
