// SPDX-License-Identifier: MIT
//! Icons for agents and browsers: the app's own icon installed on this system when there is one
//! (full colour), else a bundled brand logo (Simple Icons, src/bundled_icons.rs), else a coloured
//! initial.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

use cosmic::iced::{Alignment, Color, Length};
use cosmic::prelude::*;
use cosmic::widget::{self, icon};

use crate::bundled_icons;

/// Where to look for an item's icon: names in the system's icon folders, then a bundled slug.
pub struct Look {
    pub host: &'static [&'static str],
    pub bundled: Option<&'static str>,
}

/// Icons for agents, by foxreach's agent id (and for apps that share an agent's settings).
pub fn agent(id: &str) -> Look {
    let (host, bundled): (&'static [&'static str], Option<&'static str>) = match id {
        // Claude Code and Codex have logos of their own, apart from their makers' apps' icons.
        "claude-code" => (&["claude-code"], Some("claudecode-color")),
        "claude-desktop" => (&["claude-desktop", "com.anthropic.Claude", "claude"], Some("claude")),
        "codex" => (&["codex"], Some("codex-color")),
        "antigravity" => (&["antigravity", "google-antigravity"], Some("antigravity-color")),
        "opencode" => (&["opencode", "ai.opencode.desktop"], Some("opencode")),
        "cursor" => (&["cursor", "co.anysphere.cursor"], Some("cursor")),
        "pi" => (&[], Some("pi")),
        "zcode" => (&["zcode"], Some("zai")),
        "openclaw" => (&["openclaw"], Some("openclaw-color")),
        "hermes" => (&["hermes", "hermes-agent"], Some("hermesagent")),
        "copilot" => (&["github-copilot", "copilot"], Some("githubcopilot")),
        "qwen" => (&["qwen-code", "qwen"], Some("qwen")),
        "kimi" => (&["kimi-code", "kimi"], Some("kimi")),
        "mimo" => (&["mimocode", "mimo"], Some("xiaomimimo")),
        "minimax" => (&["minimax", "mcode"], Some("minimax")),
        "vibe" => (&["mistral-vibe", "vibe"], Some("mistralai")),
        "deepseek" => (&["deepseek"], Some("deepseek")),
        // The logo the user picked, always (no system icon lookup).
        "grok" => (&[], Some("grok")),
        "muse" => (&["muse", "muse-code", "ai.meta.muse"], Some("muse-color")),
        "t3code" => (&["com.t3tools.T3Code", "com.t3tools.t3code", "t3code"], None),
        "chatgpt" => (&["chatgpt", "codex-desktop", "com.openai.Codex"], Some("openai")),
        _ => (&[], None),
    };
    Look { host, bundled }
}

/// Icons for browsers, by name and Flatpak id (a Flatpak's own icon first).
pub fn browser(name: &str, spec: &str) -> Look {
    let flatpak = spec.strip_prefix("flatpak:");
    let (host, bundled): (&'static [&'static str], Option<&'static str>) = match (name, flatpak) {
        ("Firefox", Some(_)) => (&["org.mozilla.firefox", "firefox"], Some("firefoxbrowser")),
        ("Firefox", None) => (&["firefox", "org.mozilla.firefox"], Some("firefoxbrowser")),
        ("LibreWolf", Some(_)) => (&["io.gitlab.librewolf-community", "librewolf"], Some("librewolf")),
        ("LibreWolf", None) => (&["librewolf", "io.gitlab.librewolf-community"], Some("librewolf")),
        ("Floorp", _) => (&["one.ablaze.floorp", "floorp"], Some("floorp")),
        ("Waterfox", _) => (&["net.waterfox.waterfox", "waterfox"], None),
        ("Zen", _) => (&["app.zen_browser.zen", "zen-browser", "zen"], Some("zenbrowser")),
        _ => (&[], None),
    };
    Look { host, bundled }
}

/// The icon for an item, `size` pixels square.
pub fn view<'a, M: 'a>(look: &Look, label: &str, size: u16) -> Element<'a, M> {
    if let Some(path) = look.host.iter().find_map(|n| index().find(n)) {
        return icon::icon(icon::from_path(path)).size(size).into();
    }
    if let Some((svg, themed)) = look.bundled.and_then(bundled_icons::get) {
        return icon::icon(icon::from_svg_bytes(svg).symbolic(themed)).size(size).into();
    }
    monogram(label, size)
}

