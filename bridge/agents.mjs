// Tells the agents on this machine about the foxreach MCP server, by writing the one entry each
// agent's own settings file needs. No agent's program has to be installed for this: each one
// reads a plain settings file. Used by `foxreach install`, `foxreach uninstall` and `foxreach agents`.
//
// Rules: only the "foxreach" entry is ever added, changed or removed; every file is backed up the
// first time it is changed on a day (<file>.bak-<date>); a file that cannot be edited without
// losing something (JSON with comments, unusual TOML or YAML) is left alone and the entry to add
// by hand is printed instead.
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const NAME = "foxreach";

const exists = (p) => fs.existsSync(p);
const env = (k) => process.env[k] || "";

function onPath(cmd) {
  for (const dir of env("PATH").split(":").filter(Boolean)) {
    const file = path.join(dir, cmd);
    try {
      fs.accessSync(file, fs.constants.X_OK);
      if (fs.statSync(file).isFile()) return file;
    } catch {
      // not here
    }
  }
  return null;
}

/** Agent programs may be run (claude mcp add) unless FOXREACH_AGENT_CLI=0, as in the tests. */
const cliAllowed = () => env("FOXREACH_AGENT_CLI") !== "0";

function backup(file, log) {
  if (!exists(file)) return;
  const copy = `${file}.bak-${new Date().toISOString().slice(0, 10)}`;
  if (exists(copy)) return; // the day's first state is the one worth keeping
  fs.copyFileSync(file, copy);
  log(`  backup: ${copy}`);
}

/** Write through a temporary file and a rename, keeping the old file's mode. */
function writeFile(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const mode = exists(file) ? fs.statSync(file).mode & 0o777 : 0o600;
  const tmp = `${file}.foxreach-${process.pid}`;
  fs.writeFileSync(tmp, text, { mode });
  fs.chmodSync(tmp, mode);
  fs.renameSync(tmp, file);
}

class Refused extends Error {}

/* ---------- formats ---------- */

function readJson(file) {
  if (!exists(file)) return {};
  const text = fs.readFileSync(file, "utf8");
  if (!text.trim()) return {};
  try {
    const value = JSON.parse(text);
    if (value === null || typeof value !== "object" || Array.isArray(value)) throw new Refused("not a JSON object");
    return value;
  } catch (e) {
    if (e instanceof Refused) throw e;
    throw new Refused("not plain JSON (comments or trailing commas?), so it is left as it is");
  }
}

const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** A JSON settings file with servers under `key` (an object path such as ["mcpServers"]). */
function jsonFormat(keyPath, entryOf) {
  const parent = (obj, create) => {
    let o = obj;
    for (const k of keyPath) {
      if (o[k] === undefined) {
        if (!create) return null;
        o[k] = {};
      }
      if (typeof o[k] !== "object" || o[k] === null || Array.isArray(o[k])) throw new Refused(`"${keyPath.join(".")}" is not an object`);
      o = o[k];
    }
    return o;
  };
  return {
    current(file) {
      return parent(readJson(file), false)?.[NAME] ?? null;
    },
    set(file, command) {
      const obj = readJson(file);
      const servers = parent(obj, true);
      const entry = entryOf(command);
      if (servers[NAME] && sameJson(servers[NAME], entry)) return "already set";
      const verb = servers[NAME] ? "updated" : "added";
      servers[NAME] = entry;
      return { verb, text: `${JSON.stringify(obj, null, 2)}\n` };
    },
    remove(file) {
      const obj = readJson(file);
      const servers = parent(obj, false);
      if (!servers || !(NAME in servers)) return null;
      delete servers[NAME];
      return `${JSON.stringify(obj, null, 2)}\n`;
    },
    snippet(command) {
      let o = { [NAME]: entryOf(command) };
      for (const k of [...keyPath].reverse()) o = { [k]: o };
      return JSON.stringify(o, null, 2);
    },
  };
}

const commandArgs = (command) => ({ command: command[0], args: command.slice(1) });

