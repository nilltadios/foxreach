#!/usr/bin/env bash
# Build the AppImage the Anylinux way (pkgforge-dev/Anylinux-AppImages): quick-sharun bundles the
# app with every library it needs, glibc included, and packs it with uruntime (DwarFS, no FUSE 2),
# so it runs on any Linux distribution. Same tooling and pins as Gear Lever COSMIC.
#
# Run:      manager/packaging/build-appimage.sh [BIN_DIR] [OUT_DIR]
#           BIN_DIR: folder with the release foxreach-manager (default target/release)
#           OUT_DIR: output folder (default dist)
#           Normally run by packaging/container-build.sh inside the build container.
# Output:   OUT_DIR/foxreach-manager-VERSION-ARCH.AppImage, its .sha256 and .zsync, ARCH from
#           uname -m (x86_64 or aarch64: build on the architecture the AppImage is for, no cross builds)
# Updates:  the AppImage carries update information (packaging/update-info.sh) in the runtime's
#           .upd_info section, and the .zsync made from its final bytes is what updaters fetch from
#           the release. quick-sharun's appimagetool (pinned there by SHA-256) writes both: UPINFO
#           goes into the runtime before packing, the .zsync is made from the finished file. Nothing
#           may change the AppImage afterwards. Check: packaging/check-update-info.sh OUT_DIR/*.AppImage
# Carries:  the foxreach program from ../dist/foxreach (FOXREACH_PROGRAM overrides) in
#           share/foxreach-manager/, which the app installs to ~/.local/bin/foxreach on request.
# Expected: the AppImage path on the last line, exit 0.
#
# quick-sharun is pinned to a commit and checked by SHA-256; it pins and checks the sharun and
# appimagetool it downloads, for each architecture. The uruntime is pinned the same way, one file per
# architecture; the SHA-256s are the ones GitHub lists for the v0.8.1 release assets.
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
. packaging/update-info.sh
QS_COMMIT=1a5594c7f47ec5cb46ce70f6e11b5c5781f17f5f
QS_SHA=35ba7b95f91f4665707c4cab5ed99407ceea636c15675990bb42816d6d84cc33
RT_VERSION=v0.8.1
arch=$(uname -m)
case $arch in
  x86_64)  RT_SHA=b3c2916153e089d703cee5a7ebc540941d2f1d71baa90eb3092b15eef48345a8 ;;
  aarch64) RT_SHA=c1641dfe465f4cb70ae545fbfb9de7a221aa6e9a0b1d7ea600b213a1f10738a0 ;;
  *) echo "No pinned uruntime for $arch (x86_64 and aarch64 are)" >&2; exit 1 ;;
esac
RT_URL=https://github.com/VHSgunzo/uruntime/releases/download/$RT_VERSION/uruntime-appimage-dwarfs-lite-$arch
app=dev.nilltadios.FoxreachManager
bin="$(realpath "${1:-target/release}")"
out="$(realpath -m "${2:-dist}")"
program="$(realpath "${FOXREACH_PROGRAM:-../dist/foxreach}")"

for t in awk cp curl find grep install ldd mktemp patchelf readelf sha256sum strings tar; do
  command -v "$t" >/dev/null || { echo "Missing tool: $t (or build in the container: packaging/container-build.sh)" >&2; exit 1; }
done
[[ -x $bin/foxreach-manager ]] || { echo "$bin/foxreach-manager is missing: cargo build --locked --release" >&2; exit 1; }
[[ -x $program ]] || { echo "$program is missing: uv run --python 3.15 scripts/build_binary.py" >&2; exit 1; }
# The program must be built for the AppImage's architecture (a release build is foxreach-linux-x64 or
# foxreach-linux-arm64), and so must the app.
want=$(case $arch in x86_64) echo X86-64 ;; aarch64) echo AArch64 ;; esac)
for f in "$bin/foxreach-manager" "$program"; do
  [[ $(readelf -h "$f") == *Machine:*"$want"* ]] || { echo "$f is not built for $arch" >&2; exit 1; }
done

