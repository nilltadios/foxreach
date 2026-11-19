// Native messaging host. The browser starts it and owns its lifetime. It relays between the extension
// (stdin/stdout, 4-byte length prefix) and local MCP servers (a private Unix socket, JSON lines).
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { allOpenProfiles, namedProfile, openProfiles } from "./addon.mjs";
import { parentApp, windowsBrowser } from "./browsers.mjs";
import { describeParent, fixedSocket, folder, proofOf, register } from "./connected.mjs";
import { appendHistory } from "./history.mjs";
import { assertPrivate, isPipe, socketPath } from "./socket.mjs";
import { WIN, processesError, profileArg, profileNameArg } from "./win.mjs";

// The browser accepts at most 1 MB per message; 300k UTF-16 units cannot exceed that once encoded.
const CHUNK_CHARS = 300_000;

const sock = socketPath();
const debug = (text) => {
  if (process.env.FOXREACH_HOST_LOG) {
    fs.appendFileSync(process.env.FOXREACH_HOST_LOG, `${new Date().toISOString()} ${process.pid} ${text}\n`);
  }
};
debug(`start, socket ${sock}`);
const clients = new Map();
let nextClient = 1;

// The browser profile this host serves: the folder whose .parentlock the browser that started it
// holds open (the main browser process starts the host). The user can stop agents in one profile,
// add-on still installed, with a marker file there (`foxreach addon stop`); the host then refuses
// every agent call. Checked on each call, so it takes effect at once. FOXREACH_HOST_PROFILE: tests.
const STOP_MARK = "foxreach-agents-stopped";
// Windows: the profiles this host may serve, when it cannot tell which one (two open at once).
let candidates = [];
function findProfile() {
  if (process.env.FOXREACH_HOST_PROFILE) return process.env.FOXREACH_HOST_PROFILE;
  if (WIN) return windowsProfile();
  try {
    for (const fd of fs.readdirSync(`/proc/${process.ppid}/fd`)) {
      let target;
      try {
        target = fs.readlinkSync(`/proc/${process.ppid}/fd/${fd}`);
      } catch {
        continue;
      }
      if (path.basename(target) === ".parentlock") return path.dirname(target);
    }
  } catch {
    // no /proc (macOS), or the parent is gone
  }
  return null;
}
/**
 * Windows has no /proc: the profile named on the browser's command line (-profile <folder> or
 * -P <name>), else the one profile of that browser that is open (its parent.lock held). With
 * several open and none named, it cannot tell; agents are then refused if any of them is stopped.
 */
let toldNoTable = false;
function windowsProfile() {
  const b = windowsBrowser();
  if (processesError && !toldNoTable) {
    toldNoTable = true;
    debug(`no process table, so neither the browser nor its profile is known: ${processesError}`);
  }
  // Browser unknown (no process table, or a program foxreach does not know): every open profile of
  // every browser may be this one, so a stop in any of them stops agents here. That errs safe. Only
  // with no process table is a single open profile taken as this one's: a program foxreach does not
  // know is most likely not the browser that has it open.
  if (!b) {
    candidates = allOpenProfiles();
    return processesError && candidates.length === 1 ? candidates[0] : null;
  }
  const named = profileArg(b.cmd);
  if (named) return path.resolve(named);
  // -P <name>: the profile of that name in the browser's profiles.ini.
  const byName = namedProfile(b.name, profileNameArg(b.cmd));
  if (byName) return byName;
  const open = openProfiles(b.name);
  candidates = open;
  return open.length === 1 ? open[0] : null;
}
let profile = findProfile();
debug(`profile ${profile}${candidates.length > 1 ? ` (one of ${candidates.join(", ")})` : ""}`);

