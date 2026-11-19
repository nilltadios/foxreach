#!/usr/bin/env bash
# Check that a built AppImage can update itself: its embedded update information is the one
# packaging/update-info.sh gives for its architecture, and its .zsync describes exactly this file.
#
# Run:      manager/packaging/check-update-info.sh APPIMAGE [ZSYNC]
#           ZSYNC defaults to APPIMAGE.zsync.
# Checks:   - the .upd_info ELF section (read with readelf, so any architecture can be checked here)
#             holds update_info ARCH, ARCH being the runtime's ELF machine, which must also be the
#             one in the file name (foxreach-manager-VERSION-ARCH.AppImage);
#           - on a machine of that architecture, `APPIMAGE --appimage-updateinfo` prints the same;
#           - the .zsync's file name matches the pattern in the update information;
#           - its header's Filename and URL are the AppImage's file name (URL relative, so it
#             resolves next to the .zsync in the release), and its Length and SHA-1 are this file's.
# Needs:    readelf, dd, sha1sum, stat.
# Expected: "ok: <update information>" and "ok: <zsync> matches <AppImage> (N bytes, SHA-1 ...)",
#           exit 0; otherwise a line saying what differs, exit 1.
set -euo pipefail
here=$(dirname -- "$(realpath -- "$0")")
. "$here/update-info.sh"
(( $# == 1 || $# == 2 )) || { echo "usage: $0 APPIMAGE [ZSYNC]" >&2; exit 2; }
appimage=$1
zsync=${2:-$1.zsync}
fail() { echo "FAIL: $*" >&2; exit 1; }
for t in readelf dd sha1sum stat; do command -v "$t" >/dev/null || { echo "Missing tool: $t" >&2; exit 2; }; done
[[ -f $appimage ]] || fail "no AppImage at $appimage"

machine=$(readelf -h -- "$appimage" | awk -F: '/Machine:/ {gsub(/^ +/, "", $2); print $2}')
case $machine in
  *X86-64*)  arch=x86_64 ;;
  *AArch64*) arch=aarch64 ;;
  *) fail "$appimage: runtime is for '$machine', not x86_64 or aarch64" ;;
esac
name=$(basename -- "$appimage")
[[ $name == foxreach-manager-*-"$arch".AppImage ]] || fail "$name: the runtime is $arch, the name says otherwise"
want=$(update_info "$arch")

# .upd_info: offset and size from the section table; the content is NUL-padded.
read -r off size < <(readelf -SW -- "$appimage" | awk '{for (i = 1; i < NF; i++) if ($i == ".upd_info") {print $(i + 3), $(i + 4); exit}}') \
  || fail "$name has no .upd_info section"
got=$(dd if="$appimage" bs=1 skip="$((16#$off))" count="$((16#$size))" status=none | tr -d '\0')
[[ -n $got ]] || fail "$name: .upd_info is empty (no update information embedded)"
[[ $got == "$want" ]] || fail "$name: update information is '$got', expected '$want'"
if [[ $(uname -m) == "$arch" ]]; then
  [[ -x $appimage ]] || fail "$name is not executable"
  printed=$("$(realpath -- "$appimage")" --appimage-updateinfo)
  [[ $printed == "$want" ]] || fail "$name --appimage-updateinfo printed '$printed', expected '$want'"
fi
echo "ok: $got"

[[ -f $zsync ]] || fail "no .zsync at $zsync"
pattern=${want##*|}
# shellcheck disable=SC2053  # the pattern is a glob on purpose
[[ $(basename -- "$zsync") == $pattern ]] || fail "$(basename -- "$zsync") does not match '$pattern'"
header() { awk -v k="$1" 'NF == 0 {exit} index($0, k ": ") == 1 {print substr($0, length(k) + 3); exit}' "$zsync"; }
z_name=$(header Filename) z_url=$(header URL) z_len=$(header Length) z_sha=$(header SHA-1)
[[ $z_name == "$name" ]] || fail "$zsync: Filename is '$z_name', expected '$name'"
[[ $z_url == "$name" ]] || fail "$zsync: URL is '$z_url', expected the relative '$name'"
len=$(stat -c %s -- "$appimage")
[[ $z_len == "$len" ]] || fail "$zsync: Length is '$z_len', $name has $len bytes"
sha=$(sha1sum -- "$appimage" | awk '{print $1}')
[[ $z_sha == "$sha" ]] || fail "$zsync: SHA-1 is '$z_sha', $name has $sha"
echo "ok: $(basename -- "$zsync") matches $name ($len bytes, SHA-1 $sha)"
