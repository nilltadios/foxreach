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
| AppImage (any distribution) | `manager/packaging/container-build.sh` | `manager/dist/foxreach-manager-VERSION-x86_64.AppImage` |
| Flatpak | `manager/flatpak/build.sh` | `manager/flatpak/repo`, `manager/flatpak/dev.nilltadios.FoxreachManager.flatpak` |

Both packages carry the foxreach program built from the same checkout: run
`uv run --python 3.15 scripts/build_binary.py` first (it writes `dist/foxreach`). Rust is 1.100.0,
pinned in `rust-toolchain.toml`, the AppImage's `packaging/Containerfile` and the Flatpak manifest;
libcosmic's commit is pinned in `Cargo.lock`.

The AppImage is built the Anylinux way (quick-sharun bundles every library, glibc included, and
uruntime packs it with DwarFS), so it runs without FUSE 2 on any distribution. The build runs in an
Ubuntu 26.04 container (podman or docker); nothing is installed on the host.

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
