#!/usr/bin/env bash
# Copy one set of release files, byte for byte, from one GitHub release to another. Clients only
# look in the release marked latest (install.sh, bridge/update.mjs, the manager AppImage's update
# information), so every release carries both sets: the set it did not build is carried from the
# release that was latest before (.github/workflows/release.yml and manager-release.yml).
#
# Run:      scripts/carry_release.sh SET FROM_TAG TO_TAG [--dry-run]
#           SET: manager  every asset named foxreach-manager-* (AppImage, .zsync, Flatpak bundle and
#                         their .sha256, for x86_64 and aarch64)
#                foxreach every other asset (programs, add-ons, install.sh, install.ps1,
#                         SHA256SUMS, BUILDINFO.txt)
#           --dry-run: download and verify only, print what would be uploaded; TO_TAG may be "-".
#           CARRY_KEEP=DIR: also leave the verified files in DIR (created if needed; a file already
#             there with the same name is an error), so the caller can check them without
#             downloading again (scripts/check_release_complete.sh TAG DIR).
#           CARRY_FROM_DIR=DIR (tests, --dry-run only): take the set from the files in DIR instead
#             of downloading FROM_TAG, to check that bad files are refused.
#           The repository is GH_REPO (OWNER/REPO), else the one this checkout's gh resolves.
# Needs:    gh (signed in; `gh attestation verify` also needs network), sha256sum, awk.
# Does:     lists FROM_TAG's assets, downloads the set into a temporary folder and verifies all of
#           it before uploading anything:
#             foxreach: SHA256SUMS lists every file of the set but itself and BUILDINFO.txt, each
#               hash matches, and it names no file that is not in the set;
#             manager: every .AppImage and .flatpak has its .sha256 naming it, and the hash matches;
#               every .sha256 and .zsync belongs to a file of the set;
#             both: `gh attestation verify FILE --repo REPO` passes for every file. Every file of
#               v0.4.1 (all 22, the .sha256, .zsync, SHA256SUMS and BUILDINFO.txt included) has an
#               attestation, because release.yml attests all of dist/release/* and dist/manager/*,
#               so every file is required to have one. A copy has the same digest, so it still
#               verifies in the release it is carried to.
#           Then uploads the set to TO_TAG (--clobber), which must be a draft: the workflows publish
#           only once the release is complete.
# Expected: "verified: N files of the SET set from FROM_TAG", then "uploaded: ..." (or
#           "dry run: would upload ..."), exit 0.
#           Exit 1: an empty set, a hash or attestation that does not verify, a file missing from
#           SHA256SUMS or without its .sha256, TO_TAG not a draft, a failed download or upload;
#           each named in a line. Exit 2: usage.
set -euo pipefail
usage() { echo "usage: $0 manager|foxreach FROM_TAG TO_TAG [--dry-run]" >&2; exit 2; }
fail() { echo "carry_release.sh: $*" >&2; exit 1; }

dry_run=0
args=()
for a in "$@"; do
  if [[ $a == --dry-run ]]; then dry_run=1; else args+=("$a"); fi
