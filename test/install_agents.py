#!/usr/bin/env python3
"""`foxreach install` adds the MCP server to each agent's settings, and `uninstall` takes it out.

Run:      uv run --python 3.15 test/install_agents.py
Expected: every line PASS, exit 0.
How:      the real `foxreach install`/`uninstall`/`agents` (bun bridge/cli.mjs), with HOME pointed at
          a throwaway folder that holds realistic settings for each agent: other servers and other
          settings that must survive untouched. Agent programs are not run (FOXREACH_AGENT_CLI=0,
          and a PATH without them), except one check with a stand-in `claude` that only records
          how it was called. No real settings file is read or written.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import tomllib

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUN = shutil.which("bun") or str(pathlib.Path.home() / ".bun/bin/bun")
CLI = str(ROOT / "bridge/cli.mjs")
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


def run(home, *args, path="/usr/bin:/bin", extra_env=None):
    env = {"HOME": str(home), "PATH": path, "FOXREACH_AGENT_CLI": "0", "XDG_RUNTIME_DIR": str(home / "run")}
    env.update(extra_env or {})
    r = subprocess.run([BUN, CLI, *args], env=env, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


def seed(home):
    """Settings as agents leave them, each with something that must be kept."""
    files = {
        ".claude.json": json.dumps({"numStartups": 12, "mcpServers": {"other": {"command": "x"}}, "projects": {}}, indent=2),
        ".config/Claude/claude_desktop_config.json": json.dumps({"preferences": {"theme": "dark"}}, indent=2),
        ".codex/config.toml": '\n'.join([
            'model = "gpt-5"',
            "",
            "[mcp_servers.node_repl]",
            'command = "node"',
            "",
            "[mcp_servers.foxreach]",
            'command = "/old/bun"',
            'args = ["/old/mcp.mjs"]',
            "",
            "[mcp_servers.foxreach.env]",
            'A = "1"',
            "",
            "[projects.\"/home/x\"]",
            'trust_level = "trusted"',
            "",
        ]),
        ".gemini/settings.json": json.dumps({"mcpServers": {"web-search": {"command": "ws"}}, "model": {"name": "m"}}, indent=2),
        ".gemini/config/.keep": "",
        ".config/opencode/opencode.json": json.dumps({"$schema": "https://opencode.ai/config.json", "theme": "x"}, indent=2),
        ".pi/agent/.keep": "",
        ".zcode/cli/config.json": json.dumps({"permission": {"mode": "ask"}, "mcp": {"servers": {"other": {"type": "stdio", "command": "x"}}}}, indent=2),
        ".kimi-code/mcp.json": json.dumps({"mcpServers": {"other": {"command": "x"}}}, indent=2),
        ".config/mimocode/mimocode.json": json.dumps({"$schema": "https://mimo.xiaomi.com/config.json", "model": "mimo-v2"}, indent=2),
        ".minimax/.keep": "",
        ".vibe/config.toml": 'active_model = "devstral"\n\n[[mcp_servers]]\nname = "fetch"\ntransport = "stdio"\ncommand = "uvx"\nargs = ["mcp-server-fetch"]\n\n[tools.bash]\npermission = "ask"\n',
        ".dsh/profiles/web/cordis.patch.yml": "- id: ui-settings-models\n  config:\n    credentialOnboarding: false\n",
        ".hermes/config.yaml": "model: hermes-4\nmcp_servers:\n    github:\n        command: npx\n        args: [\"-y\", \"gh\"]\ndisplay:\n  compact: true\n",
    }
    for rel, text in files.items():
        f = home / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)


def entries(home):
    """foxreach's entry in each agent's settings, as that agent would read it."""
    out = {}
    out["claude-code"] = json.loads((home / ".claude.json").read_text()).get("mcpServers", {}).get("foxreach")
    out["claude-desktop"] = json.loads((home / ".config/Claude/claude_desktop_config.json").read_text()).get("mcpServers", {}).get("foxreach")
    out["codex"] = tomllib.loads((home / ".codex/config.toml").read_text()).get("mcp_servers", {}).get("foxreach")
    f = home / ".gemini/config/mcp_config.json"
    out["antigravity"] = json.loads(f.read_text()).get("mcpServers", {}).get("foxreach") if f.exists() else None
    out["gemini"] = json.loads((home / ".gemini/settings.json").read_text()).get("mcpServers", {}).get("foxreach")
    oc = json.loads((home / ".config/opencode/opencode.json").read_text())
    out["opencode"] = oc.get("mcp", {}).get("servers", {}).get("foxreach")
    f = home / ".pi/agent/mcp.json"
    out["pi"] = json.loads(f.read_text()).get("mcpServers", {}).get("foxreach") if f.exists() else None
    out["zcode"] = json.loads((home / ".zcode/cli/config.json").read_text()).get("mcp", {}).get("servers", {}).get("foxreach")
    out["kimi"] = json.loads((home / ".kimi-code/mcp.json").read_text()).get("mcpServers", {}).get("foxreach")
    out["mimo"] = json.loads((home / ".config/mimocode/mimocode.json").read_text()).get("mcp", {}).get("foxreach")
    f = home / ".minimax/mcp.json"
    out["minimax"] = json.loads(f.read_text()).get("mcpServers", {}).get("foxreach") if f.exists() else None
    vibe = [t for t in tomllib.loads((home / ".vibe/config.toml").read_text()).get("mcp_servers", []) if t.get("name") == "foxreach"]
    out["vibe"] = {k: v for k, v in vibe[0].items() if k != "name"} if vibe else None
    dsh = [i for op in (yaml.safe_load((home / ".dsh/profiles/web/cordis.patch.yml").read_text()) or []) if isinstance(op, dict)
           for i in op.get("insert", []) if i.get("id") == "mcp-foxreach"]
    out["deepseek"] = dsh[0] if dsh else None
    out["hermes"] = (yaml.safe_load((home / ".hermes/config.yaml").read_text()).get("mcp_servers") or {}).get("foxreach")
    return out


def main():
    work = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-agents-"))
    try:
        home = work / "home"
        home.mkdir()
        seed(home)
        code, out = run(home, "agents")
        found = {line.split()[0] for line in out.splitlines() if line.split()[1:2] == ["found"]}
        check("agents: finds every seeded agent and no other",
              found == {"claude-code", "claude-desktop", "codex", "antigravity", "opencode", "pi", "hermes", "zcode", "kimi", "mimo", "minimax", "vibe", "deepseek"}, out)

        code, out = run(home, "install", "--no-flatpak")
        check("install exits 0", code == 0, out)
        cmd = [BUN, CLI, "mcp"]
        e = entries(home)
        want = {"command": cmd[0], "args": cmd[1:]}
        check("gemini: a leftover settings.json without the gemini program (retired) is not written to", e["gemini"] is None, e["gemini"])
        for agent in ("claude-code", "claude-desktop", "antigravity", "pi", "hermes"):
            check(f"{agent}: entry added", e[agent] == want, e[agent])
        check("codex: old entry and its env sub-table replaced", e["codex"] == want, e["codex"])
        check("opencode: 2.x entry under mcp.servers", e["opencode"] == {"type": "local", "command": cmd}, e["opencode"])
        check("zcode: stdio entry under mcp.servers (its schema is strict)", e["zcode"] == {"type": "stdio", **want}, e["zcode"])
        check("kimi: entry added under mcpServers", e["kimi"] == want, e["kimi"])
        check("mimo: local entry with the command as one array", e["mimo"] == {"type": "local", "command": cmd, "enabled": True}, e["mimo"])
        check("minimax: stdio entry, enabled", e["minimax"] == {"type": "stdio", **want, "enabled": True}, e["minimax"])
        check("vibe: a [[mcp_servers]] table named foxreach", e["vibe"] == {"transport": "stdio", **want}, e["vibe"])
        check("deepseek: an insert of the MCP client plugin", e["deepseek"] == {"id": "mcp-foxreach", "name": "@deepseek-ai/dsh-mcp-client",
              "config": {"serverName": "foxreach", "transport": "stdio", **want}}, e["deepseek"])
        check("cursor: not installed, so not touched", not (home / ".cursor").exists())

        # Everything else in each file is kept.
        cj = json.loads((home / ".claude.json").read_text())
        check("claude-code: other settings and servers kept", cj["numStartups"] == 12 and "other" in cj["mcpServers"] and "projects" in cj)
        cd = json.loads((home / ".config/Claude/claude_desktop_config.json").read_text())
        check("claude-desktop: preferences kept", cd["preferences"] == {"theme": "dark"})
        toml = tomllib.loads((home / ".codex/config.toml").read_text())
        check("codex: other tables and keys kept",
              toml["model"] == "gpt-5" and "node_repl" in toml["mcp_servers"] and toml["projects"]["/home/x"]["trust_level"] == "trusted"
              and "env" not in toml["mcp_servers"]["foxreach"], toml)
        gs = json.loads((home / ".gemini/settings.json").read_text())
        check("gemini: other servers and settings kept", "web-search" in gs["mcpServers"] and gs["model"] == {"name": "m"})
        oc = json.loads((home / ".config/opencode/opencode.json").read_text())
        check("opencode: other settings kept", oc["theme"] == "x" and oc["$schema"].endswith("config.json"))
        zc = json.loads((home / ".zcode/cli/config.json").read_text())
        check("zcode: other servers and settings kept", zc["permission"] == {"mode": "ask"} and "other" in zc["mcp"]["servers"], zc)
        check("kimi, mimo: other settings kept",
              "other" in json.loads((home / ".kimi-code/mcp.json").read_text())["mcpServers"]
              and json.loads((home / ".config/mimocode/mimocode.json").read_text())["model"] == "mimo-v2")
        vt = tomllib.loads((home / ".vibe/config.toml").read_text())
        check("vibe: other servers and tables kept", vt["active_model"] == "devstral" and vt["tools"]["bash"]["permission"] == "ask"
              and any(t["name"] == "fetch" for t in vt["mcp_servers"]), vt)
        dy = yaml.safe_load((home / ".dsh/profiles/web/cordis.patch.yml").read_text())
        check("deepseek: the profile's other patches kept", dy[0] == {"id": "ui-settings-models", "config": {"credentialOnboarding": False}}, dy)
        hy = yaml.safe_load((home / ".hermes/config.yaml").read_text())
        check("hermes: other servers and settings kept",
              hy["model"] == "hermes-4" and hy["mcp_servers"]["github"]["command"] == "npx" and hy["display"] == {"compact": True}, hy)

        backups = sorted(p.name for p in home.rglob("*.bak-*"))
        check("existing files were backed up before the change",
              {n.split(".bak-")[0] for n in backups} >= {".claude.json", "claude_desktop_config.json", "config.toml", "opencode.json", "config.yaml", "config.json"}, backups)
        new_files = [home / ".gemini/config/mcp_config.json", home / ".pi/agent/mcp.json", home / ".minimax/mcp.json"]
        check("files that did not exist get no backup", all(not list(f.parent.glob(f"{f.name}.bak-*")) for f in new_files), backups)
        check("install names the shared users of a file", "ChatGPT app" in out and "T3 Code" in out, out)

        snapshot = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
        code, out = run(home, "install", "--no-flatpak")
        again = {p: p.read_bytes() for p in home.rglob("*") if p.is_file()}
        check("a second install changes nothing", code == 0 and snapshot == again and out.count("already set") >= 13, out)

        code, out = run(home, "agents")
        check("agents: reports foxreach set", out.count("foxreach set") == 13, out)

        # Uninstall: foxreach's entries go, everything else stays.
        code, out = run(home, "uninstall")
        e = entries(home)
        check("uninstall exits 0", code == 0, out)
        check("uninstall removes every foxreach entry", all(v is None for v in e.values()), e)
        check("uninstall keeps the other servers",
              "other" in json.loads((home / ".claude.json").read_text())["mcpServers"]
              and "node_repl" in tomllib.loads((home / ".codex/config.toml").read_text())["mcp_servers"]
              and "github" in yaml.safe_load((home / ".hermes/config.yaml").read_text())["mcp_servers"]
              and any(t["name"] == "fetch" for t in tomllib.loads((home / ".vibe/config.toml").read_text())["mcp_servers"])
              and yaml.safe_load((home / ".dsh/profiles/web/cordis.patch.yml").read_text()) == [{"id": "ui-settings-models", "config": {"credentialOnboarding": False}}])

        # --agents limits the work; --agents none skips it.
        code, out = run(home, "install", "--no-flatpak", "--agents", "codex,pi")
        e = entries(home)
        check("--agents: only the agents named",
              e["codex"] == want and e["pi"] == want and e["claude-code"] is None and e["gemini"] is None, e)
        run(home, "uninstall")
        code, out = run(home, "install", "--no-flatpak", "--agents", "gemini")
        check("gemini: still added when named", entries(home)["gemini"] == want, out)
        run(home, "uninstall")
        check("gemini: and removed by uninstall", entries(home)["gemini"] is None)
        code, out = run(home, "install", "--no-flatpak", "--agents", "none")
        check("--agents none: no agent changed", all(v is None for v in entries(home).values()), entries(home))
        code, out = run(home, "install", "--no-flatpak", "--agents", "nonsense")
        check("--agents with an unknown name fails and says which", code != 0 and "Unknown agent: nonsense" in out, out)

        # A file that cannot be edited safely is left alone, with the entry to add printed.
        run(home, "uninstall")
        jsonc = home / ".config/opencode/opencode.json"
        jsonc.write_text('{\n  // my comment\n  "theme": "x",\n}\n')
        before = jsonc.read_bytes()
        code, out = run(home, "install", "--no-flatpak", "--agents", "opencode")
        check("JSON with comments is not rewritten", jsonc.read_bytes() == before)
        check("...and the entry to add by hand is printed", "not changed" in out and '"servers"' in out, out)

        # opencode 1.x layout: servers directly under mcp, with "enabled".
        jsonc.write_text(json.dumps({"mcp": {"local1": {"type": "local", "command": ["a"], "enabled": True}}}))
        run(home, "install", "--no-flatpak", "--agents", "opencode")
        oc = json.loads(jsonc.read_text())
        check("opencode 1.x layout is kept", oc["mcp"].get("foxreach") == {"type": "local", "command": cmd, "enabled": True}
              and "servers" not in oc["mcp"], oc)

        # Hermes without mcp_servers yet, and with the inline empty form.
        hermes = home / ".hermes/config.yaml"
        hermes.write_text("model: x\n")
        run(home, "install", "--no-flatpak", "--agents", "hermes")
        check("hermes: mcp_servers added when missing", yaml.safe_load(hermes.read_text())["mcp_servers"]["foxreach"] == want, hermes.read_text())
        hermes.write_text("model: x\nmcp_servers: {}\n")
        run(home, "install", "--no-flatpak", "--agents", "hermes")
        check("hermes: inline empty mcp_servers becomes a block", yaml.safe_load(hermes.read_text())["mcp_servers"] == {"foxreach": want}, hermes.read_text())

        # `agents add` / `agents remove`: the agents only, never the browser's manifest.
        fresh = work / "home2"
        fresh.mkdir()
        seed(fresh)
        code, out = run(fresh, "agents", "add")
        e = entries(fresh)
        check("agents add: adds to every agent found", code == 0 and e["codex"] == want and e["kimi"] == want, out)
        check("agents add: writes no browser manifest", not (fresh / ".mozilla").exists() and not (fresh / ".local/share/foxreach").exists())
        code, out = run(fresh, "agents", "add", "--agents", "pi")
        check("agents add --agents: already set is reported", "already set" in out, out)
        code, out = run(fresh, "agents", "remove")
        check("agents remove: every entry gone", code == 0 and all(v is None for v in entries(fresh).values()), out)

        # Claude Code with its own program installed: `claude mcp add -s user` is used, not the file.
        fake = work / "bin"
        fake.mkdir()
        log = work / "claude-calls.txt"
        (fake / "claude").write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
        (fake / "claude").chmod(0o755)
        run(home, "uninstall")
        cj_before = (home / ".claude.json").read_bytes()
        code, out = run(home, "install", "--no-flatpak", "--agents", "claude-code",
                        path=f"{fake}:/usr/bin:/bin", extra_env={"FOXREACH_AGENT_CLI": "1"})
        calls = log.read_text() if log.exists() else ""
        check("claude-code: uses `claude mcp add -s user` when claude is installed",
              f"mcp add -s user foxreach -- {BUN} {CLI} mcp" in calls and (home / ".claude.json").read_bytes() == cj_before, calls + out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("failed:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
