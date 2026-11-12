# To do

Open items, newest first. Remove an item when it is done.

## Publish 0.3.0 (steps 1-3 done 2026-11-11; step 4 waits for Mozilla)

1. ~~**Sign the build for release Firefox.**~~ Done 2026-11-11: 0.3.0 signed unlisted, in
   `dist/signed/foxreach-0.3.0-signed.xpi` (sha256 7c1cc667…71d3). `test/signed_firefox.py` shows
   release Firefox 160 installs it as "signed" and refuses the unsigned build. Key in
   `~/.config/foxreach/amo.env`. 0.3.0 cannot be submitted again: raise the version for the next.
2. ~~**Release.**~~ Done 2026-11-11: v0.3.0 on GitHub (https://github.com/nilltadios/foxreach/releases/tag/v0.3.0),
   built and attested by the release workflow (first run; the tag had to be pushed a second time
   because the first push, together with the workflow's own first commit, started no run).
   `scripts/verify_release.py 0.3.0`: all 6 files match the source.
3. ~~**Enable GitHub Pages**~~ Was already on; serves docs/. `scripts/release.py --verify`: both lists
   of updates published, hashes match.
4. **addons.mozilla.org listing: submitted, awaiting Mozilla's review.** 0.3.1 (same code as 0.3.0,
   whose number the unlisted build holds) sent 2026-11-11 with `sign.py --channel listed`. Status:
   https://addons.mozilla.org/en-US/developers/addon/fe1f53e268044b529443/versions . Answer reviewer
   questions there. The next GitHub release must be 0.3.2 or later: 0.3.1 is now taken at Mozilla,
   so it could not be signed unlisted.

## Later

- Packages (deb, rpm, Flatpak extension) for the `foxreach` program; today it is one file and
  `install.sh`.
- Signed tags.
- The limits listed under "What would change this" in `spikes/bidi/FINDINGS.md`: watch for a way to
  get trusted input on release Firefox.