/// A coloured circle with the first letter, the colour picked from the name.
fn monogram<'a, M: 'a>(label: &str, size: u16) -> Element<'a, M> {
    const COLORS: [(u8, u8, u8); 6] = [(0x2f, 0x7d, 0xd1), (0x8e, 0x5b, 0xd9), (0x1f, 0x9d, 0x8b), (0xd9, 0x6c, 0x2f), (0xc2, 0x3b, 0x6b), (0x5a, 0x8f, 0x29)];
    let hash = label.bytes().fold(0u32, |h, b| h.wrapping_mul(31).wrapping_add(u32::from(b)));
    let (r, g, b) = COLORS[hash as usize % COLORS.len()];
    let letter = label.chars().find(|c| c.is_alphanumeric()).map_or_else(|| String::from("?"), |c| c.to_uppercase().collect());
    widget::container(widget::text(letter).size(f32::from(size) * 0.5).class(cosmic::theme::Text::Color(Color::WHITE)))
        .width(Length::Fixed(f32::from(size)))
        .height(Length::Fixed(f32::from(size)))
        .align_x(Alignment::Center)
        .align_y(Alignment::Center)
        .class(cosmic::theme::Container::custom(move |_| cosmic::widget::container::Style {
            background: Some(Color::from_rgb8(r, g, b).into()),
            border: cosmic::iced::Border { radius: (f32::from(size) / 2.0).into(), ..Default::default() },
            ..Default::default()
        }))
        .into()
}

/// Icon files on this system by name (without extension), indexed once.
pub struct Index {
    files: HashMap<String, PathBuf>,
}

pub fn index() -> &'static Index {
    static INDEX: OnceLock<Index> = OnceLock::new();
    INDEX.get_or_init(Index::load)
}

impl Index {
    fn load() -> Index {
        let home = crate::host::home();
        let data_home = std::env::var_os("XDG_DATA_HOME").map_or_else(|| home.join(".local/share"), PathBuf::from);
        let data_dirs = std::env::var("XDG_DATA_DIRS").unwrap_or_else(|_| String::from("/usr/local/share:/usr/share"));
        let mut roots = vec![data_home.join("icons"), home.join(".local/share/icons"), home.join(".icons")];
        roots.extend(data_dirs.split(':').filter(|d| !d.is_empty()).map(|d| Path::new(d).join("icons")));
        // Inside a Flatpak the host's folders appear under /run/host (with --filesystem=host-os:ro)
        // and the exported Flatpak icons under their usual paths (read-only permissions).
        for base in ["/run/host/usr/share", "/var/lib/flatpak/exports/share"] {
            roots.push(Path::new(base).join("icons"));
        }
        roots.push(home.join(".local/share/flatpak/exports/share/icons"));
        let pixmaps = [PathBuf::from("/usr/share/pixmaps"), PathBuf::from("/run/host/usr/share/pixmaps")];

        // Best first: scalable, then the largest raster. Only the hicolor theme holds app icons.
        let sizes = ["scalable", "512x512", "256x256", "192x192", "128x128", "96x96", "64x64", "48x48"];
        let mut files: HashMap<String, PathBuf> = HashMap::new();
        let mut add = |path: PathBuf| {
            let (Some(stem), Some(ext)) = (path.file_stem().and_then(|s| s.to_str()), path.extension().and_then(|s| s.to_str())) else {
                return;
            };
            if !matches!(ext, "svg" | "png") || stem.ends_with("-symbolic") {
                return;
            }
            files.entry(stem.to_string()).or_insert(path);
        };
        for root in &roots {
            for size in sizes {
                for entry in read_dir(&root.join("hicolor").join(size).join("apps")) {
                    add(entry);
                }
            }
            for entry in read_dir(root) {
                add(entry);
            }
        }
        for dir in &pixmaps {
            for entry in read_dir(dir) {
                add(entry);
            }
        }
        Index { files }
    }

    /// The icon named exactly, or one an AppImage integration installed as
    /// `appimagekit_<hash>_<name>`.
    pub fn find(&self, name: &str) -> Option<PathBuf> {
        if let Some(p) = self.files.get(name) {
            return Some(p.clone());
        }
        let suffix = format!("_{name}");
        self.files.iter().filter(|(k, _)| k.starts_with("appimagekit_") && k.ends_with(&suffix)).map(|(_, p)| p.clone()).next()
    }
}

fn read_dir(dir: &Path) -> Vec<PathBuf> {
    std::fs::read_dir(dir).map(|it| it.filter_map(|e| e.ok().map(|e| e.path())).filter(|p| p.is_file()).collect()).unwrap_or_default()
}
