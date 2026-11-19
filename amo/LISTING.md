# Text for the addons.mozilla.org listing

Used by `scripts/sign.py --channel listed` (through `metadata-listed.json`), `scripts/amo_listing.py`
(the icon) and for the fields that
are filled in by hand on the add-on's page there.

## Icon

AMO does not take the listing icon from the add-on's manifest: the page shows Mozilla's default icon
until an author uploads one (Edit Product Page, Media, or the add-on Edit API's `icon` field). The
add-on ships only `extension/icons/icon.svg`, so the icon for the listing is
[icon-128.png](icon-128.png), drawn from that SVG at 128x128 (AMO's recommended size; it must be a
square PNG or JPEG):

```bash
uv run --python 3.15 scripts/amo_listing.py --render    # redraw icon-128.png (Inkscape)
uv run --python 3.15 scripts/amo_listing.py --dry-run   # show the request; sends nothing
uv run --python 3.15 scripts/amo_listing.py             # upload it as the listing icon
```

The upload is one `PATCH https://addons.mozilla.org/api/v5/addons/addon/foxreach%40foxreach.local/`
with the PNG as the multipart field `icon`, signed with the same API key as `scripts/sign.py`
(`~/.config/foxreach/amo.env`). It changes only the icon; uploading another one replaces it.

## Summary

Lets AI agents on your own computer (any MCP client) operate the browser tabs you share with them.
No cloud service, no telemetry.

## Description

foxreach lets an AI agent that runs on your computer (Claude Code, Codex, or any other program that
speaks MCP) read and operate browser tabs: open pages, read them, click, type, fill forms.

You stay in charge, from the toolbar button:

- Agents see only the tabs they open and the tabs you share. Your other tabs stay private.
- A new agent has to be allowed by you before it can do anything.
- You choose the sites agents may use: every site, or only the ones you list.
- You can block sites, pause all access, and see what each agent did.

Nothing leaves your computer through foxreach. It talks only to a small program on the same
machine, which you install from https://github.com/nilltadios/foxreach. There is no account, no
server and no telemetry.

Limits of this build: clicks and keys are ordinary script events, which a few sites ignore, and
dragging, reading the console and answering page dialogs are not available. Pictures of pages need
"Every site". The project's page explains the build for browsers that allow more.

## Privacy policy

foxreach collects nothing and sends nothing to its author or to anyone else. Page content goes only
to the agent programs you allow, on your own computer. Settings (blocked and allowed sites, allowed
agents) are stored in the browser's local storage for the add-on.

## Categories

Web Development.

## Licence

MIT.
