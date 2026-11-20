#!/usr/bin/env bash
# Does a packaged manager, which carries no foxreach program, download the release, check it and
# install it, and does it still read a setup the browsers' host manifest points at?
#
# Builds a fake AppDir around a built foxreach-manager (host.rs treats the process as an AppImage
# when APPIMAGE and APPDIR are set and the executable lies inside APPDIR), with nothing in
# share/foxreach-manager. The release is a local folder given as FOXREACH_RELEASE_URL=file://...
# (curl reads file: addresses, so no server is needed): BUILDINFO.txt, SHA256SUMS and, under this
# machine's asset name, a stand-in program, a shell script that answers `version` only (a program
# too old for `status --json`, so it never becomes the reader and every install below is the
# app's own download). The manager runs with env -i and a throwaway HOME, with --install-program
# and --check only: no window, nothing written outside the throwaway folder.
#
# Cases:
#   1  Nothing installed, no manifest: --check says not installed, names no reader, prints no note
#      (the window would show "could not be read" above the Install row), downloads nothing.
#   2  --install-program: ~/.local/bin/foxreach is the release's file and says its version (9.9.9).
#   2b --check: that program is installed but too old for `status --json` (the note says so), and
#      nothing reads the status, so the next install is the app's own download again.
#   3  Again, with a release 9.9.10: the 9.9.9 file is kept as foxreach.previous.
#   3b A program at ~/.local/bin/foxreach whose status says format 2 (newer than this app reads):
#      the note says so and names the app as what to update, and it is not read with.
#   4  SHA256SUMS lists a wrong checksum: exit status not 0, "Checksum mismatch", "Nothing was
#      changed.", the installed file byte-identical to before, no temporary file left.
#   4b BUILDINFO.txt names another version than the file prints: refused the same way.
#   5  A PATH with neither curl nor wget: "curl or wget is needed.", nothing changed.
#   6a A new throwaway HOME, nothing at ~/.local/bin/foxreach, and
#      .mozilla/native-messaging-hosts/foxreach.json whose wrapper runs a program at another
#      path: --check reads through it ("read with: <that program>").
#   6b The same with a wrapper that runs a checkout (`exec "<runtime>" "<root>/bridge/host.mjs"`):
#      "read with: <runtime> <root>/bridge/cli.mjs".
#   6c A manifest left behind whose wrapper runs ~/.local/bin/foxreach, which is gone: not tried
#      again as a reader, so "not installed" with no note (a "Cannot run" note otherwise).
#   For 6a and 6b the program the manifest names is a second stand-in that prints a minimal valid
#   `status --json` (format 1, no agents, setup naming itself), rather than handing its arguments
#   to a real foxreach: the check then needs no foxreach on this machine and cannot read the
#   user's own setup.
#
# Run:      check-install-download.sh [FOXREACH_MANAGER]
#           FOXREACH_MANAGER: default manager/target/debug/foxreach-manager (cargo build --locked).
#           Needs curl, sha256sum, and x86_64 or aarch64.
# Expected: PASS after each case, last line "all passed", exit 0. Before the manager downloaded
#           the release itself (it installed a bundled copy and had no other reader), 1 failed on
#           its note, 2 to 5 failed (no copy and no reader: "has none to download it with"), 6a and
#           6b failed (nothing read), 6c failed on its note: "some failed", exit 1.
# The temporary folder (made next to FOXREACH_MANAGER) is removed on exit.
set -uo pipefail
default=$(dirname -- "$(realpath -- "$0")")/../target/debug/foxreach-manager
manager=$(realpath -- "${1:-$default}")
[[ -x $manager ]] || { echo "Missing $manager" >&2; exit 2; }
case $(uname -m) in
  x86_64) asset=foxreach-linux-x64 ;;
  aarch64) asset=foxreach-linux-arm64 ;;
  *) echo "No foxreach build for $(uname -m)" >&2; exit 2 ;;
esac

# Next to the build, so the copy below is a reflink on btrfs instead of 600 MB in /tmp.
work=$(mktemp -d -p "$(dirname -- "$manager")" fxm-download.XXXXXX)
trap 'rm -rf -- "$work"' EXIT
appdir=$work/AppDir
home=$work/home
rel=$work/release
mkdir -p "$appdir/bin" "$home" "$rel"
cp --reflink=auto -- "$manager" "$appdir/bin/foxreach-manager"

