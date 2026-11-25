// `foxreach update`: replace this program with the latest release, the way install.sh installs it
// (same files, same checksum check), then refresh what `foxreach install` wrote (the host wrapper,
// the browsers' manifests, the copies inside Flatpak browsers). The add-on updates itself.
//
// Agents that are running keep the program they started; they use the new one in their next
// session. The old program is kept beside the new one as <program>.previous.
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { compiled } from "./install.mjs";
import { VERSION } from "./version.mjs";
import { WIN, localAppData } from "./win.mjs";

const RELEASES = process.env.FOXREACH_RELEASE_URL || "https://github.com/nilltadios/foxreach/releases/latest/download";
const PLATFORMS = { linux: "linux", darwin: "darwin", win32: "windows" };
const ARCHES = { x64: "x64", arm64: "arm64" };
const MAX_ASSET_BYTES = 256 * 1024 * 1024;
const MAX_META_BYTES = 1 * 1024 * 1024;
// How long a download may deliver nothing before it is given up; a slow one that keeps coming is
// not cut off, whatever its size.
const STALL_TIMEOUT_MS = 60_000;

/** The release file built for this machine, or null. */
export function assetName(platform = process.platform, arch = process.arch) {
  if (!PLATFORMS[platform] || !ARCHES[arch]) return null;
  return `foxreach-${PLATFORMS[platform]}-${ARCHES[arch]}${platform === "win32" ? ".exe" : ""}`;
}

/**
 * macOS runs no program whose signature does not match it. Bun signs its arm64 builds; its x64
 * build keeps the signature of Bun's own runtime, which the packed program no longer matches. Sign
 * such a file ad hoc here, as install.sh does. Files that are not Mach-O programs are left alone.
 */
export function signForMac(file) {
  if (process.platform !== "darwin") return;
  const head = Buffer.alloc(4);
  const fd = fs.openSync(file, "r");
  try {
    fs.readSync(fd, head, 0, 4, 0);
  } finally {
    fs.closeSync(fd);
  }
  if (head.readUInt32LE(0) !== 0xfeedfacf) return; // 64-bit Mach-O
  if (spawnSync("/usr/bin/codesign", ["-v", file]).status === 0) return;
  const signed = spawnSync("/usr/bin/codesign", ["--force", "--sign", "-", file], { encoding: "utf8" });
  if (signed.status !== 0) throw new Error(`Could not sign ${file} for macOS: ${signed.stderr || signed.error}. Nothing was changed.`);
}

/** How this copy of foxreach is updated, for messages such as the version warning. */
export function updateAdvice() {
  return compiled
    ? "Update the foxreach program with: foxreach update (the add-on updates itself)."
    : "Update both with: uv run --python 3.15 scripts/update.py (in the foxreach folder).";
}

/** -1, 0 or 1, comparing dotted numeric versions ("0.3.10" > "0.3.9"). */
export function compareVersions(a, b) {
  const pa = String(a).split(".").map(Number);
  const pb = String(b).split(".").map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] || 0) - (pb[i] || 0);
    if (d) return Math.sign(d);
  }
  return 0;
}

/** A release file as chunks, read from the network: each chunk that arrives resets the stall timer. */
async function fetchChunks(name) {
  const url = `${RELEASES}/${name}`;
  const stall = new AbortController();
  let timer;
  const alive = () => {
    clearTimeout(timer);
    timer = setTimeout(() => stall.abort(new Error(`nothing arrived for ${STALL_TIMEOUT_MS / 1000} seconds`)), STALL_TIMEOUT_MS);
  };
  alive();
  let r;
  try {
    r = await fetch(url, { redirect: "follow", signal: stall.signal });
  } catch (e) {
    clearTimeout(timer);
    throw new Error(`Cannot reach ${url}: ${stall.signal.aborted ? stall.signal.reason.message : e.message ?? e}`);
  }
  if (!r.ok) {
    clearTimeout(timer);
    throw new Error(`${url}: HTTP ${r.status}`);
  }
  return (async function* chunks() {
    try {
      // Never the whole file at once (arrayBuffer): the caller counts the pieces against its limit.
      if (!r.body) throw new Error("the response has no body to read in pieces");
      for await (const chunk of r.body) {
        alive();
        yield Buffer.from(chunk.buffer, chunk.byteOffset, chunk.byteLength);
      }
    } catch (e) {
      throw new Error(`Download of ${url} failed: ${stall.signal.aborted ? stall.signal.reason.message : e.message ?? e}`);
    } finally {
      clearTimeout(timer);
    }
  })();
}

