#!/usr/bin/env bash
# Check a built AppImage without a display and without FUSE: extract it, run the app's --version
# through AppRun (the path a launch takes: sharun and the bundled libraries), and check that the
# foxreach program inside is the same file as the one it was built from and still runs (a strip pass
# once broke a packaged copy).
#
# Run:      manager/packaging/smoke-appimage.sh APPIMAGE PROGRAM [WORK_DIR]
#           PROGRAM: the foxreach program the AppImage was built with (dist/foxreach).
#           WORK_DIR: where to extract (default: a new temporary folder); it is left in place, so
#           its AppDir can be checked further (packaging/check-elf-alignment.sh).
# Expected: "foxreach-manager VERSION", the program's version, then "AppDir: <path>"; exit 0.
set -euo pipefail
appimage=$(realpath -- "$1")
program=$(realpath -- "$2")
work=$(realpath -m -- "${3:-$(mktemp -d)}")
mkdir -p "$work"
cd "$work"
"$appimage" --appimage-extract >/dev/null
appdir=$work/AppDir
[[ -x $appdir/AppRun ]] || { echo "No AppDir/AppRun after extracting $appimage" >&2; exit 1; }

home=$work/home
mkdir -p "$home"
# A clean environment, as a desktop launcher would give, with a throwaway home.
run() { env -i PATH=/usr/bin:/bin HOME="$home" APPDIR="$appdir" "$@"; }
out=$(run "$appdir/AppRun" --version)
echo "$out"
[[ $out == "foxreach-manager "* ]] || { echo "AppRun --version printed something else" >&2; exit 1; }

packaged=$appdir/share/foxreach-manager/foxreach
cmp -- "$packaged" "$program" || { echo "The packaged foxreach differs from $program" >&2; exit 1; }
run "$packaged" version || { echo "The packaged foxreach does not run" >&2; exit 1; }
echo "AppDir: $appdir"
