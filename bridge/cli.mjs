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
  foxreach uninstall [--keep-agents]
                      remove what install wrote, and foxreach's entry from every agent
  foxreach agents [add [--agents LIST] | remove]
                      list the agents foxreach knows (found or not, foxreach set or not);
                      add: only add the MCP server to agents; remove: only take it out
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
} else if (command === "agents") {
  const { describeAgents, registerAgents, unregisterAgents } = await import("./agents.mjs");
  const { mcpCommand } = await import("./install.mjs");
  try {
    if (rest[0] === "add") registerAgents({ command: mcpCommand(), only: agentList() });
    else if (rest[0] === "remove") unregisterAgents();
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
    else uninstall({ agents: !rest.includes("--keep-agents") });
  } catch (e) {
    console.error(`foxreach: ${e.message}`);
    process.exit(1);
  }
} else if (command === "version" || command === "--version") {
  console.log(VERSION);
} else {
  console.log(USAGE);
  process.exit(command && command !== "help" && command !== "--help" ? 2 : 0);
}
