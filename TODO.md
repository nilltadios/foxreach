# To do

Open items, newest first. Remove an item when it is done.

## Next

0. **Problems from the 2026-11-18 session (YouTube Music, Outlook): what is left.** Fixed the same
   day (commits b333266..2ce2cb9): `javascript` with a leading function declaration, ref clicks
   (settle after scrolling, where the press landed, right-click handled or not, stale refs found
   again), painting (`status` with `tabId`, `wait_for` timeouts), connection history in `status`,
   `browser_open`, agent tabs (`tabs_close` `mine`, hint at five), `type_text` `delay_ms`. They
   ship in 0.4.0 (prepared 2026-11-18). The full build reaches profiles through `docs/updates.json`
   once 0.4.0 is published; the signed build only after Mozilla approves 0.4.0 (item 3). Until a
   profile has 0.4.0, the add-on there ignores `tabId` on `status` and `delay_ms`, refuses
   `tabs_close` `mine`, and records every quit as `closed`.
   Still open:
   - **Typing a secret from a file** (not built). Proposal: `type_text` takes `text_file` instead
     of `text`; the MCP server reads it (so the secret never enters the agent's context, the
     activity log or a recording label), types it with `delay_ms`, replies only with the count,
     and deletes the file afterwards. Which files: deleting is only safe for one-use handoff
     files, and typing a file into a page is the same leak as uploading it, so only from a
     handoff folder: foxreach's own `$XDG_RUNTIME_DIR/foxreach/secrets/` (0700), and T3 Code's
     `~/.t3/userdata/secrets/files/`, which `file_upload`'s hidden-file rule refuses today. The
     activity log stores tool arguments (`logActivity` in `extension/background.js`): mark the
     call so the text is never stored. Needs the user's decision on the folders. Related: GitHub
     issue #33377 (repository not recorded in the request; check which).
   - **Outlook right-click with no menu: cause not confirmed.** The transcript shows the clicks hit
     the row itself (r224/r324 were the rows; `scrollIntoView` centres any row at the same point,
     so the repeated (444, 494) was right). The first right-click opened the menu; later ones,
     with a menu or submenu possibly still open and the display off, did not. The replies now
     say whether the page took the right-click and whether the press landed on the element: try
     it on Outlook again (the user's session, with the user's OK) to see which.
   - **Settling after a scroll is a measurement, not a guarantee**: the box is measured every
     60 ms until it holds still, but in a background tab the page redraws about once a second, so
     a virtualized list can still move the row after that. The press check then warns; a retry
     after a miss would risk acting twice and is not done.
   - **Display off: only reasoned, not seen.** With no frames at all, `status` and `wait_for` say
     the browser is not painting. Headless tests cover the background-tab case (about 1 frame/s,
     `document.hidden`); whether a DPMS-off display gives 0 frames and `hidden` false in
     LibreWolf on KDE Wayland is not measured (it needs the user's display turned off). Check it
     the next time the screens are off anyway: `status` with a `tabId` of a tab in front.
   - **Other add-ons' toolbar popups** (Proton Pass's own panel) are not part of a tab: no
     screenshot, click or key reaches them (`spikes/other-addon-overlay/FINDINGS.md`). Their
     in-page prompts are reachable by point and keys, not by `read_page`/`find`. A window-level
     capture would need privileged code that draws browser windows and panels (for example
     `drawSnapshot` on the panel's browsing context); input there is input into another add-on's
     UI outside any page, which the approval rules do not cover. Decide whether to offer it.
   - Connection history lives in `$XDG_RUNTIME_DIR/foxreach/history.jsonl`, so it is gone after a
     reboot or logout; SIGTERM to the browser (logout, `kill`) is recorded as `lost`, since
     Firefox does not treat it as a quit.
   - `browser_open` runs without the browser's approval prompt (the browser is not running to ask
     in); it can only start profiles foxreach found itself (the record, and profiles holding the
     add-on that `foxreach status` finds), each in the browser found for it. Say if it should
     ask, for example through MCP elicitation.

