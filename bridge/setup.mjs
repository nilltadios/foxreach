// Which foxreach this computer uses: what the browsers' host manifests and the agents' entries
// actually run. A user may run the release program (~/.local/bin/foxreach) or a checkout
// (`bun <folder>/bridge/mcp.mjs`, as scripts/build_install.py sets up); the manager shows and acts
// through whichever is in use rather than assuming the release program. Reads only.
import fs from "node:fs";
import path from "node:path";

/** Words of one shell command line, with double- and single-quoted parts kept whole. */
export function words(line) {
  const out = [];
  let cur = "";
  let quote = null;
  let any = false;
  for (const ch of String(line).trim()) {
    if (quote) {
      if (ch === quote) quote = null;
      else cur += ch;
    } else if (ch === '"' || ch === "'") {
      quote = ch;
      any = true;
    } else if (/\s/.test(ch)) {
      if (cur || any) out.push(cur);
      cur = "";
      any = false;
    } else {
      cur += ch;
    }
  }
  if (cur || any) out.push(cur);
  return out;
}

/** What a native-messaging manifest makes the browser run, as words: the exec line of the wrapper
 * script it names, or the program itself. Null when the manifest or its file cannot be read. */
export function manifestRuns(manifest) {
  let target;
  try {
    target = JSON.parse(fs.readFileSync(manifest, "utf8")).path;
  } catch {
    return null;
  }
  if (typeof target !== "string" || !target) return null;
  let head;
  try {
    const fd = fs.openSync(target, "r");
    try {
      const buf = Buffer.alloc(8192);
      head = buf.subarray(0, fs.readSync(fd, buf, 0, buf.length, 0)).toString("utf8");
    } finally {
      fs.closeSync(fd);
    }
  } catch {
    return null;
  }
  if (!head.startsWith("#!")) return [target];
  const line = head.split("\n").map((l) => l.trim()).find((l) => l.startsWith("exec "));
  return line ? words(line.slice(5)) : null;
}

/**
 * What a command is: a checkout ({kind: "checkout", root, runtime}), the compiled program
 * ({kind: "release", program}) or something else. `command` is how to run that foxreach's CLI.
 */
export function classify(argv) {
  if (!argv?.length) return null;
  const script = argv.find((w) => /\/bridge\/(cli|mcp|host)\.mjs$/.test(w));
  if (script) {
    const root = path.dirname(path.dirname(script));
    const runtime = argv[0] !== script ? argv[0] : null;
    return { kind: "checkout", root, runtime, command: [...(runtime ? [runtime] : []), path.join(root, "bridge/cli.mjs")] };
  }
  if (/^foxreach/.test(path.basename(argv[0]))) return { kind: "release", program: argv[0], command: [argv[0]] };
  return { kind: "other", command: argv };
}

const key = (s) => (s.kind === "checkout" ? s.root : s.kind === "release" ? s.program : s.command.join(" "));

/**
 * The foxreach in use: what the browsers' host runs, else what the agents run. `from` says which;
 * `mixed` is true when some agent or manifest runs a different one.
 */
export function setupInUse({ manifests = [], agents = [] }) {
  const host = manifests.map(manifestRuns).map(classify).filter(Boolean);
  const fromAgents = agents.filter((a) => a.set && a.command).map((a) => classify(words(a.command))).filter(Boolean);
  const pick = host[0] ?? fromAgents[0] ?? null;
  if (!pick) return { kind: "none", command: null, from: null, mixed: false };
  return { ...pick, from: host.length ? "browser" : "agents", mixed: [...host, ...fromAgents].some((s) => key(s) !== key(pick)) };
}