// Recorded in history.jsonl (bridge/history.mjs): this connection's start, and how it ended.
const browserPid = process.ppid;
const hostStarted = Date.now();
const parent = describeParent();
const historyId = { hostPid: process.pid, hostStarted };
appendHistory({ event: "start", ...historyId, browser: parent.name, packaging: parent.packaging, profile });
// When the add-on said the browser is quitting (full build only). A quit can still be cancelled
// (a page's "leave page?" prompt), so it counts only if the connection ends soon after.
let quitSaid = 0;
const QUIT_COUNTS_MS = 30_000;
function agentsStopped() {
  profile ??= findProfile();
  if (profile === null) return candidates.some((c) => fs.existsSync(path.join(c, STOP_MARK)));
  return fs.existsSync(path.join(profile, STOP_MARK));
}
const STOPPED = "The user stopped agents in this browser profile (foxreach is still installed there). " +
  "Ask the user to let agents use it again, from the foxreach manager or with: foxreach addon start --profile <folder>.";

function toBrowser(message, done) {
  const body = Buffer.from(JSON.stringify(message));
  const header = Buffer.alloc(4);
  header.writeUInt32LE(body.length);
  process.stdout.write(Buffer.concat([header, body]), done);
}

/** Tell the add-on why this host stops, then stop once the message is out (pipe writes are
 * asynchronous on Windows, and exiting at once could lose it). */
function fail(message, code) {
  toBrowser({ hostError: message }, () => process.exit(code));
  setTimeout(() => process.exit(code), 2000).unref();
}

function sendRequest(message) {
  const text = JSON.stringify(message);
  if (text.length <= CHUNK_CHARS) return toBrowser(message);
  const n = Math.ceil(text.length / CHUNK_CHARS);
  for (let i = 0; i < n; i++) {
    toBrowser({ chunk: { id: message.id, i, n, data: text.slice(i * CHUNK_CHARS, (i + 1) * CHUNK_CHARS) } });
  }
}

function fromBrowser(message) {
  if (message.bye) {
    if (message.bye === "quit") quitSaid = Date.now();
    debug(`bye ${message.bye}`);
    return;
  }
  if (message.hello) {
    hello = message.hello;
    // The browser started this host: its program says which browser it is, where the browser itself
    // may only say "Firefox" (LibreWolf does). Only an add-on that asks gets this: an older one
    // would take any message it does not know for a tool call.
    if (hello.wantsHostInfo) toBrowser({ hostInfo: { app: parentApp() } });
    registration?.update({ addon: String(hello.version ?? "") || null });
    return;
  }
  const split = String(message.id).indexOf(":");
  const client = clients.get(Number(String(message.id).slice(0, split)));
  client?.write(`${JSON.stringify({ ...message, id: Number(String(message.id).slice(split + 1)) })}\n`);
}

let hello = null;
let stdinBuffer = Buffer.alloc(0);
process.stdin.on("data", (data) => {
  stdinBuffer = Buffer.concat([stdinBuffer, data]);
  while (stdinBuffer.length >= 4) {
    const length = stdinBuffer.readUInt32LE(0);
    if (stdinBuffer.length < 4 + length) break;
    fromBrowser(JSON.parse(stdinBuffer.subarray(4, 4 + length)));
    stdinBuffer = stdinBuffer.subarray(4 + length);
  }
});

let ownsSocket = false;
let registration = null; // this browser's own socket and info file, when several may serve agents

const parentAlive = () => {
  try {
    process.kill(browserPid, 0);
    return process.ppid === browserPid; // a dead parent's children are handed to another process
  } catch {
    return false;
  }
};

// How the connection ended. Without the add-on's word that the browser is quitting, a browser
// gone within a moment exited some other way; one still running stopped the add-on, or is still
// shutting down. The browser kills its host a few seconds after closing the connection, so the
// wait stays short.
async function howItEnded(signal) {
  if (quitSaid && Date.now() - quitSaid < QUIT_COUNTS_MS) return "quit";
  if (signal) return "host";
  for (let waited = 0; waited < END_WAIT_MS && parentAlive(); waited += 100) await new Promise((r) => setTimeout(r, 100));
  if (parentAlive()) return "addon";
  return hello?.reportsQuit ? "lost" : "closed";
}
const END_WAIT_MS = 1500;