/** Write all of buf: one write may take only part of it. */
function writeAll(fd, buf) {
  for (let done = 0; done < buf.length;) done += fs.writeSync(fd, buf, done, buf.length - done);
}

/** Read a small release metadata file (BUILDINFO.txt, SHA256SUMS) with a strict byte cap. */
async function downloadText(name, maxBytes = MAX_META_BYTES) {
  const url = `${RELEASES}/${name}`;
  if (url.startsWith("file:")) {
    try {
      const filePath = new URL(url);
      if (fs.statSync(filePath).size > maxBytes) throw new Error(`${url} exceeds ${maxBytes} bytes.`);
      return fs.readFileSync(filePath, "utf8");
    } catch (e) {
      throw new Error(`Cannot read ${url}: ${e.message ?? e}`);
    }
  }
  const parts = [];
  let total = 0;
  for await (const chunk of await fetchChunks(name)) {
    total += chunk.length;
    if (total > maxBytes) throw new Error(`${url} exceeds ${maxBytes} bytes.`);
    parts.push(Buffer.from(chunk));
  }
  return Buffer.concat(parts, total).toString("utf8");
}

/**
 * Download `asset` to `destPath` and check it against SHA256SUMS, a chunk at a time, so the program
 * (over 100 MB) is never held in memory. The file is not executable until the caller makes it so,
 * and is removed here when the checksum does not match.
 */
async function streamVerifiedAsset(asset, destPath) {
  const sums = await downloadText("SHA256SUMS");
  const want = sums.split("\n").map((l) => l.trim().split(/\s+/)).find((p) => p[1] === asset)?.[0];
  const url = `${RELEASES}/${asset}`;
  const hash = crypto.createHash("sha256");
  const fd = fs.openSync(destPath, "w", 0o600);
  let total = 0;
  try {
    if (url.startsWith("file:")) {
      let srcFd;
      try {
        srcFd = fs.openSync(new URL(url), "r");
      } catch (e) {
        throw new Error(`Cannot read ${url}: ${e.message ?? e}`);
      }
      try {
        const buf = Buffer.alloc(65_536);
        let n;
        while ((n = fs.readSync(srcFd, buf, 0, buf.length, null)) > 0) {
          total += n;
          if (total > MAX_ASSET_BYTES) throw new Error(`${url} exceeds ${MAX_ASSET_BYTES} bytes.`);
          const slice = buf.subarray(0, n);
          hash.update(slice);
          writeAll(fd, slice);
        }
      } finally {
        fs.closeSync(srcFd);
      }
    } else {
      for await (const chunk of await fetchChunks(asset)) {
        total += chunk.length;
        if (total > MAX_ASSET_BYTES) throw new Error(`${url} exceeds ${MAX_ASSET_BYTES} bytes.`);
        hash.update(chunk);
        writeAll(fd, chunk);
      }
    }
  } finally {
    fs.closeSync(fd);
  }
  const got = hash.digest("hex");
  if (!want || want !== got) {
    fs.rmSync(destPath, { force: true });
    throw new Error(`Checksum mismatch for ${asset}: expected ${want ?? "nothing listed"}, got ${got}. Nothing was changed.`);
  }
}

