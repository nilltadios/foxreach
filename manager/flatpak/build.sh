#!/usr/bin/env bash
# Builds the foxreach manager Flatpak: a local repository and a single-file bundle.
#
# Run:      manager/flatpak/build.sh                 (from anywhere)
#           flatpak install --user manager/flatpak/foxreach-manager-VERSION-ARCH.flatpak
# Needs:    flatpak-builder, org.freedesktop.Sdk//26.08 (and Platform), and dist/foxreach in the
#           repository, built for this machine's architecture (uv run --python 3.15
#           scripts/build_binary.py). The manifest downloads the official Rust release pinned in
#           rust-toolchain.toml, for x86_64 or aarch64.
# Output:   manager/flatpak/repo (an OSTree repository) and foxreach-manager-VERSION-ARCH.flatpak,
#           ARCH being the architecture flatpak builds for here (x86_64 or aarch64).
# Expected: "Bundle: <path>" on the last line, exit 0.
# Crates are vendored into flatpak/vendor first, pinned by Cargo.lock, because the build sandbox
# has no network. BUILD_JOBS (default 6) and BUILD_MEMORY (default 6G) cap the build, which runs in
# its own systemd scope so a runaway build is stopped instead of the desktop.
# For CI, where there is no cargo and no user systemd: VENDOR=0 uses flatpak/vendor and
# flatpak/cargo-config.toml as they are (vendored elsewhere), BUILD_SCOPE=0 runs without the scope.
set -euo pipefail

app_id="dev.nilltadios.FoxreachManager"
flatpak_dir="$(readlink -f "$(dirname "${BASH_SOURCE[0]}")")"
project_dir="$(dirname "$flatpak_dir")"
cd "$flatpak_dir"
[[ -x $project_dir/../dist/foxreach ]] || { echo "dist/foxreach is missing: uv run --python 3.15 scripts/build_binary.py" >&2; exit 1; }
version=$(awk -F'"' '/^\[/{p=($0=="[package]")} p && /^version *=/{print $2; exit}' "$project_dir/Cargo.toml")
[[ -n $version ]] || { echo "Cannot read the version from Cargo.toml" >&2; exit 1; }
arch=$(flatpak --default-arch)

if [[ ${VENDOR:-1} == 1 ]]; then
    cargo vendor --locked --manifest-path "$project_dir/Cargo.toml" "$flatpak_dir/vendor" \
        | sed "s|$flatpak_dir/vendor|flatpak/vendor|" >"$flatpak_dir/cargo-config.toml"
else
    [[ -d $flatpak_dir/vendor ]] || { echo "VENDOR=0 but flatpak/vendor is missing" >&2; exit 1; }
fi

if command -v flatpak-builder >/dev/null; then
    builder=(flatpak-builder)
else
    builder=(flatpak run org.flatpak.Builder)
fi
scope=()
if [[ ${BUILD_SCOPE:-1} == 1 ]]; then
    scope=(systemd-run --user --scope --quiet -p MemoryMax="${BUILD_MEMORY:-6G}" -p MemorySwapMax=2G nice -n 10)
fi
"${scope[@]}" "${builder[@]}" --jobs="${BUILD_JOBS:-6}" --force-clean --disable-rofiles-fuse \
    --state-dir="$flatpak_dir/.flatpak-builder" --repo="$flatpak_dir/repo" \
    "$flatpak_dir/build-dir" "$flatpak_dir/$app_id.yml"
# The packaged program must be the same file, still runnable (a strip pass once broke it), and of
# this app's version, which the app takes as the program's without running it (src/host.rs
# bundled_version).
packaged="$flatpak_dir/build-dir/files/share/foxreach-manager/foxreach"
cmp "$packaged" "$project_dir/../dist/foxreach" || { echo "The packaged foxreach differs from dist/foxreach" >&2; exit 1; }
packaged_version=$("$packaged" version) || { echo "The packaged foxreach does not run" >&2; exit 1; }
[[ $packaged_version == "$version" ]] \
    || { echo "The packaged foxreach is $packaged_version, Cargo.toml says $version: build dist/foxreach from this checkout" >&2; exit 1; }
bundle="$flatpak_dir/foxreach-manager-$version-$arch.flatpak"
# --runtime-repo: installing the bundle fetches org.freedesktop.Platform from Flathub if needed.
flatpak build-bundle --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
    "$flatpak_dir/repo" "$bundle" "$app_id"
(cd "$flatpak_dir" && sha256sum "$(basename "$bundle")" >"$(basename "$bundle").sha256")
echo "Bundle: $bundle"
