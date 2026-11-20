#!/usr/bin/env bash
# Check a built Flatpak bundle without a display: install it for this user, run the app's
# --version inside the sandbox, and check that the installed app packs no foxreach program (the
# app downloads the release instead, src/host.rs install_download).
#
# Run:      manager/flatpak/smoke.sh BUNDLE
#           BUNDLE: foxreach-manager-VERSION-ARCH.flatpak.
# Needs:    flatpak, and org.freedesktop.Platform//26.08 installed (user or system): the bundle is
#           installed with --no-deps, so nothing is fetched.
# Expected: "foxreach-manager VERSION"; exit 0. The app stays
#           installed (remove it with: flatpak uninstall --user dev.nilltadios.FoxreachManager).
set -euo pipefail
app_id=dev.nilltadios.FoxreachManager
bundle=$(realpath -- "$1")

flatpak install --user -y --noninteractive --no-deps --no-related --reinstall --bundle "$bundle"
# flatpak run wants a session bus for the app's D-Bus permissions; give it one if there is none.
bus=()
if [[ -z ${DBUS_SESSION_BUS_ADDRESS:-} ]] && command -v dbus-run-session >/dev/null; then
    bus=(dbus-run-session --)
fi
out=$("${bus[@]}" flatpak run --user "$app_id" --version)
echo "$out"
[[ $out == "foxreach-manager "* ]] || { echo "foxreach-manager --version printed something else" >&2; exit 1; }

files="$(flatpak info --user --show-location "$app_id")/files"
[[ -d $files/bin ]] || { echo "Cannot find the installed app's files at $files" >&2; exit 1; }
[[ ! -e $files/share/foxreach-manager/foxreach ]] \
    || { echo "The Flatpak packs share/foxreach-manager/foxreach: the app downloads the program instead" >&2; exit 1; }
