# foxreach

Lets any local agent that speaks MCP operate the tabs of a Firefox-based browser, with real input. It
has no telemetry, no account, no vendor lock-in and no open network port.

```
agent ── stdio MCP ── foxreach mcp ── private Unix socket ── foxreach host ── native messaging ── add-on
(one server per agent)                (mode 0600)            (started by the browser)
```

(On Windows, a named pipe that proves itself with a private token: see [Windows](#windows).)

There are two builds of the add-on. Pick by browser:

| Browser | Build | Input |
|---|---|---|
| Firefox release and beta, distro `firefox` packages, the Firefox Flatpak | Signed build, see [Install](#install) | Script events (`isTrusted=false`), which a few sites ignore; no `drag`, `read_console`, `handle_dialog`, file drops |
| LibreWolf; Firefox Developer Edition, Nightly | Full build, see [Setup from a checkout](#setup-from-a-checkout). Tested: LibreWolf 159.0.1 (AppImage, Flatpak) and 160.0 (macOS app), Developer Edition 161.0b1 | Real input, all tools |
| Firefox ESR, Floorp, Waterfox, Zen | Either; the full build is expected to work, since they honor the same two prefs, but is not tested | |

Mozilla signs privileged code only for its own add-ons, so the full build cannot be signed, and
browsers that insist on signatures get the build without it.

## Install

For any Firefox-based browser on Linux or macOS (x86-64 or arm64). Nothing else is needed: no Bun, no `uv`.
On Windows, see [Windows](#windows).

1. **The program.** One file, which is the MCP server and the part the browser starts. Download
   [install.sh](install.sh), read it, and run it:

   ```bash
   sh install.sh
   ```

   It downloads `foxreach` from the latest [release](https://github.com/nilltadios/foxreach/releases)
   into `~/.local/bin`, checks it against the release's checksums, and runs `foxreach install`,
   which writes the native messaging manifest that lets the browser start it. Installed Flatpak
   browsers are prepared too ([how](#flatpak-browsers)). Or download `foxreach-linux-x64`,
   `foxreach-linux-arm64`, `foxreach-darwin-arm64` (Apple silicon) or `foxreach-darwin-x64` (Intel
   Mac) yourself, make it executable, and run `./foxreach install`. On a Mac, the manifest goes to
   `~/Library/Application Support/Mozilla/NativeMessagingHosts`, which Firefox and LibreWolf both
   read. The Intel build is signed ad hoc on the Mac it is installed on (`install.sh` and
   `foxreach update` do this; by hand: `codesign --force --sign - foxreach`); it was run under
   Rosetta, not yet on an Intel Mac. A file downloaded with a web browser is marked as from the
   internet, and macOS will not run an ad hoc signed program with that mark: before running it,
   clear it with `xattr -d com.apple.quarantine foxreach`. (`install.sh` downloads with `curl`,
   which sets no such mark.)
2. **The add-on.** Open `foxreach-<version>-signed.xpi` from the release in the browser and accept.
   Then click the foxreach toolbar button and choose the sites agents may use.
3. **The agent.** `foxreach install` also adds the MCP server (`foxreach mcp`) to every agent it
   finds on the machine, by writing the one entry each agent's own settings file needs. No agent's
   command-line program is needed for this:

   | Agent | Settings file |
   |---|---|
   | Claude Code, the Code tab of Claude desktop, T3 Code | `~/.claude.json` (through `claude mcp add -s user` when `claude` is installed) |
   | Claude desktop (chat) | `~/.config/Claude/claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`) |
   | Codex, the ChatGPT app, T3 Code | `~/.codex/config.toml` |
   | Antigravity (app and `agy`) | `~/.gemini/config/mcp_config.json` |
   | opencode (CLI and app) | `~/.config/opencode/opencode.json` |
   | Pi | `~/.pi/agent/mcp.json` |
   | Hermes | `~/.hermes/config.yaml` |
   | Cursor | `~/.cursor/mcp.json` |
   | ZCode | `~/.zcode/cli/config.json` |
   | OpenClaw | `~/.openclaw/openclaw.json` (`OPENCLAW_CONFIG_PATH`) |
   | GitHub Copilot CLI | `~/.copilot/mcp-config.json` |
   | Qwen Code | `~/.qwen/settings.json` |
   | Kimi Code | `~/.kimi-code/mcp.json` (`~/.kimi/mcp.json` on older releases) |
   | MiMo Code | `~/.config/mimocode/mimocode.json` |
   | MiniMax Code (`mcode`) | `~/.minimax/mcp.json` |
   | Mistral Vibe | `~/.vibe/config.toml` |
   | DeepSeek Harness (`dsh`) | `~/.dsh/profiles/web/cordis.patch.yml` |
   | Grok Build (`grok`) | `~/.grok/config.toml` (Grok also reads `~/.claude.json`) |
   | Muse Code (`muse`) | `~/.config/muse/settings.json` (`"schema_version": 1` is added to a new file) |

   Only the `foxreach` entry is added; everything else in each file stays as it was, and each file
   is backed up first (`<file>.bak-<date>`). A file it cannot edit safely, such as JSON with
   comments, is left alone and the entry to add by hand is printed. `foxreach agents` lists what
   it found; `foxreach install --agents codex,pi` names the agents instead, and `--agents none`
   skips this step. For any other agent, give it the command `~/.local/bin/foxreach mcp`.
   Agents that are running pick the server up in their next session (Claude desktop and
   Antigravity: after a restart). OpenClaw, GitHub Copilot CLI, Qwen Code, Kimi Code, MiMo Code,
   MiniMax Code, Mistral Vibe and DeepSeek Harness were checked on 2026-11-12 by installing each
   in a throwaway container and confirming with its own tools that it reads foxreach's entry
   (`test/agents_container/run.sh`).

The first time an agent calls, the browser asks whether to allow it.
`foxreach uninstall` removes what `foxreach install` wrote, including the agents' entries
(`--keep-agents` keeps those).

## Windows

The signed add-on with the `foxreach` program, as above. What has run: Windows 11 (x64), LibreWolf
with the signed build, foxreach from a checkout (Bun and `bridge\cli.mjs`, see "From a checkout"
below), and the release program `foxreach-windows-x64.exe` built here: the browser started the
host, an agent's calls went through it. `install.ps1`, `foxreach update` and `install-program` ran
against local release folders. Not yet run: a published Windows release, the arm64 build (no arm64
machine), and Firefox, Floorp, Waterfox and Zen (same registry key and profile layout, found by the
same code with stand-ins, so expected to work).

1. **The program.** Once a release carries the Windows builds: download [install.ps1](install.ps1),
   read it, and run it:

   ```powershell
   powershell -ExecutionPolicy Bypass -File install.ps1
   ```

   It downloads `foxreach-windows-x64.exe` (or `-arm64.exe`) from the latest release, checks it
   against `SHA256SUMS`, puts it at `%LOCALAPPDATA%\Programs\foxreach\foxreach.exe`, adds that
   folder to your PATH, and runs `foxreach install`. Or download the file yourself, rename it
   `foxreach.exe`, and run `foxreach.exe install`.
2. **The add-on** and **the agent**: as in [Install](#install). Agents' settings files are the same
   paths under your user folder (`C:\Users\<you>\.claude.json`, `.codex\config.toml`, ...), except
   Claude desktop: `%APPDATA%\Claude\claude_desktop_config.json`, or for the Microsoft Store build
   `%LOCALAPPDATA%\Packages\Claude_<id>\LocalCache\Roaming\Claude\` (where that build's writes to
   `%APPDATA%` land; not yet confirmed that Claude desktop reads foxreach's entry there).
   Tor Browser and Mullvad Browser are not set up on Windows. They probably read the same registry
   key, so once foxreach is installed they could start it too; the host would call them Firefox.

What differs from Linux and macOS:

- **Registration.** Browsers find the host through the registry: `foxreach install` writes the
  default value of `HKCU\Software\Mozilla\NativeMessagingHosts\foxreach` (every Firefox-based
  browser reads this key), naming `%LOCALAPPDATA%\foxreach\foxreach.json`, whose `path` is
  `foxreach.exe` itself. `foxreach uninstall` deletes both.
- **Named pipes, not a socket.** Each browser's host listens on its own pipe
  (`\\.\pipe\foxreach-<16 hex digits>`), listed with a random token in
  `%LOCALAPPDATA%\foxreach\run\browser-<key>.json`, a folder only you can read. Any local user can
  see pipe names and take one that is free, so there is no fixed pipe, and an agent sends a pipe
  nothing but a challenge until the pipe proves it holds the token from that private file. A pipe
  that cannot prove it is not listed, and its stale info file is removed (`test/windows_hosts.py`
  checks a squatted pipe gets nothing else).
- **Which profile.** With no `/proc`, the host takes the profile from the browser's command line
  (`-profile <folder>`, or `-P <name>` looked up in `profiles.ini`), else the one profile of that
  browser whose `parent.lock` is held. If two are open and neither is named, it cannot tell: it
  then refuses agents if either was stopped with `foxreach addon stop`.
- **Files agents may upload or save.** `AppData` (every browser's cookies and saved passwords,
  apps' settings) is refused, as `~/Library` is on macOS, and so are `\\server\share` paths and
  device paths outside a drive (`\\.\pipe\...`). A path is resolved first, so `\\?\C:\...` is
  judged as the plain `C:\...` it names.
- **Install from a normal terminal.** An app from the Microsoft Store (Claude desktop, for one)
  runs in a sandbox whose new files in AppData and registry keys only that app sees. `foxreach
  install` run by an agent inside such an app would register foxreach where no browser looks, so
  it refuses there and says so; run it from a terminal opened from the Start menu. Agents inside
  such an app still reach the browser (they see the hosts' info files and pipes).
- **Smart App Control.** A Windows with Smart App Control on may block unsigned programs that have
  no reputation yet, and `foxreach.exe` is not signed with a code-signing certificate. On the
  machine it was tried on, the same build was blocked once ("An Application Control policy has
  blocked this file") and ran later, and the arm64 build was blocked. Where it is blocked the
  browser cannot start it either, and `install.ps1` and `foxreach update` stop before replacing
  anything. Run foxreach from a checkout instead (below); Bun itself is signed.
- **From a checkout** (`bun bridge\cli.mjs install`): the manifest points at
  `%LOCALAPPDATA%\foxreach\foxreach-host.bat`, which runs Bun on `bridge\cli.mjs host`. The
  Python setup scripts for the full build (`scripts/setup_profile.py`, `build_install.py`) and the
  manager app are not ported to Windows yet, nor Tor Browser.
- **Updating.** `foxreach update` moves the running program aside to `foxreach.exe.previous`
  (Windows lets nobody replace a running program) and puts the new one in its place. A
  `.previous` still in use from an earlier update is kept as `foxreach.exe.previous-<number>`
  and deleted by a later update once it has closed.

## Setup from a checkout

For the full build, with real input. Needs Linux or macOS, `uv`, Bun or Node.js, and a browser that
accepts unsigned add-ons (the table above). How the browser was installed does not matter: a distro
package, a tarball, an AppImage, a Flatpak (Flatpak has its own section below), or an app in
`/Applications` or `~/Applications` on macOS, named by its folder (`--browser /Applications/LibreWolf.app`).
List what the scripts find on this machine:

```bash
uv run --python 3.15 scripts/browsers.py
```

Close the browser, then install into a profile. Leave out `--profile` to create a dedicated, logged-out
one at `~/.local/share/foxreach/profile`, which `scripts/foxreach-browser` opens.

```bash
uv run --python 3.15 scripts/setup_profile.py --profile /path/to/profile --browser /path/to/browser
```

On macOS the browser runs the native host as itself when opened from the Dock, and macOS keeps apps
out of Documents, Desktop and Downloads until each is allowed. So a checkout there could not start, and
`build_install.py` runs the host from a copy of the bridge in `~/.local/share/foxreach`, refreshed by
each `build_install.py` or `update.py`. Agents still run `bridge/cli.mjs mcp` from the checkout,
so an app that has not been allowed into its folder cannot start them; the single-file program in
`~/.local/bin` (see [Install](#install)) has no such limit.

`--browser` takes a path, a command on `PATH`, or `flatpak:<application id>`. Leave it out and setup
uses `FOXREACH_BROWSER`, else the browser whose folder the profile is in, else the only browser
installed; with several and no hint it lists them and stops. It prints the one it chose.

Setup backs up that profile's `user.js` and `prefs.js` beside themselves. It uses no automation:
Marionette deletes a user's own values for the prefs it manages. Its headless starts would disturb
"restore previous session" data and make the next normal start offer Troubleshoot Mode, so setup saves
the session and resets the startup-crash counters when it finishes.

Never start a copy of a profile that belongs to a profile group: the browser lists the copy as your
default in `profiles.ini`, and it won't open once the copy is gone. Setup refuses such a copy.

If the browser ever offers **Refresh**, don't accept it to fix foxreach: it removes add-ons and customizations.

Only one running browser can serve agents: the first one opened. Close it to switch.

If a tool says it cannot reach the browser, the message names the cause it found: no Firefox-based
browser running (open it), or one running whose add-on has not connected (wrong profile, add-on
disabled or paused, or still starting). It recognizes Firefox, LibreWolf, Floorp, Waterfox, Zen and
other Firefox derivatives by their executable; [test/connect_errors.py](test/connect_errors.py) checks
each case.

Then connect the agents. This adds the MCP server to every agent found (the table under "Install"),
with full paths, since apps started from the desktop may not have Bun on `PATH`; it leaves the
browser's manifest to `setup_profile.py`:

```bash
~/.bun/bin/bun bridge/cli.mjs agents add
```

`agents` alone lists what it finds; `agents add --agents codex` names agents; `agents remove`
takes the entries out. For any other agent, use `~/.bun/bin/bun /path/to/foxreach/bridge/cli.mjs mcp`
as a stdio MCP server.

### Tor Browser (not recommended)

An add-on that agents drive makes a Tor Browser look and behave unlike other Tor Browsers, which
can undo the anonymity Tor gives; the Tor Project advises against adding add-ons. foxreach still
works there if you choose to (tested with Tor Browser 15): it is never set up by default.

- Connect it by name, with its `Browser` folder (`foxreach addon` lists it), or with Connect in the
  manager app: `foxreach install --browser tor:<path>/tor-browser/Browser --agents none`. Tor
  Browser reads native-messaging manifests from its own data folder
  (`Browser/TorBrowser/Data/Browser/.mozilla/native-messaging-hosts`), not from `~/.mozilla`.
- Install the add-on in it, and when it asks, allow it in private windows: Tor Browser is always
  in private browsing and runs no add-on that is not allowed there.
- Found in torbrowser-launcher's folder (`~/.local/share/torbrowser`) and in unpacked downloads
  named `tor-browser*` in the home folder, Downloads, Applications or `/opt`. The Flatpak of
  torbrowser-launcher is not supported yet.
- `foxreach uninstall --part host` removes its manifest too.

### Flatpak browsers

```bash
uv run --python 3.15 scripts/setup_profile.py --browser flatpak:io.gitlab.librewolf-community
```

`foxreach install` does the same with a copy of the program itself, for the Flatpak browsers it
finds; `foxreach install --browser flatpak:<id>` prepares only that one.

A Flatpak browser starts the native host inside its sandbox, which sees neither this folder nor your
Bun. So setup copies the bridge and Bun into `~/.var/app/<id>/foxreach`, registers the host in the
Flatpak's own `native-messaging-hosts` folder, and gives the Flatpak one permission: the folder
`$XDG_RUNTIME_DIR/foxreach`, where the socket for agents is
(`flatpak override --user --filesystem=xdg-run/foxreach <id>`). Agents still use `bridge/mcp.mjs`
from this folder. It needs Bun; Node.js does not run inside the sandbox. A Flatpak can only open
profiles inside its own folder, so the dedicated profile is `~/.var/app/<id>/foxreach/profile`.

Tested with two Flatpaks ([test/flatpak_browser.py](test/flatpak_browser.py)): each finds the manifest,
starts the host in its sandbox, and an agent outside reads a page, clicks and takes a screenshot.

- **LibreWolf 159.0.1-1** (`io.gitlab.librewolf-community`): the full add-on, with trusted input.
  `setup_profile.py` installed and verified its dedicated profile.
- **Firefox 159.0.1** (`org.mozilla.firefox`): an official release, which refuses unsigned add-ons. The
  test loads the build without privileged code for the run; `setup_profile.py` cannot install into
  it. It takes the signed build.

Both also pass with the single-file program in place of the checkout's bridge
(`test/flatpak_browser.py flatpak:<id> --program dist/foxreach`).

### Runtime

The bridge has no dependencies. In tests on Bun it started in 28 ms using 23 MiB, against 140 ms and
50 MiB on Node.js ([compare_runtimes.py](test/compare_runtimes.py)). Node.js is the fallback if Bun is
missing; to choose it, run `FOXREACH_RUNTIME=node uv run --python 3.15 scripts/build_install.py` and use
`node` in the commands above. The host's path is written at build time, so rerun that script if the
runtime or this folder moves.

## Updating

foxreach has two parts. The add-on lives in the browser profile; the `foxreach` program (the MCP
server agents run, the native host the browser runs, and the installer) is one file. They should be
the same version.

- **The add-on updates itself.** Its `update_url` points at `docs/updates.json` on GitHub Pages
  (the signed build: `docs/updates-signed.json`, which holds only signed builds, so neither build
  ever replaces the other). The browser checks about once a day and installs a newer version in the
  background. Firefox has this on by default and LibreWolf leaves it on (`extensions.update.enabled`).
  The signed build from 0.4.0 is the one listed on addons.mozilla.org, which has no `update_url`:
  Firefox then asks addons.mozilla.org for updates. Signed 0.3.x copies (unlisted) reach 0.4.0
  through `docs/updates-signed.json` and from there update the same way.
- **The program, installed from a release, updates with one command:**

  ```bash
  foxreach update            # --check only says whether a newer version exists
  ```

  It downloads the latest release's build for this machine, checks it against the release's
  `SHA256SUMS` (as `install.sh` does), checks that it runs and reports the expected version, keeps
  the old program as `foxreach.previous`, and replaces it in one step. Then the new program refreshes
  what `foxreach install` wrote: the host wrapper, the browsers' manifests and the copies inside
  Flatpak browsers. Agent settings already point at the program's path and are left alone. Agents
  that are running keep the program they started and use the new one in their next session. To go
  back: `mv ~/.local/bin/foxreach.previous ~/.local/bin/foxreach && foxreach install --agents none`.
  Running `install.sh` again does the same download and check.
- **A checkout updates with git.** Run the updater with the browser closed:

  ```bash
  uv run --python 3.15 scripts/update.py            # --dry-run to preview
  ```

  It pulls, rebuilds, re-registers the native host, and reinstalls the add-on in every profile that
  setup installed into (recorded in `~/.local/state/foxreach/profiles`) whose copy is older. Agents
  use the new bridge from their next session.

If the add-on has updated itself but the program has not, the `status` tool shows both versions and a
`warning` that names the right command for this install (`foxreach update`, or `scripts/update.py`
in a checkout), and the first tool call of each agent session ends with the same note. A build made
here and not signed has no `update_url`.

## Releasing

1. Raise `version` in `extension/manifest.json` (the bridge reads it from there too) and, to the same
   number, in `manager/Cargo.toml`, the `foxreach-manager` entry in `manager/Cargo.lock` and a new
   `<release>` in `manager/resources/dev.nilltadios.FoxreachManager.metainfo.xml`; commit. The manager
   follows foxreach's version: `release.py` and the release workflow refuse when the two differ.
2. For release Firefox, have Mozilla sign that version: `uv run --python 3.15 scripts/sign.py`
   (`--dry-run` checks without sending). It needs an API key from addons.mozilla.org and sends the
   add-on's files there; the signed file lands in `dist/signed/`. `--channel listed` submits it for
   the public listing instead, with the text in [amo/](amo/LISTING.md).
3. `uv run --python 3.15 scripts/release.py` adds the add-ons and their update entries to `docs/`,
   builds the release's files into `dist/release/`, and prints the commit, tag and push commands.
   It changes no remote.
4. Pushing the tag starts [the release workflow](.github/workflows/release.yml), which builds the
   same files from the tag on GitHub's machines, attests them, and publishes the release.
5. Once per repository, serve `docs/` from `main` with GitHub Pages.
6. After the push, `uv run --python 3.15 scripts/release.py --verify` checks that Pages serves the new
   entries and that the files' hashes match, and `scripts/verify_release.py` checks the release
   against its source.
7. A listed build is approved days later, when step 3 refuses the version. Download its signed file
   from the add-on's versions page on addons.mozilla.org (or `current_version.file.url` of
   `https://addons.mozilla.org/api/v5/addons/addon/foxreach@foxreach.local/`) to
   `dist/signed/foxreach-<version>-signed.xpi` and check it against the hash shown there. Then
   `uv run --python 3.15 scripts/release.py --signed` adds only that file and its entry in
   `docs/updates-signed.json` (it refuses a file that is not this source's build plus Mozilla's
   signature); commit `docs/`, push, and run `release.py --verify` again.

[test/update.py](test/update.py) proves the update path on a throwaway profile: the browser's
background check installs a newer version from an `updates.json` of the same layout, and the bridge
warns that the versions differ. [test/release.py](test/release.py) runs steps 3, 6 and 7 in a
throwaway copy of the repository, publishing nothing.

## Checking a release

Builds are reproducible: the same commit gives the same add-on, byte for byte, and with the Bun
version in [.bun-version](.bun-version) the same `foxreach` program.

```bash
uv run --python 3.15 scripts/verify_release.py 0.3.0
```

That downloads the release, rebuilds every file from the tag, and compares. The signed add-on must
hold exactly the files the source builds, plus Mozilla's signature. Each release has `SHA256SUMS`,
and `BUILDINFO.txt` with the commit and the Bun version. To check who built a file:

```bash
gh attestation verify foxreach-linux-x64 --repo nilltadios/foxreach
```

Releases up to 0.2.0 predate this and cannot be rebuilt byte for byte. Tags are not signed.

## Undo

With the browser closed, this removes the add-on and both prefs. Leave out `--apply` to preview it.

```bash
uv run --python 3.15 scripts/remove_from_profile.py --profile /path/to/profile --apply
```

To clear the rest, remove `~/.local/share/foxreach`, `~/.local/state/foxreach` and
`~/.mozilla/native-messaging-hosts/foxreach.json` (also in `~/.librewolf/native-messaging-hosts`, if you
have that folder; on macOS, `~/Library/Application Support/Mozilla/NativeMessagingHosts/foxreach.json`). For a Flatpak browser, remove `~/.var/app/<id>/foxreach` and `foxreach.json` from the
`native-messaging-hosts` folders under `~/.var/app/<id>`, and withdraw the permission with
`flatpak override --user --nofilesystem=xdg-run/foxreach <id>`.

## Tools

`status`, `browser_open`, `tabs_list`, `tabs_create`, `tabs_close`, `tabs_select`, `navigate`, `read_page`, `find`,
`get_page_text`, `click`, `hover`, `drag`, `type_text`, `press_key`, `scroll`, `scroll_to`,
`form_input`, `file_upload`, `screenshot`, `wait`, `wait_for`, `javascript`, `read_console`,
`read_network`, `resize_window`, `handle_dialog`, `record`, `batch`.

Each tool's description is in [bridge/tools.mjs](bridge/tools.mjs).

`read_page` keeps its replies short: a link address over 40 characters shows only where it goes
(`href=→ example.org`, or `href=→` for this site), a list item or cell whose text is just its
children's is not repeated, and layout-only elements carry no id. `full_links: true` shows every
address whole; `find` still matches addresses.

`find` lists elements that match most of the query's words. When none does, it lists up to five
that match some of them, under "Nothing matches most of ...", instead of nothing.

A click or hover by ref scrolls the element into view only when it is not already in view, and
after a scroll measures it again until it stops moving: virtualized lists (mail inboxes, feeds)
redraw a frame or more after scrolling. A click by ref then says when the press landed outside the
element, and a right-click says whether the page took it (handled `contextmenu`, as a page with its
own menu does). A ref whose element the page has replaced with an equal one (same id and role, or
same role and name, and only one such element) is used again, and the reply says so; with no single
match the ref is refused as stale.

Pages that draw on animation frames (virtualized lists in mail apps and feeds) fill slowly in a
background tab, which gets about one frame a second, and not at all while the browser paints
nothing (the display is off or asleep). `status` with a `tabId` measures that page over 1.5 s
(`page.framesPerSecond`, `page.hidden`, and a `note` when it matters), and `wait_for` adds the same
note when it times out.

`status` also lists the last connections (`history`, newest first) and how each ended: `quit` (a
normal quit), `lost` (the browser exited without one: a crash, a kill, a SIGTERM such as at logout;
session-only sign-ins are then gone), `addon` (the add-on restarted while the browser stayed open),
`closed` (the build without privileged code cannot tell a quit from a crash). When no browser is
connected, the error ends with the last one. Each native host writes this to `history.jsonl` in
the socket folder (`$XDG_RUNTIME_DIR/foxreach`), so it is cleared at logout or reboot;
[test/history.py](test/history.py) checks each case on a throwaway profile.

`browser_open` starts the browser when none is connected, on a profile in foxreach's record (the
one `setup_profile.py` keeps, which the not-connected error lists), with the browser recorded for
it, and waits up to 45 s for the add-on to connect. An agent cannot pass a command or a profile
that is not recorded; a profile that a running browser already holds is refused, so a second copy
is never started on it. With several recorded profiles it takes `profile`, else the user's default
(`foxreach browsers default`).

Tabs an agent opens stay in the user's browser until closed. `tabs_list` marks the ones this
session opened, `tabs_create` reminds the agent once it has five or more open, and
`tabs_close` with `mine: true` closes every tab this session opened (and the popups its pages
opened), never a tab the user shared or another agent's.

`status` names the browser program in `app` (LibreWolf, Zen...), which the host reads from the
process that started it: forks such as LibreWolf tell add-ons they are Firefox.

`type_text` and `press_key` say which element received the keys. They refuse to type, or to send
Delete or Backspace, when focus is not on a text field, so keys meant for a search box cannot act
as page shortcuts (such as deleting the selected messages in a mail app). `allow_non_text: true`
overrides this for keyboard-driven pages. `type_text` also warns when the field's value did not change.

`type_text`'s `delay_ms` types one key at a time with a pause between, for PIN and code boxes that
move focus to the next box after each digit and lose keys sent faster. The keys follow focus, also
into another add-on's prompt inside the page (a password manager's PIN box), which `read_page` and
`find` cannot see but screenshots show; see [spikes/other-addon-overlay](spikes/other-addon-overlay/FINDINGS.md).

`press_key` and the `modifiers` of `click` and `drag` take `mod`, the key shortcuts use on the browser's
system: Cmd on macOS, Ctrl elsewhere. The MCP server turns it into `meta` or `ctrl` before the add-on
sees it, so an add-on from before `mod` gets a key it knows. `mod+a` selects all on both; `ctrl` is always the Control key,
which on macOS moves to the start of a line instead.

The agent's page gets focus and blur events in a background tab, or while the browser is behind
another app. Firefox sends none there, so widgets that track focus through those events (MUI's
Autocomplete, many React comboboxes) dropped typed text. After a click, drag or key moves focus,
foxreach sends focus and focusin to the element that now has it (and blur and focusout to the one
it left), in that document only and only while it lacks real focus. The browser window is not
activated or raised, the user's tabs see nothing, and the app the user is in keeps the keyboard.

`javascript` runs in the page's own world, like the browser's developer console: a bare assignment
or a `var` sets a global on the page's `window`, so a script started in one call (a loop that counts
its progress, say) can be read by the next; `let` and `const` stay local to each call. For work that
is more than a line or two:

- `file`: an absolute path to a `.js` or `.mjs` file, run before `code`, for a helper library kept
  on disk instead of pasted into calls. Assign helpers to `window` to keep them for later calls.
- `args`: any JSON value, available to the code as the constant `args`.
- `save_to`: writes the whole result (up to 16 MiB) to a `.json`, `.txt`, `.csv`, `.tsv`, `.md` or
  `.log` file and replies with its size and first lines, so large results need not pass through the
  agent's context.
- `timeout`: seconds to wait for the result, 30 by default, at most 600.

`file` and `save_to` refuse hidden files and folders, also through a link, and `~/Library` on macOS
except the cloud folders in it (Dropbox, OneDrive and Google Drive in `~/Library/CloudStorage`, and
iCloud Drive), where documents are kept.

## What you control

The toolbar button shows the bridge's status and recent agent activity, and it has these controls:

- **Pause** stops all agent access at once.
- **Several browsers at once:** every browser with the add-on serves agents on its own socket in
  `$XDG_RUNTIME_DIR/foxreach`. With one connected, agents use it. With several, they use the
  profile set with `foxreach browsers default --profile <folder>` (or a profile's ⋮ menu in the
  manager app), or else they must pick one with `browser_select` (ids from `browsers_list` or
  `foxreach browsers`); the choice lasts for that agent's session.
- **Stop agents in one profile**, add-on still installed: `foxreach addon stop --profile <folder>`
  (or the profile's ⋮ menu in the manager app); `foxreach addon start --profile <folder>` undoes
  it. It leaves a file named `foxreach-agents-stopped` in the profile, and the native host refuses
  every agent call there at once, even while the browser runs. The host finds its profile from the
  `.parentlock` file the browser that started it holds open.
- **Agents:** a program that connects for the first time waits until you answer its request there:
  Allow (until it disconnects), Always (that name, from now on), or Refuse. Its call waits up to a
  minute, then fails and says why. `status` needs no approval; for an agent that would have to
  wait, it asks you right away and tells the agent to point you to the browser (`agentAccess`:
  `allowed`, `waiting` or `refused`; `status` with `ask: false`, as `foxreach test` sends, only
  reports `unasked`), so an agent that starts with `status` gets you to answer
  before its first real step. The request opens a small window of its own, since the browser
  may keep the button in its extensions menu; the button also shows `?`. The name is what
  the program calls itself, so this keeps out an agent you did not mean to let in, not a program
  that lies about its name. You can switch asking off.
- **Sites agents may use:** every site, or only the ones you list (with their subdomains). On any
  other site an agent can neither open a page nor read or act on one, and a frame from such a site
  is skipped (and blacked out whole in screenshots), as are blank frames inside it. A refused site
  shows as a request you can allow with one click. Sharing a tab allows
  its site. The list limits what agents read and operate; it does not stop a listed page from
  loading its own images or scripts from elsewhere.
- **Tabs:** agents only see tabs they opened, tabs those tabs opened, and tabs you tick. Other tabs
  stay hidden, including their titles and URLs. Agent tabs are in an orange `agent` tab group.
- **Blocked sites** keeps agents off those sites and their subdomains. A tab agents can use loads
  nothing from a blocked site: not as a page, a redirect, a frame, or a request made by a script. That
  also holds for you in a tab you have shared: stop sharing it to visit a blocked site there. Enter
  host names; an address such as `https://bank.example/login` is reduced to `bank.example`.

The activity log leaves out typed text, scripts and file contents; it does show the names of files an
agent uploaded and page loads that were blocked. Private windows are not available.

## Security

Read this before installing into a profile you browse with.

- The profile needs `xpinstall.signatures.required=false` and `extensions.experiments.enabled=true`.
  That profile will then accept any unsigned add-on you agree to install, and add-ons using
  Experiments run with full browser privileges.
- foxreach itself is such an add-on. Its privileged code is
  [parent.js](extension/experiment/parent.js) and [child.sys.mjs](extension/experiment/child.sys.mjs).
- Any process running as your user can connect to the socket. It then has to be allowed from the
  toolbar button, by the name it gives itself (see above); a program that takes an allowed name
  gets in. The socket keeps out other users and websites. The host and the MCP server
  refuse a socket folder that another user owns or can write (this matters where `XDG_RUNTIME_DIR`
  is not set and the socket falls back to the shared temporary folder). On macOS the socket is in
  the user's own cache folder (`getconf DARWIN_USER_CACHE_DIR`), whatever `XDG_RUNTIME_DIR` says, so
  a browser opened from the Dock and an agent started from a shell find the same one; the
  temporary folder beside it is swept of files unused for three days.
- **Local files.** `file_upload` reads files on the agent's machine and hands them to a page, and a
  page can ask the agent to do that. It refuses hidden files and folders (`~/.ssh`, `~/.config`,
  `.env`: any name starting with a dot, also through a link), `/proc`, `/sys` and `/dev`, and on
  macOS `~/Library`, which holds there what hidden folders hold on Linux (keychains, every browser's
  cookies, mail, app settings), whether `HOME` says so or not; documents kept there by Dropbox,
  OneDrive and Google Drive (`~/Library/CloudStorage`) and iCloud Drive can still be uploaded. Set
  `FOXREACH_ALLOW_HIDDEN_UPLOADS=1` for the MCP server to lift the rules on hidden files and
  `~/Library`. Your other files (documents, downloads) can still be uploaded. `save_to` writes only
  files named as images (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`), so it cannot replace a script or
  a key; it can replace an image, though never in `~/Library` on macOS outside its cloud folders.
- **Updates.** The full add-on updates itself from this project's GitHub Pages, and it runs with
  full browser privileges. Whoever controls that repository controls what your browser installs.
  To stay on a version you have read, set `extensions.update.enabled` to `false` in that profile.
- Page content is untrusted. A page can try to instruct the agent reading it (prompt injection), and
  no tool can rule that out. In an agent tab, an agent can reach any site the profile is logged in to,
  so block sensitive sites (banking, email, your password manager's site) in the toolbar popup.
- **Fields agents may not read.** Passwords and one-time codes always, and by default payment
  cards (number, security code, expiry), ID numbers (SSN, ITIN, national, state or tax ID,
  passport, driver's license) and security-question answers. Under the toolbar button you can add
  addresses, phone numbers, email addresses, names and birth dates, or remove the defaults. The
  widest net is "Anything I type or autofill myself": every value you typed, pasted or had filled
  by the browser or a password manager is hidden, whatever the field is called (what an agent
  typed stays visible to it); it is off by default because it also stops `javascript` in any tab
  where you filled a field. For these, `read_page` and `find` say
  only that the field is filled, screenshots and recordings black them out (in frames too: a frame
  foxreach cannot look into, such as another add-on's, one on a site agents may not use, or one
  nothing can run in, is blacked out whole; a field in a frame drawn scaled, rotated, mirrored or
  through a filter cannot be placed, and that picture is refused), and `javascript` does
  not run in a tab while one holds a value (agent code runs in the page's own world, where nothing
  could stop it reading one). Clicking and typing still work. Fields are recognised by their
  autofill hint (`autocomplete="cc-number"`, `"postal-code"`...) or by words in their label, name or
  id (except with that last option), so a field labelled only "Field 7", or an "Answer" box under a list of security questions,
  is missed. Not covered either: code an agent started earlier that is still running (a listener
  added before you typed sees your keys), frames on sites agents are not allowed on, which agent
  code cannot reach (nor the blank frames inside them), a page that keeps moving a frame holding
  such a field (a picture can now and then catch the field before its cover is in place), and a
  page that paints a second copy of a frame elsewhere (CSS `-moz-element()`): only the frame
  itself is covered.
  For a page you would rather keep from agents altogether, block its site.
- How password-manager add-ons behave in agent tabs has not been tested, beyond this: frames they
  put into a page (Proton Pass's, say) are blacked out in screenshots.
- A tab you share stays shared when it moves to another site, until you untick it or close it.
- Blocking covers what a tab loads from the moment a site is on the list. A blocked site already
  open in a frame of a tab is no longer read, and screenshots black it out.
- In the build without privileged code, `javascript` runs in the page's own world. A page whose
  security policy forbids `eval` refuses it.
- **That build asks for no site at install.** You grant single sites, or all, from the toolbar
  button, and the browser enforces it. The toolbar panel opens a foxreach tab that asks the browser
  (Continue, then the browser's own prompt): asked from the panel itself, the browser's prompt
  opened hidden behind the panel, and once Allow in it did nothing after the panel had closed.
  A site you add while every site is granted is covered by that grant and gets none of its own (the
  browser stores no grant for a site it already covers), so going back to "Only these sites" opens
  that tab again for such sites, in one prompt.
  With single sites granted the browser gives no pictures of pages (`screenshot`, `record`): it
  offers those only to an add-on that holds every site. Blocked sites are then enforced by not
  being granted, not per request.
- The full build holds every site as far as the browser is concerned, because its privileged code
  could reach them anyway; there the list of sites is enforced by foxreach itself.

## Limits

- **Firefox release and beta** (and distro packages, the Snap and the Flatpak built from them) require
  signed add-ons and ignore the Experiments pref, so they get only the signed build without
  privileged code. Its input has `isTrusted=false`, which some sites reject, and `drag`,
  `read_console`, `handle_dialog` and file drops are unavailable. `setup_profile.py` cannot install
  it; it is installed by opening the signed file. The browser's own automation protocol could give
  some of this back, and was tested and turned down: it shows every site that the browser is
  automated, and opens every tab and cookie to any local program
  ([spikes/bidi/FINDINGS.md](spikes/bidi/FINDINGS.md)).
- **The program** is built for glibc Linux and macOS, on x86-64 and arm64. No packages (deb, rpm,
  dmg) are made.
- **Windows: the program and the signed build only**, tried with LibreWolf on Windows 11 x64 (see
  [Windows](#windows)); the full build's setup scripts and the manager app are Linux and macOS
  only, and an unsigned `foxreach.exe` may not start where Smart App Control is on. On macOS: tested with LibreWolf 160.0 on Apple
  silicon (`setup_profile.py`, `test/e2e.py`, `test/binary.py` and the tests that need no browser);
  the signed build in release Firefox on a Mac, and an Intel Mac, are not yet tested.
- **Snap browsers** are not handled.
- **A new profile in Mozilla's own builds opens first-run prompts** (terms of use, welcome). While one
  covers the window the browser will not bring a tab to the front; `tabs_select` says so, and the
  other tools keep working on the tab. Answer the prompt once.
- **Browser updates can break input.** The privileged code uses internal APIs, and one was removed
  as recently as 159. Run the test suite after a browser update. Tested on LibreWolf 159.0.1 and
  Firefox Developer Edition 161.0b1.
- **`find` uses no language model,** so it works with any agent, but it relies on labels and synonyms.
  It can miss an unlabeled field ("search bar" misses Hacker News' footer box); use `read_page` there.
- **`read_network`** lists requests, without headers or bodies.
- **HTML5 drag-and-drop** works within one frame, not across frames.
- **Sites with bot detection:** not tested.
- **Headed tests** ran on one monitor layout only.
- On a first install, the browser can record the add-on as active but run it only from the next start.
  `setup_profile.py` allows for this: it requires answers on two starts in a row.

## Tests

```bash
uv run --python 3.15 test/e2e.py             # expect: 293 passed, 0 failed, exit 0 (about 90 s)
```

The tests use `FOXREACH_BROWSER` (a path or a command), else the only browser installed.

| Command | What it shows |
|---|---|
| `test/e2e.py --visible` | The same suite in a real window |
| `test/untrusted.py` | The build without privileged code: holds no site until granted one, and says its input is untrusted |
| `test/binary.py` | The single-file program builds the same twice, installs, and serves a real browser |
| `test/release.py` | A release prepared in a throwaway copy matches a rebuild from its tag; damaged files are reported; `install.sh` checks checksums |
| `test/restart_loop.py` | The add-on returns after every restart |
| `test/real_site.py` | Search and read a real site (Wikipedia, needs internet) |
| `test/real_agents.py` | Claude Code and Codex each finish a task; uses a little quota |
| `test/browser_open.py` | `browser_open` starts a recorded throwaway profile and waits for it; refuses unrecorded, open and missing ones |
| `test/history.py` | How connections ended (quit, killed, add-on restarted) reaches `status` and the not-connected error |
| `test/security.py` | The bridge refuses hidden-file uploads, non-image `save_to`, and a socket folder others can write; starts no browser |
| `test/request_window.py [--rfp]` | The request window of the build without privileged code, in a real window: its cards and the browser's own permission prompt (Allow and Deny) fit, and a size or maximize the user gives it is kept; needs a desktop session |
| `test/first_run_prompt.py` | `tabs_select` says when a browser prompt keeps the tab from the front |
| `test/browsers.py` | Browsers are found however installed, and the native host goes where each looks; starts no browser |
| `test/flatpak_sandbox.py` | The Flatpak copy of the host works inside a real Flatpak sandbox |
| `test/flatpak_browser.py flatpak:<id>` | A real Flatpak browser starts the host in its sandbox and an agent outside drives a page |
| `test/install_agents.py` | `foxreach install` adds the MCP server to each agent's settings (JSON, TOML, YAML, opencode's layouts), keeps everything else, backs up, refuses what it cannot edit safely, and `uninstall` takes it out; starts no browser |
| `test/update_cli.py` | `foxreach update` against a local stand-in release: checksum and version checks refuse bad downloads and change nothing, a good one replaces the program and keeps `.previous`, and the version warning names the right command; starts no browser |
| `test/signed_firefox.py [--xpi FILE]` | Release Firefox (or LibreWolf, `--browser`) installs the Mozilla-signed build as signed and refuses it unsigned |
| `test/connect_errors.py` | What an agent is told when the browser cannot be reached, for each cause; starts no browser |
| `test/agents_container/run.sh` | In a throwaway Podman container, installs OpenClaw, Copilot CLI, Qwen, Kimi, MiMo, MiniMax, Mistral Vibe and DeepSeek Harness, runs `foxreach agents add`, and checks with each agent's own tools that it reads the entry; needs network |
| `test/background_focus.py` | With the browser behind another app, typed text reaches a widget that keeps it only after a focus event, and the user's front tab sees no focus event; needs a running headful browser (see the file) |

Tests use throwaway profiles and restart the browser with no automation attached before the tool
checks. They never touch a real profile.

An AppImage browser can leave core dumps when it unmounts at test teardown. To avoid them, run the
tests against an extracted copy: `/path/to/browser.AppImage --appimage-extract`, then
`FOXREACH_BROWSER=<dir>/squashfs-root/usr/bin/<browser>`.

The comparison with Claude in Chrome is in [test/ab/RESULTS.md](test/ab/RESULTS.md). Earlier feasibility
tests are in [spikes/trusted-input/FINDINGS.md](spikes/trusted-input/FINDINGS.md) and
[spikes/bidi/FINDINGS.md](spikes/bidi/FINDINGS.md).

## License

MIT. See [LICENSE](LICENSE).
