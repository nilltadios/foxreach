# WebDriver BiDi for browsers that refuse the Experiment: tested, not adopted

Question (2026-11-10): release Firefox refuses foxreach's privileged code, so the signed build has
no trusted input, `drag`, `read_console`, `handle_dialog` or file drops. Could the browser's own
automation protocol, WebDriver BiDi, give them back?

Decision: **no.** foxreach does not use BiDi. What it would return is real, but it costs the two
things foxreach is for: working on the sites you are logged in to, and letting agents reach only
what you share.

## What was run

`uv run --python 3.15 --with websockets spikes/bidi/probe.py <browser>`, on a throwaway profile,
headless, the browser started with `--remote-debugging-port`. Same result on both:

- Firefox 159.0.1, the Flatpak (`org.mozilla.firefox`), an official release
- Firefox Developer Edition 161.0b1, Mozilla's tarball

| Question | Result |
|---|---|
| Release Firefox offers BiDi when started with the flag | yes |
| A click is trusted (`event.isTrusted`) | yes |
| Console messages can be read | yes |
| A page dialog can be seen and answered | yes |
| A file can be attached to a file input | yes |
| A drag delivers data to a drop target (HTML drag and drop) | no: `dragstart` fired, `drop` did not |
| Pages can tell the browser is automated (`navigator.webdriver` is true) | yes |
| The client sees every tab of the browser | yes |
| The client can read cookies (`storage.getCookies`) | yes |
| The port listens on this machine only | yes, 127.0.0.1 |
| A local program needs a password or key to connect | no |
| A web page can connect | no: the browser refuses a request that names a site as its origin |

## Why not

1. **Sites see it.** With the port open, every page reads `navigator.webdriver === true`. Sign-in
   pages and bot checks use exactly that to refuse a browser. A tool for the user's own logged-in
   sites would fail on the sites that matter most. Not measured on real sites here; the flag itself
   was.
2. **It gives away the whole browser.** The port has no password. Any program on the machine that
   can open a local TCP connection gets every tab and every cookie, not the tabs the user shared.
   A Unix socket is closed to other users by its file mode; a TCP port on 127.0.0.1 is not, and a
   sandboxed app with network access can reach it too. That undoes the popup's controls: shared
   tabs, the site list, blocked sites, agent approval.
3. **It is not a normal start.** The browser has to be started with a flag every time, which a
   user's launcher, session restore and "open link" from other apps do not do.
4. **It does not return everything.** HTML drag and drop did not complete in this test.

## What would change this

A way to hand one add-on trusted input without opening the browser to everything: for example
BiDi over a pipe with a per-session secret, limited to chosen tabs, and without the
`navigator.webdriver` flag. Firefox offers none of these today.
