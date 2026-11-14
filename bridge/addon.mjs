// The add-on in browser profiles: which browsers and profiles exist, which version of foxreach each
// profile has, and installing (or updating) and removing it. Used by `foxreach addon` and the
// manager app, through `foxreach status --json`.
//
// Install opens the Mozilla-signed file in the browser, which asks the user to confirm; nothing is
// written into a profile, so a running browser is fine. Once installed, the browser updates the
// add-on itself from updates-signed.json; opening a newer file updates it now. Remove deletes the
// add-on's file from the profile, which the browser notices at its next start; the browser must be
// closed for that. Profiles set up from a checkout with the full build (scripts/setup_profile.py)
// are not offered the signed build: it would replace the full one.
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { TOR_WARNING, torData, torHostDir, torInstalls } from "./tor.mjs";

export const ADDON_ID = "foxreach@foxreach.local";
// In a profile folder: agents may not use this profile (bridge/host.mjs refuses them); the add-on stays.
export const STOP_MARK = "foxreach-agents-stopped";
const UPDATES = process.env.FOXREACH_ADDON_UPDATES || "https://nilltadios.github.io/foxreach/updates-signed.json";
// The full (unsigned) build's own update list: what its manifest's update_url names, published from
// the GitHub releases. Profiles set up from a checkout run this build.
const UPDATES_FULL = process.env.FOXREACH_ADDON_UPDATES_FULL || "https://nilltadios.github.io/foxreach/updates.json";
// The prefs scripts/setup_profile.py sets so a profile takes the full (unsigned) build.
const FULL_PREFS = ["extensions.experiments.enabled", "xpinstall.signatures.required"];

// One family per Firefox-based browser: executable names, Flatpak ids, profile folders (relative
// to the home it uses). Same list as scripts/browsers.py.
const KINDS = [
  { name: "Firefox", commands: ["firefox", "firefox-developer-edition", "firefox-devedition", "firefox-nightly", "firefox-beta", "firefox-esr", "firefox-bin"],
    flatpaks: ["org.mozilla.firefox"], roots: [".mozilla/firefox", ".config/mozilla/firefox"] },
  { name: "LibreWolf", commands: ["librewolf", "librewolf-bin"], flatpaks: ["io.gitlab.librewolf-community"], roots: [".librewolf", ".config/librewolf/librewolf"] },
  { name: "Floorp", commands: ["floorp"], flatpaks: ["one.ablaze.floorp"], roots: [".floorp"] },
  { name: "Waterfox", commands: ["waterfox"], flatpaks: ["net.waterfox.waterfox"], roots: [".waterfox"] },
  { name: "Zen", commands: ["zen", "zen-browser", "zen-bin"], flatpaks: ["app.zen_browser.zen"], roots: [".zen"] },
];
const APPIMAGE_DIRS = ["AppImages", "Applications", ".local/bin"];
// What an AppImage (this manager, or a browser) exports for itself; none of it may reach a browser.
const SCRUB = ["LD_LIBRARY_PATH", "LD_PRELOAD", "APPDIR", "APPIMAGE", "ARGV0", "OWD"];

const exists = (p) => fs.existsSync(p);
const home = () => os.homedir();

function onPath(cmd) {
  for (const dir of (process.env.PATH || "").split(":").filter(Boolean)) {
    const file = path.join(dir, cmd);
    try {
      fs.accessSync(file, fs.constants.X_OK);
      if (fs.statSync(file).isFile()) return file;
    } catch {
      // not here
    }
  }
  return null;
}

function kindOf(fileName) {
  const n = fileName.toLowerCase();
  const pairs = KINDS.flatMap((k) => k.commands.map((c) => [c, k])).sort((a, b) => b[0].length - a[0].length);
  return pairs.find(([c]) => n === c || [".", "-", "_"].some((s) => n.startsWith(c + s)))?.[1] ?? null;
}

// Programs that hand a link to the default browser instead of being one (a "firefox" shim that runs
// xdg-open, say). A script that runs one of these is not a browser to set up.
const OPENERS = /\b(xdg-open|gio open|kde-open\d*|gnome-open|exo-open|sensible-browser|x-www-browser)\b/;

