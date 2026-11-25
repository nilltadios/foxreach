// `foxreach test`: can an agent reach a browser right now? Starts the exact command the agent's
// settings name for foxreach (as the agent would), speaks MCP to it, and calls the `status` tool,
// which the add-on answers without asking the user. Reports which browser answered, or why none did.
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { agents } from "./agents.mjs";
import { socketPath } from "./socket.mjs";

// Executable names of Firefox-based browsers. Forks report themselves to add-ons as "Firefox",
// so the browser is named from the process that started the native host instead.
const BROWSERS = { firefox: "Firefox", "firefox-bin": "Firefox", "firefox-esr": "Firefox ESR", librewolf: "LibreWolf",
  "librewolf-bin": "LibreWolf", floorp: "Floorp", waterfox: "Waterfox", zen: "Zen", "zen-bin": "Zen" };

/** The browser whose native host holds foxreach's socket, from /proc, or null. */
export function socketBrowser(socket = socketPath(), proc = "/proc") {
  try {
    const line = fs.readFileSync(path.join(proc, "net/unix"), "utf8").split("\n").find((l) => l.trim().endsWith(` ${socket}`));
    const inode = line?.trim().split(/\s+/)[6];
    if (!inode) return null;
    const link = `socket:[${inode}]`;
    for (const pid of fs.readdirSync(proc).filter((d) => /^\d+$/.test(d))) {
      let fds;
      try {
        fds = fs.readdirSync(path.join(proc, pid, "fd"));
      } catch {
        continue;
      }
      if (!fds.some((fd) => { try { return fs.readlinkSync(path.join(proc, pid, "fd", fd)) === link; } catch { return false; } })) continue;
      // Walk up from the host (through its wrapper shell) to the first browser.
      for (let p = pid, i = 0; p && p !== "1" && i < 8; i++) {
        let exe = "";
        try {
          exe = fs.readlinkSync(path.join(proc, p, "exe"));
        } catch {
          // not ours to read
        }
        const name = BROWSERS[path.basename(exe)];
        if (name) return { name, flatpak: exe.startsWith("/app/"), appimage: exe.includes("/.mount_") };
        p = fs.readFileSync(path.join(proc, p, "stat"), "utf8").split(") ").pop().split(" ")[1];
      }
    }
  } catch {
    // no /proc, or the socket is not there
  }
  return null;
}

const TIMEOUT_MS = 15_000;
const MAX_PROBE_OUT_CHARS = 1 * 1024 * 1024;
const MAX_PROBE_ERR_CHARS = 64 * 1024;
const MAX_PROBE_CONCURRENCY = 4;

/** The argv an agent's foxreach entry runs, or null. Entries are objects or TOML/YAML text. */
export function entryArgv(entry) {
  if (entry === null || entry === undefined) return null;
  if (typeof entry === "string") {
    const cmd = /^\s*command\s*[=:]\s*("(?:[^"\\]|\\.)*")\s*$/m.exec(entry);
    const args = /^\s*args\s*[=:]\s*(\[.*\])\s*$/m.exec(entry);
    try {
      return cmd ? [JSON.parse(cmd[1]), ...(args ? JSON.parse(args[1]) : [])] : null;
    } catch {
      return null;
    }
  }
  const c = entry.command;
  const argv = [...(Array.isArray(c) ? c : c ? [c] : []), ...(Array.isArray(entry.args) ? entry.args : [])];
  return argv.length ? argv.map(String) : null;
}

