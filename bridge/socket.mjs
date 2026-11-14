import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

/**
 * Refuse a socket folder someone else could swap: whoever controls the folder, or one above it,
 * can put their own socket there and then sit between agents and the browser. Each folder up to
 * the root must belong to this user or root, and only a sticky one (/tmp) may be writable by others.
 */
export function assertPrivate(dir) {
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
 * macOS gives each user a private temporary folder (TMPDIR in a login session). Asked of the
 * system rather than read from TMPDIR, so the browser's host and an agent started without it (from
 * launchd, ssh or cron) agree on the socket.
 */
function darwinUserTemp() {
  const asked = spawnSync("/usr/bin/getconf", ["DARWIN_USER_TEMP_DIR"], { encoding: "utf8" });
  const dir = asked.status === 0 ? asked.stdout.trim() : "";
  return dir && path.isAbsolute(dir) ? dir : null;
}

export function socketPath() {
  if (process.env.FOXREACH_SOCKET) return process.env.FOXREACH_SOCKET;
  if (process.env.XDG_RUNTIME_DIR) return path.join(process.env.XDG_RUNTIME_DIR, "foxreach", "browser.sock");
  const mac = process.platform === "darwin" ? darwinUserTemp() : null;
  if (mac) return path.join(mac, "foxreach", "browser.sock");
  return path.join(os.tmpdir(), `foxreach-${process.getuid()}`, "foxreach", "browser.sock");
}
