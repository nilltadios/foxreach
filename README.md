# foxreach

Lets any local agent that speaks MCP operate the tabs of a Firefox-based browser, with real input. It
has no telemetry, no account, no vendor lock-in and no open network port.

```
agent ── stdio MCP ── foxreach mcp ── private Unix socket ── foxreach host ── native messaging ── add-on
(one server per agent)                (mode 0600)            (started by the browser)
```

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
   Rosetta, not yet on an Intel Mac.
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
   | Gemini CLI (retired by Google in 2026; only when the `gemini` program is still installed) | `~/.gemini/settings.json` |
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

1. Raise `version` in `extension/manifest.json` (the bridge reads it from there too), commit.
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

[test/update.py](test/update.py) proves the update path on a throwaway profile: the browser's
background check installs a newer version from an `updates.json` of the same layout, and the bridge
warns that the versions differ. [test/release.py](test/release.py) runs steps 3 and 6 in a throwaway
copy of the repository, publishing nothing.

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

`status`, `tabs_list`, `tabs_create`, `tabs_close`, `tabs_select`, `navigate`, `read_page`, `find`,
`get_page_text`, `click`, `hover`, `drag`, `type_text`, `press_key`, `scroll`, `scroll_to`,
`form_input`, `file_upload`, `screenshot`, `wait`, `wait_for`, `javascript`, `read_console`,
`read_network`, `resize_window`, `handle_dialog`, `record`, `batch`.

Each tool's description is in [bridge/tools.mjs](bridge/tools.mjs).

`read_page` keeps its replies short: a link address over 40 characters shows only where it goes
(`href=→ example.org`, or `href=→` for this site), a list item or cell whose text is just its
children's is not repeated, and layout-only elements carry no id. `full_links: true` shows every
address whole; `find` still matches addresses.

`type_text` and `press_key` say which element received the keys. They refuse to type, or to send
Delete or Backspace, when focus is not on a text field, so keys meant for a search box cannot act
as page shortcuts (such as deleting the selected messages in a mail app). `allow_non_text: true`
overrides this for keyboard-driven pages. `type_text` also warns when the field's value did not change.

`press_key` and the `modifiers` of `click` and `drag` take `mod`, the key shortcuts use on the browser's
system: Cmd on macOS, Ctrl elsewhere. `mod+a` selects all on both; `ctrl` is always the Control key,
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

`file` and `save_to` refuse hidden files and folders (`file` also refuses `~/Library` on macOS).

## What you control

The toolbar button shows the bridge's status and recent agent activity, and it has these controls:

- **Pause** stops all agent access at once.
- **Agents:** a program that connects for the first time waits until you answer its request there:
  Allow (until it disconnects), Always (that name, from now on), or Refuse. Its call waits up to a
  minute, then fails and says why. The request opens a small window of its own, since the browser
  may keep the button in its extensions menu; the button also shows `?`. The name is what
  the program calls itself, so this keeps out an agent you did not mean to let in, not a program
  that lies about its name. You can switch asking off.
- **Sites agents may use:** every site, or only the ones you list (with their subdomains). On any
  other site an agent can neither open a page nor read or act on one, and a frame from such a site
  is skipped. A refused site shows as a request you can allow with one click. Sharing a tab allows
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
  the user's own temporary folder (`getconf DARWIN_USER_TEMP_DIR`).
- **Local files.** `file_upload` reads files on the agent's machine and hands them to a page, and a
  page can ask the agent to do that. It refuses hidden files and folders (`~/.ssh`, `~/.config`,
  `.env`: any name starting with a dot, also through a link), `/proc`, `/sys` and `/dev`, and on
  macOS `~/Library`, which holds there what hidden folders hold on Linux (keychains, every browser's
  cookies, mail, app settings). Set `FOXREACH_ALLOW_HIDDEN_UPLOADS=1` for the MCP server to lift the
  rules on hidden files and `~/Library`. Your other files
  (documents, downloads) can still be uploaded. `save_to` writes only files named as images
  (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`), so it cannot replace a script or a key; it can replace
  an image.
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
  only that the field is filled, screenshots and recordings black them out, and `javascript` does
  not run in a tab while one holds a value (agent code runs in the page's own world, where nothing
  could stop it reading one). Clicking and typing still work. Fields are recognised by their
  autofill hint (`autocomplete="cc-number"`, `"postal-code"`...) or by words in their label, name or
  id (except with that last option), so a field labelled only "Field 7", or an "Answer" box under a list of security questions,
  is missed. Not covered either: code an agent started earlier that is still running (a listener
  added before you typed sees your keys), and frames on sites agents are not allowed on, which
  agent code cannot reach. For a page you would rather keep from agents altogether, block its site.
- How password-manager add-ons behave in agent tabs has not been tested.
- A tab you share stays shared when it moves to another site, until you untick it or close it.
- Blocking covers what a tab loads from the moment a site is on the list. A blocked site already
  open in a frame of a tab is no longer read, but it still shows in screenshots until the tab reloads.
- In the build without privileged code, `javascript` runs in the page's own world. A page whose
  security policy forbids `eval` refuses it.
- **That build asks for no site at install.** You grant single sites, or all, from the toolbar
  button, and the browser enforces it. With single sites granted the browser gives no pictures of
  pages (`screenshot`, `record`): it offers those only to an add-on that holds every site. Blocked
  sites are then enforced by not being granted, not per request.
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
- **Linux and macOS only.** Windows is not supported. On macOS: tested with LibreWolf 160.0 on Apple
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
uv run --python 3.15 test/e2e.py             # expect: 285 passed, 0 failed, exit 0
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
| `test/security.py` | The bridge refuses hidden-file uploads, non-image `save_to`, and a socket folder others can write; starts no browser |
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
