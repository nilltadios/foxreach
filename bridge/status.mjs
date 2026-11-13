// `foxreach status --json`: everything the manager app shows, as one JSON object. The app reads
// this rather than parsing the text other commands print. Reads only; changes nothing.
import fs from "node:fs";
import { browserStatus } from "./addon.mjs";
import { agentStatus } from "./agents.mjs";
import { compiled, hostStatus, mcpCommand } from "./install.mjs";
import { VERSION } from "./version.mjs";

export function status() {
  return {
    format: 1,
    version: VERSION,
    compiled,
    program: compiled ? fs.realpathSync(process.execPath) : null,
    mcpCommand: mcpCommand().join(" "),
    agents: agentStatus(),
    host: hostStatus(),
    browsers: browserStatus(),
  };
}
