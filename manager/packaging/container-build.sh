#!/usr/bin/env bash
# Build the release binary and the AppImage inside the Ubuntu 26.04 container (podman or docker).
#
# Run:      manager/packaging/container-build.sh            (from anywhere)
# Needs:    podman (or docker). The AppImage packs no foxreach program (the app downloads the
#           release), so nothing outside manager/ is built first.
# Output:   manager/dist/foxreach-manager-VERSION-ARCH.AppImage and .sha256, for this machine's
#           architecture (x86_64 or aarch64).
# Cargo downloads and build output are cached in manager/packaging/cache; nothing is installed on
# the host. BUILD_MEMORY (default 6G) caps the container's memory, BUILD_JOBS (default 6) the
# parallel rustc jobs: uncapped builds have run this laptop out of memory. ENGINE picks podman or
# docker (default: podman if installed).
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
engine=${ENGINE:-$(command -v podman || command -v docker)}
image=localhost/foxreach-manager-build:26.04-rust1.100.0
"$engine" build -q -t "$image" -f packaging/Containerfile packaging >/dev/null
mkdir -p packaging/cache/cargo/registry packaging/cache/cargo/git packaging/cache/target dist
"$engine" run --rm --memory="${BUILD_MEMORY:-6G}" \
  -v "$PWD/..:/repo:Z" \
  -v "$PWD/packaging/cache/cargo/registry:/root/.cargo/registry:Z" \
  -v "$PWD/packaging/cache/cargo/git:/root/.cargo/git:Z" \
  -e CARGO_TARGET_DIR=/repo/manager/packaging/cache/target \
  -w /repo/manager \
  "$image" bash -c "set -e; cargo build --locked --release -j ${BUILD_JOBS:-6}; packaging/build-appimage.sh packaging/cache/target/release dist"
ls -l dist/
