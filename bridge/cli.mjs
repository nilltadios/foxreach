#!/usr/bin/env bun
// The one program users install: MCP server, native messaging host, and installer of the host.
// `bun build --compile bridge/cli.mjs` makes it a single file (scripts/build_binary.py).
import { VERSION } from "./version.mjs";

const USAGE = `foxreach ${VERSION}: lets local MCP agents use browser tabs you share with them.

  foxreach install [--browser flatpak:<application id>]... [--no-flatpak] [--agents LIST]
                      let the browser start foxreach: writes the native messaging manifest for
                      Firefox-based browsers, and prepares installed Flatpak browsers.
                      With --browser, prepares only the Flatpak browsers named.
                      Then adds the MCP server to every agent found on this machine (each
                      settings file is backed up first). --agents claude-code,codex,... names
                      them instead; --agents none adds it to none.
  foxreach uninstall [--keep-agents] [--part host|flatpaks|flatpak:<application id>|agents]...
                      remove what install wrote, and foxreach's entry from every agent;
                      --part removes only that part (repeat it for several)
  foxreach agents [add [--agents LIST] | remove [--agents LIST]]
                      list the agents foxreach knows (found or not, foxreach set or not);
                      add: only add the MCP server to agents; remove: only take it out
  foxreach addon [latest] [--json]
                      the browsers and profiles found, with the add-on's version in each;
                      latest: the newest signed build
  foxreach addon install --browser SPEC --profile DIR
                      open the signed add-on in that browser profile, which asks to add or update
                      it (SPEC: a path, a command, or flatpak:<application id>)
  foxreach addon remove --profile DIR [--browser SPEC]
                      take the add-on out of a profile; the browser must be closed
  foxreach test [--agents LIST | --self] [--json]
                      can each agent reach a browser now? Starts the command its settings name
                      for foxreach and asks the browser for its status (no prompt in the browser)
  foxreach status --json
                      the version, agents, browser side and add-on versions as JSON
                      (for the foxreach manager app); changes nothing
  foxreach update [--check [--json]] [--force]
                      replace this program with the latest release (checksum checked, the old
                      one kept as <program>.previous) and refresh what install wrote; --check
                      only says whether a newer version exists. The add-on updates itself
  foxreach mcp        run the MCP server on stdin/stdout; this is the command agents are given
  foxreach host       run the native messaging host; the browser does this itself
  foxreach version
`;

const [command, ...rest] = process.argv.slice(2);
const valuesOf = (flag) => rest.flatMap((a, i) => (rest[i - 1] === flag ? [a] : a.startsWith(`${flag}=`) ? [a.slice(flag.length + 1)] : []));
/** --agents a,b (null when not given: every agent found; [] for "none"). */
function agentList() {
  if (!rest.some((a) => a === "--agents" || a.startsWith("--agents="))) return null;
  const named = valuesOf("--agents").flatMap((v) => v.split(",")).map((v) => v.trim()).filter(Boolean);
  return named.includes("none") ? [] : named;
}
// The browser starts the host with the manifest's path and the add-on's id as arguments.
const startedByBrowser = process.argv.slice(2).some((a) => a.endsWith("foxreach.json") || a === "foxreach@foxreach.local");

if (command === "host" || startedByBrowser) {
  await import("./host.mjs");
} else if (command === "mcp") {
  await import("./mcp.mjs");
} else if (command === "update") {
  const { checkLatest, update } = await import("./update.mjs");
  try {
    if (rest.includes("--check") && rest.includes("--json")) {
      console.log(JSON.stringify(await checkLatest()));
      process.exit(0);
    }
    process.exitCode = await update({ check: rest.includes("--check"), force: rest.includes("--force") });
  } catch (e) {
    console.error(`foxreach: ${e.message}`);
    process.exit(1);
  }
} else if (command === "agents") {
  const { describeAgents, registerAgents, unregisterAgents } = await import("./agents.mjs");
  const { mcpCommand } = await import("./install.mjs");
  try {
    if (rest[0] === "add") registerAgents({ command: mcpCommand(), only: agentList() });
    else if (rest[0] === "remove") unregisterAgents({ only: agentList() });
    else describeAgents();
  } catch (e) {
    console.error(`foxreach: ${e.message}`);
    process.exit(1);
  }
} else if (command === "install" || command === "uninstall") {
  const { install, uninstall } = await import("./install.mjs");
  const browsers = valuesOf("--browser");
  const agents = agentList();
  try {
    if (command === "install") install({ browsers, flatpaks: !rest.includes("--no-flatpak"), agents });
    else {
      const parts = valuesOf("--part");
      uninstall({ agents: !rest.includes("--keep-agents"), parts: parts.length ? parts : null });
    }
  } catch (e) {
    console.error(`foxreach: ${e.message}`);
    process.exit(1);
  }
} else if (command === "addon") {
  const addon = await import("./addon.mjs");
  const one = (flag) => valuesOf(flag)[0] ?? null;
  try {
    if (rest[0] === "latest") {
      const l = await addon.latestAddon();
      console.log(rest.includes("--json") ? JSON.stringify(l) : `foxreach ${l.version}: ${l.link}`);
    } else if (rest[0] === "install") {
      if (!one("--browser") || !one("--profile")) throw new Error("addon install needs --browser and --profile (see foxreach addon)");
      await addon.installAddon({ spec: one("--browser"), profile: one("--profile") });
    } else if (rest[0] === "remove") {
      if (!one("--profile")) throw new Error("addon remove needs --profile");
      addon.removeAddon({ profile: one("--profile"), spec: one("--browser") });
    } else {
      const rows = addon.browserStatus();
      if (rest.includes("--json")) console.log(JSON.stringify(rows));
      else for (const b of rows) {
        console.log(`${b.name} (${b.packaging}) ${b.spec}`);
        for (const p of b.profiles) {
          const a = p.addon;
          const what = !a ? "no foxreach" : `foxreach ${a.version ?? "?"}${a.full ? " (full build)" : ""}${a.active === false ? " (disabled)" : ""}`;
          console.log(`  ${p.name.padEnd(20)} ${what.padEnd(28)} ${p.inUse ? "open " : "     "} ${p.path}`);
        }
      }
    }
  } catch (e) {
    console.error(`foxreach: ${e.message}`);
    process.exit(1);
  }
} else if (command === "test") {
  const { probe, testAgents } = await import("./agenttest.mjs");
  try {
    if (rest.includes("--self")) {
      // This program's own MCP server: which browser answers right now, whatever the agents say.
      const { mcpCommand } = await import("./install.mjs");
      const r = await probe(mcpCommand());
      console.log(rest.includes("--json") ? JSON.stringify(r) : r.ok ? `reaches ${r.browser} (add-on ${r.addon})` : r.error);
      process.exit(0);
    }
    const rows = await testAgents({ only: agentList() });
    if (rest.includes("--json")) console.log(JSON.stringify(rows));
    else if (!rows.length) console.log("No agent has foxreach set.");
    else for (const r of rows) console.log(`${r.name}: ${r.ok ? `reaches ${r.browser} (add-on ${r.addon})` : r.error}`);
  } catch (e) {
    console.error(`foxreach: ${e.message}`);
    process.exit(1);
  }
} else if (command === "status") {
  const { status } = await import("./status.mjs");
  console.log(JSON.stringify(status(), null, rest.includes("--json") ? 0 : 2));
} else if (command === "version" || command === "--version") {
  console.log(VERSION);
} else {
  console.log(USAGE);
  process.exit(command && command !== "help" && command !== "--help" ? 2 : 0);
}