# release VERSION [SUMS_VERSION]: a release folder whose program says VERSION. BUILDINFO.txt names
# SUMS_VERSION when given (case 4b); SHA256SUMS="zeros" lists a wrong checksum (case 4).
release() {
    cat > "$rel/$asset" <<EOF
#!/bin/sh
[ "\$1" = version ] && { echo $1; exit 0; }
echo "unknown command: \$1" >&2
exit 1
EOF
    echo "foxreach ${2:-$1}" > "$rel/BUILDINFO.txt"
    local sum
    sum=$(sha256sum "$rel/$asset" | cut -d' ' -f1)
    [[ ${SUMS:-} == zeros ]] && sum=$(printf '0%.0s' {1..64})
    printf '%s  %s\n' "$sum" "$asset" > "$rel/SHA256SUMS"
}

echo "manager: $manager ($("$manager" --version))"
echo "asset:   $asset"
failed=0

# run HOME PATH ARG: runs the manager in the fake AppDir with a clean environment.
run() {
    output=$(env -i PATH="$2" HOME="$1" APPIMAGE="$work/fake.AppImage" APPDIR="$appdir" \
        FOXREACH_RELEASE_URL="file://$rel" "$appdir/bin/foxreach-manager" "$3" 2>&1)
    status=$?
    echo "   exit $status"
    while IFS= read -r line; do echo "   | $line"; done <<< "$output"
}
verdict() { if "$@"; then echo "   PASS"; else echo "   FAIL"; failed=1; fi; }
has() { [[ $output == *"$1"* ]]; }
bin=$home/.local/bin

echo; echo "== 1: nothing installed, no manifest: --check"
run "$home" /usr/bin:/bin --check
check1() { has "installed: none" && has "in use: none" && ! has "read with:" && ! has "note:" && [[ ! -e $bin ]]; }
verdict check1

echo; echo "== 2: --install-program from the release 9.9.9"
release 9.9.9
run "$home" /usr/bin:/bin --install-program
check2() { [[ $status -eq 0 ]] && has "9.9.9" && cmp -s "$bin/foxreach" "$rel/$asset" && [[ $("$bin/foxreach" version) == 9.9.9 && ! -e $bin/.foxreach-manager-new ]]; }
verdict check2

echo; echo "== 2b: --check with that program, which is too old for status --json"
run "$home" /usr/bin:/bin --check
check2b() { [[ $status -ne 0 ]] && has "installed: foxreach 9.9.9" && has "note: foxreach status --json failed" && ! has "read with:"; }
verdict check2b

echo; echo "== 3: again, from the release 9.9.10 (Replace)"
release 9.9.10
run "$home" /usr/bin:/bin --install-program
check3() { [[ $status -eq 0 ]] && has "kept the old program" && [[ $("$bin/foxreach" version) == 9.9.10 && $("$bin/foxreach.previous" version) == 9.9.9 ]]; }
verdict check3

echo; echo "== 3b: an installed program whose status is in a newer format"
home3b=$work/home3b
mkdir -p "$home3b/.local/bin"
cat > "$home3b/.local/bin/foxreach" <<'EOF'
#!/bin/sh
case "$1" in
  version) echo 12.0.0 ;;
  status) echo '{"format":2,"agentsById":{}}' ;;
  *) exit 1 ;;
esac
EOF
chmod 755 "$home3b/.local/bin/foxreach"
run "$home3b" /usr/bin:/bin --check
check3b() { [[ $status -ne 0 ]] && has "installed: foxreach 12.0.0" && has "note: This foxreach is newer than this app reads (status format 2" && has "Update foxreach manager." && ! has "read with:"; }
verdict check3b

# What 4 to 5 must leave as it is (nothing, when 2 and 3 installed nothing).
for f in foxreach foxreach.previous; do [[ -e $bin/$f ]] && cp -- "$bin/$f" "$work/before.$f"; done
unchanged() { cmp -s "$bin/foxreach" "$work/before.foxreach" && cmp -s "$bin/foxreach.previous" "$work/before.foxreach.previous" && [[ ! -e $bin/.foxreach-manager-new ]]; }

