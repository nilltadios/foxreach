#!/usr/bin/env python3
"""Remove foxreach from a browser profile and restore the stricter add-on settings.

Run:   uv run --python 3.15 scripts/remove_from_profile.py --profile DIR [--apply]
Without --apply it only prints what it would change. Refuses a profile that is in use.
Removes: the add-on file, the two prefs that scripts/setup_profile.py set, and the profile from
         the list scripts/update.py updates.
Expected after --apply: exit 0, and on the next start foxreach is gone from about:addons.
Warning: any other unsigned add-on in the profile stops loading once signatures are required again.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from setup_profile import ADDON_ID, PREFS, in_use, record_profile  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", type=pathlib.Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    profile = args.profile.expanduser().resolve()
    if in_use(profile):
        raise SystemExit(f"{profile} is in use. Close that browser first.")
    xpi = profile / "extensions" / f"{ADDON_ID}.xpi"
    if xpi.exists():
        print(("removing" if args.apply else "would remove"), xpi)
        if args.apply:
            xpi.unlink()
    for name in ("user.js", "prefs.js"):
        path = profile / name
        if not path.exists():
            continue
        lines = path.read_text().splitlines(keepends=True)
        kept = [l for l in lines if not any(f'"{pref}"' in l for pref in PREFS)]
        print(("removing" if args.apply else "would remove"), len(lines) - len(kept), "lines from", path)
        if args.apply:
            path.write_text("".join(kept))
    if args.apply:
        record_profile(profile, keep=False)  # scripts/update.py no longer updates it
    if not args.apply:
        print("Nothing changed. Add --apply to do this.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
