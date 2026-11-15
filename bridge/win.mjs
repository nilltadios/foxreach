// Windows: what the other modules read from /proc, ps or the file system on Linux and macOS. Native
// messaging hosts are registered in the registry there, the browser's process is found in the
// process table, and an open profile is told by its parent.lock, which the browser keeps open with
// no sharing allowed. Only imported where process.platform is "win32".
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const WIN = process.platform === "win32";

/** %APPDATA% (Roaming) and %LOCALAPPDATA%, with the usual defaults when they are not set. */
export const appData = (h = os.homedir()) => process.env.APPDATA || path.join(h, "AppData", "Roaming");
export const localAppData = (h = os.homedir()) => process.env.LOCALAPPDATA || path.join(h, "AppData", "Local");

/** The registry key Firefox and its forks read native-messaging hosts from, for this user. The
 * "Mozilla" in it is fixed in Firefox's code (NativeManifests), so forks read the same key. */
export const HOSTS_KEY = String.raw`HKCU\Software\Mozilla\NativeMessagingHosts`;

// Windows' own programs by their full path: neither PATH nor the current folder chooses them.
const SYSTEM32 = path.join(process.env.SystemRoot || String.raw`C:\Windows`, "System32");
export const POWERSHELL = path.join(SYSTEM32, "WindowsPowerShell", "v1.0", "powershell.exe");

const reg = (...args) => spawnSync(path.join(SYSTEM32, "reg.exe"), args, { encoding: "utf8", windowsHide: true });

/**
 * reg.exe query. It prints in the console's code page, the OEM one, which cannot show most letters
 * outside ASCII (a user folder called José or 田中): it runs through cmd.exe with UTF-8 set first.
 * Keys with characters cmd.exe would read itself (" %) are asked directly.
 */
