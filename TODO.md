# To do

Open items, newest first. Remove an item when it is done.

## Next

1. **foxreach manager app.** A desktop app that manages foxreach end to end, built on the same
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

2. **addons.mozilla.org listing: awaiting Mozilla's review.** 0.3.1 submitted 2026-11-11; 0.3.3 (same
   code as 0.3.2, which the unlisted channel took) submitted listed 2026-11-12 so the public page opens
   on current code. Status and reviewer questions:
   https://addons.mozilla.org/en-US/developers/addon/fe1f53e268044b529443/versions
   When 0.3.3 is approved, sync GitHub to it: download the signed file from
   https://addons.mozilla.org/en-US/developers/addon/fe1f53e268044b529443/versions/6701599 to
   `dist/signed/foxreach-0.3.3-signed.xpi`, run `scripts/release.py`, then commit, tag v0.3.3 and push
   (ask first), and `release.py --verify` / `verify_release.py 0.3.3`.

## Decide

- **Limit what a misled agent can do in a profile with logins.** Not built: agent tabs in their own
  Firefox container (logged out even in a daily profile); a shared tab stops being shared when it
  leaves the site it was shared on; confirm each file upload. (The site allowlist exists.)
- **Approval from the Claude app (phone, iPad).** MCP elicitation is not relayed to Remote Control
  clients (Claude Code 2.1.322, tested 2026-11-11; demo in ~/Documents/Claude/2026-11-11/elicitation-demo),
  so it shows only in the terminal. A permission prompt is relayed: a `request_access` tool behind a
  hook that forces a prompt would work, but needs the Claude Code plugin route and is weaker in
  bypass mode. Untested.

## Later

- Signed tags.
- The limits listed under "What would change this" in `spikes/bidi/FINDINGS.md`: watch for a way to
  get trusted input on release Firefox.
