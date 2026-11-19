#!/usr/bin/env bash
# Check a built Flatpak bundle without a display: install it for this user, run the app's
# --version and the bundled foxreach program inside the sandbox, and check that the installed
# program is the same file as the one it was built from.
#
# Run:      manager/flatpak/smoke.sh BUNDLE PROGRAM
#           BUNDLE: foxreach-manager-VERSION-ARCH.flatpak; PROGRAM: the dist/foxreach it was built with.
# Needs:    flatpak, and org.freedesktop.Platform//26.08 installed (user or system): the bundle is
#           installed with --no-deps, so nothing is fetched.
# Expected: "foxreach-manager VERSION", then the program's version; exit 0. The app stays
#           installed (remove it with: flatpak uninstall --user dev.nilltadios.FoxreachManager).
set -euo pipefail
app_id=dev.nilltadios.FoxreachManager
bundle=$(realpath -- "$1")
program=$(realpath -- "$2")

flatpak install --user -y --noninteractive --no-deps --no-related --reinstall --bundle "$bundle"
# flatpak run wants a session bus for the app's D-Bus permissions; give it one if there is none.
bus=()
if [[ -z ${DBUS_SESSION_BUS_ADDRESS:-} ]] && command -v dbus-run-session >/dev/null; then
    bus=(dbus-run-session --)
fi
out=$("${bus[@]}" flatpak run --user "$app_id" --version)
echo "$out"
[[ $out == "foxreach-manager "* ]] || { echo "foxreach-manager --version printed something else" >&2; exit 1; }

installed="$(flatpak info --user --show-location "$app_id")/files/share/foxreach-manager/foxreach"
cmp -- "$installed" "$program" || { echo "The installed foxreach differs from $program" >&2; exit 1; }
"${bus[@]}" flatpak run --user --command=/app/share/foxreach-manager/foxreach "$app_id" version \
    || { echo "The bundled foxreach does not run in the sandbox" >&2; exit 1; }
