#!/usr/bin/env python3
"""Update foxreach: the bridge (this folder) and the add-on in each profile it was installed into.

Run:      uv run --python 3.15 scripts/update.py [--profile DIR ...] [--dry-run] [--no-pull]
Profiles: the ones scripts/setup_profile.py recorded in ~/.local/state/foxreach/profiles, unless
          --profile is given.
Steps:    refuse while one of those profiles is open; `git pull --ff-only`; rebuild and re-register
          the native host; reinstall the add-on (scripts/setup_profile.py) in each profile whose
          copy is older than this folder. A profile whose add-on already updated itself to a newer
          version is left alone (that would be a downgrade); the pull is what brings the bridge level.
          Agents pick up the new bridge in their next session.
Expected: a before/after table, "up to date" and exit 0. Exit 1 if a version still differs.
Undo:     `git checkout <old commit>` (printed below) and run this again with --no-pull.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_install import version  # noqa: E402
from setup_profile import ADDON_ID, in_use, recorded_profiles  # noqa: E402

UV = ["uv", "run", "--python", "3.15"]


def installed_version(profile):
    xpi = profile / "extensions" / f"{ADDON_ID}.xpi"
    if not xpi.exists():
        return None
    with zipfile.ZipFile(xpi) as z:
        return json.loads(z.read("manifest.json"))["version"]


def key(v):
    return tuple(int(p) if p.isdigit() else 0 for p in v.split("."))


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def table(title, profiles):
    print(f"{title}: bridge {version()} ({git('rev-parse', '--short', 'HEAD')})")
    for p in profiles:
        print(f"  add-on {installed_version(p) or 'not installed'}  {p}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", type=pathlib.Path, action="append")
    parser.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    parser.add_argument("--no-pull", action="store_true", help="use this folder as it is")
    args = parser.parse_args()
    profiles = [p.expanduser().resolve() for p in args.profile] if args.profile else recorded_profiles()
    if not profiles:
        raise SystemExit("No profiles recorded. Pass --profile DIR, the profile scripts/setup_profile.py installed into.")
    table("before", profiles)
    busy = [p for p in profiles if in_use(p)]
    if busy and args.dry_run:
        print("would refuse: close LibreWolf first; in use:", ", ".join(map(str, busy)))
    elif busy:
        raise SystemExit("Close LibreWolf first; in use: " + ", ".join(map(str, busy)) + "\nNothing was changed.")

    old = git("rev-parse", "--short", "HEAD")
    if args.no_pull:
        print("not pulling")
    elif args.dry_run:
        git("fetch", "--quiet")
        print("would pull:", git("log", "--oneline", "HEAD..@{upstream}") or "nothing new")
    else:
        print(git("pull", "--ff-only") or "pulled")
        print(f"was {old}; to go back: git checkout {old}")

    current = version()
    if args.dry_run:
        print(f"would rebuild the add-on and the native host ({current} in this folder now)")
    else:
        subprocess.run([*UV, str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    for p in profiles:
        have = installed_version(p)
        if have and key(have) > key(current):
            print(f"{p}: add-on {have} is newer than this folder ({current}); left alone")
        elif have == current:
            print(f"{p}: add-on already {current}")
        elif args.dry_run:
            print(f"{p}: would install {current} (now {have or 'none'})")
        else:
            print(f"{p}: installing {current} (was {have or 'none'})")
            subprocess.run([*UV, str(ROOT / "scripts/setup_profile.py"), "--profile", str(p)], check=True)

    if args.dry_run:
        print("Dry run: nothing changed.")
        return 0
    table("after", profiles)
    behind = [p for p in profiles if installed_version(p) != version()]
    if behind:
        print("versions still differ for:", ", ".join(map(str, behind)))
        return 1
    print("up to date. Start a new agent session to use the new bridge.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
