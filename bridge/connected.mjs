// Several browsers can serve agents at once. Each native host listens on its own socket in the
// foxreach folder ($XDG_RUNTIME_DIR/foxreach, which Flatpak browsers are given too) and describes
// itself in a .json file beside it; agents list them and choose one (bridge/mcp.mjs), and the
// user may name a default. With FOXREACH_SOCKET set (tests, one fixed socket) none of this is used.
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { browserName, parentApp } from "./browsers.mjs";
import { socketPath } from "./socket.mjs";
import { isTorProgram } from "./tor.mjs";

export const fixedSocket = () => !!process.env.FOXREACH_SOCKET;
export const folder = () => path.dirname(socketPath());

/** What the browser that started this host is: name, packaging, Flatpak id, from /proc. */
function describeParent(ppid = process.ppid) {
  let exe = "";
  try {
    exe = fs.readlinkSync(`/proc/${ppid}/exe`);
  } catch {
    // no /proc
  }
  let packaging = exe.startsWith("/tmp/.mount_") ? "appimage" : "native";
  let appId = null;
  try {
    // Inside a Flatpak sandbox (the host runs in the browser's).
    appId = /^name=(.+)$/m.exec(fs.readFileSync("/.flatpak-info", "utf8"))?.[1] ?? null;
    if (appId) packaging = "flatpak";
  } catch {
    // not a Flatpak
  }
  // Tor Browser's program is called firefox(.real): tell it by its folder.
  if (isTorProgram(exe)) return { name: "Tor Browser", packaging: "tor", appId };
  // macOS has no /proc: ps names the program there (parentApp).
  return { name: browserName(path.basename(exe)) ?? parentApp(ppid) ?? "Browser", packaging, appId };
}

/**
 * For a host: a socket and an info file of its own. `update` rewrites the info (the add-on's
 * version arrives later, with its hello); `remove` deletes both.
 */
export function register({ profile }) {
  const key = crypto.randomBytes(4).toString("hex");
  const socket = path.join(folder(), `browser-${key}.sock`);
  const file = path.join(folder(), `browser-${key}.json`);
  const info = { key, socket, ...describeParent(), profile, addon: null, legacy: false, started: Date.now() };
  const write = () => {
    const tmp = `${file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(info), { mode: 0o600 });
    fs.renameSync(tmp, file);
  };
  return {
    socket,
    info,
    update(change) {
      Object.assign(info, change);
      write();
    },
    remove() {
      for (const f of [socket, file]) fs.rmSync(f, { force: true });
    },
  };
}

function alive(socket, timeoutMs = 800) {
  return new Promise((resolve) => {
    const probe = net.connect(socket);
    const done = (ok) => {
      clearTimeout(timer);
      probe.destroy();
      resolve(ok);
    };
    const timer = setTimeout(() => done(false), timeoutMs);
    probe.on("connect", () => done(true));
    probe.on("error", () => done(false));
  });
}

const slug = (s) => String(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
/** "og6ocnou.default" -> "default", as the profile manager shows it. */
const profileName = (dir) => (dir ? path.basename(dir).replace(/^[a-z0-9]{8}\./, "") : null);

/**
 * The browsers serving agents now, oldest first: {id, name, packaging, appId, profile, profileName,
 * addon, socket}. `id` is what an agent passes to browser_select ("librewolf", "firefox-flatpak",
 * "librewolf-work" when two would share a name). Files of hosts that are gone are removed.
 */
export async function connectedBrowsers() {
  let names = [];
  try {
    names = fs.readdirSync(folder()).filter((n) => /^browser-[0-9a-f]+\.json$/.test(n));
  } catch {
    return [];
  }
  const found = [];
  for (const n of names) {
    const file = path.join(folder(), n);
    let info;
    try {
      info = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch {
      continue;
    }
    if (typeof info.socket !== "string" || path.dirname(info.socket) !== folder()) continue;
    if (await alive(info.socket)) found.push(info);
    else for (const f of [file, info.socket]) fs.rmSync(f, { force: true }); // a host that died
  }
  found.sort((a, b) => a.started - b.started);
  // A host from before several browsers could connect serves only browser.sock, with no file of
  // its own (a browser keeps its host until it restarts). Count it as one more browser.
  const classic = socketPath();
  if (!found.some((b) => b.legacy) && (await alive(classic))) {
    found.push({ socket: classic, name: "Browser", packaging: "older host", appId: null, profile: null, addon: null, legacy: true, started: 0 });
  }
  const base = (b) => (b.packaging === "older host" ? "browser" : slug(b.name) + (["native", "app", "tor"].includes(b.packaging) ? "" : `-${b.packaging}`));
  return found.map((b) => {
    const shared = found.filter((o) => base(o) === base(b)).length > 1;
    const pn = profileName(b.profile);
    return {
      id: shared && pn ? `${base(b)}-${slug(pn)}` : base(b),
      name: b.name, packaging: b.packaging, appId: b.appId, profile: b.profile, profileName: pn,
      addon: b.addon, legacy: !!b.legacy, socket: b.socket,
    };
  });
}

/** The profile the user chose for agents when several browsers are connected, or null. */
export function defaultFile() {
  const config = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config");
  return path.join(config, "foxreach", "default-browser");
}

export function readDefault() {
  try {
    return fs.readFileSync(defaultFile(), "utf8").trim() || null;
  } catch {
    return null;
  }
}

export function writeDefault(profile) {
  if (!profile) {
    fs.rmSync(defaultFile(), { force: true });
    return;
  }
  fs.mkdirSync(path.dirname(defaultFile()), { recursive: true });
  fs.writeFileSync(defaultFile(), `${path.resolve(profile)}\n`);
}

/** One line per browser, for messages to agents. */
export function describe(list) {
  return list.map((b) => `- ${b.id}: ${b.name}${b.packaging === "native" ? "" : ` (${b.packaging})`}` +
    `${b.profileName ? `, profile ${b.profileName}` : ""}`).join("\n");
}
