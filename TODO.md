# To do

Open items, newest first. Remove an item when it is done.

## Next

1. **Manager app: open items from the 2026-11-12/13 session** (UI reworked into tabs; uncommitted).
   - **Add-on permissions in the app** (user request, from the toolbar panel): show pause, "ask before
     a new agent", sites (every site / only these, the list), fields agents may not read, blocked
     sites and recent activity for the connected browser. Rule: changes that make things safer apply
     from the app (pause, protection on, remove a site, block a site, ask on); changes that loosen
     protection (unpause, protection off, allow a site or every site, unblock, ask off) are only
     requested by the app and confirmed by the user in the browser, like the new-agent prompt. Reason:
     the app reaches the add-on through the same `foxreach` socket any agent (or an agent's shell) can,
     so the browser must be where loosening is approved. Needs an add-on release: settings read and
     change messages in `extension/background.js` (today only the popup changes them, lines ~1220-1295),
     a `foxreach settings` command, then the app's view. The signed build needs Mozilla to sign again.
   - Rebuild the AppImage and Flatpak with the new UI (tabs, search, Test, logos) and recheck both.
   - AppImage segfault in `wl_proxy_destroy` on a background thread (core dump 2026-11-12 23:24),
     probably when the window closes: find and fix.
   - Not yet checked for real: the "Add foxreach?" prompt in Firefox from `foxreach addon install`
     (only the removal is tested, `test/addon_firefox.py`); the AppImage removing itself.
   - `test/signed_firefox.py` stops Firefox Flatpak with SIGTERM to `flatpak run`, which leaves the
     headless Firefox running; use `flatpak kill` as `test/addon_firefox.py` does.
   - rust-analyzer on `manager/` needs more than 3 GB (measured: `~/Documents/Claude/2026-11-13/
     rust-analyzer-memory/measure.py`), so the 3 GB scope (`~/.local/bin/rust-analyzer`) keeps stopping
     it after edits. Decide: raise the cap to 5 GB, or turn the rust-analyzer-lsp plugin off for this
     repository only.

2. **foxreach manager app.** Linux part built in `manager/` (Rust, libcosmic; see
   `manager/README.md`): program install and update, the signed add-on per browser profile, agents,
   removal; AppImage (`manager/packaging/container-build.sh`) and Flatpak (`manager/flatpak/build.sh`).
   Not yet: a published Flatpak repository and `.flatpakref` (signing key, Pages), the AppImage in
   the release workflow, Windows and macOS. The original plan:
   A desktop app that manages foxreach end to end, built on the same
   code as the `foxreach` program (`bridge/agents.mjs`, `bridge/install.mjs`, `bridge/update.mjs`):
   - choose which agents get the MCP server (the list `foxreach agents` shows), add and remove them;
   - updates: see whether a newer release exists and apply it (what `foxreach update` does), and
     show the add-on's version beside the program's;
   - uninstall: selected parts (one agent, one browser's native host, the Flatpak copies), or
     everything, including the app itself.

   Packaging, one per platform:
   - Linux: an AppImage that runs on any distribution (anylinux style: bundle what it needs, glibc
     baseline old enough), and a Flatpak (a `.flatpakref` or a repo, as for CosmicAuthenticator).
     The Flatpak needs host access for agents' settings files and `~/.mozilla`, so check what its
     sandbox must be allowed and say so on its page.
   - Windows: an `.exe` installer. Native messaging there is registered in the registry
     (`HKCU\Software\Mozilla\NativeMessagingHosts\foxreach`), not a folder; agents' settings paths
     differ (`%APPDATA%`, `%USERPROFILE%`).
   - macOS: a signed `.app` in a `.dmg`. Native messaging manifests go in
     `~/Library/Application Support/Mozilla/NativeMessagingHosts`; Claude desktop's settings are
     already handled (`~/Library/Application Support/Claude`).
   The program today is Linux-only (`install.sh`, `bridge/install.mjs`); Windows and macOS need the
   host registration and agent paths ported first.

3. **addons.mozilla.org listing: awaiting Mozilla's review.** 0.3.1 submitted 2026-11-11; 0.3.3 (same
   code as 0.3.2, which the unlisted channel took) submitted listed 2026-11-12 so the public page opens
   on current code. Status and reviewer questions:
   https://addons.mozilla.org/en-US/developers/addon/fe1f53e268044b529443/versions
   When 0.3.3 is approved, sync GitHub to it: download the signed file from
   https://addons.mozilla.org/en-US/developers/addon/fe1f53e268044b529443/versions/6701599 to
   `dist/signed/foxreach-0.3.3-signed.xpi`, run `scripts/release.py`, then commit, tag v0.3.3 and push
   (ask first), and `release.py --verify` / `verify_release.py 0.3.3`.

4. **A command line beside the MCP server.** `foxreach call <tool> --tab N [--name AGENT] ...` reusing
   every tool over the same socket: text out, screenshots to files, the agent's name for the approval
   prompt. For agents without MCP or short on context (MCP tool definitions cost ~4,200 tokens at
   start; a CLI costs nothing until used), and for scripts. Ship a short skill file that says how to
   use it. Keep MCP as the main path: images reach the model directly, agents approve per tool, and
   one connection keeps an agent's refs, pointer and identity.

5. **Agents added 2026-11-12 (OpenClaw, Copilot CLI, Qwen, Kimi, MiMo, MiniMax, Vibe, DeepSeek
   Harness)** were checked in a throwaway container: each reads foxreach's entry (Qwen, MiMo and
   OpenClaw also started the server; rerun with `test/agents_container/run.sh`). Not yet seen: one
   of them calling a foxreach tool in a chat, which needs that agent's account.

## Decide

- **Limit what a misled agent can do in a profile with logins.** Not built: agent tabs in their own
  Firefox container (logged out even in a daily profile); a shared tab stops being shared when it
  leaves the site it was shared on; confirm each file upload. (The site allowlist exists.)
- **Approval from the Claude app (phone, iPad).** MCP elicitation is not relayed to Remote Control
  clients (Claude Code 2.1.322, tested 2026-11-11),
  so it shows only in the terminal. A permission prompt is relayed: a `request_access` tool behind a
  hook that forces a prompt would work, but needs the Claude Code plugin route and is weaker in
  bypass mode. Untested.

## Later

- Signed tags.
- The limits listed under "What would change this" in `spikes/bidi/FINDINGS.md`: watch for a way to
  get trusted input on release Firefox.