/** Run an MCP server command and call `status`. Resolves {ok, browser, addon, error}. */
export function probe(argv, { timeoutMs = TIMEOUT_MS } = {}) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(argv[0], argv.slice(1), { stdio: ["pipe", "pipe", "pipe"] });
    } catch (e) {
      resolve({ ok: false, error: `Cannot start ${argv[0]}: ${e.message}` });
      return;
    }
    let out = "";
    let err = "";
    let settled = false;
    const finish = (r) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      child.kill();
      if (child.exitCode === null && child.signalCode === null) {
        setTimeout(() => {
          try {
            if (child.exitCode === null && child.signalCode === null) child.kill("SIGKILL");
          } catch {
            // already gone
          }
        }, 1000).unref();
      }
      resolve(r);
    };
    const timer = setTimeout(() => finish({ ok: false, error: "No answer within 15 seconds." }), timeoutMs);
    const send = (msg) => child.stdin.write(`${JSON.stringify(msg)}\n`);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.on("error", (e) => finish({ ok: false, error: `Cannot start ${argv[0]}: ${e.message}` }));
    child.on("exit", (code) => finish({ ok: false, error: `The command exited (${code}) before answering. ${err.trim().split("\n").pop() ?? ""}`.trim() }));
    child.stderr.on("data", (d) => {
      err += d;
      if (err.length > MAX_PROBE_ERR_CHARS) err = err.slice(-MAX_PROBE_ERR_CHARS);
    });
    child.stdout.on("data", (d) => {
      if (out.length + d.length > MAX_PROBE_OUT_CHARS) {
        out = "";
        finish({ ok: false, error: "The command output exceeded the 1 MiB limit." });
        return;
      }
      out += d;
      let nl;
      while ((nl = out.indexOf("\n")) >= 0) {
        const line = out.slice(0, nl).trim();
        out = out.slice(nl + 1);
        if (!line) continue;
        let msg;
        try {
          msg = JSON.parse(line);
        } catch {
          continue;
        }
        if (msg.id === 1) {
          send({ jsonrpc: "2.0", method: "notifications/initialized" });
          send({ jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "status", arguments: { ask: false } } });
        } else if (msg.id === 2) {
          const text = msg.result?.content?.find((c) => c.type === "text")?.text;
          if (msg.error || msg.result?.isError || !text) {
            finish({ ok: false, error: (msg.error?.message ?? text ?? "No answer.").split("\n")[0] });
            return;
          }
          try {
            const s = JSON.parse(text);
            const b = s.browser ?? {};
            const found = socketBrowser();
            const name = found ? `${found.name}${found.flatpak ? " (Flatpak)" : found.appimage ? " (AppImage)" : ""}` : b.name;
            finish({ ok: true, browser: name || "a browser", browserVersion: b.version ?? null, addon: s.version ?? null, warning: s.warning ?? null });
          } catch {
            finish({ ok: false, error: text.split("\n")[0] });
          }
        }
      }
    });
    send({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "foxreach-test", version: "1" } } });
  });
}

/** Test the agents named (or every one with a foxreach entry). One result per agent. */
export async function testAgents({ only = null } = {}) {
  const list = agents().filter((a) => (only ? only.includes(a.id) : true));
  if (only) {
    const unknown = only.filter((id) => !list.some((a) => a.id === id));
    if (unknown.length) throw new Error(`Unknown agent: ${unknown.join(", ")}`);
  }
  const rows = new Array(list.length);
  let nextIndex = 0;
  const worker = async () => {
    while (nextIndex < list.length) {
      const i = nextIndex++;
      const a = list[i];
      let entry;
      try {
        entry = a.format.current(a.file);
      } catch (e) {
        rows[i] = { id: a.id, name: a.name, ok: false, error: e.message };
        continue;
      }
      if (!entry) {
        rows[i] = only ? { id: a.id, name: a.name, ok: false, error: "foxreach is not set in this agent." } : null;
        continue;
      }
      const argv = entryArgv(entry);
      if (!argv) {
        rows[i] = { id: a.id, name: a.name, ok: false, error: "Cannot read the command in its entry." };
        continue;
      }
      rows[i] = { id: a.id, name: a.name, command: argv.join(" "), ...(await probe(argv)) };
    }
  };
  await Promise.all(Array.from({ length: Math.min(MAX_PROBE_CONCURRENCY, list.length) }, worker));
  return rows.filter(Boolean);
}
