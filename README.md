# foxreach

Lets any local agent that speaks MCP operate LibreWolf tabs, with real input. It has no telemetry,
no account, no vendor lock-in and no open network port.

```
agent ── stdio MCP ── bridge/mcp.mjs ── private Unix socket ── bridge/host.mjs ── native messaging ── extension
(one server per agent)                  (mode 0600)            (started by the browser)
```

## Setup

Needs LibreWolf, `uv`, and Bun or Node.js. The scripts look for the browser at
`~/AppImages/librewolf.appimage`; set `FOXREACH_BROWSER` to use another path.

Close the browser, then install into a profile. Leave out `--profile` to create a dedicated, logged-out
one at `~/.local/share/foxreach/profile`, which `scripts/foxreach-browser` opens.

```bash
uv run --python 3.15 scripts/setup_profile.py --profile /path/to/profile
```

Setup backs up that profile's `user.js` and `prefs.js` beside themselves. It uses no automation:
Marionette deletes a user's own values for the prefs it manages. A headless start can also replace
"restore previous session" data, so on a profile you use, back up `sessionstore.jsonlz4` and
`sessionstore-backups` first and put them back after.

Only one running browser can serve agents: the first one opened. Close it to switch.

Then connect an agent, using full paths. Apps started from the desktop may not have Bun on `PATH`.

```bash
claude mcp add foxreach -- ~/.bun/bin/bun /path/to/foxreach/bridge/mcp.mjs
```

```bash
codex mcp add foxreach -- ~/.bun/bin/bun /path/to/foxreach/bridge/mcp.mjs
```

For other agents, use the same command as a stdio MCP server.

The bridge has no dependencies. In tests on Bun it started in 28 ms using 23 MiB, against 140 ms and
50 MiB on Node.js ([compare_runtimes.py](test/compare_runtimes.py)). Node.js is the fallback if Bun is
missing; to choose it, run `FOXREACH_RUNTIME=node uv run --python 3.15 scripts/build_install.py` and use
`node` in the commands above. The host's path is written at build time, so rerun that script if the
runtime or this folder moves.

## Undo

With the browser closed, this removes the add-on and both prefs. Leave out `--apply` to preview it.

```bash
uv run --python 3.15 scripts/remove_from_profile.py --profile /path/to/profile --apply
```

To clear the rest, remove `~/.local/share/foxreach` and `~/.mozilla/native-messaging-hosts/foxreach.json`.

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
- **Blocked sites** refuses tools and navigation for those sites and their subdomains.

The activity log leaves out typed text, scripts and file contents. Private windows are not available.

## Security

Read this before installing into a profile you browse with.

- The profile needs `xpinstall.signatures.required=false` and `extensions.experiments.enabled=true`.
  That profile will then accept any unsigned add-on you agree to install, and add-ons using
  Experiments run with full browser privileges.
- foxreach itself is such an add-on. Its privileged code is
  [parent.js](extension/experiment/parent.js) and [child.sys.mjs](extension/experiment/child.sys.mjs).
- Any process running as your user can connect to the socket and use the tabs agents can reach. The
  socket keeps out other users and websites, not your own programs.
- Page content is untrusted. A page can try to instruct the agent reading it (prompt injection), and
  no tool can rule that out. In an agent tab, an agent can reach any site the profile is logged in to,
  so block sensitive sites (banking, email, your password manager's site) in the toolbar popup.
- How password-manager add-ons behave in agent tabs has not been tested.
- A blocked site loaded in an iframe on an allowed page can still be read. Blocking is by tab URL.

## Limits

- **Firefox:** official releases ignore the Experiments pref, so they get only
  `dist/foxreach-untrusted.xpi`. Its input has `isTrusted=false`, which some sites reject, and
  `drag`, `read_console`, `handle_dialog` and file drops are unavailable. It also needs signing to
  install permanently. Nightly and Developer Edition should allow the full build; not tested.
- **Browser updates can break input.** The privileged code uses internal APIs, and one was removed
  as recently as 159. Run the test suite after a browser update. Tested on LibreWolf 159.0.1.
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
uv run --python 3.15 test/e2e.py             # expect: 147 passed, 0 failed, exit 0
```

| Command | What it shows |
|---|---|
| `test/e2e.py --visible` | The same suite in a real window |
| `test/untrusted.py` | The no-privilege build loads and says its input is untrusted |
| `test/restart_loop.py` | The add-on returns after every restart |
| `test/real_site.py` | Search and read a real site (Wikipedia, needs internet) |
| `test/real_agents.py` | Claude Code and Codex each finish a task; uses a little quota |

Tests use throwaway profiles and restart the browser with no automation attached before the tool
checks. They never touch a real profile.

To avoid core dumps from the AppImage's unmount at test teardown, run them against an extracted copy:
`~/AppImages/librewolf.appimage --appimage-extract`, then
`FOXREACH_BROWSER=<dir>/squashfs-root/usr/bin/librewolf`.

The comparison with Claude in Chrome is in [test/ab/RESULTS.md](test/ab/RESULTS.md). Earlier feasibility
tests are in [spikes/trusted-input/FINDINGS.md](spikes/trusted-input/FINDINGS.md).

## License

MIT. See [LICENSE](LICENSE).
