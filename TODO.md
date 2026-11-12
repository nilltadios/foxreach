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

2. **Leaner read_page replies.** Measured with `scripts/page_noise.py` (sizes: `scripts/context_cost.py`):
   no styling noise, but link addresses are 32% of Wikipedia's reply (21% on BBC News), and
   container names repeat their children's text (`listitem "UK"` then `link "UK"`). Agreed:
   - Link addresses: keep short ones inline (under ~40 characters: `#Licensing`, `/news/uk`); show
     long ones as the site only, `(→ nytimes.com)`, or `(→)` for a long same-site path. The full
     address on request by the element's ref (a `link ref=r46` tool, or `read_page links=full`);
     no second numbering scheme, the ref already identifies the link.
   - Don't repeat text: when a container's name is just its children's text joined, show it once.
   - Drop internal IDs on layout-only elements (`list #toc-…-sublist`).
   Check: rerun both scripts before and after (expect about half on link-heavy pages), and the e2e
   read tests.

3. **Release 0.3.2** (0.3.1 is taken at Mozilla). Since 0.3.0, local commits a06394d..39b1d6c (not
   pushed): the multi-agent install (`foxreach install`/`agents`), `foxreach update`, background tabs
   by default with the keyboard guard fixed for them, and the fix for plain scripts that return an
   object (the published 0.3.0 full add-on has that bug; the signed builds do not contain that
   code). Steps: raise the version, `scripts/sign.py` (unlisted), `scripts/release.py`, push and
   tag (watch the workflow start: the first tag push of 0.3.0 started none), `release.py --verify`,
   `verify_release.py`. Release users reach `foxreach update` with one last `install.sh` run.

4. **addons.mozilla.org listing: awaiting Mozilla's review** (0.3.1, submitted 2026-11-11).
   Status and reviewer questions:
   https://addons.mozilla.org/en-US/developers/addon/fe1f53e268044b529443/versions

## Decide

- **Limit what a misled agent can do in a profile with logins.** Not built: agent tabs in their own
  Firefox container (logged out even in a daily profile); a shared tab stops being shared when it
  leaves the site it was shared on; confirm each file upload. (The site allowlist exists.)
- **Password fields:** `read_page` hides typed passwords, but `javascript` can still read a filled
  password field. Options: block that read, or pause agents while the user types in one.
- **Approval from the Claude app (phone, iPad).** MCP elicitation is not relayed to Remote Control
  clients (Claude Code 2.1.322, tested 2026-11-11; demo in ~/Documents/Claude/2026-11-11/elicitation-demo),
  so it shows only in the terminal. A permission prompt is relayed: a `request_access` tool behind a
  hook that forces a prompt would work, but needs the Claude Code plugin route and is weaker in
  bypass mode. Untested.

## Later

- Signed tags.
- The limits listed under "What would change this" in `spikes/bidi/FINDINGS.md`: watch for a way to
  get trusted input on release Firefox.
