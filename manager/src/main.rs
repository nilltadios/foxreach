// SPDX-License-Identifier: MIT
//! foxreach manager: one window for the foxreach program, the add-on in each browser, the agents
//! that use it, updates, and removal. Every change is made by running `foxreach` itself.

mod app;
mod bundled_icons;
mod demo;
mod host;
mod icons;

use std::path::{Path, PathBuf};

pub const APP_ID: &str = "dev.nilltadios.FoxreachManager";

/// libcosmic looks icons up in the COSMIC icon theme; outside COSMIC it is usually missing and
/// symbolic icons come out blank, so use the desktop's own theme (KDE's, GTK's, Breeze, Adwaita).
fn fallback_icon_theme() -> Option<String> {
    let home = host::home();
    let data_home = std::env::var_os("XDG_DATA_HOME").map_or_else(|| home.join(".local/share"), PathBuf::from);
    let config_home = std::env::var_os("XDG_CONFIG_HOME").map_or_else(|| home.join(".config"), PathBuf::from);
    let data_dirs = std::env::var("XDG_DATA_DIRS").unwrap_or_else(|_| String::from("/usr/local/share:/usr/share"));
    let mut dirs = vec![data_home.join("icons"), home.join(".icons")];
    dirs.extend(data_dirs.split(':').filter(|d| !d.is_empty()).map(|d| Path::new(d).join("icons")));
    let installed = |name: &str| dirs.iter().any(|d| d.join(name).join("index.theme").is_file());
    if installed("Cosmic") {
        return None;
    }
    let read = |file: PathBuf, section: &str, key: &str| {
        std::fs::read_to_string(file).ok().and_then(|t| host::ini_value(&t, section, key))
    };
    [
        read(config_home.join("kdeglobals"), "Icons", "Theme"),
        read(config_home.join("gtk-3.0/settings.ini"), "Settings", "gtk-icon-theme-name"),
        Some(String::from("breeze")),
        Some(String::from("Adwaita")),
    ]
    .into_iter()
    .flatten()
    .find(|n| installed(n))
}

fn check() -> i32 {
    println!("packaging: {:?}", host::packaging());
    let snap = host::snapshot();
    match &snap.installed {
        Some((p, v)) => println!("installed: foxreach {v} ({})", p.join(" ")),
        None => println!("installed: none ({})", host::install_path().display()),
    }
    match &snap.bundled {
        Some((p, v)) => println!("bundled: foxreach {v} ({})", p.display()),
        None => println!("bundled: none"),
    }
    match &snap.in_use {
        Some(u) => println!("in use: foxreach {} ({:?}: {})", u.version, u.source, u.command.join(" ")),
        None => println!("in use: none"),
    }
    if let Some(r) = &snap.reader {
        println!("read with: {}", r.join(" "));
    }
    if let Some(note) = &snap.note {
        println!("note: {note}");
    }
    let Some(status) = &snap.status else {
        return 1;
    };
    println!("mcp command: {}", status.mcp_command);
    for a in status.agents.iter().filter(|a| a.found || a.set) {
        println!("agent {}: {}{}", a.id, if a.set { "set" } else { "not set" }, a.command.as_ref().map(|c| format!(" ({c})")).unwrap_or_default());
    }
    for b in &status.browsers {
        for p in &b.profiles {
            let addon = p.addon.as_ref().map_or_else(|| String::from("none"), |a| a.version.clone().unwrap_or_else(|| String::from("?")));
            println!("profile {} {}: add-on {addon}", b.spec, p.path);
        }
    }
    0
}

fn main() -> cosmic::iced::Result {
    match std::env::args().nth(1).as_deref() {
        Some("--version") => {
            println!("foxreach-manager {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        // What the window would show, without a window: for checking a package from a terminal.
        Some("--check") => std::process::exit(check()),
        // Install the bundled program as the Install button does, without a window.
        Some("--install-program") => {
            let out = match host::bundled_program() {
                Some((src, _)) => host::install_bundled(&src),
                // No bundled copy: download the release through the foxreach this build reads with.
                None => match host::snapshot().reader {
                    Some(reader) => host::foxreach(&reader, &["install-program"]),
                    None => {
                        eprintln!("This build carries no foxreach program and has none to download it with.");
                        std::process::exit(1);
                    }
                },
            };
            println!("{}", out.text);
            std::process::exit(if out.ok { 0 } else { 1 });
        }
        // Remove parts as the Remove button does, without a window: --remove host,agents,program,app
        Some("--remove") => {
            let names = std::env::args().nth(2).unwrap_or_default();
            let Some(parts) = names.split(',').map(app::Part::parse).collect::<Option<Vec<_>>>() else {
                eprintln!("--remove takes host, agents, program, app, flatpak:<id>, separated by commas");
                std::process::exit(2);
            };
            let snap = host::snapshot();
            // The same choice as the window: the foxreach in use, else the one that read the status.
            let program = snap.in_use.map(|u| u.command).or(snap.reader);
            let (out, _) = app::remove_parts(program, parts);
            println!("{}", out.text);
            std::process::exit(if out.ok { 0 } else { 1 });
        }
        _ => {}
    }
    let mut settings = cosmic::app::Settings::default()
        .size(cosmic::iced::Size::new(860.0, 760.0))
        .size_limits(cosmic::iced::Limits::NONE.min_width(420.0).min_height(320.0));
    if let Some(theme) = fallback_icon_theme() {
        settings = settings.default_icon_theme(theme);
    }
    let demo = std::env::args().any(|a| a == "--demo");
    cosmic::app::run::<app::App>(settings, demo)
}
