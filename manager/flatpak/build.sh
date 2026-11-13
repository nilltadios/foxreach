#!/usr/bin/env bash
# Builds the foxreach manager Flatpak: a local repository and a single-file bundle.
#
# Run:      manager/flatpak/build.sh                 (from anywhere)
#           flatpak install --user manager/flatpak/dev.nilltadios.FoxreachManager.flatpak
# Needs:    flatpak-builder, org.freedesktop.Sdk//26.08 (and Platform), and dist/foxreach in the
#           repository (uv run --python 3.15 scripts/build_binary.py). The manifest downloads the
#           official Rust release pinned in rust-toolchain.toml.
# Output:   manager/flatpak/repo (an OSTree repository) and dev.nilltadios.FoxreachManager.flatpak
# Expected: "Bundle: <path>" on the last line, exit 0.
# Crates are vendored into flatpak/vendor first, pinned by Cargo.lock, because the build sandbox
# has no network. BUILD_JOBS (default 6) and BUILD_MEMORY (default 6G) cap the build, which runs in
# its own systemd scope so a runaway build is stopped instead of the desktop.
set -euo pipefail

app_id="dev.nilltadios.FoxreachManager"
flatpak_dir="$(readlink -f "$(dirname "${BASH_SOURCE[0]}")")"
project_dir="$(dirname "$flatpak_dir")"
cd "$flatpak_dir"
[[ -x $project_dir/../dist/foxreach ]] || { echo "dist/foxreach is missing: uv run --python 3.15 scripts/build_binary.py" >&2; exit 1; }

cargo vendor --locked --manifest-path "$project_dir/Cargo.toml" "$flatpak_dir/vendor" \
    | sed "s|$flatpak_dir/vendor|flatpak/vendor|" >"$flatpak_dir/cargo-config.toml"

if command -v flatpak-builder >/dev/null; then
    builder=(flatpak-builder)
else
    builder=(flatpak run org.flatpak.Builder)
fi
systemd-run --user --scope --quiet -p MemoryMax="${BUILD_MEMORY:-6G}" -p MemorySwapMax=2G \
    nice -n 10 "${builder[@]}" --jobs="${BUILD_JOBS:-6}" --force-clean --disable-rofiles-fuse \
    --state-dir="$flatpak_dir/.flatpak-builder" --repo="$flatpak_dir/repo" \
    "$flatpak_dir/build-dir" "$flatpak_dir/$app_id.yml"
# The packaged program must be the same file, still runnable (a strip pass once broke it).
packaged="$flatpak_dir/build-dir/files/share/foxreach-manager/foxreach"
cmp "$packaged" "$project_dir/../dist/foxreach" || { echo "The packaged foxreach differs from dist/foxreach" >&2; exit 1; }
"$packaged" version >/dev/null || { echo "The packaged foxreach does not run" >&2; exit 1; }
flatpak build-bundle "$flatpak_dir/repo" "$flatpak_dir/$app_id.flatpak" "$app_id"
echo "Bundle: $flatpak_dir/$app_id.flatpak"
