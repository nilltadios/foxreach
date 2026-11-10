# foxreach

Lets any local agent that speaks MCP operate the tabs of a Firefox-based browser, with real input. It
has no telemetry, no account, no vendor lock-in and no open network port.

```
agent ── stdio MCP ── bridge/mcp.mjs ── private Unix socket ── bridge/host.mjs ── native messaging ── extension
(one server per agent)                  (mode 0600)            (started by the browser)
```

## Setup

Needs Linux, `uv`, Bun or Node.js, and a Firefox-based browser that accepts unsigned add-ons:

| Browser | Works |
|---|---|
| LibreWolf | Yes. Tested: 159.0.1, AppImage and Flatpak |
| Firefox Developer Edition, Nightly | Yes. Tested: Developer Edition 161.0b1, Mozilla's tarball |
| Firefox ESR, Floorp, Waterfox, Zen | Expected to, since they honor the same two prefs; not tested |
| Firefox release and beta, most distro `firefox` packages, the Firefox Snap and Flatpak | No: they refuse unsigned add-ons. See [Limits](#limits) |

How the browser was installed does not matter: a distro package, a tarball, an AppImage, or a Flatpak
(Flatpak has its own section below). List what the scripts find on this machine:

```bash
uv run --python 3.15 scripts/browsers.py
```

Close the browser, then install into a profile. Leave out `--profile` to create a dedicated, logged-out
one at `~/.local/share/foxreach/profile`, which `scripts/foxreach-browser` opens.

```bash
uv run --python 3.15 scripts/setup_profile.py --profile /path/to/profile --browser /path/to/browser
```

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

Then connect an agent, using full paths. Apps started from the desktop may not have Bun on `PATH`.

```bash
claude mcp add foxreach -- ~/.bun/bin/bun /path/to/foxreach/bridge/mcp.mjs
```

```bash
codex mcp add foxreach -- ~/.bun/bin/bun /path/to/foxreach/bridge/mcp.mjs
```

For other agents, use the same command as a stdio MCP server.

### Flatpak browsers

```bash
uv run --python 3.15 scripts/setup_profile.py --browser flatpak:io.gitlab.librewolf-community
```

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
  test loads the untrusted build for the run; `setup_profile.py` cannot install into it.

### Runtime

The bridge has no dependencies. In tests on Bun it started in 28 ms using 23 MiB, against 140 ms and
50 MiB on Node.js ([compare_runtimes.py](test/compare_runtimes.py)). Node.js is the fallback if Bun is
missing; to choose it, run `FOXREACH_RUNTIME=node uv run --python 3.15 scripts/build_install.py` and use
`node` in the commands above. The host's path is written at build time, so rerun that script if the
runtime or this folder moves.

## Updating

foxreach has two parts. The add-on lives in the browser profile; the bridge (the MCP server and the
native host) runs from this folder. They should be the same version.

- **The add-on updates itself.** Its `update_url` points at `docs/updates.json` on GitHub Pages. The
  browser checks it about once a day and installs a newer version in the background, without a
  restart. Firefox has this on by default and LibreWolf leaves it on (`extensions.update.enabled`).
- **The bridge updates with this folder.** Run the updater with the browser closed:

  ```bash
  uv run --python 3.15 scripts/update.py            # --dry-run to preview
  ```

  It pulls, rebuilds, re-registers the native host, and reinstalls the add-on in every profile that
  setup installed into (recorded in `~/.local/state/foxreach/profiles`) whose copy is older. Agents
  use the new bridge from their next session.

If the add-on has updated itself but this folder has not, the `status` tool shows both versions and a
`warning`, and the first tool call of each agent session ends with a note saying so. Run the updater.

The untrusted build has no `update_url`, so it never replaces itself with the full build.

## Releasing

1. Raise `version` in `extension/manifest.json` (the bridge reads it from there too), commit.
2. `uv run --python 3.15 scripts/release.py` adds `docs/foxreach-<version>.xpi` and an entry in
   `docs/updates.json`, and prints the commit, tag and push commands. It changes no remote.
3. Once per repository, serve `docs/` from `main` with GitHub Pages.
4. After the push, `uv run --python 3.15 scripts/release.py --verify` checks that Pages serves the new
   entry and that the file's hash matches it.

[test/update.py](test/update.py) proves the path on a throwaway profile: the browser's background check
installs a newer version from an `updates.json` of the same layout, and the bridge warns that the
versions differ.

## Undo

With the browser closed, this removes the add-on and both prefs. Leave out `--apply` to preview it.

```bash
uv run --python 3.15 scripts/remove_from_profile.py --profile /path/to/profile --apply
```

To clear the rest, remove `~/.local/share/foxreach`, `~/.local/state/foxreach` and
`~/.mozilla/native-messaging-hosts/foxreach.json` (also in `~/.librewolf/native-messaging-hosts`, if you
have that folder). For a Flatpak browser, remove `~/.var/app/<id>/foxreach` and `foxreach.json` from the
`native-messaging-hosts` folders under `~/.var/app/<id>`, and withdraw the permission with
`flatpak override --user --nofilesystem=xdg-run/foxreach <id>`.

## Tools

`status`, `tabs_list`, `tabs_create`, `tabs_close`, `tabs_select`, `navigate`, `read_page`, `find`,
`get_page_text`, `click`, `hover`, `drag`, `type_text`, `press_key`, `scroll`, `scroll_to`,
`form_input`, `file_upload`, `screenshot`, `wait`, `wait_for`, `javascript`, `read_console`,
`read_network`, `resize_window`, `handle_dialog`, `record`, `batch`.

Each tool's description is in [bridge/tools.mjs](bridge/tools.mjs).

## What you control

The toolbar button shows the bridge's status and recent agent activity, and it has these controls:

- **Pause** stops all agent access at once.
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
- Any process running as your user can connect to the socket and use the tabs agents can reach. The
  socket keeps out other users and websites, not your own programs. The host and the MCP server
  refuse a socket folder that another user owns or can write (this matters where `XDG_RUNTIME_DIR`
  is not set and the socket falls back to the shared temporary folder).
- **Local files.** `file_upload` reads files on the agent's machine and hands them to a page, and a
  page can ask the agent to do that. It refuses hidden files and folders (`~/.ssh`, `~/.config`,
  `.env`: any name starting with a dot, also through a link), `/proc`, `/sys` and `/dev`. Set
  `FOXREACH_ALLOW_HIDDEN_UPLOADS=1` for the MCP server to lift the first rule. Your other files
  (documents, downloads) can still be uploaded. `save_to` writes only files named as images
  (`.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`), so it cannot replace a script or a key; it can replace
  an image.
- **Updates.** The full add-on updates itself from this project's GitHub Pages, and it runs with
  full browser privileges. Whoever controls that repository controls what your browser installs.
  To stay on a version you have read, set `extensions.update.enabled` to `false` in that profile.
- Page content is untrusted. A page can try to instruct the agent reading it (prompt injection), and
  no tool can rule that out. In an agent tab, an agent can reach any site the profile is logged in to,
  so block sensitive sites (banking, email, your password manager's site) in the toolbar popup.
- How password-manager add-ons behave in agent tabs has not been tested.
- A tab you share stays shared when it moves to another site, until you untick it or close it.
- Blocking covers what a tab loads from the moment a site is on the list. A blocked site already
  open in a frame of a tab is no longer read, but it still shows in screenshots until the tab reloads.
- In the build without privileged code, `javascript` runs in the page's own world. A page whose
  security policy forbids `eval` refuses it.

## Limits

- **Firefox release and beta** (and distro packages, the Snap and the Flatpak built from them) require
  signed add-ons and ignore the Experiments pref, so they get only `dist/foxreach-untrusted.xpi`. Its
  input has `isTrusted=false`, which some sites reject, and `drag`, `read_console`, `handle_dialog`
  and file drops are unavailable. It also needs signing to install permanently, so
  `setup_profile.py` cannot install it.
- **Linux only.** The scripts read `/proc` and use Linux paths. macOS and Windows are not supported.
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
uv run --python 3.15 test/e2e.py             # expect: 159 passed, 0 failed, exit 0
```

The tests use `FOXREACH_BROWSER` (a path or a command), else the only browser installed.

| Command | What it shows |
|---|---|
| `test/e2e.py --visible` | The same suite in a real window |
| `test/untrusted.py` | The no-privilege build loads and says its input is untrusted |
| `test/restart_loop.py` | The add-on returns after every restart |
| `test/real_site.py` | Search and read a real site (Wikipedia, needs internet) |
| `test/real_agents.py` | Claude Code and Codex each finish a task; uses a little quota |
| `test/security.py` | The bridge refuses hidden-file uploads, non-image `save_to`, and a socket folder others can write; starts no browser |
| `test/first_run_prompt.py` | `tabs_select` says when a browser prompt keeps the tab from the front |
| `test/browsers.py` | Browsers are found however installed, and the native host goes where each looks; starts no browser |
| `test/flatpak_sandbox.py` | The Flatpak copy of the host works inside a real Flatpak sandbox |
| `test/flatpak_browser.py flatpak:<id>` | A real Flatpak browser starts the host in its sandbox and an agent outside drives a page |

Tests use throwaway profiles and restart the browser with no automation attached before the tool
checks. They never touch a real profile.

An AppImage browser can leave core dumps when it unmounts at test teardown. To avoid them, run the
tests against an extracted copy: `/path/to/browser.AppImage --appimage-extract`, then
`FOXREACH_BROWSER=<dir>/squashfs-root/usr/bin/<browser>`.

The comparison with Claude in Chrome is in [test/ab/RESULTS.md](test/ab/RESULTS.md). Earlier feasibility
tests are in [spikes/trusted-input/FINDINGS.md](spikes/trusted-input/FINDINGS.md).

## License

MIT. See [LICENSE](LICENSE).
