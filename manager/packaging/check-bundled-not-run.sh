#!/usr/bin/env bash
# Issue #19: does the manager run the foxreach program bundled in its AppImage when it starts?
#
# Builds a fake AppDir around a built foxreach-manager (host.rs treats the process as an AppImage
# when APPIMAGE and APPDIR are set and the executable lies inside APPDIR). Its bundled program,
# share/foxreach-manager/foxreach, is a script that logs its arguments, sleeps 3 s, then runs the
# real foxreach program. The manager runs with a throwaway HOME whose ~/.local/bin/foxreach is a
# copy of the real program, and only with --check (reads `foxreach status --json`, changes nothing,
# opens no window).
#
# Cases:
#   P  Plain build, no bundled copy: the installed program alone, the time to compare A with.
#   A  AppImage, installed program current: the bundled copy must not run.
#   B  AppImage, FOXREACH_MANAGER_PROGRAM names a missing program: the bundled copy reads the
#      status (it runs `status --json`), and --check prints the status (exit 0).
#   C  Plain build, FOXREACH_MANAGER_BUNDLED points at the logging script: that copy's version is
#      not the manager's, so it is still asked (`version` in the log).
#
# Run:      check-bundled-not-run.sh [FOXREACH_MANAGER] [FOXREACH_PROGRAM]
#           FOXREACH_MANAGER: default manager/target/debug/foxreach-manager (cargo build --locked).
#           FOXREACH_PROGRAM: the real foxreach program, default ~/.local/bin/foxreach.
# Expected (after the fix):
#   P: "bundled: none", "bundled copy runs: 0", PASS
#   A: "bundled copy runs: 0", time well under 3000 ms, "bundled: foxreach <manager version>", PASS
#   B: "bundled copy runs: 1" (status --json), "read with: <AppDir>/share/foxreach-manager/foxreach",
#      exit 0, PASS
#   C: a "version" line in the log, "bundled: foxreach <program version>", PASS
#   last line "all passed", exit 0. Before the fix A logs `version` and takes over 3000 ms, and B
#   logs `version` before `status --json`: both FAIL, "some failed", exit 1.
# The temporary folder (made next to FOXREACH_MANAGER) is removed on exit.
set -uo pipefail
default=$(dirname -- "$(realpath -- "$0")")/../target/debug/foxreach-manager
manager=$(realpath -- "${1:-$default}")
program=$(realpath -- "${2:-$HOME/.local/bin/foxreach}")
[[ -x $manager && -x $program ]] || { echo "Missing $manager or $program" >&2; exit 2; }

# Next to the build, so the copies below are reflinks on btrfs instead of 700 MB in /tmp.
work=$(mktemp -d -p "$(dirname -- "$manager")" fxm-issue19.XXXXXX)
trap 'rm -rf -- "$work"' EXIT
appdir=$work/AppDir
home=$work/home
log=$work/bundled.log
mkdir -p "$appdir/bin" "$appdir/share/foxreach-manager" "$home/.local/bin"
cp --reflink=auto -- "$manager" "$appdir/bin/foxreach-manager"
cp --reflink=auto -- "$program" "$home/.local/bin/foxreach"
cat > "$appdir/share/foxreach-manager/foxreach" <<EOF
#!/usr/bin/env bash
echo "\$*" >> "$log"
sleep 3
exec "$program" "\$@"
EOF
chmod 755 "$appdir/share/foxreach-manager/foxreach"

echo "manager: $manager ($("$manager" --version))"
echo "program: $program ($("$program" version))"
failed=0

# run NAME ENV... : runs --check with a clean environment, prints time, output and log.
run() {
    local name=$1; shift
    : > "$log"
    local start end out code
    start=$(date +%s%N)
    out=$(env -i PATH=/usr/bin:/bin HOME="$home" "$@" --check 2>&1)
    code=$?
    end=$(date +%s%N)
    elapsed=$(( (end - start) / 1000000 ))
    runs=$(wc -l < "$log")
    echo
    echo "== case $name: ${elapsed} ms, exit $code, bundled copy runs: $runs"
    sed 's/^/   /' <<< "$out" | head -n 8
    echo "   log:"; sed 's/^/     /' "$log"
    output=$out; status=$code
}
verdict() { if "$@"; then echo "   PASS"; else echo "   FAIL"; failed=1; fi; }

run P "$manager"
verdict test "$runs" -eq 0 -a "$status" -eq 0

ai=(APPIMAGE="$work/fake.AppImage" APPDIR="$appdir")
run A "${ai[@]}" "$appdir/bin/foxreach-manager"
verdict test "$runs" -eq 0 -a "$elapsed" -lt 3000 -a "$status" -eq 0

run B "${ai[@]}" FOXREACH_MANAGER_PROGRAM="$work/no-such-foxreach" "$appdir/bin/foxreach-manager"
verdict bash -c '[[ $1 == "status --json" && $2 -eq 0 && $3 == *"read with: $4"* ]]' _ "$(cat "$log")" "$status" "$output" "$appdir/share/foxreach-manager/foxreach"

run C FOXREACH_MANAGER_BUNDLED="$appdir/share/foxreach-manager/foxreach" "$manager"
verdict bash -c '[[ $1 == *"version"* && $2 == *"bundled: foxreach $3 "* ]]' _ "$(cat "$log")" "$output" "$("$program" version)"

echo
if (( failed )); then echo "some failed"; exit 1; fi
echo "all passed"
