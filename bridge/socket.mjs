import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { WIN, localAppData } from "./win.mjs";

/**
 * Refuse a socket folder someone else could swap: whoever controls the folder, or one above it,
 * can put their own socket there and then sit between agents and the browser. Each folder up to
 * the root must belong to this user or root, and only a sticky one (/tmp) may be writable by others.
 */
export function assertPrivate(dir) {
  // Windows: the folder is in %LOCALAPPDATA%, which only this user (and administrators) may open,
  // and the endpoints are named pipes, not files in it (see socketPath).
  if (WIN) return;
  const me = process.getuid();
  for (let d = path.resolve(dir), first = true; ; d = path.dirname(d), first = false) {
    const st = fs.statSync(d);
    const shared = (st.mode & 0o022) !== 0;
    if ((st.uid !== me && st.uid !== 0) || (shared && (first || !(st.mode & 0o1000)))) {
      throw new Error(`Unsafe socket folder: ${d} is owned or writable by someone else. Set XDG_RUNTIME_DIR, or FOXREACH_SOCKET to a path in a folder only you can write.`);
    }
    if (d === path.dirname(d)) return;
  }
}

/**
 * macOS gives each user private folders under /var/folders. The socket goes in the cache one
 * (DARWIN_USER_CACHE_DIR): the temporary one beside it is swept of files unused for three days,
 * which a browser left open that long would lose its socket to. Asked of the system rather than
 * read from the environment, so the browser's host and an agent started from anywhere (launchd,
 * ssh, cron, a shell that exports XDG_RUNTIME_DIR) agree on it.
 */
function darwinUserCache() {
  const asked = spawnSync("/usr/bin/getconf", ["DARWIN_USER_CACHE_DIR"], { encoding: "utf8" });
  const dir = asked.status === 0 ? asked.stdout.trim() : "";
  return dir && path.isAbsolute(dir) ? dir : null;
}

/**
 * Windows: hosts listen on named pipes, not sockets in a private folder. Every local user can list
 * pipe names and take one that is free, so there is no fixed pipe there (an agent would trust
 * whoever took it first). Each host gets a pipe of its own, listed with a secret token in a file in
 * the private folder below, and an agent talks to a pipe only once it has proved it knows that token
 * (bridge/connected.mjs, bridge/mcp.mjs). socketPath() is null on Windows unless FOXREACH_SOCKET
 * names one (tests).
 */
export const pipeName = (key) => String.raw`\\.\pipe\foxreach-` + key;
export const PIPE_RE = /^\\\\\.\\pipe\\foxreach-[0-9a-f]{16}$/;
/** True for any named pipe (\\.\pipe\... or \\?\pipe\...), which is no file to remove. */
export const isPipe = (p) => /^\\\\[.?]\\pipe\\/i.test(String(p ?? ""));

/** The folder holding each host's info file: the socket's folder, except on Windows. */
export function runFolder() {
  if (WIN && !process.env.FOXREACH_SOCKET) return path.join(localAppData(), "foxreach", "run");
  return path.dirname(socketPath());
}

export function socketPath() {
  if (process.env.FOXREACH_SOCKET) return process.env.FOXREACH_SOCKET;
  if (WIN) return null;
  const mac = process.platform === "darwin" ? darwinUserCache() : null;
  if (mac) return path.join(mac, "foxreach", "browser.sock");
  if (process.env.XDG_RUNTIME_DIR) return path.join(process.env.XDG_RUNTIME_DIR, "foxreach", "browser.sock");
  return path.join(os.tmpdir(), `foxreach-${process.getuid()}`, "foxreach", "browser.sock");
}
