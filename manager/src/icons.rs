// SPDX-License-Identifier: MIT
//! Icons for agents and browsers: always the brand logo bundled with the app
//! (src/bundled_icons.rs), never an icon installed on the system, so an item looks the same on
//! every machine and desktop. Every agent, app and browser foxreach knows has a bundled logo (the
//! tests below), except Pale Moon, whose branding licence forbids it; that and names foxreach
//! does not know get a coloured initial.

use cosmic::iced::{Alignment, Color, Length};
use cosmic::prelude::*;
use cosmic::widget::{self, icon};

use crate::bundled_icons;

/// An item's logo: its name in src/bundled_icons.rs, or none for a coloured initial.
pub struct Look {
    pub bundled: Option<&'static str>,
}

/// Icons for agents, by foxreach's agent id (and for apps that share an agent's settings).
pub fn agent(id: &str) -> Look {
    let bundled = match id {
        // Claude Code and Codex have logos of their own, apart from their makers' apps' icons.
        "claude-code" => Some("claudecode-color"),
        "claude-desktop" => Some("claude"),
        "codex" => Some("codex-color"),
        "antigravity" => Some("antigravity-color"),
        "opencode" => Some("opencode"),
        "cursor" => Some("cursor"),
        "pi" => Some("pi"),
        "zcode" => Some("zai"),
        "openclaw" => Some("openclaw-color"),
        "hermes" => Some("hermesagent"),
        "copilot" => Some("githubcopilot"),
        "qwen" => Some("qwen"),
        "kimi" => Some("kimi"),
        "mimo" => Some("xiaomimimo"),
        "minimax" => Some("minimax"),
        "vibe" => Some("mistralai"),
        "deepseek" => Some("deepseek"),
        "grok" => Some("grok"),
        "muse" => Some("muse-color"),
        "t3code" => Some("t3code"),
        "chatgpt" => Some("openai"),
        _ => None,
    };
    Look { bundled }
}

/// Icons for browsers, by name. A Flatpak or other packaging shows the same logo; `_spec` stays so
/// the callers need no change.
pub fn browser(name: &str, _spec: &str) -> Look {
    let bundled = match name {
        "Firefox" | "Firefox ESR" => Some("firefoxbrowser"),
        "LibreWolf" => Some("librewolf"),
        "Floorp" => Some("floorp"),
        "Waterfox" => Some("waterfox"),
        "Zen" => Some("zenbrowser"),
        "Tor Browser" => Some("torbrowser"),
        "Mullvad Browser" => Some("mullvadbrowser"),
        "IceCat" => Some("gnuicecat"),
        // No bundled logo: Pale Moon's branding licence forbids use in other products.
        "Pale Moon" => None,
        _ => None,
    };
    Look { bundled }
}

/// The icon for an item, `size` pixels square.
pub fn view<'a, M: 'a>(look: &Look, label: &str, size: u16) -> Element<'a, M> {
    if let Some((data, themed)) = look.bundled.and_then(bundled_icons::get) {
        // An app icon published only as a PNG (T3 Code's) is bundled as one.
        let handle = if data.starts_with(PNG) { icon::from_raster_bytes(data) } else { icon::from_svg_bytes(data).symbolic(themed) };
        return icon::icon(handle).size(size).into();
    }
    monogram(label, size)
}

const PNG: &[u8] = b"\x89PNG\r\n\x1a\n";

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

#[cfg(test)]
mod tests {
    use super::{PNG, agent, browser};
    use crate::bundled_icons;