0. **Manager: add-on update issues seen 2026-11-14** (release build of `manager/` from main, Browsers
   tab; reported by the user with screenshots):
   - **Firefox Flatpak is not offered an update.** Profile `default-release` shows `0.3.2` (no "Full
     build", so the signed build) with no Update button, while LibreWolf Flatpak's full build 0.3.2 gets
     "Update to 0.3.4". Not checked yet; likely cause is that `docs/updates-signed.json` has nothing
     newer than 0.3.2: 0.3.4 was approved on AMO 2026-11-18, but its signed file was deliberately not
     published there, since 0.4.0 replaces it (item 3). If so, the app should say why (for example
     "0.4.0 waiting for Mozilla's signature") instead of showing nothing.
   - **Update can be clicked while the browser is open.** The LibreWolf AppImage `default` profile was
     updated to full build 0.3.4 with LibreWolf running. Earlier the app refused this (the add-on file
     is replaced on disk, which needs the browser closed); find what changed and restore the guard, or
     confirm the open-browser path is now meant to work.
   - **Blank page after the update.** Updating opens `nilltadios.github.io/foxreach/foxreach-0.3.4.xpi`
     in the browser and the tab stays an empty dark page after the add-on is installed. Close that tab
     once the install finishes, or install without leaving a visible page.

0. **Manager AppImage and Flatpak in the release.** v0.3.4 (2026-11-14) shipped only the foxreach
   program and the add-on; from 0.4.0 the release also carries the manager's AppImage and Flatpak
   bundle for x86_64 and aarch64 (`.github/workflows/manager.yml`, called by `release.yml`; see "In
   CI" in `manager/README.md`). Done: CI run 44439124322 on main f44430b passed all five jobs; the
   aarch64 AppImage and Flatpak from it, and the v0.3.4 `foxreach-linux-arm64`, ran on Fedora Asahi
   Remix with 16 KiB pages (2026-11-18, details in `manager/README.md`); the x86_64 packages passed
   the same run but were not launched by hand. Since 2026-11-20 the manager has its own version and
   release (tag `manager-v<version>`, `manager-release.yml`), its packages no longer carry the
   program, and every release carries both sets of files (README.md, "Releasing"). Left:
   - AppImage updates: done for the release after 0.4.0 (update information and a `.zsync` per
     architecture, `manager/README.md`, "AppImage updates"); a 0.4.0 AppImage has none and has to
     be replaced by hand once. Not yet seen working against a real release.
   - a signed Flatpak repository on GitHub Pages with a `.flatpakref`, next to the bundles: built
     (`.github/workflows/pages.yml`, `manager/flatpak/build-site.sh`, key in
     `manager/flatpak/signing-key.asc`) and tested locally from an x86_64 bundle; live once the
     `GPG_PRIVATE_KEY` secret is set and Pages is switched to deploy from GitHub Actions.
   - `scripts/verify_release.py` and `test/release.py` checking the new files the way they check the
     programs (reproducible builds, hashes in `SHA256SUMS`; the manager files are not in it).

0. **macOS: still to check.** The signed build in release Firefox on a Mac (only LibreWolf's full build
   was tested, 2026-11-13); the Intel build on a real Intel Mac (it ran under Rosetta, after the ad hoc
   signing `install.sh` does); agents other than Claude Code and Claude desktop, whose settings paths
   on macOS were taken to be the same as on Linux.

1. **Manager app: open items from the 2026-11-12/13 session** (UI reworked into tabs).
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
   - Manager reworked 2026-11-14: it reads with its own foxreach (bundled, or the
     checkout a development build came from), acts through the foxreach the browsers and agents run
     (`status --json` `setup`: release program or checkout), Install downloads the release when there
     is no bundled copy (`foxreach install-program`), a checkout gets "Use the release program", tabs
     that cannot be read say why with Install. Not yet clicked in the window: Install, the switch
     dialog. `scripts/browsers.py` (setup scripts) does not see through wrapper scripts as
     `bridge/addon.mjs` now does. Merging into the `macos` branch will conflict in `bridge/update.mjs`
     (asset choice) and `test/status_json.py`.
   - Several browsers at once (2026-11-14): each native host has its own socket and
     info file in $XDG_RUNTIME_DIR/foxreach (`bridge/connected.mjs`), the first also serves
     browser.sock for older agents; agents get `browsers_list` / `browser_select` (one browser: used
     automatically; several: the default from `foxreach browsers default`, else the agent must
     choose). Tests: `test/multi_browser.py` (two hosts), `test/multi_real.py` (a real browser).
     Not yet on the `macos` branch (macOS socket folder differs).
   - Tor Browser done 2026-11-14 (`bridge/tor.mjs`): manifest in its own data folder
     (`Browser/TorBrowser/Data/Browser/.mozilla/native-messaging-hosts`, found by asking the running
     browser for XREUserNativeManifests), add-on must be allowed in private windows; all three builds
     run (`test/browser_compat.py --private`). Offered with a warning, connected only by name. Not
     yet: torbrowser-launcher's Flatpak; clicking Connect in the manager window.
   - More browsers still to add: Mullvad Browser (same start-script family as Tor, likely the same
     data-folder rule; not installed here), IceCat. Helium is Chromium-based (no support).
   - The AppImage and Flatpak with the new UI (tabs, search, Test, logos): built by CI from main
     f44430b; the aarch64 ones opened and drew on Asahi (KDE Plasma, Wayland), the x86_64 ones were
     not launched by hand.
   - AppImage segfault in `wl_proxy_destroy` (core dump 2026-11-12 23:24, on closing the window):
     iced-winit's detached Wayland thread dropped its objects while the main thread was already in
     `exit()`. Closing now ends the process directly (`Message::Quit`, `on_app_exit`,
     `on_close_requested` in `manager/src/app.rs`, 2026-11-14). Not reproduced either before or after
     (5 clean closes each, `~/Documents/Claude/2026-11-14/manager-close-crash/close-loop.sh`); the
     header's own close button path was not exercised on screen.
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
   Not yet: a published Flatpak repository and `.flatpakref` (signing key, Pages), Windows and macOS.
   (The release workflow builds and attaches the AppImage and Flatpak bundles, item 0.) The original
   plan: a desktop app that manages foxreach end to end, built on the same
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
   The program runs on Linux and macOS (host registration, socket, release builds, `install.sh`,
   `foxreach update`); Windows needs the host registration and agent paths ported first.

3. **addons.mozilla.org listing: 0.4.0 approved 2026-11-18.** One version number for both, so the
   listing and the GitHub release stay in sync. 0.4.0 (listed) is approved and public; 0.3.4
   (listed) was approved earlier the same day; 0.3.3 and 0.3.1 (listed) are disabled; 0.3.2 and
   0.3.0 are signed unlisted. The signed 0.3.4 file was never published to
   `docs/updates-signed.json` (the owner's choice: 0.4.0 replaces it). Status and reviewer questions:
   https://addons.mozilla.org/en-US/developers/addon/fe1f53e268044b529443/versions
   - Done (branch `manager-test-ci`, not pushed yet): the signed 0.4.0 file, downloaded from
     addons.mozilla.org and checked against its stated hash, holds exactly the listed build of
     94830a4 plus `META-INF/`; `release.py --signed` (new, for a version already released) added it
     to `docs/` with its entry in `docs/updates-signed.json`. It has no `update_url`, so signed
     0.3.2 copies take it from that list and then update from addons.mozilla.org.
   - Left: push (ask first), then `release.py --verify` (the signed list should show 0.4.0, hash
     matches). Add the file to the v0.4.0 release, outside `SHA256SUMS`, so the README's install
     step finds it: `gh release upload v0.4.0 docs/foxreach-0.4.0-signed.xpi --repo nilltadios/foxreach`.
   - Not yet seen: a release Firefox on signed 0.3.2 actually moving to 0.4.0 and then asking
     addons.mozilla.org (Firefox's source says it does, `XPIInstall.sys.mjs` UpdateChecker).
   - `scripts/verify_release.py` compares a signed file with the unlisted build only, so a listed
     signed file named in a release's `SHA256SUMS` would be reported as differing in
     `manifest.json` (its missing `update_url`).
   - The listing shows Mozilla's default icon: upload `amo/icon-128.png` with
     `scripts/amo_listing.py` (prepared and dry-run only; see `amo/LISTING.md`).

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