/** Where `foxreach install-program` puts the program, as install.sh does. */
export function programPath() {
  // Windows: a per-user program's usual place, %LOCALAPPDATA%\Programs (install.ps1 puts it there).
  if (WIN) return path.join(process.env.FOXREACH_BIN_DIR || path.join(localAppData(), "Programs", "foxreach"), "foxreach.exe");
  return path.join(process.env.FOXREACH_BIN_DIR || path.join(os.homedir(), ".local/bin"), "foxreach");
}

/** A new file to download into, beside `target`: Windows runs only files named .exe. */
const freshName = (dir, what) => path.join(dir, WIN ? `foxreach-${what}-${process.pid}.exe` : `.foxreach-${what}-${process.pid}`);

/**
 * Put `fresh` at `target`, the old file kept as <target>.previous. Elsewhere a rename replaces a
 * running program; Windows refuses to replace one, but lets it be renamed aside first.
 */
function swapIn(fresh, target, log) {
  const previous = `${target}.previous`;
  if (!fs.existsSync(target)) {
    fs.renameSync(fresh, target);
    return false;
  }
  if (!WIN) {
    fs.copyFileSync(target, previous);
    fs.chmodSync(previous, 0o755);
    fs.renameSync(fresh, target); // a running agent keeps the old file open; new starts get this one
    return true;
  }
  // A program from an earlier update may still run (a browser's host, an agent): Windows will not
  // delete it, but lets it be renamed aside. Such leftovers are deleted once they have closed.
  const dir = path.dirname(target);
  const aside = `${path.basename(previous)}-`;
  for (const n of fs.readdirSync(dir)) {
    if (!n.startsWith(aside)) continue;
    try {
      fs.rmSync(path.join(dir, n), { force: true });
    } catch {
      // still running
    }
  }
  try {
    fs.rmSync(previous, { force: true });
  } catch {
    try {
      fs.renameSync(previous, `${previous}-${Date.now()}`);
    } catch (e) {
      throw new Error(`${previous} is still running (an agent or browser started it) and cannot be moved aside: close them, then try again. Nothing was changed. (${e.code})`);
    }
  }
  fs.renameSync(target, previous); // allowed while it runs: agents and browsers keep the old one
  try {
    fs.renameSync(fresh, target);
  } catch (e) {
    fs.renameSync(previous, target);
    throw e;
  }
  log?.(`kept the old program as ${previous}`);
  return true;
}

/** Windows: a program it would not start at all was most likely blocked by Smart App Control. */
const sacHint = (says) => (WIN && says.error ? " Windows did not start it: Smart App Control may block programs that are not signed." : "");

/** How to undo, in this system's words: on Windows for PowerShell and for cmd.exe, which differ. */
function undo(target) {
  if (!WIN) return `mv ${target}.previous ${target} && ${target} install --agents none`;
  const ps = (p) => `'${p.replace(/'/g, "''")}'`;
  return `\n  PowerShell: Move-Item -Force ${ps(`${target}.previous`)} ${ps(target)}; & ${ps(target)} install --agents none` +
    `\n  cmd.exe:    move /y "${target}.previous" "${target}" && "${target}" install --agents none`;
}

/**
 * `foxreach install-program`: put the latest release at `to` (default ~/.local/bin/foxreach), as
 * install.sh does: same file, same checksum check, the old file kept as <to>.previous. Works from a
 * checkout too, so the manager can install the program without a bundled copy. Registers nothing:
 * the new program's own `install` does that.
 */
