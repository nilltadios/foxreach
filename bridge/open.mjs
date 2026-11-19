// browser_open: start a browser profile foxreach is set up in, for an agent that finds no browser
// connected. Only profiles in foxreach's own record (setup_profile.py writes it, one line per
// profile: path, version, browser) can be opened, each with the browser recorded for it; an agent
// cannot pass a command or a program.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { recordedProfiles } from "./browsers.mjs";
import { readDefault } from "./connected.mjs";

/** The recorded profile an agent means: by path or folder name; else the default, else the only one. */
export function chooseProfile(want, profiles = recordedProfiles()) {
  const list = profiles.map((p) => `- ${p.profile}${p.browser ? ` (${p.browser})` : ""}`).join("\n");
  if (!profiles.length) {
    throw new Error("foxreach has no record of a profile it is set up in, so it cannot open one. Ask the user to open the browser.");
  }
  if (want) {
    const w = String(want).trim();
    const hit = profiles.filter((p) => p.profile === path.resolve(w) || path.basename(p.profile) === w);
    if (hit.length === 1) return hit[0];
    throw new Error(`No single recorded profile matches "${w}". Recorded:\n${list}`);
  }
  const preferred = readDefault();
  const fallback = profiles.find((p) => preferred && path.resolve(p.profile) === preferred) ?? (profiles.length === 1 ? profiles[0] : null);
  if (fallback) return fallback;
  throw new Error(`foxreach is set up in several profiles; pass profile (its path) to choose one, or ask the user which:\n${list}`);
}

const pidAlive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === "EPERM";
  }
};

/**
 * Whether a browser holds this profile open now: Firefox keeps a `lock` link to "host:+pid" there
 * while it runs (Linux). A Flatpak browser's pid is in its sandbox's numbering, so for one of those
 * the question is whether that Flatpak runs at all.
 */
export function profileInUse({ profile, browser }) {
  if (browser.startsWith("flatpak:")) {
    const ps = spawnSync("flatpak", ["ps", "--columns=application"], { encoding: "utf8" });
    return ps.status === 0 && ps.stdout.split("\n").some((l) => l.trim() === browser.slice(8));
  }
  try {
    const pid = Number(/\+(\d+)$/.exec(fs.readlinkSync(path.join(profile, "lock")))?.[1]);
    return pid > 0 && pidAlive(pid);
  } catch {
    return false; // no lock: not running (or not Linux; macOS keeps .parentlock only)
  }
}

// Variables an AppImage sets for its own program. An agent started from one (Claude desktop's and
// T3 Code's AppImages do this) passes them on, with its mount in PATH and LD_LIBRARY_PATH, and the
// browser must not load that AppImage's libraries. LD_PRELOAD from the session can crash a browser.
const APPIMAGE_VARS = ["APPIMAGE", "APPDIR", "ARGV0", "OWD", "LD_PRELOAD"];

export function launchEnv(env = process.env) {
  const out = { ...env };
  for (const k of APPIMAGE_VARS) delete out[k];
  for (const [k, v] of Object.entries(out)) {
    if (typeof v !== "string" || !v.includes("/tmp/.mount_")) continue;
    // A list of folders keeps every entry but the AppImage's own.
    const kept = v.split(":").filter((part) => part && !part.includes("/tmp/.mount_"));
    if (kept.length) out[k] = kept.join(":");
    else delete out[k];
  }
  return out;
}

/** Starts the browser on the profile, detached from this server; resolves to the command line. */
export function launch({ profile, browser }) {
  if (!browser) return Promise.reject(new Error(`The record does not say which browser opens ${profile}. Ask the user to open it.`));
  const [cmd, ...args] = browser.startsWith("flatpak:")
    ? ["flatpak", "run", browser.slice(8), "--profile", profile]
    : [browser, "--profile", profile];
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { detached: true, stdio: "ignore", env: launchEnv() });
    child.on("error", (e) => reject(new Error(`Could not start ${cmd}: ${e.message}`)));
    child.on("spawn", () => {
      child.unref();
      resolve([cmd, ...args].join(" "));
    });
  });
}