# quick-sharun looks for libc in Debian and Arch layouts; elsewhere use the folder the binary loads it from.
if [[ -z ${LIB_DIR:-} ]]; then
  libc=$(ldd "$bin/foxreach-manager" | awk '$1 ~ /^libc\.so/ {print $3; exit}')
  [[ -n $libc && -e $libc ]] || { echo "Cannot find the libc foxreach-manager uses; set LIB_DIR" >&2; exit 1; }
  LIB_DIR=$(dirname -- "$(realpath -- "$libc")")
  [[ -d /usr$LIB_DIR && $LIB_DIR != /usr/* ]] && LIB_DIR=/usr$LIB_DIR
fi
export LIB_DIR

version=$(awk -F'"' '/^\[/{p=($0=="[package]")} p && /^version *=/{print $2; exit}' Cargo.toml)
[[ -n $version ]] || { echo "Cannot read the version from Cargo.toml" >&2; exit 1; }
cache="$PWD/packaging/cache"
mkdir -p "$cache" "$out"
qs="$cache/quick-sharun-$QS_COMMIT.sh"
if [[ ! -f $qs ]] || ! echo "$QS_SHA  $qs" | sha256sum -c --status; then
  curl -fsSL -o "$qs.part" "https://raw.githubusercontent.com/pkgforge-dev/Anylinux-AppImages/$QS_COMMIT/useful-tools/quick-sharun.sh"
  echo "$QS_SHA  $qs.part" | sha256sum -c --status || { echo "quick-sharun checksum mismatch" >&2; rm -f "$qs.part"; exit 1; }
  mv "$qs.part" "$qs"
fi
chmod +x "$qs"
rt_cache="$cache/uruntime-appimage-dwarfs-lite-$RT_VERSION-$arch"
if [[ ! -f $rt_cache ]] || ! echo "$RT_SHA  $rt_cache" | sha256sum -c --status; then
  curl -fsSL -o "$rt_cache.part" "$RT_URL"
  echo "$RT_SHA  $rt_cache.part" | sha256sum -c --status || { echo "uruntime checksum mismatch" >&2; rm -f "$rt_cache.part"; exit 1; }
  mv "$rt_cache.part" "$rt_cache"
fi

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
sed "s/^X-AppImage-Version=.*/X-AppImage-Version=$version/" "resources/$app.desktop" > "$work/$app.desktop"
export APPDIR="$work/AppDir"
export DESKTOP="$work/$app.desktop"
export ICON="$PWD/resources/$app.svg"
export OUTPATH="$out"
export OUTNAME="foxreach-manager-$version-$arch.AppImage"
export MAIN_BIN=foxreach-manager
export VERSION="$version"
UPINFO=$(update_info "$arch")
export UPINFO
# Rendering is wgpu with a software fallback; the host's GPU drivers are used, not bundled ones.
export DEPLOY_VULKAN=0 DEPLOY_OPENGL=0
# No test launch (it would also pick up the GPU drivers it happened to load), and no guessing of
# /usr/share folders from words in the binary (it copied whatever the build machine had).
export STRACE_MODE=0 DEPLOY_DATADIR=0
qs_home="$work/home"; mkdir -p "$qs_home"
export TMPDIR="$work"
cp "$rt_cache" "$work/uruntime" && chmod +x "$work/uruntime"
# REUSE_CHECK_DELAY=0: the mount helper exits with the app instead of lingering on stdout.
"$work/uruntime" --appimage-addenvs $'REUSE_CHECK_DELAY=0\nDWARFS_CACHESIZE=64M' >/dev/null
export RUNTIME="$work/uruntime"
# The font library mentions /usr/share/fonts, which makes quick-sharun copy every system font; the
# app reads the host's fonts, so an empty folder stops the copy.
mkdir -p "$APPDIR/share/fonts"
( cd "$work" && HOME="$qs_home" XDG_CONFIG_HOME="$qs_home/.config" XDG_DATA_HOME="$qs_home/.local/share" "$qs" "$bin/foxreach-manager" )
# quick-sharun rewrites /usr/share strings in the binary to point into the AppImage. The app reads
# the host's icon themes and config, so the unmodified binary goes back; bundled libraries are
# still found through sharun.
install -m755 "$bin/foxreach-manager" "$APPDIR/shared/bin/foxreach-manager"
rm -f "$APPDIR/bin/01-path-mapping-hardcoded.hook"
rmdir "$APPDIR/share/fonts" 2>/dev/null || true
install -Dm755 "$program" "$APPDIR/share/foxreach-manager/foxreach"
install -Dm644 "resources/$app.metainfo.xml" "$APPDIR/share/metainfo/$app.appdata.xml"
install -Dm644 "resources/$app.svg" "$APPDIR/share/icons/hicolor/scalable/apps/$app.svg"
rm -f "$out/$OUTNAME" "$out/$OUTNAME.zsync"
( cd "$work" && HOME="$qs_home" XDG_CONFIG_HOME="$qs_home/.config" XDG_DATA_HOME="$qs_home/.local/share" "$qs" --make-appimage )
[[ -s $out/$OUTNAME.zsync ]] || { echo "appimagetool wrote no $OUTNAME.zsync" >&2; exit 1; }
( cd "$out" && sha256sum "$OUTNAME" > "$OUTNAME.sha256" )
echo "$out/$OUTNAME"
