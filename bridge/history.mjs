// When browsers connected to foxreach and how each connection ended, so an agent can tell that the
// browser closed or crashed between its tasks (status, and the error when nothing is connected).
// Each native host appends JSON lines to history.jsonl in the foxreach socket folder: the one
// folder every host can write, Flatpak browsers' included. It is cleared with that folder, at
// logout or reboot.
import fs from "node:fs";
import path from "node:path";
import { folder } from "./connected.mjs";

const KEEP_LINES = 200;
const MAX_BYTES = 64 * 1024;

export const historyFile = () => path.join(folder(), "history.jsonl");

export function appendHistory(record, file = historyFile()) {
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
    if ((fs.statSync(file, { throwIfNoEntry: false })?.size ?? 0) > MAX_BYTES) {
      const kept = fs.readFileSync(file, "utf8").trim().split("\n").slice(-KEEP_LINES);
      fs.writeFileSync(file, `${kept.join("\n")}\n`, { mode: 0o600 });
    }
    fs.appendFileSync(file, `${JSON.stringify({ at: new Date().toISOString(), ...record })}\n`, { mode: 0o600 });
  } catch {
    // history is a convenience: never let it stop the host
  }
}

const HOW = {
  quit: "the browser quit normally",
  addon: "the add-on's connection ended while the browser kept running (add-on reloaded, updated or disabled, or the browser was still shutting down)",
  lost: "the browser exited without quitting normally (crashed, was killed, or the session ended); sign-ins kept only for the session are gone",
  closed: "the browser closed (this add-on, an older one or the build without privileged code, cannot tell a normal quit from a crash)",
  host: "foxreach's native host was stopped",
  unknown: "no end was recorded (the host was killed together with the browser, or the computer stopped)",
};

const alive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === "EPERM";
  }
};

/**
 * The last sessions, newest first: { browser, profile, started, ended, how, detail }. A session
 * still running has ended null. A start with no end whose host is gone is reported as "unknown";
 * a Flatpak host's pid is in its sandbox's numbering, so it cannot be checked from here.
 */
export function recentSessions(limit = 5, file = historyFile()) {
  let lines;
  try {
    lines = fs.readFileSync(file, "utf8").trim().split("\n");
  } catch {
    return [];
  }
  const sessions = new Map();
  for (const line of lines) {
    let r;
    try {
      r = JSON.parse(line);
    } catch {
      continue;
    }
    const key = `${r.hostPid}@${r.hostStarted}`;
    if (r.event === "start") {
      sessions.set(key, { browser: r.browser, profile: r.profile, packaging: r.packaging, started: r.at, ended: null, how: null, hostPid: r.hostPid });
    } else if (r.event === "end" && sessions.has(key)) {
      Object.assign(sessions.get(key), { ended: r.at, how: r.how });
    }
  }
  return [...sessions.values()].reverse().slice(0, limit).map(({ hostPid, packaging, ...s }) => {
    if (!s.ended && packaging !== "flatpak" && !alive(hostPid)) s.how = "unknown";
    return { ...s, ...(s.how ? { detail: HOW[s.how] ?? s.how } : {}) };
  });
}

/** One sentence on the last session that ended, for the error shown when nothing is connected. */
export function lastEndNote(file = historyFile()) {
  const last = recentSessions(5, file).find((s) => s.how);
  if (!last) return "";
  const when = last.ended ? ` at ${new Date(last.ended).toLocaleString()}` : "";
  return ` Last connection: ${last.browser ?? "a browser"}${last.profile ? ` (profile ${last.profile})` : ""}, ended${when}: ${last.detail}.`;
}
