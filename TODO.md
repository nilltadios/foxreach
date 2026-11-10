# To do

Open items, newest first. Remove an item when it is done.

## Publish 0.3.0 (prepared 2026-11-10; each step sends something out, so each waits for the owner)

1. **Sign the build for release Firefox.** Make an API key at
   https://addons.mozilla.org/developers/addon/api/key/ and save it in `~/.config/foxreach/amo.env`
   (two lines, see `scripts/sign.py --help`; `chmod 600`). Then `uv run --python 3.15 scripts/sign.py`.
   Not run yet: it needs the key and uploads the add-on to Mozilla. `--dry-run` passes.
2. **Release.** Commit, then `uv run --python 3.15 scripts/release.py` and the commands it prints
   (tag and push). The release workflow has never run: watch its first run.
3. **Enable GitHub Pages** for `docs/` (the command is in `release.py`'s output). Until then no
   installed add-on can update itself.
4. **List on addons.mozilla.org:** `scripts/sign.py --channel listed`, with the next version number
   (a version can be submitted once). Text and reviewer notes are in `amo/`. Mozilla reviews it.

## Later

- Packages (deb, rpm, Flatpak extension) for the `foxreach` program; today it is one file and
  `install.sh`.
- Signed tags.
- The limits listed under "What would change this" in `spikes/bidi/FINDINGS.md`: watch for a way to
  get trusted input on release Firefox.
