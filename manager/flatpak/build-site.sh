#!/usr/bin/env bash
# Lays out the GitHub Pages site: docs/ as it is (the add-on's update lists and .xpi files, which
# installed add-ons poll) plus a signed Flatpak repository of the manager, with a .flatpakref and a
# .flatpakrepo. Only writes SITE; deploying it is the pages workflow's job.
#
# Run:      manager/flatpak/build-site.sh SITE BASE_URL BUNDLE...
#           manager/flatpak/build-site.sh --docs-only SITE
#           SITE: a new or empty folder. BASE_URL: where SITE will be served
#           (https://nilltadios.github.io/foxreach/; http://127.0.0.1:PORT/ for a local test).
#           BUNDLE: foxreach-manager-VERSION-ARCH.flatpak files, one per architecture.
# Needs:    flatpak, ostree, gpg; GNUPGHOME holding the secret key whose public half is
#           manager/flatpak/signing-key.asc (any other key is refused).
# Output:   SITE/ = docs/* and docs/.nojekyll, plus
#             repo/                                  OSTree repository, branch stable, every commit
#                                                    and the summary signed
#             dev.nilltadios.FoxreachManager.flatpakref   one-click install (RuntimeRepo: Flathub)
#             foxreach.flatpakrepo                   the remote, for `flatpak remote-add`
#             icon.svg
#           --docs-only writes docs/ alone (the fallback when the Flatpak part cannot be built).
# Expected: one "stable <arch> <commit>" line per bundle, then "Site: SITE", exit 0.
#
# Same bundles, same repository: each bundle's commit (branch master, as the bundle has it) is
# recommitted to stable with its own timestamp and no parent, and the appstream commits take the
# newest app commit's time (SOURCE_DATE_EPOCH), so a rebuild from unchanged bundles gives the same
# commits and clients see no update. Only the summary file differs between runs.
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../.." && pwd)
app=dev.nilltadios.FoxreachManager
remote=foxreach
keyfile=$root/manager/flatpak/signing-key.asc
fail() { echo "build-site.sh: $*" >&2; exit 1; }
usage() { echo "usage: $0 SITE BASE_URL BUNDLE... | $0 --docs-only SITE" >&2; exit 2; }

docs_only=0
if [[ ${1:-} == --docs-only ]]; then docs_only=1; shift; (( $# == 1 )) || usage; else (( $# >= 3 )) || usage; fi
site=$1
if [[ -e $site ]] && [[ -n $(ls -A -- "$site") ]]; then fail "$site exists and is not empty"; fi
mkdir -p -- "$site"
site=$(realpath -- "$site")
cp -a -- "$root/docs/." "$site/"
if (( docs_only )); then echo "Site: $site (docs/ only, no Flatpak repository)"; exit 0; fi

base=${2%/}/
shift 2
for t in flatpak ostree gpg base64 date; do command -v "$t" >/dev/null || fail "missing tool: $t"; done
for n in repo "$app.flatpakref" "$remote.flatpakrepo" icon.svg; do
  [[ ! -e $site/$n ]] || fail "docs/ already has $n, which the Flatpak part writes"
done
gpghome=${GNUPGHOME:-$HOME/.gnupg}
work=$(mktemp -d)
trap 'rm -rf -- "${work:?}"' EXIT
# OSTree verifies against a legacy keyring file, not GnuPG's keybox.
mkdir -m700 "$work/verify"
gpg --batch --dearmor < "$keyfile" > "$work/verify/pubring.gpg"
fpr=$(GNUPGHOME=$work/verify gpg --batch --with-colons --show-keys -- "$keyfile" 2>/dev/null | awk -F: '$1 == "fpr" {print $10; exit}')
[[ -n $fpr ]] || fail "cannot read $keyfile"
GNUPGHOME=$gpghome gpg --batch --list-secret-keys -- "$fpr" >/dev/null 2>&1 \
  || fail "no secret key $fpr in $gpghome: the site must be signed with the key in manager/flatpak/signing-key.asc"

repo=$site/repo
ostree --repo="$repo" init --mode=archive-z2
newest=0 seen=" " n=0
for bundle in "$@"; do
  [[ -f $bundle ]] || fail "no bundle at $bundle"
  n=$((n + 1)) src=$work/import-$n
  ostree --repo="$src" init --mode=archive-z2
  flatpak build-import-bundle --no-update-summary "$src" "$bundle" >/dev/null
  ref=$(ostree --repo="$src" refs | awk -v a="app/$app/" 'index($0, a) == 1')
  [[ $(wc -l <<<"$ref") == 1 && -n $ref ]] || fail "$bundle holds no single app/$app ref: $ref"
  arch=$(cut -d/ -f3 <<<"$ref")
  [[ $seen != *" $arch "* ]] || fail "two bundles for $arch"
  seen+="$arch "
  date=$(ostree --repo="$src" show "$ref" | sed -n 's/^Date: *//p')
  ts=$(date -u -d "$date" +%s)
  (( ts > newest )) && newest=$ts
  flatpak build-commit-from --no-update-summary --src-repo="$src" --src-ref="$ref" \
    --timestamp="$(date -u -d "@$ts" +%Y-%m-%dT%H:%M:%SZ)" \
    --gpg-sign="$fpr" --gpg-homedir="$gpghome" "$repo" "app/$app/$arch/stable" >/dev/null
done

SOURCE_DATE_EPOCH=$newest flatpak build-update-repo --title="foxreach" \
  --comment="The foxreach manager, signed builds from the GitHub releases" \
  --homepage="https://github.com/nilltadios/foxreach" --icon="${base}icon.svg" \
  --default-branch=stable --gpg-sign="$fpr" --gpg-homedir="$gpghome" --prune "$repo" >/dev/null
rm -f -- "$repo/.lock"

for arch in $seen; do
  ref=app/$app/$arch/stable
  commit=$(ostree --repo="$repo" rev-parse "$ref")
  shown=$(ostree --repo="$repo" show --gpg-homedir="$work/verify" "$commit" 2>&1) || true
  [[ $shown == *"Good signature"* ]] || fail "$ref ($commit) is not signed by $fpr: $shown"
  echo "stable $arch $commit"
done

key=$(base64 -w0 < "$work/verify/pubring.gpg")
common="Url=${base}repo/
Homepage=https://github.com/nilltadios/foxreach/blob/main/manager/README.md
Icon=${base}icon.svg
GPGKey=$key
"
printf '[Flatpak Repo]\nTitle=foxreach\nComment=The foxreach manager\nDescription=Signed builds of the foxreach manager from its GitHub releases\nDefaultBranch=stable\n%s' \
  "$common" > "$site/$remote.flatpakrepo"
printf '[Flatpak Ref]\nTitle=foxreach manager\nName=%s\nBranch=stable\nIsRuntime=false\nSuggestRemoteName=%s\nRuntimeRepo=https://dl.flathub.org/repo/flathub.flatpakrepo\n%s' \
  "$app" "$remote" "$common" > "$site/$app.flatpakref"
cp -- "$root/manager/resources/$app.svg" "$site/icon.svg"
echo "Site: $site"