echo; echo "== 4: SHA256SUMS lists a wrong checksum"
SUMS=zeros release 9.9.11
run "$home" /usr/bin:/bin --install-program
check4() { [[ $status -ne 0 ]] && has "Checksum mismatch for $asset" && has "Nothing was changed." && unchanged; }
verdict check4

echo; echo "== 4b: BUILDINFO.txt says 9.9.12, the file prints 9.9.11"
release 9.9.11 9.9.12
run "$home" /usr/bin:/bin --install-program
check4b() { [[ $status -ne 0 ]] && has "Nothing was changed." && unchanged; }
verdict check4b

echo; echo "== 5: a PATH with neither curl nor wget"
release 9.9.11
bare=$work/bare-bin
mkdir -p "$bare"
for t in sh bash cat chmod cp head mkdir mv rm sha256sum test; do ln -s "$(command -v "$t")" "$bare/$t"; done
run "$home" "$bare" --install-program
check5() { [[ $status -ne 0 ]] && has "curl or wget is needed." && has "Nothing was changed." && unchanged; }
verdict check5

# A second stand-in, at another path, that answers status --json for the setup it names.
standin() {
    cat > "$1" <<EOF
#!/bin/sh
case "\$1" in
  version) echo 9.8.7 ;;
  status) echo '{"format":1,"mcpCommand":"x mcp","setup":{"kind":"$2","root":"$3","command":$4,"mixed":false},"agents":[],"host":{"wrapper":null,"wrapperRuns":null,"manifests":[],"flatpaks":[]}}' ;;
  *) exit 1 ;;
esac
EOF
    chmod 755 "$1"
}
# manifest HOME EXEC_LINE: a browser host manifest in HOME whose wrapper ends in EXEC_LINE.
manifest() {
    mkdir -p "$1/.mozilla/native-messaging-hosts" "$1/.local/share/foxreach"
    printf '#!/bin/sh\nunset LD_PRELOAD\n%s\n' "$2" > "$1/.local/share/foxreach/foxreach-host"
    chmod 755 "$1/.local/share/foxreach/foxreach-host"
    printf '{"name":"foxreach","path":"%s","type":"stdio","allowed_extensions":["foxreach@nilltadios"]}\n' "$1/.local/share/foxreach/foxreach-host" \
        > "$1/.mozilla/native-messaging-hosts/foxreach.json"
}

echo; echo "== 6a: the browsers' host runs a program at another path, nothing at ~/.local/bin/foxreach"
home6=$work/home6a
other="$work/other place/foxreach"
mkdir -p "$(dirname -- "$other")"
standin "$other" release "" "[\"$other\"]"
manifest "$home6" "exec \"$other\" host"
run "$home6" /usr/bin:/bin --check
check6a() { [[ $status -eq 0 ]] && has "installed: none" && has "read with: $other" && has "in use: foxreach 9.8.7" && [[ ! -e $home6/.local/bin/foxreach ]]; }
verdict check6a

echo; echo "== 6b: the browsers' host runs a checkout"
home6=$work/home6b
root=$work/checkout
runtime=$work/runtime/bun
mkdir -p "$root/bridge" "$(dirname -- "$runtime")"
# The runtime stand-in drops the script path and answers as the program would.
standin "$work/checkout-cli" checkout "$root" "[\"$runtime\",\"$root/bridge/cli.mjs\"]"
printf '#!/bin/sh\nshift\nexec "%s" "$@"\n' "$work/checkout-cli" > "$runtime"
chmod 755 "$runtime"
manifest "$home6" "exec \"$runtime\" \"$root/bridge/host.mjs\""
run "$home6" /usr/bin:/bin --check
check6b() { [[ $status -eq 0 ]] && has "installed: none" && has "read with: $runtime $root/bridge/cli.mjs" && has "Checkout(\"$root\")"; }
verdict check6b

echo; echo "== 6c: the browsers' host runs ~/.local/bin/foxreach, which is gone"
home6=$work/home6c
manifest "$home6" "exec \"$home6/.local/bin/foxreach\" host"
run "$home6" /usr/bin:/bin --check
check6c() { has "installed: none" && has "in use: none" && ! has "read with:" && ! has "note:"; }
verdict check6c

echo
if (( failed )); then echo "some failed"; exit 1; fi
echo "all passed"
