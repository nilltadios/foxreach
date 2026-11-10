// Native messaging host. The browser starts it and owns its lifetime. It relays between the extension
// (stdin/stdout, 4-byte length prefix) and local MCP servers (a private Unix socket, JSON lines).
import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { assertPrivate, socketPath } from "./socket.mjs";

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

function toBrowser(message) {
  const body = Buffer.from(JSON.stringify(message));
  const header = Buffer.alloc(4);
  header.writeUInt32LE(body.length);
  process.stdout.write(Buffer.concat([header, body]));
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
  if (message.hello) {
    hello = message.hello;
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

function shutdown() {
  debug("shutdown");
  // Only the host that created the socket may remove it: it may be another browser's.
  if (ownsSocket) {
    try {
      fs.unlinkSync(sock);
    } catch {}
  }
  process.exit(0);
}
process.stdin.on("end", shutdown);
process.on("SIGTERM", shutdown);

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
      const { id, client, tool, args } = request ?? {};
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
  fs.mkdirSync(path.dirname(sock), { recursive: true, mode: 0o700 });
  try {
    assertPrivate(path.dirname(sock));
  } catch (e) {
    debug(e.message);
    toBrowser({ hostError: e.message });
    process.exit(4);
  }
  if (await isAlive(sock)) {
    // Another browser profile already serves agents. Staying up would steal its socket.
    debug("socket busy");
    toBrowser({ hostError: `Another browser already serves ${sock}.` });
    process.exit(3);
  }
  fs.rmSync(sock, { force: true });
  ownsSocket = true;
  const server = net.createServer(serveClient);
  const oldMask = process.umask(0o177);
  server.listen(sock, () => {
    process.umask(oldMask);
    debug("listening");
  });
})();
