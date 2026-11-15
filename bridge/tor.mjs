// Tor Browser. foxreach works in it (checked with Tor Browser 15, all three builds), but two things
// differ from other Firefox-based browsers, both found by testing:
// - it reads native-messaging manifests from its own data folder,
//   <Browser>/TorBrowser/Data/Browser/.mozilla/native-messaging-hosts, whatever HOME is;
// - it is always in private browsing, so the add-on runs only once allowed in private windows
//   (the box in the browser's install prompt).
// It is offered, never set up by default, and marked not recommended.
import fs from "node:fs";
import path from "node:path";

export const TOR_WARNING =
  "Not recommended: an add-on that agents drive makes this Tor Browser look and behave unlike other " +
  "Tor Browsers, which can undo the anonymity Tor gives. The Tor Project advises against adding add-ons.";
export const TOR_PRIVATE_NOTE = "When Tor Browser asks, allow foxreach in private windows: Tor Browser has no other kind.";

const isDir = (p) => {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
};
const children = (dir) => {
  try {
    return fs.readdirSync(dir).map((n) => path.join(dir, n));
  } catch {
    return [];
  }
};

/** A Tor Browser install's Browser folder (holding start-tor-browser), else null. */
function browserDir(candidate) {
  const dir = path.join(candidate, "Browser");
  return fs.existsSync(path.join(dir, "start-tor-browser")) && isDir(path.join(dir, "TorBrowser")) ? dir : null;
}

/**
 * Tor Browser installs of this user, as their Browser folders: torbrowser-launcher's
 * (~/.local/share/torbrowser/tbb/<arch>/tor-browser) and unpacked downloads named tor-browser* in
 * the home folder, Downloads, Applications or /opt.
 */
export function torInstalls(h) {
  const candidates = [
    ...children(path.join(h, ".local/share/torbrowser/tbb")).map((arch) => path.join(arch, "tor-browser")),
    ...[h, path.join(h, "Downloads"), path.join(h, "Applications"), "/opt"]
      .flatMap((d) => children(d).filter((p) => path.basename(p).toLowerCase().startsWith("tor-browser"))),
  ];
  const found = new Set();
  for (const c of candidates) {
    const dir = browserDir(c);
    if (!dir) continue;
    try {
      found.add(fs.realpathSync(dir));
    } catch {
      // gone meanwhile
    }
  }
  return [...found].sort();
}

/** Where its profiles live (profiles.ini) and where it reads native-messaging manifests. */
export const torData = (dir) => path.join(dir, "TorBrowser/Data/Browser");
export const torHostDir = (dir) => path.join(torData(dir), ".mozilla/native-messaging-hosts");

/** True when a program path belongs to a Tor Browser install. */
export const isTorProgram = (exe) => /\/tor-browser[^/]*\/Browser\//.test(exe) || exe.includes("/TorBrowser/");
