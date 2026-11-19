# foxreach manager

A desktop app (Rust, [libcosmic](https://github.com/pop-os/libcosmic)) that manages foxreach end to
end on Linux:

- **The program.** Installs the foxreach program it carries as `~/.local/bin/foxreach`, the path
  agents and browsers are given (never a path inside the AppImage or the Flatpak, which would stop
  working). Checks for a newer release and applies it (`foxreach update`). Sets up the browser
  side (`foxreach install --agents none`).
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

## Build

| Package | Command | Output |
|---|---|---|
| Plain build, for development | `cargo build --release` in `manager/` | `manager/target/release/foxreach-manager` |
| AppImage (any distribution) | `manager/packaging/container-build.sh` | `manager/dist/foxreach-manager-VERSION-ARCH.AppImage` |
| Flatpak | `manager/flatpak/build.sh` | `manager/flatpak/repo`, `manager/flatpak/foxreach-manager-VERSION-ARCH.flatpak` |

ARCH is the building machine's, `x86_64` or `aarch64`: each package is built natively, never
cross-built. Both packages carry the foxreach program built from the same checkout for that
architecture, as `dist/foxreach`: `uv run --python 3.15 scripts/build_binary.py` builds it for this
machine, `--target foxreach-linux-x64` or `--target foxreach-linux-arm64` the release's file
(`dist/release/`, copy it to `dist/foxreach`). The AppImage build refuses a program or app built
for another architecture. Rust is 1.100.0, pinned in `rust-toolchain.toml`, the AppImage's
`packaging/Containerfile` and the Flatpak manifest (one archive per architecture); libcosmic's commit
is pinned in `Cargo.lock`. The AppImage's quick-sharun and uruntime (per architecture) are pinned by
commit or release and SHA-256 in `packaging/build-appimage.sh`.

The AppImage is built the Anylinux way (quick-sharun bundles every library, glibc included, and
uruntime packs it with DwarFS), so it runs without FUSE 2 on any distribution. The build runs in an
Ubuntu 26.04 container (podman or docker; `ENGINE=docker` picks docker); nothing is installed on
the host.

### In CI

`.github/workflows/manager.yml` builds all four files on GitHub's native runners (`ubuntu-24.04`
and `ubuntu-24.04-arm`) when `manager/` (or the workflow, `scripts/build_binary.py`,
`.bun-version`) changes on a branch or in a pull request, or by hand (*Run workflow*):

| File | Built by | Checked by |
|---|---|---|
| `foxreach-manager-VERSION-x86_64.AppImage`, `-aarch64.AppImage` | `packaging/container-build.sh` (docker) | `packaging/smoke-appimage.sh`: extracts it (no FUSE needed), runs `AppRun --version`, checks the packaged foxreach is the same file and runs |
| `foxreach-manager-VERSION-x86_64.flatpak`, `-aarch64.flatpak` | `flatpak/build.sh` in the `ghcr.io/flathub-infra/flatpak-github-actions:freedesktop-26.08` container, with crates vendored by an earlier job (`VENDOR=0 BUILD_SCOPE=0`) | `flatpak/build.sh`'s own check (packaged foxreach is the same file and runs), then `flatpak/smoke.sh`: `flatpak install --user`, `flatpak run ... --version`, the bundled foxreach run in the sandbox |

Each comes with a `.sha256`; they are the run's artifacts `manager-appimage-ARCH` and
`manager-flatpak-ARCH`. On a `v*` tag, `release.yml` runs this workflow too and attaches the files
to the release with build attestations (`gh attestation verify FILE --repo nilltadios/foxreach`),
after the release's own files; a failure there leaves the rest of the release as it was. The files
carry the manager's own version from `Cargo.toml`, which does not follow foxreach's yet.

### Running each package

- AppImage: `chmod +x foxreach-manager-*.AppImage && ./foxreach-manager-*.AppImage`. Without FUSE:
  `./foxreach-manager-*.AppImage --appimage-extract-and-run`.
- Flatpak: `flatpak install --user foxreach-manager-*-ARCH.flatpak` (fetches
  `org.freedesktop.Platform//26.08` from Flathub if it is not installed), then
  `flatpak run dev.nilltadios.FoxreachManager`.

### aarch64 with 16 KiB pages (Asahi Linux on Apple Silicon)

A 16 KiB-page kernel cannot load an ELF file whose LOAD segments are aligned to less than 16 KiB.
The aarch64 CI jobs fail if any ELF file shipped in either package (the AppImage runtime, sharun,
every bundled library, the app, the foxreach program) is aligned below 0x4000
(`packaging/check-elf-alignment.sh`). As found on 2026-11-18: the uruntime (v0.8.1), and
quick-sharun's sharun, helper libraries and appimagetool for aarch64 are aligned to 64 KiB, and the DwarFS inside the uruntime is jemalloc built
for pages up to 64 KiB (`--with-lg-page=16` in DwarFS's static build script for non-x86 targets),
so it should run with 4, 16 and 64 KiB pages; the app itself uses the system allocator (no jemalloc
or mimalloc in `Cargo.lock`). **Not known to work yet: the foxreach program.** Bun's tracking issue
for non-4 KiB pages on aarch64 Linux (oven-sh/bun#17627) is still open, with "check it works on
16k" unticked; Bun's automated triage of a crash report (oven-sh/bun#32418, Bun 1.3.14) says
`bun build --compile` programs do not run with non-4 KiB pages yet, while a maintainer wrote earlier
(oven-sh/bun#6241) that JavaScriptCore expects 16 KiB pages on this target. Bun's release notes from
1.3.12 to 1.4.4 say nothing about page sizes, and nobody has reported Bun 1.4.4 on 16 KiB pages
either way. If the program fails there, the manager still opens, but the program it installs does
not run. GitHub's arm runners are expected to use 4 KiB pages (the jobs print `getconf PAGESIZE`),
so only a run on an Asahi machine can tell: `getconf PAGESIZE` (16384), then `foxreach version`
with the release's `foxreach-linux-arm64`, and the packages' `--version` and `--check`.

## Check a build

```bash
foxreach-manager --check            # what the window would show, as text; exit 0 if it read foxreach
foxreach-manager --install-program  # install the bundled program, as the Install button does
```

For the packages: `./foxreach-manager-*.AppImage --check` and
`flatpak run dev.nilltadios.FoxreachManager --check`. Its first line names the packaging the app
detected (`Plain`, `AppImage(...)`, `Flatpak { ... }`).

For testing against a throwaway home: `HOME=<folder> FOXREACH_AGENT_CLI=0`, and
`FOXREACH_MANAGER_BUNDLED=<path to a foxreach build>` to give a plain build a program to install,
or `FOXREACH_MANAGER_PROGRAM="bun /path/to/bridge/cli.mjs"` to run a checkout. The program's side
is tested by `test/status_json.py`.

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