function regQuery(...args) {
  if (args.some((a) => /["%\r\n]/.test(a))) return reg("query", ...args);
  const line = [path.join(SYSTEM32, "reg.exe"), "query", ...args].map((a) => `"${a}"`).join(" ");
  return spawnSync(path.join(SYSTEM32, "cmd.exe"), ["/d", "/s", "/c", `"chcp 65001 >nul & ${line}"`],
    { encoding: "utf8", windowsHide: true, windowsVerbatimArguments: true });
}

/** A key's default value, or null (also when the key has none: reg.exe prints "(value not set)"). */
export function regDefault(key) {
  const r = regQuery(key, "/ve");
  if (r.error || r.status !== 0) return null;
  const m = /REG_(?:EXPAND_)?SZ\s+(.*)$/m.exec(r.stdout);
  const value = m ? m[1].trim() : "";
  return value && !/^\(.*\)$/.test(value) ? value : null;
}

export function regSetDefault(key, value) {
  const r = reg("add", key, "/ve", "/t", "REG_SZ", "/d", value, "/f");
  if (r.error || r.status !== 0) throw new Error(`Could not write ${key}: ${(r.stderr || r.stdout || r.error || "").toString().trim()}`);
}

/** Delete a key; true when it was there. */
export function regDelete(key) {
  if (regDefault(key) === null && regQuery(key).status !== 0) return false;
  const r = reg("delete", key, "/f");
  return !r.error && r.status === 0;
}

// reg.exe prints hives in full (HKEY_LOCAL_MACHINE\...) whatever short name it was given.
const HIVES = { hklm: "HKEY_LOCAL_MACHINE", hkcu: "HKEY_CURRENT_USER", hkcr: "HKEY_CLASSES_ROOT", hku: "HKEY_USERS" };
const fullKey = (key) => key.replace(/^(HKLM|HKCU|HKCR|HKU)(?=\\|$)/i, (h) => HIVES[h.toLowerCase()]);

/** The subkeys of a key (full names), or []. */
export function regSubkeys(key) {
  const r = regQuery(key);
  if (r.error || r.status !== 0) return [];
  const prefix = `${fullKey(key).toLowerCase()}\\`;
  return r.stdout.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.toLowerCase().startsWith(prefix));
}

let table;
/** Why the process table could not be read, or null. */
export let processesError = null;
/**
 * Every process: {pid, ppid, name, exe, cmd}. One PowerShell call, about a second, so it is asked
 * once per run (the host asks at its start; nothing else needs it more than once). Printed as UTF-8:
 * in the console's code page a profile folder named with letters outside ASCII would be mangled.
 */
export function processes() {
  if (table) return table;
  const script = "[Console]::OutputEncoding = [Text.Encoding]::UTF8; Get-CimInstance Win32_Process | ForEach-Object { " +
    "[string]$_.ProcessId + \"`t\" + [string]$_.ParentProcessId + \"`t\" + $_.Name + \"`t\" + $_.ExecutablePath + \"`t\" + " +
    "($_.CommandLine -replace \"[`t`r`n]\", ' ') }";
  const r = spawnSync(POWERSHELL, ["-NoProfile", "-NonInteractive", "-Command", script],
    { encoding: "utf8", windowsHide: true, timeout: 20_000, maxBuffer: 64 * 1024 * 1024 });
  table = [];
  if (r.error || r.status !== 0) {
    processesError = `${POWERSHELL}: ${r.error?.message ?? `exit ${r.status}: ${(r.stderr ?? "").trim().slice(0, 300)}`}`;
    return table;
  }
  for (const line of r.stdout.split(/\r?\n/)) {
    const [pid, ppid, name, exe, cmd] = line.split("\t");
    if (!/^\d+$/.test(pid ?? "")) continue;
    table.push({ pid: Number(pid), ppid: Number(ppid), name: name ?? "", exe: exe ?? "", cmd: cmd ?? "" });
  }
  return table;
}

/** Image names of this user's running processes (lower case, without .exe), from tasklist: fast. */
export function runningImages() {
  const r = spawnSync(path.join(SYSTEM32, "tasklist.exe"), ["/FO", "CSV", "/NH"], { encoding: "utf8", windowsHide: true, timeout: 20_000 });
  if (r.error || r.status !== 0) return null;
  return r.stdout.split(/\r?\n/).map((l) => /^"([^"]+)"/.exec(l)?.[1]).filter(Boolean).map(exeBase);
}

/** "C:\...\LibreWolf.exe" -> "librewolf". */
export const exeBase = (p) => path.win32.basename(String(p)).toLowerCase().replace(/\.exe$/, "");

/**
 * The process and its ancestors, nearest first (the host, the cmd.exe a .bat wrapper runs in, the
 * browser): up to `depth` of them, from the process table.
 */
export function ancestors(pid = process.pid, depth = 6) {
  const byPid = new Map(processes().map((p) => [p.pid, p]));
  const out = [];
  for (let p = byPid.get(pid); p && out.length < depth; p = byPid.get(p.ppid)) {
    out.push(p);
    if (p.ppid === p.pid) break;
  }
  return out;
}

/** True while a browser has the profile open: it holds parent.lock open and shares it with nobody. */
export function profileLocked(profile) {
  const lock = path.join(profile, "parent.lock");
  let fd;
  try {
    fd = fs.openSync(lock, "r+");
  } catch (e) {
    return e.code === "EBUSY" || e.code === "EPERM" || e.code === "EACCES";
  }
  fs.closeSync(fd);
  return false;
}

/** The value of -profile / --profile in a command line, or null. */
export function profileArg(cmd) {
  const m = /(?:^|\s)--?profile(?:\s+|=)("([^"]+)"|(\S+))/i.exec(cmd ?? "");
  return m ? (m[2] ?? m[3]) : null;
}

/** The value of -P <name> (a profile by its name in profiles.ini), or null. -P alone opens the
 * profile manager. */
export function profileNameArg(cmd) {
  const m = /(?:^|\s)--?P(?:\s+|=)("([^"]+)"|([^\s"-]\S*))/i.exec(cmd ?? "");
  return m ? (m[2] ?? m[3]) : null;
}

/** Paths compare without regard to case or slashes on Windows. */
export const samePath = (a, b) => !!a && !!b && path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase();
