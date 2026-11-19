# shellcheck shell=bash
# The AppImage's update information: where AppImage updaters (Gear Lever, appimageupdatetool,
# AppImageUpdate) look for a newer version. Sourced by build-appimage.sh, which embeds it, and by
# check-update-info.sh, which checks it; the repository is named here only.
#
# Form: gh-releases-zsync|OWNER|REPO|latest|foxreach-manager-*-ARCH.AppImage.zsync
#   "latest" is the newest release that is not a prerelease. That release also carries the add-on
#   and the foxreach program; the pattern matches exactly one of its files per architecture.
UPDATE_REPO=nilltadios/foxreach

# update_info ARCH  (x86_64 or aarch64)
update_info() {
  printf 'gh-releases-zsync|%s|%s|latest|foxreach-manager-*-%s.AppImage.zsync' \
    "${UPDATE_REPO%%/*}" "${UPDATE_REPO#*/}" "$1"
}
