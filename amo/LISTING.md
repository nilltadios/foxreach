# Text for the addons.mozilla.org listing

Used by `scripts/sign.py --channel listed` (through `metadata-listed.json`) and for the fields that
are filled in by hand on the add-on's page there.

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
