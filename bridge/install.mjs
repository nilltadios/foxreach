// Registers the native messaging host for this user, so the browser can start it. Used by
// `foxreach install` and `foxreach uninstall`; touches no browser profile.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { registerAgents, unregisterAgents } from "./agents.mjs";

export const ADDON_ID = "foxreach@foxreach.local";
const HOST_NAME = "foxreach";
// What an AppImage browser exports for itself; none of it may reach this program.
const UNSET = "unset LD_LIBRARY_PATH LD_PRELOAD APPDIR APPIMAGE ARGV0 OWD\n";
// Folders a browser reads native-messaging manifests from, relative to the home folder. On Linux
// every Firefox-based build reads .mozilla; a fork may read its own folder as well. On macOS they
// read one folder in Application Support (LibreWolf too: checked with LibreWolf 160 on macOS 27).
const FORK_DIRS = [".librewolf"];
const darwin = process.platform === "darwin";
const MAC_HOSTS = "Library/Application Support/Mozilla/NativeMessagingHosts";

/** Where to write the manifest outside a sandbox; with all, every folder one may have gone to. */
function hostDirs(home, all = false) {
  if (darwin) return [path.join(home, MAC_HOSTS)];
  return [".mozilla", ...FORK_DIRS.filter((f) => all || exists(path.join(home, f)))]
    .map((d) => path.join(home, d, "native-messaging-hosts"));
}
// Flatpak browsers, and which of those folders each keeps between runs.
const FLATPAKS = {
  "org.mozilla.firefox": [".mozilla"],
  "io.gitlab.librewolf-community": [".mozilla", ".librewolf"],
  "one.ablaze.floorp": [".mozilla"],
  "net.waterfox.waterfox": [".mozilla"],
  "app.zen_browser.zen": [".mozilla"],
};

/** True inside the single-file build, where this program is its own runtime. */
export const compiled = typeof Bun !== "undefined" && Bun.main.startsWith("/$bunfs/");

/** The command that starts this program again: the binary itself, or the runtime and the script. */
function self() {
  if (compiled) return [fs.realpathSync(process.execPath)];
  return [process.execPath, fileURLToPath(new URL("./cli.mjs", import.meta.url))];
}

/** What agents run: this program with "mcp". */
export const mcpCommand = () => [...self(), "mcp"];

const quote = (s) => `"${String(s).replace(/(["$`\\])/g, "\\$1")}"`;

function writeWrapper(file, command) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `#!/bin/sh\n${UNSET}exec ${command.map(quote).join(" ")} host\n`, { mode: 0o755 });
  fs.chmodSync(file, 0o755);
}

function writeManifest(folder, wrapper) {
  fs.mkdirSync(folder, { recursive: true });
  const file = path.join(folder, `${HOST_NAME}.json`);
  fs.writeFileSync(file, `${JSON.stringify({
    name: HOST_NAME,
    description: "foxreach bridge between the browser and local MCP agents",
    path: wrapper,
    type: "stdio",
    allowed_extensions: [ADDON_ID],
  }, null, 2)}\n`);
  return file;
}

const dataDir = (home) => path.join(process.env.XDG_DATA_HOME || path.join(home, ".local/share"), "foxreach");
const exists = (p) => fs.existsSync(p);

function flatpak(...args) {
  return spawnSync("flatpak", args, { encoding: "utf8" });
}

/** Installed Flatpak browsers we know, or the ones named with --browser flatpak:<id>. */
function flatpakBrowsers(named) {
  if (named.length) return named;
  const listed = flatpak("list", "--app", "--columns=application");
  if (listed.error || listed.status !== 0) return [];
  return listed.stdout.split(/\s+/).filter((id) => id in FLATPAKS);
}

/**
 * A Flatpak browser starts the host inside its sandbox, which sees neither this program nor the
 * socket folder. So a copy goes into the Flatpak's own folder, and the Flatpak is given the one
 * folder where the socket for agents lives.
 */
