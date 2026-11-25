// Native messaging host. The browser starts it and owns its lifetime. It relays between the extension
// (stdin/stdout, 4-byte length prefix) and local MCP servers (a private Unix socket, JSON lines).
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { allOpenProfiles, allProfiles, namedProfile, openProfiles } from "./addon.mjs";
import { parentApp, windowsBrowser } from "./browsers.mjs";
import { describeParent, fixedSocket, folder, proofOf, register } from "./connected.mjs";
import { appendHistory } from "./history.mjs";
import { assertPrivate, isPipe, socketPath } from "./socket.mjs";
import { WIN, processesError, profileArg, profileNameArg } from "./win.mjs";

// The browser accepts at most 1 MB per message; 300k UTF-16 units cannot exceed that once encoded.
const CHUNK_CHARS = 300_000;
const MAX_FROM_BROWSER_BYTES = 64 * 1024 * 1024;
const MAX_CLIENTS = 64;
const MAX_CLIENT_LINE_CHARS = 96 * 1024 * 1024;
const MAX_CLIENT_WRITE_BUFFER_BYTES = 64 * 1024 * 1024;
const STDOUT_HIGH_WATER_BYTES = 16 * 1024 * 1024;
const SLAB_COMPACT_MIN_BYTES = 65_536;
const SLAB_COMPACT_FACTOR = 4;
const LINE_TIMEOUT_MS = 30_000;
// Set once this host is stopping over an error (fail): what the browser still sends is not read.
let failing = false;

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
const MAC = (process.env.FOXREACH_PLATFORM || process.platform) === "darwin";
function findProfile() {
  if (process.env.FOXREACH_HOST_PROFILE) return process.env.FOXREACH_HOST_PROFILE;
  if (WIN) return windowsProfile();
  if (MAC) return macProfile();
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
    // no /proc, or the parent is gone
  }
  return null;
}
/**
 * macOS has no /proc: ask lsof which .parentlock file the parent browser holds open, else fall back
 * to the open profiles of that browser (or all known browsers), refusing agents if any candidate is stopped.
 */
