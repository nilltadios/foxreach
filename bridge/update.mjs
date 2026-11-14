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

const RELEASES = process.env.FOXREACH_RELEASE_URL || "https://github.com/nilltadios/foxreach/releases/latest/download";
const ASSETS = { x64: "foxreach-linux-x64", arm64: "foxreach-linux-arm64" };

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

async function download(name) {
  const url = `${RELEASES}/${name}`;
  // A local release folder (tests, an offline mirror): Node's fetch does not read file: URLs.
  if (url.startsWith("file:")) {
    try {
      return fs.readFileSync(new URL(url));
    } catch (e) {
      throw new Error(`Cannot read ${url}: ${e.message ?? e}`);
    }
  }
  let r;
  try {
    r = await fetch(url, { redirect: "follow" });
  } catch (e) {
    throw new Error(`Cannot reach ${url}: ${e.message ?? e}`);
  }
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return Buffer.from(await r.arrayBuffer());
}

/** The release file for this machine and SHA256SUMS, checked against each other. */
async function verifiedAsset(asset) {
  const [file, sums] = await Promise.all([download(asset), download("SHA256SUMS")]);
  const want = sums.toString("utf8").split("\n").map((l) => l.trim().split(/\s+/)).find((p) => p[1] === asset)?.[0];
  const got = crypto.createHash("sha256").update(file).digest("hex");
  if (!want || want !== got) {
    throw new Error(`Checksum mismatch for ${asset}: expected ${want ?? "nothing listed"}, got ${got}. Nothing was changed.`);
  }
  return file;
}

/** Where `foxreach install-program` puts the program, as install.sh does. */
export function programPath() {
  return path.join(process.env.FOXREACH_BIN_DIR || path.join(os.homedir(), ".local/bin"), "foxreach");
}

/**
 * `foxreach install-program`: put the latest release at `to` (default ~/.local/bin/foxreach), as
 * install.sh does: same file, same checksum check, the old file kept as <to>.previous. Works from a
 * checkout too, so the manager can install the program without a bundled copy. Registers nothing:
 * the new program's own `install` does that.
 */
export async function installProgram({ to = programPath(), log = console.log } = {}) {
  if (process.platform !== "linux") throw new Error(`No foxreach build for ${process.platform}.`);
  const asset = ASSETS[process.arch];
  if (!asset) throw new Error(`No foxreach build for ${process.arch}.`);
  const latest = await latestVersion();
  log(`Downloading foxreach ${latest} (${asset}) from ${RELEASES}`);
  const file = await verifiedAsset(asset);
  const dir = path.dirname(to);
  fs.mkdirSync(dir, { recursive: true });
  const fresh = path.join(dir, `.foxreach-install-${process.pid}`);
  try {
    fs.writeFileSync(fresh, file, { mode: 0o755 });
    fs.chmodSync(fresh, 0o755);
    const says = spawnSync(fresh, ["version"], { encoding: "utf8", timeout: 30_000 });
    if (says.status !== 0 || (says.stdout ?? "").trim() !== latest) {
      throw new Error(`The downloaded program does not run as foxreach ${latest} (it said: ${(says.stdout ?? "").trim() || says.stderr || says.error}). Nothing was changed.`);
    }
    if (fs.existsSync(to)) {
      fs.copyFileSync(to, `${to}.previous`);
      log(`kept the old program as ${to}.previous`);
    }
    fs.renameSync(fresh, to);
  } finally {
    fs.rmSync(fresh, { force: true });
  }
  log(`installed ${to} (foxreach ${latest})`);
  return { program: to, version: latest };
}

async function latestVersion() {
  const info = (await download("BUILDINFO.txt")).toString("utf8");
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

  const asset = ASSETS[process.arch];
  if (!asset) throw new Error(`No foxreach build for ${process.arch}.`);
  const target = fs.realpathSync(process.execPath);
  const dir = path.dirname(target);
  try {
    fs.accessSync(dir, fs.constants.W_OK);
  } catch {
    throw new Error(`${dir} is not writable, so ${target} cannot be replaced.`);
  }

  log(`Downloading foxreach ${latest} (${asset}) from ${RELEASES}`);
  const file = await verifiedAsset(asset);

  // Next to the program, so the final rename replaces it in one step (same filesystem).
  const fresh = path.join(dir, `.foxreach-update-${process.pid}`);
  try {
    fs.writeFileSync(fresh, file, { mode: 0o755 });
    fs.chmodSync(fresh, 0o755);
    const says = spawnSync(fresh, ["version"], { encoding: "utf8", timeout: 30_000 });
    const reported = (says.stdout ?? "").trim();
    if (says.status !== 0 || reported !== latest) {
      throw new Error(`The downloaded program does not run as foxreach ${latest} (it said: ${reported || says.stderr || says.error}). Nothing was changed.`);
    }
    fs.copyFileSync(target, `${target}.previous`);
    fs.chmodSync(`${target}.previous`, 0o755);
    fs.renameSync(fresh, target); // a running agent keeps the old file open; new starts get this one
  } finally {
    fs.rmSync(fresh, { force: true });
  }
  log(`Updated ${target}: ${VERSION} -> ${latest} (the old one is ${target}.previous).`);

  // The new program refreshes what install wrote: host wrapper, manifests, Flatpak copies. Agent
  // entries already point at this path, so they are left as they are.
  log("Refreshing the browser side with the new version:");
  const refreshed = spawnSync(target, ["install", "--agents", "none"], { stdio: "inherit" });
  if (refreshed.status !== 0) {
    log(`The refresh failed. To go back: mv ${target}.previous ${target} && ${target} install --agents none`);
    return 1;
  }
  log("Agents use the new version in their next session.");
  log(`Undo: mv ${target}.previous ${target} && ${target} install --agents none`);
  return 0;
}