let ending = false;
async function shutdown(signal) {
  if (ending) return;
  ending = true;
  debug(`shutdown${signal ? ` on ${signal}` : ""}`);
  registration?.remove();
  // Only the host that created the socket may remove it: it may be another browser's.
  // A named pipe is no file (FOXREACH_SOCKET may name one on Windows, or a socket file).
  if (ownsSocket && !isPipe(sock)) {
    try {
      fs.unlinkSync(sock);
    } catch {}
  }
  const how = await howItEnded(signal);
  debug(`ended: ${how}`);
  appendHistory({ event: "end", ...historyId, how });
  process.exit(0);
}
process.stdin.on("end", () => shutdown());
process.on("SIGTERM", () => shutdown("SIGTERM"));

function serveClient(conn) {
  const clientId = nextClient++;
  clients.set(clientId, conn);
  let buffer = "";
  conn.setEncoding("utf8");
  conn.on("data", (data) => {
    buffer += data;
    let end;
    while ((end = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + 1);
      if (!line.trim()) continue;
      let request;
      try {
        request = JSON.parse(line);
      } catch {
        continue; // not ours to crash over: any local program can write to the socket
      }
      // Only a tool call goes on to the add-on. Its other messages (hello, chunk, hostError) are
      // the host's own and must not be forgeable from the socket.
      // Windows: an agent asks the host to prove it is the one its info file names (bridge/mcp.mjs).
      if (request && typeof request.challenge === "string") {
        const token = registration?.info.token;
        if (token) conn.write(`${JSON.stringify({ proof: proofOf(token, request.challenge.slice(0, 200)) })}\n`);
        continue;
      }
      const { id, client, tool, args } = request ?? {};
      if (agentsStopped()) {
        conn.write(`${JSON.stringify({ id, error: STOPPED })}\n`);
        continue;
      }
      sendRequest({ id: `${clientId}:${id}`, client: String(client ?? "agent").slice(0, 80), tool: String(tool), args });
    }
  });
  // The add-on keeps what the user allowed per connection; tell it when one ends.
  conn.on("close", () => {
    if (clients.delete(clientId)) toBrowser({ closed: clientId });
  });
  conn.on("error", () => {});
}

function isAlive(file) {
  return new Promise((resolve) => {
    const probe = net.connect(file);
    probe.on("connect", () => {
      probe.destroy();
      resolve(true);
    });
    probe.on("error", () => resolve(false));
  });
}

(async () => {
  fs.mkdirSync(folder(), { recursive: true, mode: 0o700 });
  try {
    assertPrivate(folder());
  } catch (e) {
    debug(e.message);
    return fail(e.message, 4);
  }
  const serve = (file) =>
    new Promise((resolve) => {
      const server = net.createServer(serveClient);
      // Windows: a pipe is not a file, and umask means nothing to it.
      const oldMask = WIN ? null : process.umask(0o177);
      server.listen(file, () => {
        if (oldMask !== null) process.umask(oldMask);
        debug(`listening on ${file}`);
        resolve();
      });
    });
  if (fixedSocket()) {
    // One fixed socket (FOXREACH_SOCKET): the first browser serves it, others leave it alone.
    if (await isAlive(sock)) {
      debug("socket busy");
      return fail(`Another browser already serves ${sock}.`, 3);
    }
    if (!isPipe(sock)) fs.rmSync(sock, { force: true });
    ownsSocket = true;
    await serve(sock);
    return;
  }
  // Each browser serves agents on its own socket, described in a file beside it, so several
  // browsers can be connected at once and agents choose (bridge/connected.mjs).
  registration = register({ profile });
  await serve(registration.socket);
  // Agents from before several browsers could connect use browser.sock: serve it too while no
  // other browser does. (Not on Windows, which has no such agents and no fixed pipe.)
  let legacy = false;
  if (sock && !(await isAlive(sock))) {
    fs.rmSync(sock, { force: true });
    ownsSocket = true;
    legacy = true;
    await serve(sock);
  }
  registration.update({ legacy });
})();
