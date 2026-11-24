// SPDX-License-Identifier: MIT
//! foxreach manager: one window for the foxreach program, the add-on in each browser, the agents
//! that use it, updates, and removal. Every change is made by running `foxreach` itself.

mod app;
mod bundled_icons;
mod demo;
mod host;
mod icons;
mod window_icons;
mod x11_window;

pub const APP_ID: &str = "dev.nilltadios.FoxreachManager";

fn check(icons: Option<&window_icons::Prepared>) -> i32 {
    println!("packaging: {:?}", host::packaging());
    if let Some(icons) = icons {
        println!("window icons: {}", icons.summary);
    }
    let snap = host::snapshot();
    match &snap.installed {
        Some((p, v)) => println!("installed: foxreach {v} ({})", p.join(" ")),
        None => println!("installed: none ({})", host::install_path().display()),
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
    let first = std::env::args().nth(1);
    // First, while this process has no other thread: the private icon theme for the six icons
    // libcosmic looks up by name (src/window_icons.rs) changes XDG_DATA_DIRS for a moment. Only for
    // the window and --check, which says what the window would show; the other modes draw nothing.
    let icons = match first.as_deref() {
        Some("--version" | "--install-program" | "--remove") => None,
        _ => Some(window_icons::prepare()),
    };
    match first.as_deref() {
        Some("--version") => {
            println!("foxreach-manager {}", env!("CARGO_PKG_VERSION"));
            return Ok(());
        }
        // What the window would show, without a window: for checking a package from a terminal.
        Some("--check") => std::process::exit(check(icons.as_ref())),
        // Install the latest release as the Install button does, without a window: through the
        // foxreach that reads the status, else downloaded by this app.
        Some("--install-program") => {
            let out = match host::snapshot().reader {
                Some(reader) => host::foxreach(&reader, &["install-program"]),
                None => host::install_download(),
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
    if let Some(theme) = icons.and_then(|i| i.theme) {
        settings = settings.default_icon_theme(theme);
    }
    let demo = std::env::args().any(|a| a == "--demo");
    cosmic::app::run::<app::App>(settings, demo)
}
