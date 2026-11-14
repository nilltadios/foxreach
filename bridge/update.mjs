// `foxreach update`: replace this program with the latest release, the way install.sh installs it
// (same files, same checksum check), then refresh what `foxreach install` wrote (the host wrapper,
// the browsers' manifests, the copies inside Flatpak browsers). The add-on updates itself.
//
// Agents that are running keep the program they started; they use the new one in their next
// session. The old program is kept beside the new one as <program>.previous.
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { compiled } from "./install.mjs";
import { VERSION } from "./version.mjs";

const RELEASES = process.env.FOXREACH_RELEASE_URL || "https://github.com/nilltadios/foxreach/releases/latest/download";
const PLATFORMS = { linux: "linux", darwin: "darwin" };
const ARCHES = { x64: "x64", arm64: "arm64" };

/** The release file built for this machine, or null. */
export function assetName(platform = process.platform, arch = process.arch) {
  return PLATFORMS[platform] && ARCHES[arch] ? `foxreach-${PLATFORMS[platform]}-${ARCHES[arch]}` : null;
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

async function download(name) {
  const url = `${RELEASES}/${name}`;
  let r;
  try {
    r = await fetch(url, { redirect: "follow" });
  } catch (e) {
    throw new Error(`Cannot reach ${url}: ${e.message ?? e}`);
  }
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return Buffer.from(await r.arrayBuffer());
}

async function latestVersion() {
  const info = (await download("BUILDINFO.txt")).toString("utf8");
  const m = /^foxreach (\d+(?:\.\d+)*)\s*$/m.exec(info);
  if (!m) throw new Error("The latest release does not say its version (BUILDINFO.txt).");
  return m[1];
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
  const [file, sums] = await Promise.all([download(asset), download("SHA256SUMS")]);
  const want = sums.toString("utf8").split("\n").map((l) => l.trim().split(/\s+/)).find((p) => p[1] === asset)?.[0];
  const got = crypto.createHash("sha256").update(file).digest("hex");
  if (!want || want !== got) {
    throw new Error(`Checksum mismatch for ${asset}: expected ${want ?? "nothing listed"}, got ${got}. Nothing was changed.`);
  }

  // Next to the program, so the final rename replaces it in one step (same filesystem).
  const fresh = path.join(dir, `.foxreach-update-${process.pid}`);
  try {
    fs.writeFileSync(fresh, file, { mode: 0o755 });
    fs.chmodSync(fresh, 0o755);
    signForMac(fresh);
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
