// Explains a failed connection to the native host: is any Firefox-based browser running at all?
// The add-on works in Firefox and its derivatives, so this looks for all of them, not one.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// Executable names of Firefox-based browsers, with the name to show. AppImage, Flatpak and distro
// builds all end in one of these.
const GECKO = new Map([
  ["firefox", "Firefox"], ["firefox-bin", "Firefox"], ["firefox-esr", "Firefox ESR"],
  ["librewolf", "LibreWolf"], ["librewolf-bin", "LibreWolf"],
  ["floorp", "Floorp"], ["waterfox", "Waterfox"], ["zen", "Zen"], ["zen-bin", "Zen"],
  ["mullvadbrowser", "Mullvad Browser"], ["palemoon", "Pale Moon"], ["icecat", "IceCat"],
]);

/** Names of the Firefox-based browsers running now, or null where processes cannot be listed. */
export function runningBrowsers(procRoot = process.env.FOXREACH_PROC || "/proc") {
  if (process.env.FOXREACH_PS || (process.platform === "darwin" && !process.env.FOXREACH_PROC)) return psBrowsers();
  let entries;
  try {
    entries = fs.readdirSync(procRoot);
  } catch {
    return null; // no /proc (macOS, BSD): say nothing about what runs
  }
  const found = new Set();
  for (const pid of entries) {
    if (!/^\d+$/.test(pid)) continue;
    try {
      const name = GECKO.get(path.basename(fs.readlinkSync(path.join(procRoot, pid, "exe"))));
      if (name) found.add(name);
    } catch {
      // process gone, or not ours to read
    }
  }
  return [...found].sort();
}

/**
 * The Firefox-based browser this process was started by, by name, or null. The native host's parent
 * is the browser: on Linux its executable is in /proc (inside a Flatpak too), on macOS ps names it.
 */
export function parentApp(pid = process.ppid) {
  let exe = "";
  try {
    if (process.platform === "darwin") {
      exe = spawnSync("/bin/ps", ["-o", "comm=", "-p", String(pid)], { encoding: "utf8" }).stdout?.trim() ?? "";
    } else {
      exe = fs.readlinkSync(`/proc/${pid}/exe`);
    }
  } catch {
    return null;
  }
  return GECKO.get(path.basename(exe)) ?? null;
}

/**
 * macOS has no /proc. ps lists this user's processes with each one's executable path, which for a
 * browser is <name>.app/Contents/MacOS/<executable>, the same executable names as on Linux.
 */
function psBrowsers() {
  let out;
  if (process.env.FOXREACH_PS) {
    // For tests: a file standing in for what ps prints.
    try {
      out = fs.readFileSync(process.env.FOXREACH_PS, "utf8");
    } catch {
      return null;
    }
  } else {
    const ps = spawnSync("/bin/ps", ["-x", "-o", "comm="], { encoding: "utf8" });
    if (ps.status !== 0) return null;
    out = ps.stdout;
  }
  const found = new Set();
  for (const line of out.split("\n")) {
    const name = GECKO.get(path.basename(line.trim()));
    if (name) found.add(name);
  }
  return [...found].sort();
}

/**
 * Profiles foxreach was set up in, from the record setup_profile.py keeps (one line per profile:
 * path, version, browser). Empty when there is none, as after a plain install.
 */
export function recordedProfiles(file = process.env.FOXREACH_PROFILES ||
    path.join(process.env.XDG_STATE_HOME || path.join(os.homedir(), ".local/state"), "foxreach/profiles")) {
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return [];
  }
  return text.split("\n").map((line) => line.split("\t")).filter(([p]) => p && path.isAbsolute(p))
    .filter(([p]) => fs.existsSync(p))
    .map(([profile, , browser]) => ({ profile, browser: browser || "" }));
}

const shellQuote = (s) => (/^[\w@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, "'\\''")}'`);

/** The command that opens a recorded profile in its browser, or null when the browser is unknown. */
export function openCommand({ profile, browser }) {
  if (!browser) return null;
  if (browser.startsWith("flatpak:")) return `flatpak run ${shellQuote(browser.slice(8))} --profile ${shellQuote(profile)}`;
  return `${shellQuote(browser)} --profile ${shellQuote(profile)}`;
}

function profileHint(profiles) {
  if (!profiles.length) return "";
  const lines = profiles.slice(0, 3).map((p) => {
    const cmd = openCommand(p);
    return cmd ? `${p.profile} (open it with: ${cmd})` : p.profile;
  });
  return ` foxreach is set up in: ${lines.join("; ")}.`;
}

/** The error to show when the bridge cannot reach the browser's native host. */
export function notConnectedMessage(code, socket, browsers = runningBrowsers(), profiles = recordedProfiles()) {
  const detail = `(${code} on ${socket})`;
  const hint = profileHint(profiles);
  if (browsers && browsers.length === 0) {
    return "No Firefox-based browser is running, so foxreach cannot reach it. Open the browser (Firefox, " +
      `LibreWolf or another) with the profile that has the foxreach add-on, then try again.${hint} ${detail}`;
  }
  if (browsers) {
    const which = browsers.join(" and ");
    return `${which} ${browsers.length > 1 ? "are" : "is"} running, but the foxreach add-on has not connected. ` +
      "Check that this is the profile foxreach is installed in, that the add-on is enabled in about:addons " +
      `and not paused from its toolbar button. If the browser has only just started, wait a few seconds.${hint} ` +
      detail;
  }
  return "The browser is not connected. Open the Firefox-based browser with the profile that has the foxreach " +
    `add-on, then try again.${hint} ${detail}`;
}