/**
 * For a launcher on PATH that is a shell script: the browser it starts, when one of its `exec`
 * lines names an existing browser program or AppImage ({kind, packaging, path}); "opener" when it
 * passes links to the default browser and starts no browser itself; null for a binary or a script
 * that says neither (a distribution's own launcher, which is kept as it is).
 */
function scriptTarget(file, h) {
  let head = "";
  try {
    const fd = fs.openSync(file, "r");
    try {
      const buf = Buffer.alloc(65536);
      head = buf.subarray(0, fs.readSync(fd, buf, 0, buf.length, 0)).toString("utf8");
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
  if (!head.startsWith("#!")) return null;
  const code = head.split("\n").map((l) => l.replace(/(^|\s)#.*$/, "").trim()).filter(Boolean);
  for (const line of code) {
    const m = /(?:^|[;&|]\s*)exec\s+(?:-\S+\s+)*("[^"]+"|'[^']+'|\S+)/.exec(line);
    if (!m) continue;
    const raw = m[1].replace(/^["']|["']$/g, "").replace(/^~(?=\/)/, h).replace(/\$\{?HOME\}?/g, h);
    if (!path.isAbsolute(raw)) continue;
    let real;
    try {
      real = fs.realpathSync(raw);
      if (!fs.statSync(real).isFile()) continue;
    } catch {
      continue;
    }
    const kind = kindOf(path.basename(raw)) ?? kindOf(path.basename(real));
    if (kind && real !== fs.realpathSync(file)) return { kind, packaging: real.toLowerCase().endsWith(".appimage") ? "appimage" : "native", path: real };
  }
  return code.some((l) => OPENERS.test(l)) ? "opener" : null;
}

function flatpakIds() {
  const r = spawnSync("flatpak", ["list", "--app", "--columns=application"], { encoding: "utf8" });
  return r.error || r.status !== 0 ? [] : r.stdout.split(/\s+/).filter(Boolean);
}

/** Every Firefox-based browser found: commands on PATH, AppImages in the usual folders, Flatpaks. */
export function findBrowsers({ h = home() } = {}) {
  const found = [];
  const seen = new Set();
  const add = (kind, packaging, spec) => {
    let real = spec;
    try {
      if (packaging !== "flatpak") real = fs.realpathSync(spec);
    } catch {
      return; // a link to nothing
    }
    if (seen.has(real)) return;
    seen.add(real);
    const appId = packaging === "flatpak" ? spec.slice(8) : null;
    found.push({ name: kind.name, packaging, spec, appId, home: appId ? path.join(h, ".var/app", appId) : h, kind });
  };
  for (const kind of KINDS) {
    for (const c of kind.commands) {
      const p = onPath(c);
      if (!p) continue;
      // A wrapper script counts as the browser it starts (so a wrapper and its AppImage show once);
      // a shim that only opens links in the default browser is not a browser.
      const target = scriptTarget(p, h);
      if (target === "opener") continue;
      if (target) add(target.kind, target.packaging, target.path);
      else add(kind, p.toLowerCase().endsWith(".appimage") ? "appimage" : "native", p);
    }
  }
  for (const dir of APPIMAGE_DIRS) {
    let names = [];
    try {
      names = fs.readdirSync(path.join(h, dir)).sort();
    } catch {
      continue;
    }
    for (const n of names) {
      const p = path.join(h, dir, n);
      let file = false;
      try {
        file = fs.statSync(p).isFile();
      } catch {
        // a link to nothing
      }
      if (n.toLowerCase().endsWith(".appimage") && kindOf(n) && file) add(kindOf(n), "appimage", p);
    }
  }
  // Tor Browser: by its install folders (its launcher on PATH is not the browser); offered with a
  // warning, its profiles in its own data folder.
  for (const dir of torInstalls(h)) {
    found.push({ name: "Tor Browser", packaging: "tor", spec: path.join(dir, "start-tor-browser"), appId: null, home: h,
      kind: { name: "Tor Browser", roots: [] }, roots: [torData(dir)], torDir: dir, warning: TOR_WARNING });
  }
  const installed = flatpakIds();
  for (const kind of KINDS) for (const id of kind.flatpaks) if (installed.includes(id)) add(kind, "flatpak", `flatpak:${id}`);
  return found;
}

/** Profiles listed in a profiles.ini: [{name, path, isDefault}]. */
function iniProfiles(root) {
  let text;
  try {
    text = fs.readFileSync(path.join(root, "profiles.ini"), "utf8");
  } catch {
    return [];
  }
  const out = [];
  let cur = null;
  let defaults = new Set();
  for (const raw of text.split("\n")) {
    const line = raw.trim();
    const sec = /^\[(.+)\]$/.exec(line);
    if (sec) {
      cur = sec[1].startsWith("Profile") ? { name: null, path: null, relative: true, isDefault: false } : null;
      if (cur) out.push(cur);
      continue;
    }
    const [k, ...v] = line.split("=");
    const value = v.join("=");
    if (/^Install/.test(k) || k === "Default") {
      // [Install<hash>] Default=<path> names the profile that install starts with.
      if (k === "Default" && !cur) defaults.add(value);
    }
    if (!cur) continue;
    if (k === "Name") cur.name = value;
    else if (k === "Path") cur.path = value;
    else if (k === "IsRelative") cur.relative = value === "1";
    else if (k === "Default" && value === "1") cur.isDefault = true;
  }
  return out.filter((p) => p.path).map((p) => {
    const full = p.relative ? path.join(root, p.path) : p.path;
    // [Install...] Default= is the profile the browser starts with; a profile's own Default=1 is the
    // older profile manager's choice and counts only when no install names one.
    return { name: p.name || p.path, path: full, isDefault: defaults.size ? defaults.has(p.path) : p.isDefault };
  }).filter((p) => exists(p.path));
}

/** The add-on in a profile: {version, full, active} or null when it is not installed. */
export function addonIn(profile) {
  let version = null;
  let active = null;
  try {
    const list = JSON.parse(fs.readFileSync(path.join(profile, "extensions.json"), "utf8")).addons ?? [];
    const a = list.find((x) => x.id === ADDON_ID && x.location === "app-profile");
    if (a) {
      version = a.version ?? null;
      active = !!a.active;
    }
  } catch {
    // the browser has not written its list yet
  }
  const file = exists(path.join(profile, "extensions", `${ADDON_ID}.xpi`));
  if (!version && !file) return null;
  return { version, active, file, full: hasFullPrefs(profile), stopped: exists(path.join(profile, STOP_MARK)) };
}

function hasFullPrefs(profile) {
  try {
    const text = fs.readFileSync(path.join(profile, "user.js"), "utf8");
    return FULL_PREFS.every((p) => text.includes(`"${p}"`));
  } catch {
    return false;
  }
}

/** True while a browser has this profile open (the browser must be closed to remove the add-on). */
export function inUse(profile, appId = null) {
  if (appId) {
    // The lock inside a Flatpak names a process in its sandbox; ask Flatpak what runs instead.
    const r = spawnSync("flatpak", ["ps", "--columns=application"], { encoding: "utf8" });
    return !r.error && r.status === 0 && r.stdout.split(/\s+/).includes(appId);
  }
  const lock = path.join(profile, "lock");
  try {
    const pid = fs.readlinkSync(lock).split("+").pop();
    return /^\d+$/.test(pid) && exists(`/proc/${pid}`);
  } catch {
    return false;
  }
}

/** Profiles recorded by setup_profile.py: {path: browser spec}. */
function recordedProfiles() {
  const file = process.env.FOXREACH_PROFILES ||
    path.join(process.env.XDG_STATE_HOME || path.join(home(), ".local/state"), "foxreach/profiles");
  try {
    return Object.fromEntries(fs.readFileSync(file, "utf8").split("\n").map((l) => l.split("\t"))
      .filter(([p]) => p && path.isAbsolute(p) && exists(p)).map(([p, , b]) => [p, b || ""]));
  } catch {
    return {};
  }
}

/** Every browser with its profiles and the add-on in each, as data. */
export function browserStatus({ h = home() } = {}) {
  const recorded = recordedProfiles();
  const browsers = findBrowsers({ h });
  const placed = new Set();
  const rows = browsers.map((b) => {
    // Inside a Flatpak, XDG_CONFIG_HOME is ~/.var/app/<id>/config (no dot), where newer builds keep
    // the folders that are ~/.config/... outside it (Firefox keeps its profiles in config/mozilla/firefox).
    const roots = b.appId ? b.kind.roots.flatMap((r) => (r.startsWith(".config/") ? [r, r.slice(1)] : [r])) : b.kind.roots;
    const profiles = b.roots ? b.roots.flatMap((r) => iniProfiles(r)) : roots.flatMap((r) => iniProfiles(path.join(b.home, r)));
    for (const [p, spec] of Object.entries(recorded)) {
      if (spec === b.spec && !profiles.some((x) => x.path === p)) profiles.push({ name: path.basename(p), path: p, isDefault: false });
    }
    const seen = new Set();
    return {
      name: b.name, packaging: b.packaging, spec: b.spec,
      warning: b.warning ?? null,
      // A browser foxreach must be connected to by name (Tor Browser): whether it is, and how.
      connect: b.torDir ? { ready: exists(path.join(torHostDir(b.torDir), "foxreach.json")), spec: `tor:${b.torDir}` } : null,
      profiles: profiles.filter((p) => !seen.has(p.path) && seen.add(p.path)).map((p) => {
        placed.add(p.path);
        return { ...p, inUse: inUse(p.path, b.appId), addon: addonIn(p.path) };
      }),
    };
  });
  // The same folder can belong to two builds of one browser (a distro package and an AppImage):
  // keep each profile under the first build only.
  const once = new Set();
  for (const r of rows) r.profiles = r.profiles.filter((p) => !once.has(p.path) && once.add(p.path));
  return rows;
}

/** The newest released build: {version, link, hash}. The signed one, or with full the full one;
 * both lists come from the GitHub releases, never from a checkout. */
export async function latestAddon({ full = false } = {}) {
  const url = full ? UPDATES_FULL : UPDATES;
  let r;
  try {
    r = await fetch(url, { redirect: "follow" });
  } catch (e) {
    throw new Error(`Cannot reach ${url}: ${e.message ?? e}`);
  }
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  const updates = (await r.json())?.addons?.[ADDON_ID]?.updates ?? [];
  const num = (v) => String(v).split(".").map(Number);
  const newer = (a, b) => {
    const pa = num(a.version), pb = num(b.version);
    for (let i = 0; i < Math.max(pa.length, pb.length); i++) if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0);
    return false;
  };
  const best = updates.filter((u) => u.version && u.update_link?.startsWith("https://")).reduce((a, u) => (!a || newer(u, a) ? u : a), null);
  if (!best) throw new Error(`${url} lists no build.`);
  return { version: best.version, link: best.update_link, hash: best.update_hash ?? null };
}

function browserFor(spec) {
  const b = findBrowsers().find((x) => x.spec === spec);
  if (!b) throw new Error(`No browser ${spec} was found. Browsers: ${findBrowsers().map((x) => x.spec).join(", ") || "none"}`);
  return b;
}

function profileOf(b, profile) {
  const real = path.resolve(profile);
  const known = browserStatus().find((x) => x.spec === b.spec)?.profiles.find((p) => p.path === real);
  if (!known) throw new Error(`${real} is not a profile of ${b.spec}.`);
  return known;
}

/**
 * Open the latest released add-on in a browser profile: the browser asks the user to add (or update)
 * it. A profile with the full build gets the full build of the latest release (the signed one would
 * replace it); any other the signed build. Returns the command started.
 */
export async function installAddon({ spec, profile, log = console.log }) {
  const b = browserFor(spec);
  const p = profileOf(b, profile);
  const latest = await latestAddon({ full: !!p.addon?.full });
  const args = ["--profile", p.path, latest.link];
  // Tor Browser's start script picks its own profile; it gets the link only.
  const argv = b.torDir ? [b.spec, latest.link] : b.appId ? ["flatpak", "run", b.appId, ...args] : [b.spec, ...args];
  const env = { ...process.env };
  for (const k of SCRUB) delete env[k];
  const child = spawn(argv[0], argv.slice(1), { env, detached: true, stdio: "ignore" });
  child.on("error", () => {});
  child.unref();
  log(`Opened foxreach ${latest.version} in ${b.name} (${p.name}): ${argv.join(" ")}`);
  log("Confirm in the browser: Add (or Update). The browser keeps it updated after that.");
  return argv;
}

const today = () => new Date().toISOString().slice(0, 10);

/** Stop agents in a profile, add-on kept (on: true), or let them use it again. Works while it is open. */
export function setAgentsStopped({ profile, on, log = console.log }) {
  const real = path.resolve(profile);
  if (!exists(path.join(real, "prefs.js")) && !exists(path.join(real, "times.json"))) throw new Error(`${real} is not a browser profile.`);
  const mark = path.join(real, STOP_MARK);
  if (on) {
    fs.writeFileSync(mark, "Agents may not use this browser profile; foxreach stays installed. Undo: foxreach addon start --profile <this folder>\n");
    log(`Agents can no longer use ${real}; the add-on stays installed.`);
  } else {
    fs.rmSync(mark, { force: true });
    log(`Agents can use ${real} again.`);
  }
}

/** Remove the add-on from a closed profile; for a full-build profile, also the prefs setup set. */
export function removeAddon({ profile, spec = null, log = console.log }) {
  const real = path.resolve(profile);
  const b = spec ? browserFor(spec) : null;
  // A profile inside ~/.var/app/<id> belongs to that Flatpak, whose lock names a sandbox PID.
  const apps = path.join(home(), ".var/app") + path.sep;
  const appId = b?.appId ?? (real.startsWith(apps) ? real.slice(apps.length).split(path.sep)[0] : null);
  if (inUse(real, appId)) throw new Error(`${real} is in use. Close ${b?.name ?? appId ?? "the browser"} first.`);
  const file = path.join(real, "extensions", `${ADDON_ID}.xpi`);
  const full = hasFullPrefs(real);
  if (!exists(file)) {
    log(`foxreach is not installed in ${real}.`);
  } else {
    fs.rmSync(file);
    log(`removed ${file} (the browser drops foxreach from its list at its next start)`);
  }
  if (full) {
    for (const name of ["user.js", "prefs.js"]) {
      const f = path.join(real, name);
      if (!exists(f)) continue;
      const lines = fs.readFileSync(f, "utf8").split(/(?<=\n)/);
      const kept = lines.filter((l) => !FULL_PREFS.some((p) => l.includes(`"${p}"`)));
      if (kept.length === lines.length) continue;
      const copy = `${f}.bak-${today()}`;
      if (!exists(copy)) fs.copyFileSync(f, copy);
      fs.writeFileSync(f, kept.join(""));
      log(`removed ${lines.length - kept.length} foxreach prefs from ${f} (backup ${copy}); other unsigned add-ons there stop loading`);
    }
  }
  forgetProfile(real, log);
}

/** Drop the profile from setup_profile.py's record, so scripts/update.py stops updating it. */
function forgetProfile(profile, log) {
  const file = process.env.FOXREACH_PROFILES ||
    path.join(process.env.XDG_STATE_HOME || path.join(home(), ".local/state"), "foxreach/profiles");
  let text;
  try {
    text = fs.readFileSync(file, "utf8");
  } catch {
    return;
  }
  const lines = text.split("\n").filter(Boolean);
  const kept = lines.filter((l) => path.resolve(l.split("\t")[0]) !== profile);
  if (kept.length === lines.length) return;
  fs.writeFileSync(file, kept.map((l) => `${l}\n`).join(""));
  log(`removed ${profile} from ${file}`);
}