function installFlatpak(id, home, log) {
  if (!compiled) throw new Error(`A Flatpak browser (${id}) needs the single-file foxreach program, or scripts/build_install.py from a checkout.`);
  const base = path.join(home, ".var/app", id);
  const copy = path.join(base, "foxreach/foxreach");
  fs.mkdirSync(path.dirname(copy), { recursive: true });
  fs.copyFileSync(self()[0], copy);
  fs.chmodSync(copy, 0o755);
  const wrapper = path.join(base, "foxreach/foxreach-host");
  writeWrapper(wrapper, [copy]);
  const runtimeDir = process.env.XDG_RUNTIME_DIR || `/run/user/${process.getuid()}`;
  fs.mkdirSync(path.join(runtimeDir, "foxreach"), { recursive: true, mode: 0o700 });
  const granted = flatpak("override", "--user", "--filesystem=xdg-run/foxreach", id);
  if (granted.status !== 0) throw new Error(`flatpak override failed for ${id}: ${granted.stderr || granted.error}`);
  // Once the browser has run, the folders it keeps exist; anything else in its home is thrown away.
  const dirs = FLATPAKS[id] ?? [".mozilla"];
  const kept = dirs.filter((d) => exists(path.join(base, d)));
  for (const d of kept.length ? kept : dirs) log(writeManifest(path.join(base, d, "native-messaging-hosts"), wrapper));
  log(`${copy} (a copy the sandbox can run)`);
  log(`permission for ${id}: xdg-run/foxreach`);
}

export function install({ browsers = [], flatpaks = true, agents = null, home = os.homedir(), log = console.log } = {}) {
  const command = self();
  const named = browsers.filter((b) => b.startsWith("flatpak:")).map((b) => b.slice(8));
  // Naming browsers limits the work to them; otherwise everything found is prepared.
  if (!named.length) {
    const wrapper = path.join(dataDir(home), "foxreach-host");
    writeWrapper(wrapper, command);
    log(wrapper);
    for (const d of hostDirs(home)) log(writeManifest(d, wrapper));
  }
  if (named.length && darwin) throw new Error("Flatpak browsers exist on Linux only.");
  if ((flatpaks || named.length) && !darwin) for (const id of flatpakBrowsers(named)) installFlatpak(id, home, log);
  const mcp = mcpCommand();
  log("\nThe browser can now start foxreach.");
  log(`The MCP server's command, for any agent: ${mcp.join(" ")}`);
  if (agents === null || agents.length) {
    log(agents === null ? "Agents found on this machine:" : "Agents:");
    registerAgents({ command: mcp, only: agents, h: home, log });
  }
  log("Next: install the foxreach add-on in the browser, if it is not there yet. " +
    "Agents that are running pick up the server when they start a new session.");
  log("Undo with: foxreach uninstall");
}

export function uninstall({ home = os.homedir(), agents = true, log = console.log } = {}) {
  const remove = (p) => {
    if (!exists(p)) return;
    fs.rmSync(p, { recursive: true, force: true });
    log(`removed ${p}`);
  };
  for (const d of hostDirs(home, true)) remove(path.join(d, `${HOST_NAME}.json`));
  remove(path.join(dataDir(home), "foxreach-host"));
  const apps = path.join(home, ".var/app");
  for (const id of exists(apps) ? fs.readdirSync(apps) : []) {
    const base = path.join(apps, id);
    if (!exists(path.join(base, "foxreach/foxreach-host"))) continue;
    for (const d of [".mozilla", ...FORK_DIRS]) remove(path.join(base, d, "native-messaging-hosts", `${HOST_NAME}.json`));
    // A profile made for foxreach may live beside the copy; leave the folder if it holds one.
    remove(path.join(base, "foxreach/foxreach"));
    remove(path.join(base, "foxreach/foxreach-host"));
    remove(path.join(base, "foxreach/bridge"));
    remove(path.join(base, "foxreach/bun"));
    const withdrawn = flatpak("override", "--user", "--nofilesystem=xdg-run/foxreach", id);
    if (withdrawn.status === 0) log(`withdrew the xdg-run/foxreach permission from ${id}`);
  }
  if (agents) unregisterAgents({ h: home, log });
  log("The add-on itself is removed in the browser, under Add-ons.");
}