// opencode 2.x keeps servers under mcp.servers; 1.x directly under mcp (with "enabled").
const opencodeFormat = {
  layout(obj) {
    const mcp = obj.mcp;
    if (mcp && typeof mcp === "object" && mcp.servers && typeof mcp.servers === "object") return "v2";
    if (mcp && typeof mcp === "object" && Object.values(mcp).some((v) => v && typeof v === "object" && "type" in v)) return "v1";
    return "v2";
  },
  formatFor(obj) {
    return this.layout(obj) === "v1"
      ? jsonFormat(["mcp"], (c) => ({ type: "local", command: c, enabled: true }))
      : jsonFormat(["mcp", "servers"], (c) => ({ type: "local", command: c }));
  },
  current(file) {
    return this.formatFor(readJson(file)).current(file);
  },
  set(file, command) {
    return this.formatFor(readJson(file)).set(file, command);
  },
  remove(file) {
    return this.formatFor(readJson(file)).remove(file);
  },
  snippet(command) {
    return jsonFormat(["mcp", "servers"], (c) => ({ type: "local", command: c })).snippet(command);
  },
};

// Codex: a [mcp_servers.foxreach] table. TOML is edited as text: the foxreach table and its
// sub-tables are replaced as a whole, everything else is kept byte for byte.
const TOML_HEADER = /^\s*\[\s*mcp_servers\.("?)foxreach\1(\.[^\]]*)?\s*\]\s*(#.*)?$/;
const tomlFormat = {
  split(text) {
    const keep = [];
    const ours = [];
    let inOurs = false;
    for (const line of text.split("\n")) {
      if (/^\s*\[/.test(line)) inOurs = TOML_HEADER.test(line);
      (inOurs ? ours : keep).push(line);
    }
    // foxreach written another way (an inline table) is not something to edit blindly.
    if (keep.some((l) => /^\s*(mcp_servers\.)?"?foxreach"?\s*=/.test(l))) {
      throw new Refused("foxreach is set as an inline table; edit it by hand");
    }
    return { keep: keep.join("\n"), ours: ours.join("\n") };
  },
  block(command) {
    // JSON strings and arrays of strings are valid TOML basic strings and arrays.
    return `[mcp_servers.${NAME}]\ncommand = ${JSON.stringify(command[0])}\nargs = ${JSON.stringify(command.slice(1))}\n`;
  },
  current(file) {
    if (!exists(file)) return null;
    return this.split(fs.readFileSync(file, "utf8")).ours.trim() || null;
  },
  set(file, command) {
    const text = exists(file) ? fs.readFileSync(file, "utf8") : "";
    const { keep, ours } = this.split(text);
    const block = this.block(command);
    if (ours.trim() === block.trim()) return "already set";
    const base = keep.replace(/\s*$/, "");
    return { verb: ours.trim() ? "updated" : "added", text: `${base}${base ? "\n\n" : ""}${block}` };
  },
  remove(file) {
    if (!exists(file)) return null;
    const { keep, ours } = this.split(fs.readFileSync(file, "utf8"));
    if (!ours.trim()) return null;
    return `${keep.replace(/\s*$/, "")}\n`;
  },
  snippet(command) {
    return this.block(command).trim();
  },
};

// Hermes: mcp_servers: in a YAML file. Edited as text, for the common block layout only.
const yamlFormat = {
  lines: (file) => (exists(file) ? fs.readFileSync(file, "utf8").split("\n") : []),
  indentOf: (line) => line.match(/^ */)[0].length,
  // [start, end) of the mcp_servers block, or null; refuses layouts it does not handle.
  block(lines) {
    const i = lines.findIndex((l) => /^mcp_servers\s*:/.test(l));
    if (i < 0) return null;
    const rest = lines[i].replace(/^mcp_servers\s*:\s*/, "").replace(/\s+#.*$/, "");
    if (rest !== "" && rest !== "{}") throw new Refused("mcp_servers is written inline; edit it by hand");
    let end = i + 1;
    while (end < lines.length && (lines[end].trim() === "" || lines[end].trim().startsWith("#") || /^\s/.test(lines[end]))) end++;
    while (end > i + 1 && lines[end - 1].trim() === "") end--;
    return { start: i, end, inline: rest === "{}" };
  },
  ours(lines, b) {
    for (let j = b.start + 1; j < b.end; j++) {
      if (/^\s+"?foxreach"?\s*:/.test(lines[j])) {
        const ind = this.indentOf(lines[j]);
        let k = j + 1;
        while (k < b.end && (lines[k].trim() === "" || this.indentOf(lines[k]) > ind)) k++;
        return { from: j, to: k, indent: ind };
      }
    }
    return null;
  },
  childIndent(lines, b) {
    for (let j = b.start + 1; j < b.end; j++) if (lines[j].trim() && !lines[j].trim().startsWith("#")) return this.indentOf(lines[j]);
    return 2;
  },
  entry(command, ind) {
    const p = " ".repeat(ind);
    return [`${p}${NAME}:`, `${p}  command: ${JSON.stringify(command[0])}`, `${p}  args: ${JSON.stringify(command.slice(1))}`];
  },
  current(file) {
    const lines = this.lines(file);
    const b = this.block(lines);
    const o = b && this.ours(lines, b);
    return o ? lines.slice(o.from, o.to).join("\n") : null;
  },
  set(file, command) {
    const lines = this.lines(file);
    const b = this.block(lines);
    if (!b) {
      const base = lines.join("\n").replace(/\s*$/, "");
      return { verb: "added", text: `${base}${base ? "\n\n" : ""}mcp_servers:\n${this.entry(command, 2).join("\n")}\n` };
    }
    const o = this.ours(lines, b);
    const ind = o ? o.indent : this.childIndent(lines, b);
    const entry = this.entry(command, ind);
    if (o && lines.slice(o.from, o.to).join("\n").trimEnd() === entry.join("\n")) return "already set";
    const out = [...lines];
    if (b.inline) out[b.start] = "mcp_servers:";
    if (o) out.splice(o.from, o.to - o.from, ...entry);
    else out.splice(b.start + 1, 0, ...entry);
    return { verb: o ? "updated" : "added", text: out.join("\n").replace(/\n*$/, "\n") };
  },
  remove(file) {
    const lines = this.lines(file);
    const b = this.block(lines);
    const o = b && this.ours(lines, b);
    if (!o) return null;
    const out = [...lines];
    out.splice(o.from, o.to - o.from);
    return out.join("\n").replace(/\n*$/, "\n");
  },
  snippet(command) {
    return ["mcp_servers:", ...this.entry(command, 2)].join("\n");
  },
};

// Mistral Vibe: [[mcp_servers]] tables in TOML, each with a name. Edited as text: the table
// named foxreach is replaced as a whole, everything else is kept byte for byte.
const vibeFormat = {
  split(text) {
    const lines = text.split("\n");
    const keep = [];
    const ours = [];
    let block = null;
    const flush = () => {
      if (!block) return;
      const mine = block.some((l) => /^\s*name\s*=\s*["']foxreach["']\s*(#.*)?$/.test(l));
      (mine ? ours : keep).push(...block);
      block = null;
    };
    for (const line of lines) {
      if (/^\s*\[/.test(line)) {
        flush();
        if (/^\s*\[\[\s*mcp_servers\s*\]\]/.test(line)) {
          block = [line];
          continue;
        }
      }
      if (block) block.push(line);
      else keep.push(line);
    }
    flush();
    return { keep: keep.join("\n"), ours: ours.join("\n") };
  },
  block(command) {
    return `[[mcp_servers]]\nname = "${NAME}"\ntransport = "stdio"\ncommand = ${JSON.stringify(command[0])}\nargs = ${JSON.stringify(command.slice(1))}\n`;
  },
  current(file) {
    if (!exists(file)) return null;
    return this.split(fs.readFileSync(file, "utf8")).ours.trim() || null;
  },
  set(file, command) {
    const { keep, ours } = this.split(exists(file) ? fs.readFileSync(file, "utf8") : "");
    const block = this.block(command);
    if (ours.trim() === block.trim()) return "already set";
    const base = keep.replace(/\s*$/, "");
    return { verb: ours.trim() ? "updated" : "added", text: `${base}${base ? "\n\n" : ""}${block}` };
  },
  remove(file) {
    if (!exists(file)) return null;
    const { keep, ours } = this.split(fs.readFileSync(file, "utf8"));
    return ours.trim() ? `${keep.replace(/\s*$/, "")}\n` : null;
  },
  snippet(command) {
    return this.block(command).trim();
  },
};

// DeepSeek Harness (dsh): a profile's cordis.patch.yml, a YAML list of edits to its plugin set.
// foxreach adds one "insert" of the MCP client plugin (packages/mcp/mcp-client in
// deepseek-ai/deepseek-harness), between marker comments so it can be found and removed again.
const DSH_START = "# >>> foxreach (added by foxreach install)";
const DSH_END = "# <<< foxreach";
const dshFormat = {
  text: (file) => (exists(file) ? fs.readFileSync(file, "utf8") : ""),
  find(text) {
    const a = text.indexOf(DSH_START);
    const b = text.indexOf(DSH_END);
    return a >= 0 && b > a ? { a, b: b + DSH_END.length } : null;
  },
  block(command) {
    return [DSH_START, "- insert:", "    - id: mcp-foxreach", "      name: '@deepseek-ai/dsh-mcp-client'", "      config:",
      `        serverName: ${NAME}`, "        transport: stdio", `        command: ${JSON.stringify(command[0])}`,
      `        args: ${JSON.stringify(command.slice(1))}`, DSH_END].join("\n");
  },
  current(file) {
    const text = this.text(file);
    const at = this.find(text);
    return at ? text.slice(at.a, at.b) : null;
  },
  set(file, command) {
    const text = this.text(file);
    const block = this.block(command);
    const at = this.find(text);
    if (at && text.slice(at.a, at.b) === block) return "already set";
    const rest = at ? text.slice(0, at.a) + text.slice(at.b) : text;
    // Only a plain top-level list is safe to append to.
    const first = rest.split("\n").find((l) => l.trim() && !l.trim().startsWith("#"));
    if (first && !first.startsWith("- ")) throw new Refused("it is not a plain YAML list of patches");
    const base = rest.replace(/\s*$/, "");
    return { verb: at ? "updated" : "added", text: `${base}${base ? "\n\n" : ""}${block}\n` };
  },
  remove(file) {
    const text = this.text(file);
    const at = this.find(text);
    if (!at) return null;
    return `${(text.slice(0, at.a) + text.slice(at.b)).replace(/\n{3,}/g, "\n\n").replace(/\s*$/, "")}\n`;
  },
  snippet(command) {
    return this.block(command);
  },
};

/* ---------- the agents ---------- */

const home = () => os.homedir();
const xdgConfig = (h) => env("XDG_CONFIG_HOME") || path.join(h, ".config");

/**
 * Each agent: where its settings are, how to tell it is installed, and notes on who else reads the
 * same file. `detect` looks for the agent's own folder or program, not for foxreach.
 */
export function agents(h = home()) {
  const claudeDesktop = process.platform === "darwin"
    ? path.join(h, "Library/Application Support/Claude")
    : path.join(xdgConfig(h), "Claude");
  const codexHome = env("CODEX_HOME") || path.join(h, ".codex");
  const opencodeDir = path.join(xdgConfig(h), "opencode");
  const piDir = env("PI_CODING_AGENT_DIR") || path.join(h, ".pi/agent");
  const hermesDir = env("HERMES_HOME") || path.join(h, ".hermes");
  // Kimi Code keeps its data in ~/.kimi-code (KIMI_CODE_HOME); older releases used ~/.kimi.
  const kimiHome = env("KIMI_CODE_HOME") ||
    (!exists(path.join(h, ".kimi-code")) && exists(path.join(h, ".kimi")) ? path.join(h, ".kimi") : path.join(h, ".kimi-code"));
  const mimoDir = env("MIMOCODE_HOME") ? path.join(env("MIMOCODE_HOME"), "config") : path.join(xdgConfig(h), "mimocode");
  const vibeHome = env("VIBE_HOME") || path.join(h, ".vibe");
  // OpenClaw: OPENCLAW_CONFIG_PATH, else OPENCLAW_STATE_DIR, else ~/.openclaw (~/.openclaw-<profile>
  // with OPENCLAW_PROFILE), under OPENCLAW_HOME if set. https://docs.openclaw.ai/help/environment
  const tilde = (p) => (p.startsWith("~/") ? path.join(h, p.slice(2)) : p);
  const clawHome = env("OPENCLAW_HOME") ? tilde(env("OPENCLAW_HOME")) : h;
  const clawState = env("OPENCLAW_STATE_DIR") ? tilde(env("OPENCLAW_STATE_DIR"))
    : path.join(clawHome, env("OPENCLAW_PROFILE") ? `.openclaw-${env("OPENCLAW_PROFILE")}` : ".openclaw");
  const clawConfig = env("OPENCLAW_CONFIG_PATH") ? tilde(env("OPENCLAW_CONFIG_PATH")) : path.join(clawState, "openclaw.json");
  return [
    {
      id: "claude-code", name: "Claude Code", file: path.join(h, ".claude.json"),
      format: jsonFormat(["mcpServers"], commandArgs),
      detect: () => exists(path.join(h, ".claude.json")) || exists(path.join(h, ".claude")) || !!onPath("claude"),
      also: "also the Code tab of Claude desktop, and T3 Code",
      // Running sessions rewrite ~/.claude.json, so its own command is used when it is installed.
      cli: (command) => onPath("claude") && cliAllowed()
        ? { add: ["claude", "mcp", "add", "-s", "user", NAME, "--", ...command], remove: ["claude", "mcp", "remove", "-s", "user", NAME] }
        : null,
    },
    {
      id: "claude-desktop", name: "Claude desktop (chat)", file: path.join(claudeDesktop, "claude_desktop_config.json"),
      format: jsonFormat(["mcpServers"], commandArgs),
      detect: () => exists(claudeDesktop), restart: true,
    },
    {
      id: "codex", name: "Codex", file: path.join(codexHome, "config.toml"),
      format: tomlFormat,
      detect: () => exists(codexHome) || !!onPath("codex"),
      also: "also the ChatGPT app and T3 Code",
    },
    {
      id: "antigravity", name: "Antigravity (app and agy)", file: path.join(h, ".gemini/config/mcp_config.json"),
      format: jsonFormat(["mcpServers"], commandArgs),
      detect: () => exists(path.join(h, ".gemini/config")) || exists(path.join(h, ".gemini/antigravity")) ||
        exists(path.join(h, ".antigravity")) || !!onPath("agy"),
      restart: true,
    },
    {
      // Google retired Gemini CLI for individual accounts on 2026-06-18 in favour of Antigravity's
      // agy (above). Its settings.json lingers in ~/.gemini, which Antigravity shares, so only the
      // program itself counts as installed; the entry stays known so uninstall can remove it.
      id: "gemini", name: "Gemini CLI (retired by Google in 2026)", file: path.join(h, ".gemini/settings.json"),
      format: jsonFormat(["mcpServers"], commandArgs),
      detect: () => !!onPath("gemini"),
    },
    {
      id: "opencode", name: "opencode (CLI and app)",
      file: exists(path.join(opencodeDir, "opencode.jsonc")) ? path.join(opencodeDir, "opencode.jsonc") : path.join(opencodeDir, "opencode.json"),
      format: opencodeFormat,
      detect: () => exists(opencodeDir) || exists(path.join(h, ".local/share/opencode")) || !!onPath("opencode"),
    },
    {
      id: "pi", name: "Pi", file: path.join(piDir, "mcp.json"),
      format: jsonFormat(["mcpServers"], commandArgs),
      detect: () => exists(path.dirname(piDir)) || exists(piDir),
    },
    {
      id: "hermes", name: "Hermes", file: path.join(hermesDir, "config.yaml"),
      format: yamlFormat,
      detect: () => exists(hermesDir) || !!onPath("hermes"),
      note: "run /reload-mcp in a running chat",
    },
    {
      id: "cursor", name: "Cursor", file: path.join(h, ".cursor/mcp.json"),
      format: jsonFormat(["mcpServers"], commandArgs),
      detect: () => exists(path.join(h, ".cursor")),
    },
    {
      // Read from ZCode 1.x's bundled CLI (resources/glm/zcode.cjs): user servers live under
      // mcp.servers, a strict schema, so the entry carries only type, command and args.
      id: "zcode", name: "ZCode", file: path.join(h, ".zcode/cli/config.json"),
      format: jsonFormat(["mcp", "servers"], (command) => ({ type: "stdio", ...commandArgs(command) })),
      detect: () => exists(path.join(h, ".zcode")) || !!onPath("zcode"),
      note: "start a new session",
    },
    {
      // https://docs.openclaw.ai/cli/mcp/transports.md: mcp.servers.<name> with command and args.
      // The file is JSON5; one with comments or trailing commas is left alone and the entry printed.
      id: "openclaw", name: "OpenClaw", file: clawConfig,
      format: jsonFormat(["mcp", "servers"], commandArgs),
      detect: () => exists(clawState) || exists(clawConfig) || !!onPath("openclaw"),
      note: "it picks the change up by itself",
    },
    {
      // https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/add-mcp-servers
      id: "copilot", name: "GitHub Copilot CLI", file: path.join(h, ".copilot/mcp-config.json"),
      format: jsonFormat(["mcpServers"], (c) => ({ type: "local", ...commandArgs(c), tools: ["*"] })),
      detect: () => exists(path.join(h, ".copilot")) || !!onPath("copilot"),
      note: "start a new session",
    },
    {
      // https://qwenlm.github.io/qwen-code-docs/en/users/features/mcp/ (user scope, the default)
      id: "qwen", name: "Qwen Code", file: path.join(h, ".qwen/settings.json"),
      format: jsonFormat(["mcpServers"], commandArgs),
      detect: () => exists(path.join(h, ".qwen")) || !!onPath("qwen"),
    },
    {
      // https://www.kimi.com/code/docs/en/kimi-code-cli/customization/mcp.html
      id: "kimi", name: "Kimi Code", file: path.join(kimiHome, "mcp.json"),
      format: jsonFormat(["mcpServers"], commandArgs),
      detect: () => exists(kimiHome) || !!onPath("kimi"),
    },
    {
      // https://mimo.xiaomi.com/mimocode/mcp-servers and /config-overrides: mcp.<name>, type "local",
      // command as one array (opencode's 1.x layout). A mimocode.jsonc with comments is left alone.
      id: "mimo", name: "MiMo Code",
      file: ["mimocode.jsonc", "mimocode.json", "config.json"].map((f) => path.join(mimoDir, f)).find(exists) ?? path.join(mimoDir, "mimocode.json"),
      format: jsonFormat(["mcp"], (c) => ({ type: "local", command: c, enabled: true })),
      detect: () => exists(mimoDir) || !!onPath("mimo"),
    },
    {
      // https://code.minimax.io/docs/code/agents/mcp.md
      id: "minimax", name: "MiniMax Code", file: path.join(h, ".minimax/mcp.json"),
      format: jsonFormat(["mcpServers"], (c) => ({ type: "stdio", ...commandArgs(c), enabled: true })),
      detect: () => exists(path.join(h, ".minimax")) || !!onPath("mcode"),
    },
    {
      // https://docs.mistral.ai/vibe/code/cli/mcp-servers
      id: "vibe", name: "Mistral Vibe", file: path.join(vibeHome, "config.toml"),
      format: vibeFormat,
      detect: () => exists(vibeHome) || !!onPath("vibe"),
    },
    {
      // The MCP client plugin's design note in deepseek-ai/deepseek-harness gives the entry; the
      // profile file (~/.dsh/profiles/web, the profile `dsh web` runs) is from Composio's guide.
      id: "deepseek", name: "DeepSeek Harness (dsh)", file: path.join(h, ".dsh/profiles/web/cordis.patch.yml"),
      format: dshFormat,
      detect: () => exists(path.join(h, ".dsh")) || !!onPath("dsh"),
      note: "restart dsh",
    },
  ];
}

export const AGENT_IDS = agents("/").map((a) => a.id);

function run(argv) {
  const r = spawnSync(argv[0], argv.slice(1), { encoding: "utf8" });
  return { ok: !r.error && r.status === 0, out: `${r.stdout ?? ""}${r.stderr ?? ""}`.trim() };
}

/** Which agents to act on: the ones named, or every one that is installed. */
function chosen(list, only) {
  if (only === null || only === undefined) return list.filter((a) => a.detect());
  const unknown = only.filter((id) => !list.some((a) => a.id === id));
  if (unknown.length) throw new Error(`Unknown agent: ${unknown.join(", ")}. Known: ${list.map((a) => a.id).join(", ")}`);
  return list.filter((a) => only.includes(a.id));
}

/** Add the MCP server to agents. Returns the results, one per agent. */
export function registerAgents({ command, only = null, h = home(), log = console.log } = {}) {
  const results = [];
  const list = chosen(agents(h), only);
  if (!list.length) log("  no agent found; add the MCP server to yours by hand (command above)");
  for (const a of list) {
    const label = `${a.name}${a.also ? ` (${a.also})` : ""}`;
    try {
      const cli = a.cli?.(command);
      if (cli) {
        const now = a.format.current(a.file);
        // Claude Code stores more than command and args (type, env): compare what matters.
        if (now && now.command === command[0] && sameJson(now.args ?? [], command.slice(1))) {
          log(`  ${label}: already set`);
          results.push({ id: a.id, status: "already set" });
          continue;
        }
        if (now) run(cli.remove);
        const r = run(cli.add);
        if (!r.ok) throw new Error(`${cli.add.slice(0, 3).join(" ")} failed: ${r.out}`);
        log(`  ${label}: ${now ? "updated" : "added"} (${cli.add.slice(0, 5).join(" ")})`);
        results.push({ id: a.id, status: now ? "updated" : "added" });
        continue;
      }
      const change = a.format.set(a.file, command);
      if (change === "already set") {
        log(`  ${label}: already set in ${a.file}`);
        results.push({ id: a.id, status: "already set" });
        continue;
      }
      backup(a.file, log);
      writeFile(a.file, change.text);
      const extra = [a.restart && "restart it to load", a.note].filter(Boolean).join("; ");
      log(`  ${label}: ${change.verb} in ${a.file}${extra ? ` (${extra})` : ""}`);
      results.push({ id: a.id, status: change.verb });
    } catch (e) {
      const why = e instanceof Refused ? `${a.file} is ${e.message}` : e.message;
      log(`  ${label}: not changed: ${why}. Add this to ${a.file} by hand:\n${indent(a.format.snippet(command), 6)}`);
      results.push({ id: a.id, status: "refused", error: why });
    }
  }
  return results;
}

/** Remove the MCP server's entry from every agent that has one. */
export function unregisterAgents({ h = home(), log = console.log } = {}) {
  for (const a of agents(h)) {
    try {
      const cli = a.cli?.([]);
      if (cli && a.format.current(a.file)) {
        const r = run(cli.remove);
        log(r.ok ? `removed foxreach from ${a.name} (${cli.remove.slice(0, 5).join(" ")})` : `${a.name}: ${r.out}`);
        continue;
      }
      const text = a.format.remove(a.file);
      if (text === null) continue;
      backup(a.file, log);
      writeFile(a.file, text);
      log(`removed foxreach from ${a.name} (${a.file})`);
    } catch (e) {
      log(`${a.name}: ${a.file} not changed (${e.message}); remove the foxreach entry by hand`);
    }
  }
}

/** One line per known agent: installed or not, and whether foxreach is set in it. */
export function describeAgents({ h = home(), log = console.log } = {}) {
  for (const a of agents(h)) {
    let set;
    try {
      set = a.format.current(a.file) ? "foxreach set" : "foxreach not set";
    } catch (e) {
      set = `cannot read (${e.message})`;
    }
    log(`${a.id.padEnd(15)} ${(a.detect() ? "found" : "not found").padEnd(10)} ${set.padEnd(17)} ${a.file}`);
  }
}

const indent = (text, n) => text.split("\n").map((l) => " ".repeat(n) + l).join("\n");
