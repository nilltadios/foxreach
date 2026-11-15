// `foxreach status --json`: everything the manager app shows, as one JSON object. The app reads
// this rather than parsing the text other commands print. Reads only; changes nothing.
import fs from "node:fs";
import { browserStatus } from "./addon.mjs";
import { agentStatus } from "./agents.mjs";
import { connectedBrowsers, readDefault } from "./connected.mjs";
import { compiled, hostStatus, mcpCommand } from "./install.mjs";
import { setupInUse } from "./setup.mjs";
import { VERSION } from "./version.mjs";

export async function status() {
  const agents = agentStatus();
  const host = hostStatus();
  return {
    format: 1,
    version: VERSION,
    compiled,
    program: compiled ? fs.realpathSync(process.execPath) : null,
    mcpCommand: mcpCommand().join(" "),
    // The foxreach the browsers and agents actually run, which need not be this one.
    setup: setupInUse({ manifests: host.manifests, agents }),
    agents,
    host,
    browsers: browserStatus(),
    // The browsers serving agents right now, and the profile agents use when several are.
    connected: (await connectedBrowsers()).map(({ socket, legacy, ...b }) => b),
    defaultBrowser: readDefault(),
  };
}
