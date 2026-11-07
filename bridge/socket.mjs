import os from "node:os";
import path from "node:path";

export function socketPath() {
  if (process.env.FOXREACH_SOCKET) return process.env.FOXREACH_SOCKET;
  const base = process.env.XDG_RUNTIME_DIR ?? path.join(os.tmpdir(), `foxreach-${process.getuid()}`);
  return path.join(base, "foxreach", "browser.sock");
}
