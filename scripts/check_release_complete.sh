#!/usr/bin/env bash
# Check that a GitHub release holds everything clients look for in the release marked latest, so
# it can be published as latest: the foxreach program and add-on (install.sh, bridge/update.mjs)
# and one manager AppImage with its .zsync (the AppImage's update information) and one Flatpak
# bundle (.github/workflows/pages.yml) per architecture. The release workflows run it on the draft
# before publishing it.
#
# Run:      scripts/check_release_complete.sh TAG [DIR]
#           Without DIR: downloads BUILDINFO.txt, SHA256SUMS and the .sha256 files into a
#             temporary folder; the hash of every other file is the sha256 digest GitHub reports
#             for the asset (computed by GitHub from the uploaded bytes). An asset with no digest
#             is downloaded and hashed.
#           With DIR (a folder holding the release's files, as the workflows have them after
#             building and carrying): the files are read and hashed from DIR, nothing is
#             downloaded; only the asset list comes from GitHub, and each file read from DIR must
#             also have the digest GitHub reports for its asset, so DIR is shown to be what was
#             uploaded.
#           RELEASE_ASSETS_FILE=FILE (tests, needs DIR): the asset list from FILE, one name per
#             line, instead of from GitHub; there are then no digests, and hashes come from DIR.
#           The repository is GH_REPO (OWNER/REPO), else the one this checkout's gh resolves.
#           Works on a draft: gh looks a tag up among drafts too (cli/cli
#           pkg/cmd/release/shared/fetch.go, FetchRelease: the published lookup and a GraphQL
#           draft lookup run together, either may answer; view and download both use it).
# Checks:   - BUILDINFO.txt, first line "foxreach <version>";
#           - SHA256SUMS lists every asset that is not a manager file, except itself and
#             BUILDINFO.txt, with matching hashes, and lists nothing that is not in the release;
#           - the program for each platform in TARGETS in scripts/build_binary.py (read from that
#             file, so a platform added there is checked here without editing this script);
#           - for x86_64 and aarch64: exactly one foxreach-manager-*-ARCH.AppImage, with exactly
#             one .AppImage.zsync and one .AppImage.sha256, both named after it, the hash
#             matching; exactly one foxreach-manager-*-ARCH.flatpak and one .flatpak.sha256 named
#             after it, matching. "Exactly one": the update information's pattern
#             foxreach-manager-*-ARCH.AppImage.zsync must match one file per architecture.
#           The .zsync's content is checked when it is built (manager/packaging/check-update-info.sh).
# Needs:    gh (signed in), sha256sum, awk.
# Expected: "ok: TAG is complete: foxreach X, manager Y (N assets)", exit 0. Otherwise one
#           "missing: ..." or "wrong: ..." line per problem on stderr, exit 1. Exit 2: usage.
set -euo pipefail
here=$(dirname -- "$(realpath -- "$0")")
(( $# == 1 || $# == 2 )) || { echo "usage: $0 TAG [DIR]" >&2; exit 2; }
tag=$1 dir=${2:-}
for t in gh sha256sum awk; do command -v "$t" >/dev/null || { echo "Missing tool: $t" >&2; exit 2; }; done
[[ -z $dir || -d $dir ]] || { echo "$0: no folder $dir" >&2; exit 2; }
[[ -z ${RELEASE_ASSETS_FILE:-} || -n $dir ]] || { echo "$0: RELEASE_ASSETS_FILE needs DIR" >&2; exit 2; }

declare -A digest=()
names=()
if [[ -n ${RELEASE_ASSETS_FILE:-} ]]; then
  while IFS= read -r n; do [[ -n $n ]] && names+=("$n"); done < "$RELEASE_ASSETS_FILE"
else
  export GH_REPO=${GH_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}
  listing=$(gh release view "$tag" --json assets --jq '.assets[] | "\(.name)\t\(.digest // "")"') \
    || { echo "$0: cannot read release $tag in $GH_REPO" >&2; exit 1; }
  while IFS=$'\t' read -r n d; do
    [[ -n $n ]] || continue
    names+=("$n")
    if [[ $d == sha256:* ]]; then digest[$n]=${d#sha256:}; fi
  done <<< "$listing"
fi

work=$(mktemp -d)
trap 'rm -rf -- "${work:?}"' EXIT
# The checks run in this shell, not in $(...), so that every problem sets bad; results come back
# in F (a local file), H (a hash) and ONE (an asset name).
bad=0
missing() { echo "missing: $*" >&2; bad=1; }
wrong() { echo "wrong: $*" >&2; bad=1; }
has() { local x; for x in "${names[@]}"; do [[ $x == "$1" ]] && return 0; done; return 1; }

# F: the local copy of asset NAME, from DIR, else downloaded once into the work folder.
file_of() {
  local n=$1
  if [[ -n $dir ]]; then
    F=$dir/$n
    [[ -f $F ]] || { missing "$n is in release $tag but not in $dir"; return 1; }
  else
    F=$work/$n
    [[ -f $F ]] || gh release download "$tag" --dir "$work" --pattern "$n" >/dev/null \
      || { missing "$n could not be downloaded from $tag"; return 1; }
  fi
}
# H: the sha256 of asset NAME. With DIR, the file's own hash, which must equal GitHub's digest
# when GitHub reports one; without DIR, GitHub's digest, or the downloaded file's hash if none.
declare -A hashed=()
hash_of() {
  local n=$1
  if [[ -n ${hashed[$n]:-} ]]; then H=${hashed[$n]}; return 0; fi
  if [[ -z $dir && -n ${digest[$n]:-} ]]; then
    H=${digest[$n]}
  else
    file_of "$n" || return 1
    H=$(sha256sum -- "$F" | awk '{print $1}')
    if [[ -n ${digest[$n]:-} && ${digest[$n]} != "$H" ]]; then
      wrong "$F is not the file uploaded to $tag as $n (sha256 $H here, ${digest[$n]} on GitHub)"
    fi
  fi
  hashed[$n]=$H
}
# ONE: the one asset matching GLOB; otherwise says that none (naming WANTED, default GLOB) or
# several do.
exactly_one() {
  local x found=()
  # shellcheck disable=SC2053  # the pattern is a glob on purpose
  for x in "${names[@]}"; do if [[ $x == $1 ]]; then found+=("$x"); fi; done
  if (( ${#found[@]} == 0 )); then missing "${2:-$1}"; return 1; fi
  if (( ${#found[@]} > 1 )); then wrong "${#found[@]} files match $1, one is allowed: ${found[*]}"; return 1; fi
  ONE=${found[0]}
}
# NAME.sha256 is the only GLOB.sha256, names NAME and holds NAME's hash.
sha_file_matches() {
  local n=$1 glob=$2 s want h
  exactly_one "$glob.sha256" "$n.sha256" || return 0
  s=$ONE
  [[ $s == "$n.sha256" ]] || { wrong "$s is not named after $n"; return 0; }
  hash_of "$s" || return 0   # with DIR, also that this .sha256 is the uploaded one
  file_of "$s" || return 0
  read -r want h < "$F" || true
  h=${h#\*}
  [[ $h == "$n" ]] || wrong "$s names '$h', not $n"
  hash_of "$n" || return 0
  [[ $H == "${want:-}" ]] || wrong "$n does not match $s"
}

# BUILDINFO.txt
version=
if ! has BUILDINFO.txt; then
  missing "BUILDINFO.txt"
elif hash_of BUILDINFO.txt && file_of BUILDINFO.txt; then
  first=$(head -n 1 -- "$F")
  if [[ $first =~ ^foxreach\ ([0-9]+\.[0-9]+\.[0-9]+[^[:space:]]*)$ ]]; then version=${BASH_REMATCH[1]}
  else wrong "BUILDINFO.txt's first line is '$first', not 'foxreach <version>'"
  fi
fi

# SHA256SUMS against the assets that are not manager files, both ways. Lines are sha256sum's
# "HASH  NAME" (or "HASH *NAME").
if ! has SHA256SUMS; then
  missing "SHA256SUMS"
elif hash_of SHA256SUMS && file_of SHA256SUMS; then
  declare -A listed=()
  while read -r h n; do
    [[ -n ${h:-} ]] || continue
    n=${n#\*}
    listed[$n]=$h
    if [[ $n == foxreach-manager-* ]]; then wrong "SHA256SUMS lists $n, a manager file (those have their own .sha256)"
    elif ! has "$n"; then missing "$n (listed in SHA256SUMS, not in release $tag)"
    fi
  done < "$F"
  for n in "${names[@]}"; do
    [[ $n == foxreach-manager-* || $n == SHA256SUMS || $n == BUILDINFO.txt ]] && continue
    if [[ -z ${listed[$n]:-} ]]; then wrong "$n is in release $tag but not listed in SHA256SUMS"
    elif hash_of "$n" && [[ $H != "${listed[$n]}" ]]; then wrong "$n does not match its hash in SHA256SUMS"
    fi
  done
fi

# The programs, one per platform in build_binary.TARGETS.
targets=$(awk '/^TARGETS = \{/ {p = 1; next} p && /^\}/ {exit} p && match($0, /^ *"[^"]+":/) {s = substr($0, RSTART, RLENGTH); gsub(/[ ":]/, "", s); print s}' "$here/build_binary.py")
[[ -n $targets ]] || { echo "$0: cannot read TARGETS from $here/build_binary.py" >&2; exit 2; }
while IFS= read -r n; do has "$n" || missing "$n (a platform in scripts/build_binary.py TARGETS)"; done <<< "$targets"

# The manager's files, per architecture.
manager=()
for arch in x86_64 aarch64; do
  if exactly_one "foxreach-manager-*-$arch.AppImage"; then
    a=$ONE
    manager+=("$a")
    if exactly_one "foxreach-manager-*-$arch.AppImage.zsync" "$a.zsync"; then
      [[ $ONE == "$a.zsync" ]] || wrong "$ONE is not named after $a"
      [[ -z $dir ]] || hash_of "$ONE" || true   # with DIR, that this .zsync is the uploaded one
    fi
    sha_file_matches "$a" "foxreach-manager-*-$arch.AppImage"
  fi
  if exactly_one "foxreach-manager-*-$arch.flatpak"; then
    manager+=("$ONE")
    sha_file_matches "$ONE" "foxreach-manager-*-$arch.flatpak"
  fi
done

mver=$(printf '%s\n' "${manager[@]}" | sed 's/^foxreach-manager-\(.*\)-[^-]*$/\1/' | sort -u | paste -sd, -)
# One manager version for both architectures: a release with two would give each a different app.
[[ $mver != *,* ]] || wrong "the manager files are of several versions: $mver"
(( bad == 0 )) || exit 1
echo "ok: $tag is complete: foxreach $version, manager $mver (${#names[@]} assets)"
