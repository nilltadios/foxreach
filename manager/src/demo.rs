// SPDX-License-Identifier: MIT
//! `--demo`: the window with built-in sample data and no foxreach program, for working on the UI
//! (manager/dev.sh rebuilds and reopens it on every save). Actions change the sample in memory.

use std::path::PathBuf;

use crate::host::{Addon, Agent, Browser, FlatpakBrowser, HostSide, Output, Profile, Snapshot, Status, TestResult};

fn agent(id: &str, name: &str, found: bool, set: bool, command: Option<&str>) -> Agent {
    Agent {
        id: id.into(),
        name: name.into(),
        file: demo_file(id),
        found,
        set,
        command: command.map(String::from),
        error: None,
    }
}

/// The settings file each agent really uses (what `foxreach status` reports on a real system).
fn demo_file(id: &str) -> String {
    String::from(match id {
        "claude-code" => "~/.claude.json",
        "claude-desktop" => "~/.config/Claude/claude_desktop_config.json",
        "codex" => "~/.codex/config.toml",
        "antigravity" => "~/.gemini/config/mcp_config.json",
        "opencode" => "~/.config/opencode/opencode.json",
        "pi" => "~/.pi/agent/mcp.json",
        "hermes" => "~/.hermes/config.yaml",
        "cursor" => "~/.cursor/mcp.json",
        "zcode" => "~/.zcode/cli/config.json",
        "openclaw" => "~/.openclaw/openclaw.json",
        "copilot" => "~/.copilot/mcp-config.json",
        "qwen" => "~/.qwen/settings.json",
        "kimi" => "~/.kimi-code/mcp.json",
        "mimo" => "~/.config/mimocode/mimocode.json",
        "minimax" => "~/.minimax/mcp.json",
        "vibe" => "~/.vibe/config.toml",
        "deepseek" => "~/.dsh/profiles/web/cordis.patch.yml",
        "grok" => "~/.grok/config.toml",
        "muse" => "~/.config/muse/settings.json",
        _ => "~/",
    })
}

const MCP: &str = "/home/you/.local/bin/foxreach mcp";

pub fn snapshot() -> Snapshot {
    let agents = vec![
        agent("claude-code", "Claude Code", true, true, Some(MCP)),
        agent("claude-desktop", "Claude desktop (chat)", true, false, None),
        agent("codex", "Codex", true, true, Some("/home/you/.bun/bin/bun /home/you/foxreach/bridge/mcp.mjs")),
        agent("antigravity", "Antigravity (app and agy)", false, false, None),
        agent("opencode", "opencode (CLI and app)", true, false, None),
        agent("pi", "Pi", false, false, None),
        agent("hermes", "Hermes", false, false, None),
        agent("cursor", "Cursor", false, false, None),
        agent("zcode", "ZCode", true, true, Some(MCP)),
        agent("openclaw", "OpenClaw", false, false, None),
        agent("copilot", "GitHub Copilot CLI", false, false, None),
        agent("qwen", "Qwen Code", false, false, None),
        agent("kimi", "Kimi Code", false, false, None),
        agent("mimo", "MiMo Code", false, false, None),
        agent("minimax", "MiniMax Code", false, false, None),
        agent("vibe", "Mistral Vibe", false, false, None),
        agent("deepseek", "DeepSeek Harness (dsh)", false, false, None),
        agent("grok", "Grok Build", false, false, None),
        agent("muse", "Muse Code", false, false, None),
    ];
    let profile = |name: &str, default: bool, in_use: bool, addon: Option<(&str, bool)>| Profile {
        name: name.into(),
        path: format!("/home/you/.mozilla/firefox/{name}"),
        is_default: default,
        in_use,
        addon: addon.map(|(v, full)| Addon { version: Some(v.into()), active: Some(true), full }),
    };
    Snapshot {
        installed: Some((vec![String::from("/home/you/.local/bin/foxreach")], String::from("0.3.3"))),
        bundled: Some((PathBuf::from("/app/share/foxreach-manager/foxreach"), String::from("0.3.3"))),
        status: Some(Status {
            mcp_command: MCP.into(),
            agents,
            host: HostSide {
                wrapper: Some(String::from("/home/you/.local/share/foxreach/foxreach-host")),
                wrapper_runs: Some(String::from("/home/you/.local/bin/foxreach")),
                manifests: vec![String::from("/home/you/.mozilla/native-messaging-hosts/foxreach.json")],
                flatpaks: vec![
                    FlatpakBrowser { id: String::from("org.mozilla.firefox"), prepared: false },
                    FlatpakBrowser { id: String::from("io.gitlab.librewolf-community"), prepared: true },
                ],
            },
            browsers: vec![
                Browser {
                    name: String::from("Firefox"),
                    packaging: String::from("native"),
                    spec: String::from("/usr/bin/firefox"),
                    profiles: vec![profile("default-release", true, false, Some(("0.3.0", false))), profile("work", false, false, None)],
                },
                Browser {
                    name: String::from("LibreWolf"),
                    packaging: String::from("appimage"),
                    spec: String::from("/home/you/AppImages/librewolf.appimage"),
                    profiles: vec![profile("default", true, true, Some(("0.3.2", true)))],
                },
                Browser {
                    name: String::from("Firefox"),
                    packaging: String::from("flatpak"),
                    spec: String::from("flatpak:org.mozilla.firefox"),
                    profiles: vec![profile("flatpak-default", true, false, None)],
                },
            ],
        }),
        note: None,
    }
}

pub fn test(agent: Option<&str>) -> TestResult {
    match agent {
        Some("codex") => TestResult { ok: true, browser: Some("LibreWolf (AppImage)".into()), addon: Some("0.3.2".into()),
            warning: Some("foxreach versions differ: add-on 0.3.2, bridge 0.3.3.".into()), error: None },
        Some("zcode") => TestResult { error: Some("No browser is running foxreach. Open LibreWolf.".into()), ..Default::default() },
        _ => TestResult { ok: true, browser: Some("LibreWolf (AppImage)".into()), addon: Some("0.3.3".into()), ..Default::default() },
    }
}

/// Apply an agent switch to the sample, as `foxreach agents add|remove` would.
pub fn set_agent(snap: &mut Snapshot, id: &str, on: bool) -> Output {
    if let Some(a) = snap.status.as_mut().and_then(|s| s.agents.iter_mut().find(|a| a.id == id)) {
        a.set = on;
        a.command = on.then(|| MCP.to_string());
    }
    Output { ok: true, text: format!("{} foxreach {} {id}", if on { "Added" } else { "Removed" }, if on { "to" } else { "from" }) }
}
