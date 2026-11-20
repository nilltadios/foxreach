# foxreach manager

A desktop app (Rust, [libcosmic](https://github.com/pop-os/libcosmic)) that manages foxreach end to
end on Linux:

- **The program.** *Install* downloads the latest foxreach release for this machine (about
  109 MB), checks it against the release's `SHA256SUMS` and installs it as `~/.local/bin/foxreach`,
  the path agents and browsers are given (never a path inside the AppImage or the Flatpak, which
  would stop working). The packages carry no copy of the program. Checks for a newer release and
  applies it (`foxreach update`). Sets up the browser side (`foxreach install --agents none`).
- **The add-on in each browser profile.** Lists every Firefox-based browser (Firefox, LibreWolf,
  Floorp, Waterfox, Zen; native, AppImage or Flatpak) and each of its profiles, with the add-on's
  version there. *Install* and *Update* open the Mozilla-signed build in that profile; the browser
  asks you to confirm, and keeps it updated itself after that (`updates-signed.json`). *Remove*
  takes the add-on out of a profile whose browser is closed. Profiles set up with the full build
  from a checkout (`scripts/setup_profile.py`) are not offered the signed build, which would
  replace it.
- **Agents.** A switch per agent found on this machine adds or removes foxreach's MCP entry
  (`foxreach agents add|remove --agents ID`). An agent whose entry runs another foxreach (a
  checkout, an old path) is marked, and *Update entries* points them all at the installed one.
- **Removal.** Any part (the browser side, the copy inside a Flatpak browser, every agent's entry,
  the program, this app) or everything. Files go to the trash where they are whole files (the
  program, the AppImage); agents' settings files are backed up first, as the program always does.

Every change is made by running `foxreach` itself, the same commands the terminal offers
(`foxreach status --json` is what the window shows), so the app and the command line cannot
disagree.

## Install

**Flatpak, one click:** open [the install
file](https://nilltadios.github.io/foxreach/dev.nilltadios.FoxreachManager.flatpakref) in
Discover, GNOME Software or COSMIC Store.

**Flatpak, terminal:**

```sh
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak remote-add --user --if-not-exists foxreach https://nilltadios.github.io/foxreach/foxreach.flatpakrepo
flatpak install --user foxreach dev.nilltadios.FoxreachManager
```

Updates then come through `flatpak update` or the software store, like any other app. The
repository is signed (branch `stable`, x86_64 and aarch64) and holds the bundles of the newest
GitHub release, rebuilt by `.github/workflows/pages.yml` (see "Flatpak repository" below).

**Installed from a downloaded `.flatpak` file?** That copy never updates (the 0.4.0 bundle among
them). Add the remote (second command above), install from it, then remove the bundle's copy;
settings and data are kept, they belong to the app ID, not the copy:

```sh
flatpak install --user foxreach dev.nilltadios.FoxreachManager
flatpak uninstall --user dev.nilltadios.FoxreachManager//master
```

**Signing key fingerprint:** `E1A0 35A9 FEE5 859F C171  64C6 EDFF 56DE 84A4 9D3D`
(`manager/flatpak/signing-key.asc`). To compare it with the key the site serves:

```sh
curl -s https://nilltadios.github.io/foxreach/foxreach.flatpakrepo | sed -n 's/^GPGKey=//p' | base64 -d | gpg --show-keys
```

**Offline, or without the remote:** the release's `foxreach-manager-VERSION-ARCH.flatpak`
(`flatpak install --user FILE`) and `foxreach-manager-VERSION-ARCH.AppImage` (see "Running each
package"). The AppImage updates itself from the release after 0.4.0 ("AppImage updates").

## Build

| Package | Command | Output |
|---|---|---|
| Plain build, for development | `cargo build --release` in `manager/` | `manager/target/release/foxreach-manager` |
| AppImage (any distribution) | `manager/packaging/container-build.sh` | `manager/dist/foxreach-manager-VERSION-ARCH.AppImage` |
| Flatpak | `manager/flatpak/build.sh` | `manager/flatpak/repo`, `manager/flatpak/foxreach-manager-VERSION-ARCH.flatpak` |

ARCH is the building machine's, `x86_64` or `aarch64`: each package is built natively, never
cross-built. Neither package carries the foxreach program, so nothing outside `manager/` is built
first: the app downloads the release when asked (see "Check a build"). The AppImage build refuses
an app built for another architecture. Rust is 1.100.0, pinned in `rust-toolchain.toml`, the AppImage's
`packaging/Containerfile` and the Flatpak manifest (one archive per architecture); libcosmic's commit
is pinned in `Cargo.lock`. The AppImage's quick-sharun and uruntime (per architecture) are pinned by
commit or release and SHA-256 in `packaging/build-appimage.sh`.

The AppImage is built the Anylinux way (quick-sharun bundles every library, glibc included, and
uruntime packs it with DwarFS), so it runs without FUSE 2 on any distribution. The build runs in an
Ubuntu 26.04 container (podman or docker; `ENGINE=docker` picks docker); nothing is installed on
the host.

### In CI

`.github/workflows/manager.yml` builds all four files on GitHub's native runners (`ubuntu-26.04`
and `ubuntu-26.04-arm`) when `manager/` (or the workflow, or `bridge/agents.mjs`, `addon.mjs`,
`browsers.mjs`, which the tests read) changes
on a branch or in a pull request, or by hand (*Run workflow*). Its `test` job runs `cargo test
--locked` first (among the tests: every agent, linked app and browser foxreach knows has a bundled
icon); the package jobs wait for it, so a failing test builds and attaches nothing:

| File | Built by | Checked by |
|---|---|---|
| `foxreach-manager-VERSION-x86_64.AppImage`, `-aarch64.AppImage`, each with its `.zsync` | `packaging/container-build.sh` (docker) | `packaging/smoke-appimage.sh`: extracts it (no FUSE needed), runs `AppRun --version`, checks that no foxreach program is packed; `packaging/check-update-info.sh`: the update information below is embedded, and the `.zsync` is that file's (name, length, SHA-1) |
| `foxreach-manager-VERSION-x86_64.flatpak`, `-aarch64.flatpak` | `flatpak/build.sh` in the `ghcr.io/flathub-infra/flatpak-github-actions:freedesktop-26.08` container, with crates vendored by an earlier job (`VENDOR=0 BUILD_SCOPE=0`) | `flatpak/smoke.sh`: `flatpak install --user`, `flatpak run ... --version`, no foxreach program packed |

Each package comes with a `.sha256`; they are the run's artifacts `manager-appimage-ARCH` and
`manager-flatpak-ARCH`. On a `manager-v<version>` tag, `.github/workflows/manager-release.yml` runs
this workflow and publishes the files in a release of their own, with build attestations
(`gh attestation verify FILE --repo nilltadios/foxreach`). The files carry the version from
`Cargo.toml`, which is the manager's own, apart from foxreach's; the tag must be `manager-v` plus
that version (or that plus `-rc<N>` for a rehearsal, a prerelease never marked latest), and the
metainfo must list it. A foxreach release (`v*`, `release.yml`) builds no manager: it carries the
files of the latest release unchanged, so that whichever release is latest has both (README.md,
"Releasing"). Run (44439124322, main f44430b, 2026-11-18) passed all five jobs.

### Flatpak repository

`.github/workflows/pages.yml` publishes the GitHub Pages site: `docs/` (the add-on's update lists
and `.xpi` files, the start page `index.html` with its logo `foxreach.svg`, and `404.html`) plus a
signed OSTree repository (`repo/`, branch `stable`, both architectures),
`dev.nilltadios.FoxreachManager.flatpakref`, `foxreach.flatpakrepo` and `icon.svg`, laid out by
`flatpak/build-site.sh`. Every `docs/` file reaches the site unchanged except `index.html`: its
Flatpak part is a comment in `docs/` (from a `<!-- flatpak:begin` line to a `flatpak:end -->`
line), and `build-site.sh` switches it on, with the site address and the key fingerprint filled in,
only after it wrote the repository. So the page links to no Flatpak file when Pages serves
`main:/docs` or the fallback below deploys `docs/` alone. `flatpak/check-site-pages.sh --full|--docs-only SITE`
checks a built site before upload (both pages present, every link and asset on them in the site,
the Flatpak part on exactly when the repository is there), and after deploying, the workflow
fetches the site's root and a missing address and fails unless they answer 200 with the start page
and 404 with `404.html`. Nothing of the repository is committed: each run takes the bundles of the release
marked latest (every release carries both; else of the newest release that has both), checks their
`.sha256` and build attestation, and signs with the
`GPG_PRIVATE_KEY` secret, which must be the key in `flatpak/signing-key.asc` (refused otherwise).
It runs on a push to main that changes `docs/`, after `release.yml` or `manager-release.yml`
publishes a release (not after a rehearsal), and by hand. Unchanged bundles give the same commits (each bundle's commit is recommitted to `stable`
with its own timestamp; the appstream commits take the newest app commit's time), so a redeploy is
not an update for clients. The bundles themselves stay on branch `master`, as before.

If the Flatpak part fails, `docs/` is deployed alone, so the add-on update lists still go out, and
the run fails (a start page or 404 page that fails its check stops any deployment, this one too); the repository is then missing until the next good run. Until Pages is set to deploy
from GitHub Actions the workflow only checks `docs/` as a site without the repository, and Pages
keeps serving `main:/docs`.

Locally (needs the signing key in `GNUPGHOME`):
`manager/flatpak/build-site.sh SITE http://127.0.0.1:PORT/foxreach/ manager/flatpak/foxreach-manager-*.flatpak`,
then `manager/flatpak/check-site-pages.sh --full SITE`; serve SITE's parent folder with SITE named
`foxreach`, so the pages' `/foxreach/...` paths resolve as on GitHub.

### AppImage updates

From the release after 0.4.0, each AppImage carries update information, so AppImage updaters (Gear
Lever, `appimageupdatetool`, AppImageUpdate) find and download a newer one, as a delta:
`gh-releases-zsync|nilltadios|foxreach|latest|foxreach-manager-*-ARCH.AppImage.zsync`, defined once
in `packaging/update-info.sh`. `./foxreach-manager-*.AppImage --appimage-updateinfo` prints it.
quick-sharun's appimagetool (pinned by SHA-256 in the pinned quick-sharun) writes it into the
runtime's `.upd_info` section before packing and makes the `.zsync` from the finished file; its
`URL` is the AppImage's bare file name, so it resolves next to the `.zsync` in the same release.
The `.zsync` is attested with the other files but has no `.sha256` of its own: a
`….AppImage.zsync.sha256` would also start with the pattern. It records the AppImage's SHA-1,
which updaters check after downloading.

- "latest" is the release GitHub marks latest, never a prerelease. Whether it is a `manager-v` or
  a `v` release, it carries the manager files and foxreach's (the set it did not build is copied
  from the release that was latest before); the pattern matches one file per architecture there,
  which `scripts/check_release_complete.sh` checks before the release is published. Both release
  workflows upload to a draft and publish only when it is complete, so the latest release always
  has its `.zsync`.
- **The 0.4.0 AppImage has no update information** (its `.upd_info` is empty) and the 0.4.0
  release has no `.zsync`: replace that AppImage by hand once with a newer one; updates work from
  then on.

### Running each package

- AppImage: `chmod +x foxreach-manager-*.AppImage && ./foxreach-manager-*.AppImage`. Without FUSE:
  `./foxreach-manager-*.AppImage --appimage-extract-and-run`.
- Flatpak: `flatpak install --user foxreach-manager-*-ARCH.flatpak` (fetches
  `org.freedesktop.Platform//26.08` from Flathub if it is not installed), then
  `flatpak run dev.nilltadios.FoxreachManager`.

### aarch64 with 16 KiB pages (Asahi Linux on Apple Silicon)

A 16 KiB-page kernel cannot load an ELF file whose LOAD segments are aligned to less than 16 KiB.
The aarch64 CI jobs fail if any ELF file shipped in either package (the AppImage runtime, sharun,
every bundled library, the app) is aligned below 0x4000
(`packaging/check-elf-alignment.sh`). As found on 2026-11-18: the uruntime (v0.8.1), and
quick-sharun's sharun, helper libraries and appimagetool for aarch64 are aligned to 64 KiB, and the DwarFS inside the uruntime is jemalloc built
for pages up to 64 KiB (`--with-lg-page=16` in DwarFS's static build script for non-x86 targets),
so it should run with 4, 16 and 64 KiB pages; the app itself uses the system allocator (no jemalloc
or mimalloc in `Cargo.lock`).

**Run on 16 KiB pages, 2026-11-18** (Fedora Asahi Remix 45, aarch64, an Apple Silicon Mac,
`getconf PAGESIZE` 16384, KDE Plasma on Wayland):

- the v0.3.4 release's `foxreach-linux-arm64` (a Bun 1.4.4 compiled program, its checksum matching
  the release's `SHA256SUMS`): `foxreach version` printed 0.3.4, and `foxreach status --json`
  returned its report;
- `foxreach-manager-0.1.0-aarch64.AppImage` from CI run 44439124322 (main f44430b; the arm runner's
  page size was 4096, and the alignment check passed on 71 ELF files in the AppImage and 2 in the
  Flatpak) ran directly, mounted through FUSE (`fuse.dwarfs`), not extracted: `--version` printed,
  the bundled foxreach ran from the mount, and the window opened and drew;
- `foxreach-manager-0.1.0-aarch64.flatpak` from the same run, installed with
  `flatpak install --user --bundle` (runtime from Flathub): `--version` and the bundled foxreach ran
  in the sandbox, and the window opened and drew.

The only log noise was `MESA: error: Opening /dev/dri/card2 failed: Permission denied` (started over
SSH), which did not stop the drawing. Background: Bun's tracking issue for non-4 KiB pages on
aarch64 Linux (oven-sh/bun#17627) is still open, and Bun's automated triage of a crash report
(oven-sh/bun#32418, Bun 1.3.14) said `bun build --compile` programs did not run with non-4 KiB
pages; the Bun 1.4.4 program above does. GitHub's arm runners use 4 KiB pages, so CI cannot show
this: after a change of `.bun-version`, check again on an Asahi machine with `getconf PAGESIZE`,
`foxreach version` from the release's `foxreach-linux-arm64`, and the packages' `--version`.

## Check a build

```bash
foxreach-manager --check            # what the window would show, as text; exit 0 if it read foxreach
foxreach-manager --install-program  # install the latest release, as the Install button does
```

For the packages: `./foxreach-manager-*.AppImage --check` and
`flatpak run dev.nilltadios.FoxreachManager --check`. Its first line names the packaging the app
detected (`Plain`, `AppImage(...)`, `Flatpak { ... }`).

For testing against a throwaway home: `HOME=<folder> FOXREACH_AGENT_CLI=0`, and
`FOXREACH_MANAGER_PROGRAM="bun /path/to/bridge/cli.mjs"` to run a checkout. The program's side
is tested by `test/status_json.py`.

`FOXREACH_MANAGER_CAPTURE=/path/shot.ppm foxreach-manager --demo` (with `FOXREACH_MANAGER_TAB=browsers|foxreach`
for another tab) saves the window's own picture as a binary PPM about 2 s after its data is shown,
then exits: a screenshot of a test window kept behind your other windows, which a screen capture
cannot see.

Every icon the window shows is built in, so it looks the same on every desktop. The six that
libcosmic's own widgets look up by name in the icon theme (the window buttons, the close button of
a banner or toast, the tab bar's scroll arrows) are written at start as a private icon theme in
`$XDG_DATA_HOME/dev.nilltadios.FoxreachManager/icons/` (in the Flatpak under
`~/.var/app/dev.nilltadios.FoxreachManager/data/`), only when they changed; `--check` says where on
its `window icons:` line, or why the desktop's theme is used instead (the folder cannot be written).

Install, when no foxreach reads the status, is the app's own download (`src/host.rs`
`install_download`): `BUILDINFO.txt`, `SHA256SUMS` and `foxreach-linux-x64` or
`foxreach-linux-arm64` from `FOXREACH_RELEASE_URL` (as for `install.sh` and `foxreach update`),
else `https://github.com/nilltadios/foxreach/releases/latest/download`, with the host's curl (or
wget). The file must match its line in `SHA256SUMS` and print the version `BUILDINFO.txt` names;
otherwise nothing is changed. When a foxreach does read the status, it runs `foxreach
install-program` itself. With nothing at `~/.local/bin/foxreach` (or a program too old for
`status --json`), the app reads with what the browsers' host manifest
(`~/.mozilla/native-messaging-hosts/foxreach.json`, then `~/.librewolf/...`) runs: a checkout, or
the program at another path. A setup only agents run, with no browser manifest, shows as not
installed. A `foxreach status --json` with a higher `format` than this app reads is not read; the
app says to update it.

`packaging/check-install-download.sh` shows the download and the reading with a debug build in a
fake AppDir, a throwaway home and a local release folder (`file://`), with `--install-program` and
`--check` only (not run in CI; needs curl; the wget path is not covered, as wget does not read
`file://`).

## The Flatpak's permissions

foxreach's work happens outside any sandbox: it writes agents' settings files (`~/.claude.json`,
`~/.codex/config.toml`, ...), the browsers' native messaging manifests (`~/.mozilla`,
`~/.librewolf`), copies itself into Flatpak browsers and gives them a permission
(`flatpak override`), runs `claude mcp add`, and opens browsers. So the Flatpak runs the foxreach
program on the host with `flatpak-spawn --host`, which needs
`--talk-name=org.freedesktop.Flatpak`. **That permission lets the app run any program as you,
outside the sandbox: the Flatpak does not limit what it can do.** It is packaged as a Flatpak for
installing and updating, not for confinement. The other permissions are the usual ones for a
window (Wayland or X11, GPU) and read access to the COSMIC theme (`xdg-config/cosmic:ro`).

The AppImage has the same access as any program you run. It cleans the variables it sets for
itself (`LD_LIBRARY_PATH`, `APPDIR`, ...) before starting foxreach, so its bundled libraries never
reach foxreach, `claude` or `flatpak`.

## Removing the app

*Remove → This app* moves the AppImage file to the trash, or uninstalls the Flatpak
(`flatpak uninstall dev.nilltadios.FoxreachManager`) once the window closes. By hand: delete the
AppImage, or run that command.