function macProfile() {
  const lsof = fs.existsSync("/usr/sbin/lsof") ? "/usr/sbin/lsof" : "lsof";
  const r = spawnSync(lsof, ["-Fn", "-p", String(process.ppid)], { encoding: "utf8" });
  if (!r.error && r.status === 0) {
    for (const line of r.stdout.split("\n")) {
      if (line.startsWith("n") && path.basename(line.slice(1)) === ".parentlock") {
        return path.dirname(line.slice(1));
      }
    }
  }
  const app = parentApp();
  const open = app ? openProfiles(app) : allOpenProfiles();
  candidates = open;
  return open.length === 1 ? open[0] : null;
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
/** Null, or what to answer an agent with: the user stopped agents here, or maybe here. */
function agentsStopped() {
  profile ??= findProfile();
  if (profile !== null) return fs.existsSync(path.join(profile, STOP_MARK)) ? STOPPED : null;
  // Which profile this is could not be told. With some open ones to choose from, a stop in any of
  // them counts; with none found at all, a stop in any profile foxreach can find does. Not knowing
  // must never let agents into a profile the user stopped them in.
  const maybe = candidates.length ? candidates : allProfiles();
  const stopped = maybe.filter((c) => fs.existsSync(path.join(c, STOP_MARK)));
  return stopped.length ? stoppedMaybeHere(stopped) : null;
}
const STOPPED = "The user stopped agents in this browser profile (foxreach is still installed there). " +
  "Ask the user to let agents use it again, from the foxreach manager or with: foxreach addon start --profile <folder>.";
const stoppedMaybeHere = (stopped) => `The user stopped agents in ${stopped.length === 1 ? "a browser profile" : "browser profiles"} ` +
  `(${stopped.join(", ")}), and foxreach cannot tell which profile this browser is using, so it refuses agents here as well. ` +
  "Ask the user to let agents use it again, from the foxreach manager or with: foxreach addon start --profile <folder>.";

// What waits to go to the browser, in order. While the browser reads slower than agents send,
// their sockets are paused, so the wait stays in the agents and not in this process's memory.
const outQueue = [];
let outQueuedBytes = 0;
let outDraining = false;
let clientsPaused = false;
function setClientsPaused(paused) {
  if (clientsPaused === paused) return;
  clientsPaused = paused;
  for (const c of clients.values()) {
    if (paused) c.pause();
    else c.resume();
  }
}
function flushOut() {
  while (outQueue.length) {
    const item = outQueue.shift();
    outQueuedBytes -= item.frame.length;
    const ok = process.stdout.write(item.frame, item.done);
    if (!ok) {
      outDraining = true;
      setClientsPaused(true);
      process.stdout.once("drain", () => {
        outDraining = false;
        flushOut();
      });
      return;
    }
  }
  if (outQueuedBytes < STDOUT_HIGH_WATER_BYTES) setClientsPaused(false);
}

function toBrowser(message, done) {
  const body = Buffer.from(JSON.stringify(message));
  const header = Buffer.alloc(4);
  header.writeUInt32LE(body.length);
  const frame = Buffer.concat([header, body]);
  outQueue.push({ frame, done });
  outQueuedBytes += frame.length;
  if (outQueuedBytes >= STDOUT_HIGH_WATER_BYTES) setClientsPaused(true);
  if (!outDraining) flushOut();
}

/** Tell the add-on why this host stops, then stop once the message is out (pipe writes are
 * asynchronous on Windows, and exiting at once could lose it). */
function fail(message, code) {
  failing = true;
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
  if (client) toClient(client, `${JSON.stringify({ ...message, id: Number(String(message.id).slice(split + 1)) })}\n`);
}

/**
 * Write to an agent's socket, unless the agent has stopped reading: one that already has more than
 * MAX_CLIENT_WRITE_BUFFER_BYTES waiting is dropped, so what it does not read cannot grow this
 * process. (What waits is then at most that limit and one more message.)
 */
function toClient(conn, text) {
  if (conn.writableLength > MAX_CLIENT_WRITE_BUFFER_BYTES) {
    debug("dropping an agent that does not read its replies");
    conn.destroy();
    return;
  }
  conn.write(text);
}

let hello = null;
// Chunked stdin accumulator: avoids O(n^2) Buffer.concat on large replies and never leaves a
// zero-length or tiny subarray pinning a multi-megabyte ArrayBuffer while idle.
let stdinChunks = [];
let stdinBytes = 0;
function compactRemainder(rem) {
  if (rem.length === 0) return null;
  if (rem.buffer.byteLength > SLAB_COMPACT_MIN_BYTES && rem.buffer.byteLength > rem.length * SLAB_COMPACT_FACTOR) {
    return Buffer.from(rem);
  }
  return rem;
}
function peekStdinLength() {
  if (stdinChunks[0].length >= 4) return stdinChunks[0].readUInt32LE(0);
  const joined = Buffer.concat(stdinChunks, stdinBytes);
  stdinChunks = [joined];
  return joined.readUInt32LE(0);
}
function takeStdin(n) {
  if (stdinChunks.length === 1) {
    const only = stdinChunks[0];
    if (only.length === n) {
      stdinChunks = [];
      stdinBytes = 0;
      return only;
    }
    const frame = only.subarray(0, n);
    const rem = compactRemainder(only.subarray(n));
    stdinChunks = rem ? [rem] : [];
    stdinBytes -= n;
    return frame;
  }
  const joined = Buffer.concat(stdinChunks, stdinBytes);
  const frame = joined.subarray(0, n);
  const rem = compactRemainder(joined.subarray(n));
  stdinChunks = rem ? [rem] : [];
  stdinBytes = rem ? rem.length : 0;
  return frame;
}
process.stdin.on("data", (data) => {
  if (failing || !data.length) return;
  stdinChunks.push(data);
  stdinBytes += data.length;
  while (stdinBytes >= 4) {
    const length = peekStdinLength();
    if (length > MAX_FROM_BROWSER_BYTES) {
      stdinChunks = [];
      stdinBytes = 0;
      debug(`oversized native message from browser: ${length} bytes`);
      fail(`Browser message exceeded ${MAX_FROM_BROWSER_BYTES} bytes.`, 5);
      return;
    }
    if (stdinBytes < 4 + length) break;
    const frame = takeStdin(4 + length);
    let message;
    try {
      message = JSON.parse(frame.subarray(4).toString("utf8"));
    } catch (e) {
      debug(`malformed browser JSON: ${e.message}`);
      continue;
    }
    fromBrowser(message);
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
  if (clients.size >= MAX_CLIENTS) {
    conn.destroy();
    return;
  }
  const clientId = nextClient++;
  clients.set(clientId, conn);
  if (clientsPaused) conn.pause();
  let buffer = "";
  let lineTimer = null;
  const clearLineTimer = () => {
    if (lineTimer !== null) {
      clearTimeout(lineTimer);
      lineTimer = null;
    }
  };
  const armLineTimer = () => {
    clearLineTimer();
    if (!buffer.length) return;
    lineTimer = setTimeout(() => {
      buffer = "";
      conn.destroy();
    }, LINE_TIMEOUT_MS);
    lineTimer.unref?.();
  };
  conn.setEncoding("utf8");
  conn.on("data", (data) => {
    if (buffer.length + data.length > MAX_CLIENT_LINE_CHARS) {
      buffer = "";
      clearLineTimer();
      conn.destroy();
      return;
    }
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
        if (token) toClient(conn, `${JSON.stringify({ proof: proofOf(token, request.challenge.slice(0, 200)) })}\n`);
        continue;
      }
      const { id, client, tool, args } = request ?? {};
      const stopped = agentsStopped();
      if (stopped) {
        toClient(conn, `${JSON.stringify({ id, error: stopped })}\n`);
        continue;
      }
      sendRequest({ id: `${clientId}:${id}`, client: String(client ?? "agent").slice(0, 80), tool: String(tool), args });
    }
    armLineTimer();
  });
  // The add-on keeps what the user allowed per connection; tell it when one ends.
  conn.on("close", () => {
    buffer = "";
    clearLineTimer();
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
      server.on("error", (e) => {
        if (oldMask !== null) process.umask(oldMask);
        debug(`listen error on ${file}: ${e.message}`);
        fail(`Cannot listen on ${file}: ${e.message}`, 5);
      });
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
