// Explains a failed connection to the native host: is any Firefox-based browser running at all?
// The add-on works in Firefox and its derivatives, so this looks for all of them, not one.
import fs from "node:fs";
import path from "node:path";

// Executable names of Firefox-based browsers, with the name to show. AppImage, Flatpak and distro
// builds all end in one of these.
const GECKO = new Map([
  ["firefox", "Firefox"], ["firefox-bin", "Firefox"], ["firefox-esr", "Firefox ESR"],
  ["librewolf", "LibreWolf"], ["librewolf-bin", "LibreWolf"],
  ["floorp", "Floorp"], ["waterfox", "Waterfox"], ["zen", "Zen"], ["zen-bin", "Zen"],
  ["mullvadbrowser", "Mullvad Browser"], ["palemoon", "Pale Moon"], ["icecat", "IceCat"],
]);

/** Names of the Firefox-based browsers running now, or null where processes cannot be listed. */
export function runningBrowsers(procRoot = process.env.FOXREACH_PROC || "/proc") {
  let entries;
  try {
    entries = fs.readdirSync(procRoot);
  } catch {
    return null; // no /proc (macOS, BSD): say nothing about what runs
  }
  const found = new Set();
  for (const pid of entries) {
    if (!/^\d+$/.test(pid)) continue;
    try {
      const name = GECKO.get(path.basename(fs.readlinkSync(path.join(procRoot, pid, "exe"))));
      if (name) found.add(name);
    } catch {
      // process gone, or not ours to read
    }
  }
  return [...found].sort();
}

/** The error to show when the bridge cannot reach the browser's native host. */
export function notConnectedMessage(code, socket, browsers = runningBrowsers()) {
  const detail = `(${code} on ${socket})`;
  if (browsers && browsers.length === 0) {
    return "No Firefox-based browser is running, so foxreach cannot reach it. Open the browser (Firefox, " +
      `LibreWolf or another) with the profile that has the foxreach add-on, then try again. ${detail}`;
  }
  if (browsers) {
    const which = browsers.join(" and ");
    return `${which} ${browsers.length > 1 ? "are" : "is"} running, but the foxreach add-on has not connected. ` +
      "Check that this is the profile foxreach is installed in, that the add-on is enabled in about:addons " +
      "and not paused from its toolbar button. If the browser has only just started, wait a few seconds. " +
      detail;
  }
  return "The browser is not connected. Open the Firefox-based browser with the profile that has the foxreach " +
    `add-on, then try again. ${detail}`;
}
