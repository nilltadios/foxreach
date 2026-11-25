// Several browsers can serve agents at once. Each native host listens on its own socket in the
// foxreach folder ($XDG_RUNTIME_DIR/foxreach, which Flatpak browsers are given too) and describes
// itself in a .json file beside it; agents list them and choose one (bridge/mcp.mjs), and the
// user may name a default. With FOXREACH_SOCKET set (tests, one fixed socket) none of this is used.
import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { browserName, parentApp, windowsBrowser } from "./browsers.mjs";
import { PIPE_RE, pipeName, runFolder, socketPath } from "./socket.mjs";
import { isTorProgram } from "./tor.mjs";
import { WIN, appData, samePath } from "./win.mjs";

export const fixedSocket = () => !!process.env.FOXREACH_SOCKET;
export const folder = () => runFolder();
// On Windows the endpoint is a named pipe, which is no file: never removed, never compared to the folder.
const pipes = () => WIN && !fixedSocket();
const removeFile = (f) => {
  if (pipes() && PIPE_RE.test(f)) return;
  fs.rmSync(f, { force: true });
};

/** The answer a host gives to an agent's challenge: proof it holds the info file's token. */
export const proofOf = (token, nonce) => crypto.createHmac("sha256", String(token)).update(String(nonce)).digest("hex");

/** What the browser that started this host is: name, packaging, Flatpak id, from /proc. */
export function describeParent(ppid = process.ppid) {
  if (WIN) {
    const b = windowsBrowser();
    return { name: b?.name ?? "Browser", packaging: "native", appId: null };
  }
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
  const key = crypto.randomBytes(pipes() ? 8 : 4).toString("hex");
  const socket = pipes() ? pipeName(key) : path.join(folder(), `browser-${key}.sock`);
  const file = path.join(folder(), `browser-${key}.json`);
  const info = { key, socket, ...describeParent(), profile, addon: null, legacy: false, started: Date.now() };
  // Windows: agents trust the pipe only once it proves it knows this (bridge/mcp.mjs).
  if (pipes()) info.token = crypto.randomBytes(32).toString("hex");
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
      for (const f of [socket, file]) removeFile(f);
    },
  };
}

/** True when the proof a host sent answers the challenge for this token. */
export function proofMatches(token, challenge, proof) {
  const want = Buffer.from(proofOf(token, challenge));
  const got = Buffer.from(String(proof ?? ""));
  return got.length === want.length && crypto.timingSafeEqual(got, want);
}

const MAX_PROOF_LINE_CHARS = 4096;

/**
 * Whether a host answers on this socket: "yes", "no" (nothing listens), or, with a token (Windows),
 * "impostor" when whatever listens does not prove it knows the token, and "silent" when it does
 * not answer the challenge in time.
 */
function alive(socket, token = null, timeoutMs = 800) {
  return new Promise((resolve) => {
    const probe = net.connect(socket);
    probe.setEncoding("utf8");
    const challenge = token ? crypto.randomBytes(32).toString("hex") : null;
    let buffer = "";
    let timer;
    let settled = false;
    const done = (answer) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      probe.destroy();
      resolve(answer);
    };
    timer = setTimeout(() => done("no"), timeoutMs);
    probe.on("connect", () => {
      if (!token) return done("yes");
      clearTimeout(timer);
      timer = setTimeout(() => done("silent"), 3000);
      probe.write(`${JSON.stringify({ challenge })}\n`);
    });
    probe.on("data", (data) => {
      if (buffer.length + data.length > MAX_PROOF_LINE_CHARS) return done("impostor");
      buffer += data;
      const end = buffer.indexOf("\n");
      if (end === -1) return;
      let reply = null;
      try {
        reply = JSON.parse(buffer.slice(0, end));
      } catch {
        // not a host's answer
      }
      done(proofMatches(token, challenge, reply?.proof) ? "yes" : "impostor");
    });
    probe.on("error", () => done("no"));
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
    if (typeof info.socket !== "string") continue;
    if (pipes() ? !PIPE_RE.test(info.socket) || typeof info.token !== "string" : path.dirname(info.socket) !== folder()) continue;
    // Windows: a pipe name that came free may have been taken by another program; one that cannot
    // prove it knows the token is not this browser's host, which is then gone.
    const answer = await alive(info.socket, pipes() ? info.token : null);
    if (answer === "yes") found.push(info);
    else if (answer !== "silent") for (const f of [file, info.socket]) removeFile(f); // a host that died
  }
  found.sort((a, b) => a.started - b.started);
  // A host from before several browsers could connect serves only browser.sock, with no file of
  // its own (a browser keeps its host until it restarts). Count it as one more browser. (Windows
  // has no such hosts, and no fixed pipe: socketPath() is null there.)
  const classic = socketPath();
  if (classic && !found.some((b) => b.legacy) && (await alive(classic)) === "yes") {
    found.push({ socket: classic, name: "Browser", packaging: "older host", appId: null, profile: null, addon: null, legacy: true, started: 0 });
  }
  const base = (b) => (b.packaging === "older host" ? "browser" : slug(b.name) + (["native", "app", "tor"].includes(b.packaging) ? "" : `-${b.packaging}`));
  const taken = new Map();
  return found.map((b) => {
    const shared = found.filter((o) => base(o) === base(b)).length > 1;
    const pn = profileName(b.profile);
    // Two that would still share an id (the same profile, or none known) get -2, -3...: each one
    // can be chosen.
    let id = shared && pn ? `${base(b)}-${slug(pn)}` : base(b);
    const n = (taken.get(id) ?? 0) + 1;
    taken.set(id, n);
    if (n > 1) id = `${id}-${n}`;
    const row = {
      id,
      name: b.name, packaging: b.packaging, appId: b.appId, profile: b.profile, profileName: pn,
      addon: b.addon, legacy: !!b.legacy, socket: b.socket,
    };
    // Not enumerable: listings (status --json, browsers_list) never show it.
    Object.defineProperty(row, "token", { value: b.token ?? null, enumerable: false });
    return row;
  });
}

/** The profile the user chose for agents when several browsers are connected, or null. */
export function defaultFile() {
  const config = process.env.XDG_CONFIG_HOME || (WIN ? appData() : path.join(os.homedir(), ".config"));
  return path.join(config, "foxreach", "default-browser");
}

export function readDefault() {
  try {
    return fs.readFileSync(defaultFile(), "utf8").trim() || null;
  } catch {
    return null;
  }
}

/** True when this profile is the default one (Windows: whatever the case or the slashes). */
export function isDefault(profile, preferred = readDefault()) {
  if (!profile || !preferred) return false;
  return WIN ? samePath(profile, preferred) : path.resolve(profile) === preferred;
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
