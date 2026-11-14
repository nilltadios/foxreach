#!/usr/bin/env bash
# Fast UI loop: build the debug app, open it in demo mode (built-in sample data, nothing on the
# system is read or changed), and rebuild and reopen it every time a file in src/ is saved.
#
# Run:      manager/dev.sh [--real]     (--real: the live view of this computer instead of the demo)
# Needs:    inotifywait (inotify-tools). On KDE the window goes to the external monitor when one is
#           connected (MONITOR=DP-1 by default, via the kde-desktop-app-testing skill's on-screen.sh).
# Stop:     Ctrl+C (closes the window it opened).
# An incremental debug build after a UI edit takes a few seconds; the window reopens on the same
# tab only if you go back to it (the demo resets on every start).
set -uo pipefail
cd -- "$(dirname -- "$0")"
mode=--demo
[[ ${1:-} == --real ]] && mode=
monitor=${MONITOR:-DP-1}
placer=~/.claude/skills/kde-desktop-app-testing/scripts/on-screen.sh
pid=

build() {
  # Capped: this laptop has run out of memory on uncapped Rust builds.
  systemd-run --user --scope --quiet -p MemoryMax=5G -p MemorySwapMax=2G nice -n 10 cargo build -j 4 2>&1 \
    | grep -E '^(error|warning)|^\s+-->|Finished' || true
  [[ ${PIPESTATUS[0]} -eq 0 ]]
}

start() {
  if [[ -x $placer ]] && kscreen-doctor -o 2>/dev/null | grep -q "$monitor"; then
    "$placer" "$monitor" dev.nilltadios.FoxreachManager -- target/debug/foxreach-manager $mode &
  else
    target/debug/foxreach-manager $mode &
  fi
  pid=$!
}

stop() {
  [[ -n $pid ]] || return
  # The window is a child of $pid (directly, or through on-screen.sh): end that tree only.
  pkill -TERM -P "$pid" 2>/dev/null
  kill "$pid" 2>/dev/null
  wait "$pid" 2>/dev/null
  pid=
}

trap 'stop; exit 0' INT TERM
build && start
while inotifywait -qq -r -e close_write,moved_to,create src Cargo.toml; do
  sleep 0.3 # an editor's save is often several writes
  if build; then
    stop
    start
  else
    echo "build failed; the open window is the previous build"
  fi
done