    /// The quoted value after each `key` at the start of a line: `id: "codex"` -> codex.
    fn values<'a>(source: &'a str, key: &str) -> Vec<&'a str> {
        source.lines().filter_map(|l| l.trim_start().strip_prefix(key)?.split('"').next()).collect()
    }

    fn assert_bundled(what: &str, bundled: Option<&str>) {
        let name = bundled.unwrap_or_else(|| panic!("{what} has no bundled icon: it would show a coloured initial"));
        let (data, _) = bundled_icons::get(name).unwrap_or_else(|| panic!("{what}: no bundled icon named {name} (manager/scripts/fetch_icons.py)"));
        assert!(data.starts_with(PNG) || std::str::from_utf8(data).is_ok_and(|s| s.contains("<svg")), "{what}: {name} is neither SVG nor PNG");
    }

    #[test]
    fn every_agent_and_app_foxreach_knows_has_a_bundled_icon() {
        // The agents `foxreach agents` lists, read from the program's own list, so a new one fails here.
        let ids = values(include_str!("../../bridge/agents.mjs"), "id: \"");
        assert!(ids.len() >= 19, "read only {} agent ids from bridge/agents.mjs", ids.len());
        let hosts: Vec<&str> = ids.iter().flat_map(|id| crate::app::linked(id).iter().map(|(host, _)| *host)).collect();
        assert!(hosts.contains(&"t3code"));
        for id in ids.iter().chain(&hosts) {
            assert_bundled(id, agent(id).bundled);
        }
    }

    #[test]
    fn every_browser_foxreach_knows_has_a_bundled_icon() {
        // The browsers whose profiles `foxreach status` lists (bridge/addon.mjs), Tor Browser, and
        // the names a connected browser is shown by (the GECKO list in bridge/browsers.mjs).
        let mut names = values(include_str!("../../bridge/addon.mjs"), "{ name: \"");
        assert!(names.len() >= 5, "read only {} browsers from bridge/addon.mjs", names.len());
        let gecko = include_str!("../../bridge/browsers.mjs").split("const GECKO").nth(1).and_then(|s| s.split("]);").next()).unwrap();
        let shown: Vec<&str> = gecko.split('[').skip(1).filter_map(|pair| pair.split('"').nth(3)).collect();
        assert!(shown.contains(&"Firefox ESR") && shown.len() >= 12, "read {shown:?} from bridge/browsers.mjs");
        names.extend(shown);
        names.push("Tor Browser");
        // Pale Moon's branding may not be used in other products (manager/scripts/fetch_icons.py).
        names.retain(|n| *n != "Pale Moon");
        for name in names {
            assert_bundled(name, browser(name, "").bundled);
            assert_bundled(name, browser(name, "flatpak:x").bundled);
        }
    }

    /// The window looks the same on every desktop only while every icon the app draws is compiled
    /// in. This fails on an icon looked up by name in the icon theme or read from a system folder.
    #[test]
    fn no_icon_comes_from_the_system() {
        // Split, so this test's own source does not match.
        let forbidden: Vec<String> = [
            ("from", "_name("),       // icon by name from the theme
            ("from", "_path("),       // icon file read from disk
            ("Named", "::new("),      // libcosmic's named-icon builder
            ("freedesktop", "_icons"), // the theme lookup crate
            ("-symbol", "ic\""),      // a theme icon's name
            ("share/", "icons"),      // system icon folders
            ("pix", "maps"),
            ("join(\"", "icons\")"),
            ("default_icon", "_theme("),
        ]
        .iter()
        .map(|(a, b)| format!("{a}{b}"))
        .collect();
        // The one exception: main.rs's fallback_icon_theme() finds the desktop's icon theme for the
        // few icons libcosmic's own widgets look up by name (window buttons, banner and toast close
        // buttons, tab scroll arrows), which the app has no way to replace.
        let allowed = |file: &str, pattern: &str| file == "main.rs" && (pattern.starts_with("default_icon") || pattern.starts_with("join("));
        let src = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
        let mut found = Vec::new();
        for entry in std::fs::read_dir(&src).unwrap() {
            let path = entry.unwrap().path();
            let file = path.file_name().unwrap().to_string_lossy().into_owned();
            if !file.ends_with(".rs") {
                continue;
            }
            for (n, line) in std::fs::read_to_string(&path).unwrap().lines().enumerate() {
                for p in forbidden.iter().filter(|p| line.contains(p.as_str()) && !allowed(&file, p)) {
                    found.push(format!("src/{file}:{}: {p}", n + 1));
                }
            }
        }
        assert!(
            found.is_empty(),
            "icons taken from the system, so the window would look different on each desktop:\n{}\n\
             Draw the icon for the app instead: an SVG in resources/ui/ (see resources/ui/SOURCES.md), \
             compiled in with include_bytes! and drawn with icon::from_svg_bytes(..).symbolic(true).",
            found.join("\n")
        );
    }
}
