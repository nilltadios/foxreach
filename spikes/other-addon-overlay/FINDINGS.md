# Another add-on's prompt inside a page

Question (2026-11-18, Proton Pass on Outlook's sign-in): foxreach screenshots of Proton Pass's PIN
prompt came out blank, `find` could not locate it, and typing the six digits at once failed while
one digit every 0.4 s (desktop input) worked. What can foxreach do with another add-on's UI?

Probe: [probe.py](probe.py) with [pinbox/](pinbox/), a stand-in add-on whose content script puts
its own `moz-extension:` page, in an iframe inside a closed shadow root, over the site. The page
has six one-digit boxes that move focus to the next one 120 ms after each digit, as such prompts
do. Throwaway profile, LibreWolf 159.0.1 AppImage, headless.

```
FOXREACH_BROWSER=~/AppImages/librewolf.appimage uv run --python 3.15 spikes/other-addon-overlay/probe.py
```

Output (2026-11-18, after `delay_ms` was added to `type_text`):

```
screenshot shows the other add-on's iframe: yes (pixel (208, 0, 0))
read_page lists its inputs: no
read_page mentions the frame at all: no
find returns: - textbox "Email" #email [r2]
click by point into the first box: Clicked div at (75, 334).
type_text, all at once: Typed 6 characters into an element foxreach cannot see.
type_text allow_non_text: Typed 6 characters into an element foxreach cannot see.
...the boxes then hold: 1_____
type_text delay_ms=250: Typed 6 characters into an element foxreach cannot see.
...the boxes then hold: 123456
background tab, delay_ms=250: the boxes hold: 14____
background tab, delay_ms=1200: the boxes hold: 123456
```

## What this shows

- **Screenshots include another add-on's in-page frame** when the page is being painted. The blank
  Proton Pass shots on 2026-11-18 were most likely taken while the display was off (that session
  turned the screens off; the browser then paints nothing, see `status` with `tabId`), or were of
  Proton Pass's toolbar popup, which is not part of any tab (below).
- **`read_page` and `find` never see inside it.** Its document belongs to the other add-on
  (`moz-extension:`); a content script cannot be injected there, and foxreach leaves such frames
  out of `read_page` on purpose (`extension/background.js`, "Another add-on's frame"). Point
  coordinates from a screenshot are the way to click into it.
- **Trusted typing does reach it**: the browser delivers the keys to whatever has focus, the other
  add-on's frame included. The reply says "an element foxreach cannot see", and cannot warn when
  keys are lost.
- **Fast typing loses keys in boxes that move focus themselves**: all six keys reach the first box
  before it moves focus, and `maxlength=1` drops five. `type_text` now has `delay_ms`, one key per
  call with a pause between (the keys follow focus). In a background tab the other add-on's own
  timers are slowed to one a second, so the delay must be over a second there, or the tab brought
  to the front first.

## Limits that remain

- **Toolbar popups (browser-action panels) of other add-ons** are separate browser windows, not
  part of a tab: `screenshot` (`tabs.captureTab`) does not show them and no tool can click or type
  into them. A window-level capture would need privileged code that draws the browser's own
  windows and panels, and input into them would be input into another add-on's UI outside any page,
  which foxreach's rules (agents act in tabs they may use) do not cover. Not built; see TODO.md.
- Reading another add-on's frame (its text, its fields) is deliberately not offered: it is the
  other add-on's private UI, often a password manager's.
