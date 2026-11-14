#!/bin/sh
# Installs the foxreach program for this user and lets Firefox-based browsers start it. Linux, macOS.
#
# Run:    sh install.sh            (any arguments go to `foxreach install`, see `foxreach help`)
# Does:   downloads foxreach for this machine from the latest GitHub release, checks it against
#         the release's SHA256SUMS, puts it in ~/.local/bin, and runs `foxreach install`.
# Undo:   foxreach uninstall && rm ~/.local/bin/foxreach
# The checksum shows the download is complete, not who built it. To check that, see
# "Checking a release" in README.md.
set -eu

REPO="nilltadios/foxreach"
BASE="${FOXREACH_RELEASE_URL:-https://github.com/$REPO/releases/latest/download}"
DEST="${FOXREACH_BIN_DIR:-$HOME/.local/bin}"

case "$(uname -s)" in
  Linux) os=linux ;;
  Darwin) os=darwin ;;
  *) echo "foxreach runs on Linux and macOS only." >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) file="foxreach-$os-x64" ;;
  aarch64 | arm64) file="foxreach-$os-arm64" ;;
  *) echo "No foxreach build for $(uname -m)." >&2; exit 1 ;;
esac

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi
}

fetch() {
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then wget -q "$1" -O "$2"
  else echo "curl or wget is needed." >&2; exit 1
  fi
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
echo "Downloading $file from $BASE"
fetch "$BASE/$file" "$tmp/$file"
fetch "$BASE/SHA256SUMS" "$tmp/SHA256SUMS"
want="$(awk -v f="$file" '$2 == f { print $1 }' "$tmp/SHA256SUMS")"
got="$(sha256 "$tmp/$file" | awk '{ print $1 }')"
if [ -z "$want" ] || [ "$want" != "$got" ]; then
  echo "Checksum mismatch for $file: expected ${want:-nothing listed}, got $got. Nothing was installed." >&2
  exit 1
fi

mkdir -p "$DEST"
chmod 755 "$tmp/$file"
# macOS runs no program whose signature does not match it. The x64 build keeps the signature of
# Bun's own runtime, which the packed program no longer matches: sign it ad hoc for this Mac.
if [ "$os" = darwin ] && ! codesign -v "$tmp/$file" 2>/dev/null; then
  codesign --force --sign - "$tmp/$file"
fi
mv "$tmp/$file" "$DEST/foxreach"
echo "Installed $DEST/foxreach ($("$DEST/foxreach" version))"
case ":$PATH:" in *":$DEST:"*) ;; *) echo "Note: $DEST is not on your PATH." ;; esac
"$DEST/foxreach" install "$@"
