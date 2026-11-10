#!/usr/bin/env bun
// The one program users install: MCP server, native messaging host, and installer of the host.
// `bun build --compile bridge/cli.mjs` makes it a single file (scripts/build_binary.py).
import { VERSION } from "./version.mjs";

const USAGE = `foxreach ${VERSION}: lets local MCP agents use browser tabs you share with them.

  foxreach install [--browser flatpak:<application id>]... [--no-flatpak]
                      let the browser start foxreach: writes the native messaging manifest for
                      Firefox-based browsers, and prepares installed Flatpak browsers.
                      With --browser, prepares only the Flatpak browsers named
  foxreach uninstall  remove what install wrote
  foxreach mcp        run the MCP server on stdin/stdout; this is the command agents are given
  foxreach host       run the native messaging host; the browser does this itself
  foxreach version
`;

const [command, ...rest] = process.argv.slice(2);
// The browser starts the host with the manifest's path and the add-on's id as arguments.
const startedByBrowser = process.argv.slice(2).some((a) => a.endsWith("foxreach.json") || a === "foxreach@foxreach.local");

if (command === "host" || startedByBrowser) {
  await import("./host.mjs");
} else if (command === "mcp") {
  await import("./mcp.mjs");
} else if (command === "install" || command === "uninstall") {
  const { install, uninstall } = await import("./install.mjs");
  const browsers = rest.flatMap((a, i) => (rest[i - 1] === "--browser" ? [a] : a.startsWith("--browser=") ? [a.slice(10)] : []));
  try {
    if (command === "install") install({ browsers, flatpaks: !rest.includes("--no-flatpak") });
    else uninstall();
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
