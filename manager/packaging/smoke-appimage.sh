#!/usr/bin/env bash
# Check a built AppImage without a display and without FUSE: extract it, run the app's --version
# through AppRun (the path a launch takes: sharun and the bundled libraries), and check that no
# foxreach program is packed (the app downloads the release instead, src/host.rs install_download).
#
# Run:      manager/packaging/smoke-appimage.sh APPIMAGE [WORK_DIR]
#           WORK_DIR: where to extract (default: a new temporary folder); it is left in place, so
#           its AppDir can be checked further (packaging/check-elf-alignment.sh).
# Expected: "foxreach-manager VERSION", then "AppDir: <path>"; exit 0.
set -euo pipefail
appimage=$(realpath -- "$1")
work=$(realpath -m -- "${2:-$(mktemp -d)}")
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

[[ ! -e $appdir/share/foxreach-manager/foxreach ]] \
    || { echo "The AppImage packs share/foxreach-manager/foxreach: the app downloads the program instead" >&2; exit 1; }
# libxkbcommon.so.0 is bundled (DT_NEEDED); winit dlopens libxkbcommon-x11.so.0 on X11. Both share
# private xkb_keymap struct layouts, so shipping libxkbcommon without a matching libxkbcommon-x11
# crashes on X11 hosts with a different libxkbcommon version.
if [[ -e $appdir/lib/libxkbcommon.so.0 ]]; then
  [[ -e $appdir/lib/libxkbcommon-x11.so.0 ]] \
      || { echo "The AppImage bundles lib/libxkbcommon.so.0 without lib/libxkbcommon-x11.so.0 (crashes on X11)" >&2; exit 1; }
  xkb_ver=$(basename -- "$(realpath -- "$appdir/lib/libxkbcommon.so.0")")
  xkb_x11_ver=$(basename -- "$(realpath -- "$appdir/lib/libxkbcommon-x11.so.0")")
  [[ ${xkb_ver#libxkbcommon.so.} == "${xkb_x11_ver#libxkbcommon-x11.so.}" ]] \
      || { echo "Mismatched libxkbcommon ($xkb_ver) and libxkbcommon-x11 ($xkb_x11_ver) in AppDir/lib" >&2; exit 1; }
fi
echo "AppDir: $appdir"
