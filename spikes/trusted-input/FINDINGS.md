# Trusted input in LibreWolf: findings

This records the first feasibility tests. The first-click problem and the "not tested yet" list below were later resolved in the product. Teardown core dumps still occur with the AppImage; ../../README.md gives the workaround.

Tested 2026-11-06 on LibreWolf 159.0.1 (AppImage), headless, on throwaway profiles.

## Reproduce

```bash
cd spikes/trusted-input   # from the repository root
uv run --python 3.15 run_spike.py --permanent   # daily-use case; expect 4 PASS, exit 0
uv run --python 3.15 run_spike.py               # temporary install, Marionette attached
uv run --python 3.15 diagnose_manifest.py ext   # LibreWolf's own verdict on an add-on
```

## Results

| Check | Result |
|---|---|
| Event dispatched from a content script | `isTrusted=false` |
| Click through the Experiment API | `isTrusted=true` |
| Typing through the Experiment API | `isTrusted=true`, text reached the input |
| Permanent unsigned install, after a restart without Marionette | Experiment API present, `navigator.webdriver=false`, all of the above hold |

The `--permanent` run passed on 4 of 5 runs. The failing run predates the first-click
probe and is the delivery problem described below.

## What the add-on needs

Both prefs must be set in the profile. Neither is on by default in this build:

- `xpinstall.signatures.required=false`: without it, unsigned add-ons are rejected with
  "signature is required", including add-ons dropped into a profile's `extensions/`.
- `extensions.experiments.enabled=true`: without it, `experiment_apis` is rejected as
  "requires a privileged add-on".

The first needs reading with care: `greprefs.js` says `false`, but the browser's
`firefox.js` default of `true` wins. Same pattern for the second, in the opposite direction.

Firefox release builds ignore these prefs (`AddonSettings.sys.mjs`: "Official releases
ignore this preference"), so trusted input is LibreWolf-only, or Nightly/Developer Edition.
Not tested on those. A plain release Firefox would get untrusted input and need AMO signing.

## Security cost

- Signature checks off means the profile accepts any unsigned add-on the user agrees to install.
- An Experiment add-on runs with full browser privileges. Any add-on using this route can
  do anything the browser can.
- A dedicated profile limits both. So does keeping the privileged code small and auditable.

## API notes for 159

- `windowUtils.sendMouseEvent` is gone. Use chrome-only
  `window.synthesizeMouseEvent(type, x, y, data, options)`.
- Keyboard: `nsITextInputProcessor` with `beginInputTransactionForTests(content)`.
- These are internal APIs and can change in any release, as `sendMouseEvent` just did.
  The build needs a version check and this spike as a regression test.
- `librewolf.cfg` forces `devtools.debugger.remote-enabled` off on every start, which
  overrides `user.js`. Marionette works and is used by this harness only.

## Open problems

1. **The first click after a fresh start can be silently dropped.** The call reports success,
   but no events arrive. Across the 4 probed runs, it was delivered on attempt 1 twice and on
   attempt 2 twice (about 350 ms apart). Cause not established. Delivery was only measured
   after a restart with one tab. The real click tool must confirm delivery and retry, not
   trust the return value.
2. **Test teardown leaves SIGBUS core dumps.** 37 today, all from these test instances between
   22:37 and 22:44 (small helper processes such as `-contentproc`, about 1.7 MB each). The
   previous 30 days had 6, all on 2026-10-14. Signalling only the browser, not the AppImage
   runtime, did not stop them, so that explanation was wrong or incomplete. The harness should
   next try a graceful quit and waiting for helpers. Results are not affected.

## Not tested yet

Visible (non-headless) windows, iframes, HiDPI coordinates, scrolling, drag, and any real site.