done
(( ${#args[@]} == 3 )) || usage
set_name=${args[0]} from=${args[1]} to=${args[2]}
[[ $set_name == manager || $set_name == foxreach ]] || usage
[[ $to != - ]] || (( dry_run )) || fail "TO_TAG '-' is only for --dry-run"
[[ $from != "$to" ]] || fail "FROM_TAG and TO_TAG are the same release ($from)"
[[ -z ${CARRY_FROM_DIR:-} ]] || (( dry_run )) || fail "CARRY_FROM_DIR is for tests and only works with --dry-run"
for t in gh sha256sum awk; do command -v "$t" >/dev/null || fail "missing tool: $t"; done

repo=${GH_REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)} || fail "cannot tell the repository: set GH_REPO=OWNER/REPO"
export GH_REPO=$repo

in_set() {  # NAME: is it in the chosen set?
  if [[ $set_name == manager ]]; then [[ $1 == foxreach-manager-* ]]; else [[ $1 != foxreach-manager-* ]]; fi
}

work=$(mktemp -d)
trap 'rm -rf -- "${work:?}"' EXIT
files=$work/files
mkdir "$files"

names=()
if [[ -n ${CARRY_FROM_DIR:-} ]]; then
  [[ -d $CARRY_FROM_DIR ]] || fail "CARRY_FROM_DIR: no folder $CARRY_FROM_DIR"
  for p in "$CARRY_FROM_DIR"/*; do
    [[ -f $p ]] || continue
    n=$(basename -- "$p")
    if in_set "$n"; then names+=("$n"); cp -- "$p" "$files/$n"; fi
  done
  source_desc=$CARRY_FROM_DIR
else
  listing=$(gh release view "$from" --json assets --jq '.assets[].name') || fail "cannot read release $from in $repo"
  while IFS= read -r n; do
    [[ -n $n ]] && in_set "$n" && names+=("$n")
  done <<< "$listing"
  source_desc="$from"
fi
(( ${#names[@]} )) || fail "$source_desc has no files of the $set_name set: nothing to carry"

if [[ -z ${CARRY_FROM_DIR:-} ]]; then
  patterns=()
  for n in "${names[@]}"; do patterns+=(--pattern "$n"); done
  gh release download "$from" --dir "$files" "${patterns[@]}" || fail "download from $from failed"
fi
for n in "${names[@]}"; do [[ -f $files/$n ]] || fail "$n was not downloaded from $source_desc"; done

has() { local x; for x in "${names[@]}"; do [[ $x == "$1" ]] && return 0; done; return 1; }
hash_of() { sha256sum -- "$files/$1" | awk '{print $1}'; }
bad=0
problem() { echo "carry_release.sh: $*" >&2; bad=1; }

# Hashes. Lines are sha256sum's "HASH  NAME" (or "HASH *NAME").
if [[ $set_name == foxreach ]]; then
  has BUILDINFO.txt || problem "$source_desc has no BUILDINFO.txt"
  if ! has SHA256SUMS; then
    problem "$source_desc has no SHA256SUMS"
  else
    declare -A listed=()
    while read -r h n; do
      [[ -n ${h:-} ]] || continue
      n=${n#\*}
      listed[$n]=$h
      has "$n" || problem "SHA256SUMS lists $n, which is not in $source_desc"
    done < "$files/SHA256SUMS"
    for n in "${names[@]}"; do
      [[ $n == SHA256SUMS || $n == BUILDINFO.txt ]] && continue
      if [[ -z ${listed[$n]:-} ]]; then problem "$n is not listed in SHA256SUMS"
      elif [[ $(hash_of "$n") != "${listed[$n]}" ]]; then problem "$n does not match its hash in SHA256SUMS"
      fi
    done
  fi
else
  for n in "${names[@]}"; do
    case $n in
      *.AppImage | *.flatpak)
        if ! has "$n.sha256"; then problem "$n has no $n.sha256"; continue; fi
        read -r h f < "$files/$n.sha256" || true
        f=${f#\*}
        [[ $f == "$n" ]] || problem "$n.sha256 names '$f', not $n"
        [[ $(hash_of "$n") == "${h:-}" ]] || problem "$n does not match $n.sha256" ;;
      *.sha256 | *.zsync)
        has "${n%.*}" || problem "$n has no ${n%.*} beside it in $source_desc" ;;
      *) problem "$n is not a kind of manager file this script knows" ;;
    esac
  done
fi

# Provenance: built by this repository's workflows.
for n in "${names[@]}"; do
  out=$(gh attestation verify "$files/$n" --repo "$repo" 2>&1) || problem "$n: no valid attestation for $repo: $(tail -n 3 <<< "$out" | tr '\n' ' ')"
done
(( bad == 0 )) || fail "the $set_name set from $source_desc did not verify; nothing was uploaded"
echo "verified: ${#names[@]} files of the $set_name set from $source_desc"

if [[ -n ${CARRY_KEEP:-} ]]; then
  mkdir -p -- "$CARRY_KEEP"
  for n in "${names[@]}"; do [[ ! -e $CARRY_KEEP/$n ]] || fail "CARRY_KEEP: $CARRY_KEEP/$n already exists"; done
  for n in "${names[@]}"; do cp -- "$files/$n" "$CARRY_KEEP/$n"; done
  echo "kept in $CARRY_KEEP"
fi

if (( dry_run )); then
  printf 'dry run: would upload to %s:\n' "$to"
  printf '  %s\n' "${names[@]}"
  exit 0
fi

draft=$(gh release view "$to" --json isDraft --jq .isDraft) || fail "cannot read release $to in $repo"
[[ $draft == true ]] || fail "$to is not a draft: files are carried only into a release that is not published yet"
paths=()
for n in "${names[@]}"; do paths+=("$files/$n"); done
gh release upload "$to" "${paths[@]}" --clobber || fail "upload to $to failed"
echo "uploaded: ${#names[@]} files of the $set_name set from $from to $to"