export async function installProgram({ to = programPath(), log = console.log } = {}) {
  const asset = assetName();
  if (!asset) throw new Error(`No foxreach build for ${process.platform} on ${process.arch}.`);
  const latest = await latestVersion();
  log(`Downloading foxreach ${latest} (${asset}) from ${RELEASES}`);
  const dir = path.dirname(to);
  fs.mkdirSync(dir, { recursive: true });
  const fresh = freshName(dir, "install");
  try {
    await streamVerifiedAsset(asset, fresh);
    fs.chmodSync(fresh, 0o755);
    signForMac(fresh);
    const says = spawnSync(fresh, ["version"], { encoding: "utf8", timeout: 30_000 });
    if (says.status !== 0 || (says.stdout ?? "").trim() !== latest) {
      throw new Error(`The downloaded program does not run as foxreach ${latest} (it said: ${(says.stdout ?? "").trim() || says.stderr || says.error}).${sacHint(says)} Nothing was changed.`);
    }
    if (!WIN && fs.existsSync(to)) {
      fs.copyFileSync(to, `${to}.previous`);
      log(`kept the old program as ${to}.previous`);
      fs.renameSync(fresh, to);
    } else {
      swapIn(fresh, to, log);
    }
  } finally {
    fs.rmSync(fresh, { force: true });
  }
  log(`installed ${to} (foxreach ${latest})`);
  return { program: to, version: latest };
}

async function latestVersion() {
  const info = await downloadText("BUILDINFO.txt");
  const m = /^foxreach (\d+(?:\.\d+)*)\s*$/m.exec(info);
  if (!m) throw new Error("The latest release does not say its version (BUILDINFO.txt).");
  return m[1];
}

/** {current, latest, newer} for `foxreach update --check --json`. */
export async function checkLatest() {
  const latest = await latestVersion();
  return { current: VERSION, latest, newer: compareVersions(latest, VERSION) > 0 };
}

export async function update({ check = false, force = false, log = console.log } = {}) {
  if (!compiled) {
    log(`This foxreach (${VERSION}) runs from a checkout, which is updated with git: ` +
      "uv run --python 3.15 scripts/update.py (in the foxreach folder).");
    return 0;
  }
  const latest = await latestVersion();
  const order = compareVersions(latest, VERSION);
  if (order <= 0 && !force) {
    log(`foxreach ${VERSION} is up to date (latest release: ${latest}).`);
    return 0;
  }
  if (check) {
    log(`foxreach ${latest} is available; this is ${VERSION}. Update with: foxreach update`);
    return 0;
  }

  const asset = assetName();
  if (!asset) throw new Error(`No foxreach build for ${process.platform} on ${process.arch}.`);
  const target = fs.realpathSync(process.execPath);
  const dir = path.dirname(target);
  try {
    fs.accessSync(dir, fs.constants.W_OK);
  } catch {
    throw new Error(`${dir} is not writable, so ${target} cannot be replaced.`);
  }

  log(`Downloading foxreach ${latest} (${asset}) from ${RELEASES}`);

  // Next to the program, so the final rename replaces it in one step (same filesystem).
  const fresh = freshName(dir, "update");
  try {
    await streamVerifiedAsset(asset, fresh);
    fs.chmodSync(fresh, 0o755);
    signForMac(fresh);
    const says = spawnSync(fresh, ["version"], { encoding: "utf8", timeout: 30_000 });
    const reported = (says.stdout ?? "").trim();
    if (says.status !== 0 || reported !== latest) {
      throw new Error(`The downloaded program does not run as foxreach ${latest} (it said: ${reported || says.stderr || says.error}).${sacHint(says)} Nothing was changed.`);
    }
    swapIn(fresh, target);
  } finally {
    fs.rmSync(fresh, { force: true });
  }
  log(`Updated ${target}: ${VERSION} -> ${latest} (the old one is ${target}.previous).`);

  // The new program refreshes what install wrote: host wrapper, manifests, Flatpak copies. Agent
  // entries already point at this path, so they are left as they are.
  log("Refreshing the browser side with the new version:");
  const refreshed = spawnSync(target, ["install", "--agents", "none"], { stdio: "inherit" });
  if (refreshed.status !== 0) {
    log(`The refresh failed. To go back: ${undo(target)}`);
    return 1;
  }
  log("Agents use the new version in their next session.");
  log(`Undo: ${undo(target)}`);
  return 0;
}
