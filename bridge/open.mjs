// browser_open: start a browser profile foxreach is in, for an agent that finds no browser
// connected. It opens only profiles foxreach itself found: those in its own record (setup_profile.py
// writes it, one line per profile: path, version, browser), each with the browser recorded for it,
// and those `foxreach status` finds with the add-on installed (an install from addons.mozilla.org,
// the release link or the manager writes no record), each with the browser whose profiles it is in.
// An agent cannot pass a command or a program, only choose one of these profiles.
import { spawn } from "node:child_process";
import path from "node:path";
import { browserStatus, inUse } from "./addon.mjs";
import { recordedProfiles } from "./browsers.mjs";
import { readDefault } from "./connected.mjs";

/**
 * The profiles browser_open can start, as {profile, browser}: the record's first (its browser is
 * the one the user set up), then every other profile holding the add-on, with the browser it was
 * found under. Left out: profiles where the add-on is disabled or the user stopped agents (foxreach
 * does not connect there), and Tor Browser's, whose start script picks its own profile and which
 * reads its own native-messaging manifests. A record line from before the record named browsers
 * gets the browser found for its profile.
 */
export function openableProfiles(recorded = recordedProfiles(), rows = detectedBrowsers()) {
  const found = new Map();
  for (const row of rows) {
    if (row.packaging === "tor" || row.connect) continue;
    for (const p of row.profiles) {
      const a = p.addon;
      const key = path.resolve(p.path);
      if (a && a.active !== false && !a.stopped && !found.has(key)) found.set(key, { profile: p.path, browser: row.spec });
    }
  }
  const out = recorded.map((r) => (r.browser ? r : { ...r, browser: found.get(path.resolve(r.profile))?.browser ?? "" }));
  for (const r of recorded) found.delete(path.resolve(r.profile));
  return [...out, ...found.values()];
}

// Detection runs on the not-connected error's path too: a failure there must not hide that error.
function detectedBrowsers() {
  try {
    return browserStatus();
  } catch {
    return [];
  }
}

/** The profile an agent means: by path or folder name; else the default, else the only one. */
export function chooseProfile(want, profiles = openableProfiles()) {
  const list = profiles.map((p) => `- ${p.profile}${p.browser ? ` (${p.browser})` : ""}`).join("\n");
  if (!profiles.length) {
    throw new Error("foxreach found no browser profile with its add-on that it can open. Ask the user to open the browser.");
  }
  if (want) {
    const w = String(want).trim();
    const hit = profiles.filter((p) => p.profile === path.resolve(w) || path.basename(p.profile) === w);
    if (hit.length === 1) return hit[0];
    throw new Error(`No single profile foxreach can open matches "${w}". It can open:\n${list}`);
  }
  const preferred = readDefault();
  const fallback = profiles.find((p) => preferred && path.resolve(p.profile) === preferred) ?? (profiles.length === 1 ? profiles[0] : null);
  if (fallback) return fallback;
  throw new Error(`foxreach is in several profiles; pass profile (its path) to choose one, or ask the user which:\n${list}`);
}

/**
 * Whether a browser holds this profile open now (the lock on Linux, .parentlock on macOS, the
 * locked file on Windows). A Flatpak browser's pid is in its sandbox's numbering, so for one of
 * those the question is whether that Flatpak runs at all.
 */
export function profileInUse({ profile, browser }) {
  return inUse(profile, browser.startsWith("flatpak:") ? browser.slice(8) : null);
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
