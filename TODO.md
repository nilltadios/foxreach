# To do

Open items, newest first. Remove an item when it is done.

## Make foxreach installable on normal Firefox, and more trustworthy (noted 2026-11-09)

1. **Sign the untrusted build through addons.mozilla.org**, unlisted first (`web-ext sign` with an
   AMO API key). A signed build installs permanently on release Firefox with no prefs changed. Do
   this first: it is small.
2. **Decide on WebDriver BiDi for release Firefox.** The full build cannot be signed (Mozilla keeps
   the Experiment privilege for its own add-ons), so release Firefox loses trusted input, `drag`,
   `read_console`, `handle_dialog` and file drops. BiDi could give them back; it needs the browser
   started with a flag, opens a local port, and shows `navigator.webdriver` to pages. Not tested.
3. **List on AMO**, so users get Mozilla's review and updates through the store instead of GitHub Pages.
4. **No Bun or `uv` for users:** compile the bridge to one binary; ship an installer or packages that
   register the native host.
5. **Narrower permissions:** site access per site or on demand instead of `<all_urls>` up front.
6. **Per-agent approval:** today any program running as the user can drive shared tabs through the
   socket. Add an approval prompt in the popup.
7. **Verifiable releases:** tags, checksums, and a build that reproduces the published file.
