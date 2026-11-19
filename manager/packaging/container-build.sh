#!/usr/bin/env bash
# Build the release binary and the AppImage inside the Ubuntu 26.04 container (podman or docker).
#
# Run:      manager/packaging/container-build.sh            (from anywhere)
# Needs:    podman (or docker), and dist/foxreach in the repository: the foxreach program the
#           AppImage carries (uv run --python 3.15 scripts/build_binary.py builds it).
# Output:   manager/dist/foxreach-manager-VERSION-ARCH.AppImage and .sha256, for this machine's
#           architecture (x86_64 or aarch64); dist/foxreach must be built for the same one.
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
[[ -x ../dist/foxreach ]] || { echo "../dist/foxreach is missing: uv run --python 3.15 scripts/build_binary.py" >&2; exit 1; }
"$engine" run --rm --memory="${BUILD_MEMORY:-6G}" \
  -v "$PWD/..:/repo:Z" \
  -v "$PWD/packaging/cache/cargo/registry:/root/.cargo/registry:Z" \
  -v "$PWD/packaging/cache/cargo/git:/root/.cargo/git:Z" \
  -e CARGO_TARGET_DIR=/repo/manager/packaging/cache/target \
  -w /repo/manager \
  "$image" bash -c "set -e; cargo build --locked --release -j ${BUILD_JOBS:-6}; packaging/build-appimage.sh packaging/cache/target/release dist"
ls -l dist/
