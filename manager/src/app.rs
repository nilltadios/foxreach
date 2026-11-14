// SPDX-License-Identifier: MIT
//! The window: three tabs (Agents, Browsers, foxreach), each managing its own part.

use std::collections::{BTreeSet, HashMap};

use cosmic::iced::futures::channel::oneshot;
use cosmic::iced::{Alignment, Length};
use cosmic::prelude::*;
use cosmic::widget::{self, icon, segmented_button, settings, tooltip};
use cosmic::{Application, Task};

use crate::host::{self, Output, Packaging, ProgramUpdate, Snapshot, Source, TestResult};
use crate::{demo, icons};

/// Something that can be removed, picked on the foxreach tab.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Part {
    /// The native messaging host for browsers installed on the host.
    Host,
    /// The copy inside one Flatpak browser.
    Flatpak(String),
    /// foxreach's entry in every agent.
    Agents,
    /// ~/.local/bin/foxreach itself.
    Program,
    /// This app.
    App,
}

impl Part {
    /// host, agents, program, app, flatpak:<id> (the headless --remove flag).
    pub fn parse(name: &str) -> Option<Part> {
        Some(match name {
            "host" => Part::Host,
            "agents" => Part::Agents,
            "program" => Part::Program,
            "app" => Part::App,
            _ => Part::Flatpak(name.strip_prefix("flatpak:")?.to_string()),
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Tab {
    Agents,
    Browsers,
    Foxreach,
}

/// True when an agent's foxreach command runs a different foxreach than `ours`. A checkout counts
/// once whichever script starts it (`bridge/mcp.mjs` and `bridge/cli.mjs mcp` are the same server).
fn differs(theirs: Option<&str>, ours: Option<&str>) -> bool {
    let (Some(theirs), Some(ours)) = (theirs, ours) else {
        return false;
    };
    let checkout = |c: &str| c.split_whitespace().find_map(|w| w.split_once("/bridge/").map(|(root, _)| root.to_string()));
    match (checkout(theirs), checkout(ours)) {
        (Some(a), Some(b)) => a != b,
        _ => theirs.trim() != ours.trim(),
    }
}

/// Apps that read an agent's settings file, so they get foxreach with it.
fn linked(id: &str) -> &'static [(&'static str, &'static str)] {
    match id {
        "claude-code" => &[("claude-desktop", "Claude desktop, Code tab"), ("t3code", "T3 Code"), ("grok", "Grok Build")],
        "codex" => &[("chatgpt", "ChatGPT app"), ("t3code", "T3 Code")],
        _ => &[],
    }
}

/// The linked app a search matches, for agents that share their settings with it.
fn linked_match(id: &str, q: &str) -> Option<&'static str> {
    if q.is_empty() {
        return None;
    }
    linked(id).iter().map(|(_, label)| *label).find(|label| label.to_lowercase().contains(q))
}

/// foxreach's own logo (the add-on's icon), compiled in.
const LOGO: &[u8] = include_bytes!("../resources/dev.nilltadios.FoxreachManager.svg");

fn logo(size: u16) -> icon::Icon {
    icon::icon(icon::from_svg_bytes(LOGO)).size(size)
}

#[derive(Clone, Debug)]
enum TestState {
    Running,
    Done(TestResult),
}

#[derive(Clone, Debug)]
pub enum Message {
    Refresh,
    Refreshed(Box<Snapshot>),
    TabSelected(segmented_button::Entity),
    Search(String),
    SearchBrowsers(String),
    /// A command finished: what to show, then refresh.
    Ran(String, Output),
    ToggleAgent(String, bool),
    Test(Option<String>),
    TestAll,
    Tested(Option<String>, TestResult),
    UseThisEverywhere,
    /// Install the program (bundled copy, else the latest release) and point these agents at it.
    InstallProgram(Vec<String>),
    AskSwitch,
    SetUpBrowsers,
    SetUpFlatpak(String),
    /// Ask before connecting a browser that is not recommended; then ConnectBrowser.
    AskConnect { name: String, spec: String, warning: String },
    ConnectBrowser(String),
    CheckUpdates,
    Checked(Result<ProgramUpdate, String>, Result<host::Latest, String>),
    /// The latest released add-on (signed and full), fetched once at start.
    LatestAddon(Result<host::Latest, String>),
    ApplyUpdate,
    InstallAddon { spec: String, profile: String },
    /// Stop agents in a profile (true), add-on kept, or let them use it again.
    SetStopped { profile: String, stopped: bool },
    /// The profile agents use when several browsers are connected; None clears it.
    SetDefault(Option<String>),
    /// The ⋮ menu of a profile row, by profile path.
    ToggleMenu(String),
    CloseMenu,
    /// Start a browser on the profile that has foxreach, then check the connection.
    OpenBrowser { spec: String, profile: String },
    Opened(Output),
    RemoveAddon { spec: String, profile: String },
    TogglePart(Part, bool),
    AskRemove { everything: bool },
    ConfirmRemove,
    CloseDialog,
    Removed(Output, bool),
    DismissError,
    CloseToast(widget::ToastId),
    Quit,
}

enum Dialog {
    Remove { parts: Vec<Part>, everything: bool },
    /// From a checkout to the release program.
    Switch { agents: Vec<String> },
    /// Connect a browser that is not recommended (Tor Browser): its warning first.
    ConnectRisky { name: String, spec: String, warning: String },
}

pub struct App {
    core: cosmic::Core,
    demo: bool,
    tabs: segmented_button::SingleSelectModel,
    snap: Snapshot,
    loaded: bool,
    busy: Option<String>,
    error: Option<(String, String)>,
    toasts: widget::Toasts<Message>,
    search: String,
    browser_search: String,
    tests: HashMap<Option<String>, TestState>,
    update: Option<ProgramUpdate>,
    latest_addon: Option<String>,
    /// The full build's latest release, for profiles set up from a checkout.
    latest_full: Option<String>,
    /// Whether the latest releases were asked for since start.
    asked_latest: bool,
    parts: BTreeSet<Part>,
    dialog: Option<Dialog>,
    packaging: Packaging,
    /// The profile whose ⋮ menu is open.
    menu: Option<String>,
}

/// Run blocking work on a thread and hand the result back as a message.
fn background<T: Send + 'static>(
    work: impl FnOnce() -> T + Send + 'static,
    message: impl FnOnce(T) -> Message + Send + 'static,
) -> Task<cosmic::Action<Message>> {
    cosmic::task::future(async move {
        let (sender, receiver) = oneshot::channel();
        std::thread::spawn(move || {
            let _ = sender.send(work());
        });
        match receiver.await {
            Ok(value) => message(value),
            Err(_) => Message::Refresh,
        }
    })
}

fn caption<'a>(text: impl Into<String>) -> Element<'a, Message> {
    widget::text::caption(text.into()).into()
}

/// A filled dot in the theme's success colour, shown in place of the word "Connected".
fn connected_dot<'a>(size: f32) -> Element<'a, Message> {
    widget::container(widget::space().width(Length::Fixed(size)).height(Length::Fixed(size)))
        .class(cosmic::theme::Container::custom(move |theme| cosmic::iced::widget::container::Style {
            background: Some(cosmic::iced::Background::Color(theme.cosmic().success_color().into())),
            border: cosmic::iced::Border { radius: (size / 2.0).into(), ..Default::default() },
            ..Default::default()
        }))
        .into()
}

/// The same, in the theme's accent colour: a note about the item rather than part of its name.
fn inline_note<'a>(text: impl Into<String>) -> Element<'a, Message> {
    widget::container(widget::text::caption(text.into()).class(cosmic::theme::Text::Accent))
        .padding(cosmic::iced::Padding { bottom: 1.2, ..cosmic::iced::Padding::ZERO })
        .into()
}

/// A caption beside 14 px body or heading text, in a bottom-aligned row, on the same baseline.
/// cosmic-text centres glyphs in their line, so a baseline sits (line − (ascent − descent) × size) / 2
/// above the bottom of its text box: 21 px lines at 14 px against 17 px lines at 12 px leave the
/// caption (4 − 2 × 0.776) / 2 ≈ 1.2 px low (Open Sans: ascent 1.069, descent 0.293). Measured on
/// screen before this pad: 1 to 1.5 px low.
fn inline_caption<'a>(text: impl Into<String>) -> Element<'a, Message> {
    widget::container(widget::text::caption(text.into()))
        .padding(cosmic::iced::Padding { bottom: 1.2, ..cosmic::iced::Padding::ZERO })
        .into()
}

/// Text with a tooltip (file paths and other detail live there, not on the page).
fn with_tip<'a>(content: impl Into<Element<'a, Message>>, tip: impl Into<String>) -> Element<'a, Message> {
    tooltip(content, widget::text::caption(tip.into()), tooltip::Position::Bottom).into()
}

fn packaging_label(p: &str) -> &'static str {
    match p {
        "flatpak" => "Flatpak",
        "appimage" => "AppImage",
        _ => "",
    }
}

impl App {
    fn tab(&self) -> Tab {
        self.tabs.active_data::<Tab>().copied().unwrap_or(Tab::Agents)
    }

    /// The foxreach this computer uses (the installed program or a checkout): what the app acts through.
    fn program(&self) -> Option<Vec<String>> {
        self.snap.in_use.as_ref().map(|u| u.command.clone())
    }

    /// The program that runs a removal: the one in use, else the one that read the status (the
    /// bundled program, or this build's checkout; uninstall does not depend on where the program
    /// lives). An installed program too old for `status` is never used: it does not know --part and
    /// would remove everything.
    fn remover(&self) -> Option<Vec<String>> {
        self.program().or_else(|| self.snap.reader.clone())
    }

    /// Whether the Install button can work: a bundled copy to copy, or a foxreach to download with.
    fn can_install(&self) -> bool {
        self.busy.is_none() && (self.demo || self.snap.bundled.is_some() || self.snap.reader.is_some())
    }

    /// Agents that have foxreach now, by id: re-pointed when the program changes.
    fn set_agents(&self) -> Vec<String> {
        self.snap.status.as_ref().map(|s| s.agents.iter().filter(|a| a.set).map(|a| a.id.clone()).collect()).unwrap_or_default()
    }

    /// What Install does, for its tooltip.
    fn install_tip(&self) -> String {
        match &self.snap.bundled {
            Some((_, v)) => format!("Installs the foxreach {v} this app carries as {}", host::install_path().display()),
            None => format!("Downloads the latest foxreach release (checksum checked) to {}", host::install_path().display()),
        }
    }

    /// Shown when foxreach is not set up: what it means, and the button that fixes it.
    fn not_set_up(&self, what: &str) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        settings::section()
            .add(settings::item_row(vec![
                widget::row::with_capacity(3)
                    .spacing(spacing.space_s)
                    .align_y(Alignment::Center)
                    .push(logo(32))
                    .push(widget::text::body(format!("foxreach is not installed on this computer. Install it to {what}.")).width(Length::Fill))
                    .push(with_tip(
                        widget::button::suggested("Install").on_press_maybe(self.can_install().then(|| Message::InstallProgram(Vec::new()))),
                        self.install_tip(),
                    ))
                    .into(),
            ]))
            .into()
    }

    /// A tab whose data could not be read: why, and Install when that is the fix.
    fn unreadable(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let why = self.snap.note.clone().unwrap_or_else(|| String::from("foxreach could not be read."));
        let mut col = widget::column::with_capacity(2).spacing(spacing.space_s).push(caption(why));
        if self.snap.in_use.is_none() {
            col = col.push(self.not_set_up("see and manage agents and browsers"));
        }
        col.into()
    }

    /// The program that reads (tests): the installed one, else the bundled one.
    fn reader(&self) -> Option<Vec<String>> {
        self.remover()
    }

    fn can_act(&self) -> bool {
        self.busy.is_none() && (self.demo || self.program().is_some())
    }

    fn toast(&mut self, text: impl Into<String>) -> Task<cosmic::Action<Message>> {
        self.toasts.push(widget::Toast::new(text)).map(cosmic::Action::App)
    }

    /// Run the installed foxreach with arguments; the result comes back as Message::Ran.
    fn run_foxreach(&mut self, label: &str, args: Vec<String>) -> Task<cosmic::Action<Message>> {
        if self.demo {
            return self.update(Message::Ran(label.to_string(), Output { ok: true, text: format!("demo: foxreach {}", args.join(" ")) }));
        }
        let Some(program) = self.program() else {
            return Task::none();
        };
        self.busy = Some(label.to_string());
        let label = label.to_string();
        background(
            move || {
                let refs: Vec<&str> = args.iter().map(String::as_str).collect();
                host::foxreach(&program, &refs)
            },
            move |out| Message::Ran(label, out),
        )
    }

    fn refresh(&mut self) -> Task<cosmic::Action<Message>> {
        if self.demo {
            if !self.loaded {
                self.snap = demo::snapshot();
                self.loaded = true;
            }
            return Task::none();
        }
        self.busy.get_or_insert_with(|| String::from("Reading…"));
        background(host::snapshot, |s| Message::Refreshed(Box::new(s)))
    }

    fn start_test(&mut self, agent: Option<String>) -> Task<cosmic::Action<Message>> {
        if self.demo {
            let r = demo::test(agent.as_deref());
            self.tests.insert(agent, TestState::Done(r));
            return Task::none();
        }
        let Some(program) = self.reader() else {
            return Task::none();
        };
        self.tests.insert(agent.clone(), TestState::Running);
        let key = agent.clone();
        background(move || host::test(&program, agent.as_deref()), move |r| Message::Tested(key, r))
    }

    /* ---------- Agents ---------- */

    fn agents_tab(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut col = widget::column::with_capacity(4).spacing(spacing.space_s);
        let Some(status) = &self.snap.status else {
            return self.unreadable();
        };
        if self.snap.in_use.is_none() && !self.demo {
            col = col.push(self.not_set_up("connect agents to your browser"));
        }
        let set_count = status.agents.iter().filter(|a| a.set).count();
        col = col.push(
            widget::row::with_capacity(2)
                .spacing(spacing.space_xs)
                .align_y(Alignment::Center)
                .push(widget::search_input("Search agents", &self.search).on_input(Message::Search).on_clear(Message::Search(String::new())))
                .push(widget::button::standard("Test all").on_press_maybe((self.busy.is_none() && set_count > 0).then_some(Message::TestAll))),
        );

        let ours = self.snap.in_use.as_ref().map(|_| status.mcp_command.clone());
        let stale = status.agents.iter().filter(|a| a.set && differs(a.command.as_deref(), ours.as_deref())).count();
        if stale > 0 {
            col = col.push(settings::section().add(settings::item_row(vec![
                widget::row::with_capacity(3)
                    .spacing(spacing.space_s)
                    .align_y(Alignment::Center)
                    .push(icon::from_name("dialog-warning-symbolic").size(20))
                    .push(widget::text::body(format!("{stale} {} another foxreach", if stale == 1 { "agent uses" } else { "agents use" })).width(Length::Fill))
                    .push(widget::button::standard("Point at this one").on_press_maybe(self.can_act().then_some(Message::UseThisEverywhere)))
                    .into(),
            ])));
        }

        let q = self.search.trim().to_lowercase();
        // Apps with no settings of their own (T3 Code, the ChatGPT app) find the agent they use.
        let matches = |a: &&host::Agent| q.is_empty() || a.name.to_lowercase().contains(&q) || a.id.contains(&q) || linked_match(&a.id, &q).is_some();
        // Found on this computer (or already set up) first, the rest below.
        let (here, elsewhere): (Vec<_>, Vec<_>) = status.agents.iter().filter(matches).partition(|a| a.found || a.set);
        if !here.is_empty() {
            let mut list = settings::section().title("On this computer");
            for a in &here {
                list = list.add(self.agent_row(a, ours.as_deref()));
            }
            col = col.push(list);
        }
        if !elsewhere.is_empty() {
            let mut list = settings::section().title("Not installed");
            for a in &elsewhere {
                list = list.add(self.agent_row(a, ours.as_deref()));
            }
            col = col.push(list);
        }
        if here.is_empty() && elsewhere.is_empty() {
            col = col.push(settings::section().add(settings::item_row(vec![caption("No agent by that name.")])));
        }
        col.into()
    }

    fn agent_row<'a>(&'a self, a: &'a host::Agent, ours: Option<&str>) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        // The settings file is the name's tooltip; each linked app's icon has its own. They sit side by side
        // so hovering an icon shows one tooltip, not both.
        let mut title = widget::row::with_capacity(4)
            .spacing(spacing.space_xxs)
            .align_y(Alignment::Center)
            .push(with_tip(widget::text::body(a.name.clone()), a.file.clone()));
        for (id, label) in linked(&a.id) {
            title = title.push(with_tip(icons::view(&icons::agent(id), label, 16), format!("Also used by {label}")));
        }
        let status: Option<Element<'_, Message>> = match self.tests.get(&Some(a.id.clone())) {
            Some(TestState::Running) => Some(caption("Testing…")),
            Some(TestState::Done(r)) if r.ok => {
                let mut text = r.browser.clone().unwrap_or_default();
                if r.warning.is_some() {
                    text.push_str(" · versions differ");
                }
                let line = widget::row::with_capacity(2)
                    .spacing(spacing.space_xxs)
                    .align_y(Alignment::Center)
                    .push(connected_dot(8.0))
                    .push(widget::text::caption(text));
                let tip = r.warning.clone().unwrap_or_else(|| format!("add-on {}", r.addon.clone().unwrap_or_default()));
                Some(with_tip(line, format!("Connected · {tip}")))
            }
            Some(TestState::Done(r)) => Some(with_tip(widget::text::caption("Cannot reach a browser"), r.error.clone().unwrap_or_default())),
            None if a.error.is_some() => Some(with_tip(widget::text::caption("Cannot read its settings"), a.error.clone().unwrap_or_default())),
            None if a.set && differs(a.command.as_deref(), ours) => {
                Some(with_tip(widget::text::caption("Uses another foxreach"), a.command.clone().unwrap_or_default()))
            }
            None if !a.name.to_lowercase().contains(&self.search.trim().to_lowercase())
                && let Some(label) = linked_match(&a.id, &self.search.trim().to_lowercase()) =>
            {
                Some(caption(format!("{label} uses these settings")))
            }
            None => None,
        };
        let mut text = widget::column::with_capacity(2).spacing(2).push(title);
        if let Some(s) = status {
            text = text.push(s);
        }
        let mut row = widget::row::with_capacity(5)
            .spacing(spacing.space_s)
            .align_y(Alignment::Center)
            .push(icons::view(&icons::agent(&a.id), &a.name, 32))
            .push(text.width(Length::Fill));
        if a.set {
            let running = matches!(self.tests.get(&Some(a.id.clone())), Some(TestState::Running));
            row = row.push(widget::button::text("Test").on_press_maybe((!running && self.busy.is_none()).then(|| Message::Test(Some(a.id.clone())))));
        }
        let id = a.id.clone();
        row = row.push(
            widget::toggler(a.set).on_toggle_maybe((self.can_act() && a.error.is_none()).then_some(move |on| Message::ToggleAgent(id.clone(), on))),
        );
        settings::item_row(vec![row.into()]).into()
    }

    /* ---------- Browsers ---------- */

    fn browsers_tab(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let mut col = widget::column::with_capacity(8).spacing(spacing.space_m);
        let Some(status) = &self.snap.status else {
            return self.unreadable();
        };
        if self.snap.in_use.is_none() && !self.demo {
            col = col.push(self.not_set_up("add foxreach to your browsers"));
        }

        col = col.push(
            widget::search_input("Search browsers and profiles", &self.browser_search)
                .on_input(Message::SearchBrowsers)
                .on_clear(Message::SearchBrowsers(String::new())),
        );
        // The browsers serving agents now (several can be), from the hosts' own files.
        let connected = &status.connected;
        let is_default = |profile: Option<&String>| profile.is_some_and(|p| status.default_browser.as_deref() == Some(p.as_str()));
        let stopped_profiles: Vec<&str> = status
            .browsers
            .iter()
            .flat_map(|b| b.profiles.iter())
            .filter(|p| p.addon.as_ref().is_some_and(|a| a.stopped))
            .map(|p| p.path.as_str())
            .collect();
        // Not connected: the browser to open is the one with a foxreach profile (its default first).
        let with_foxreach = status
            .browsers
            .iter()
            .flat_map(|b| b.profiles.iter().filter(|p| p.addon.is_some()).map(move |p| (b, p)))
            .min_by_key(|(_, p)| !p.is_default);
        let open_now = status.browsers.iter().find(|b| b.profiles.iter().any(|p| p.in_use && p.addon.is_some()));
        let mut text = widget::column::with_capacity(connected.len() + 1).spacing(4);
        let mut open = None;
        let conn_icon: Element<'_, Message> = match connected.first() {
            Some(c) => icons::view(&icons::browser(&c.name, if c.packaging == "flatpak" { "flatpak:" } else { "" }), &c.name, 32),
            None => icon::from_name("network-offline-symbolic").size(32).into(),
        };
        if connected.is_empty() {
            let (line, hint) = match (open_now, with_foxreach) {
                (Some(b), _) => (format!("{} is open, but foxreach is not connected", b.name), String::from("Restart the browser, then Check")),
                (None, Some((b, p))) => {
                    open = Some((b.name.clone(), b.spec.clone(), p.path.clone()));
                    (String::from("No browser open with foxreach"), format!("Open {} ({}) to connect", b.name, p.name))
                }
                (None, None) => (String::from("No browser has foxreach yet"), String::from("Install it in a profile below")),
            };
            text = text.push(widget::text::body(line)).push(caption(hint));
        } else {
            for c in connected {
                let kind = packaging_label(&c.packaging);
                let mut line = widget::row::with_capacity(4)
                    .spacing(spacing.space_xs)
                    .align_y(Alignment::End)
                    .push(widget::container(connected_dot(10.0)).padding(cosmic::iced::Padding { bottom: 5.0, ..cosmic::iced::Padding::ZERO }))
                    .push(widget::text::body(if kind.is_empty() { c.name.clone() } else { format!("{} {kind}", c.name) }));
                if let Some(pn) = &c.profile_name {
                    line = line.push(inline_caption(format!("· profile {pn}")));
                }
                if is_default(c.profile.as_ref()) {
                    line = line.push(inline_note("(default for agents)"));
                }
                if c.profile.as_deref().is_some_and(|p| stopped_profiles.contains(&p)) {
                    line = line.push(inline_caption("· agents stopped"));
                }
                text = text.push(with_tip(line, format!("Agents call it \"{}\" (browser_select)", c.id)));
            }
            let hint = match connected.len() {
                1 => String::from("Agents use this browser"),
                _ if connected.iter().any(|c| is_default(c.profile.as_ref())) => String::from("Agents use your default unless they choose another"),
                _ => String::from("Agents choose one, or make one the default in its profile's ⋮ menu"),
            };
            text = text.push(caption(hint));
        }
        let idle = self.busy.is_none();
        let mut conn = widget::row::with_capacity(5)
            .spacing(spacing.space_s)
            .align_y(Alignment::Center)
            .push(conn_icon)
            .push(text)
            .push(widget::space().width(Length::Fill));
        if let Some((name, spec, profile)) = open {
            conn = conn.push(widget::button::suggested(format!("Open {name}")).on_press_maybe(idle.then_some(Message::OpenBrowser { spec, profile })));
        }
        conn = conn.push(widget::button::standard("Check").on_press_maybe(idle.then_some(Message::Refresh)));
        col = col.push(settings::section().add(settings::item_row(vec![conn.into()])));

        let q = self.browser_search.trim().to_lowercase();
        let latest = self.latest_addon.as_deref();
        let mut any = false;
        for b in &status.browsers {
            // A browser that matches shows all its profiles; otherwise only the profiles that match.
            let kind = packaging_label(&b.packaging);
            let browser_hit = q.is_empty() || b.name.to_lowercase().contains(&q) || kind.to_lowercase().contains(&q) || b.spec.to_lowercase().contains(&q);
            let profiles: Vec<_> = b.profiles.iter().filter(|p| browser_hit || p.name.to_lowercase().contains(&q)).collect();
            if !browser_hit && profiles.is_empty() {
                continue;
            }
            any = true;
            let look = icons::browser(&b.name, &b.spec);
            // The name and the smaller "AppImage"/"Flatpak" share a bottom edge, so their baselines
            // line up (centred, the smaller text sits higher); the pair is centred on the icon.
            let mut title = widget::row::with_capacity(2)
                .spacing(spacing.space_xs)
                .align_y(Alignment::End)
                .push(with_tip(widget::text::heading(b.name.clone()), b.spec.clone()));
            // What the rows below are: this browser's profiles, counted.
            let count = match b.profiles.len() {
                0 => String::new(),
                1 => String::from("1 profile"),
                n => format!("{n} profiles"),
            };
            let detail = [kind, count.as_str()].into_iter().filter(|t| !t.is_empty()).collect::<Vec<_>>().join(" · ");
            if !detail.is_empty() {
                title = title.push(inline_caption(detail));
            }
            let head = widget::row::with_capacity(2)
                .spacing(spacing.space_xs)
                .align_y(Alignment::Center)
                .push(icons::view(&look, &b.name, 24))
                .push(title);
            let mut section = settings::section();
            let warning_line = b.warning.as_ref().map(|w| -> Element<'_, Message> {
                with_tip(
                    widget::row::with_capacity(2)
                        .spacing(spacing.space_xxs)
                        .align_y(Alignment::Center)
                        .push(icon::from_name("dialog-warning-symbolic").size(16))
                        .push(caption("Not recommended for agents")),
                    w.clone(),
                )
            });
            if let Some(c) = b.connect.as_ref().filter(|c| !c.ready) {
                let ask = Message::AskConnect { name: b.name.clone(), spec: c.spec.clone(), warning: b.warning.clone().unwrap_or_default() };
                section = section.add(settings::item_row(vec![
                    widget::row::with_capacity(2)
                        .align_y(Alignment::Center)
                        .push(widget::text::body("Not connected to foxreach").width(Length::Fill))
                        .push(widget::button::standard("Connect").on_press_maybe(self.can_act().then_some(ask)))
                        .into(),
                ]));
            }
            if let Some(id) = b.spec.strip_prefix("flatpak:")
                && let Some(f) = status.host.flatpaks.iter().find(|f| f.id == id)
                && !f.prepared
            {
                section = section.add(settings::item_row(vec![
                    widget::row::with_capacity(2)
                        .align_y(Alignment::Center)
                        .push(widget::text::body("Not connected to foxreach yet").width(Length::Fill))
                        .push(widget::button::suggested("Connect").on_press_maybe(self.can_act().then(|| Message::SetUpFlatpak(id.to_string()))))
                        .into(),
                ]));
            }
            for p in profiles {
                section = section.add(self.profile_row(b, p, latest));
            }
            if b.profiles.is_empty() {
                section = section.add(settings::item_row(vec![caption("No profile yet. Open the browser once.")]));
            }
            let mut block = widget::column::with_capacity(3).spacing(spacing.space_xs).push(head);
            if let Some(w) = warning_line {
                block = block.push(w);
            }
            col = col.push(block.push(section));
        }
        if status.browsers.is_empty() {
            col = col.push(caption("No Firefox-based browser found."));
        } else if !any {
            col = col.push(caption("No browser or profile by that name."));
        }
        col.into()
    }

    fn profile_row<'a>(&'a self, b: &'a host::Browser, p: &'a host::Profile, latest: Option<&str>) -> Element<'a, Message> {
        let spacing = cosmic::theme::spacing();
        let act = self.can_act();
        let install = || Message::InstallAddon { spec: b.spec.clone(), profile: p.path.clone() };
        // Bottom-aligned, so the note sits on the name's baseline rather than at the top.
        let mut name = widget::row::with_capacity(2)
            .spacing(spacing.space_xs)
            .align_y(Alignment::End)
            .push(widget::text::body(p.name.clone()));
        if p.is_default {
            name = name.push(inline_note("(your current default profile)"));
        }
        let status = self.snap.status.as_ref();
        let connected = status.is_some_and(|s| s.connected.iter().any(|c| c.profile.as_deref() == Some(p.path.as_str())));
        if status.is_some_and(|s| s.default_browser.as_deref() == Some(p.path.as_str())) {
            name = name.push(inline_caption("· default for agents"));
        }
        // A person icon marks the row as a browser profile.
        let name = widget::row::with_capacity(2)
            .spacing(spacing.space_xs)
            .align_y(Alignment::Center)
            .push(icon::from_name("avatar-default-symbolic").size(16))
            .push(name);
        let mut buttons: Vec<Element<'_, Message>> = Vec::new();
        let state = match &p.addon {
            Some(a) => {
                let v = a.version.clone().unwrap_or_default();
                // Updates come from the GitHub releases only: the full build's list for a profile set up
                // from a checkout, the signed list for the others. The browser asks to confirm.
                let newest = if a.full { self.latest_full.as_deref() } else { latest };
                if let Some(n) = newest.filter(|n| host::compare_versions(n, &v).is_gt()) {
                    buttons.push(
                        with_tip(
                            widget::button::suggested(format!("Update to {n}")).on_press_maybe(act.then(install)),
                            "Opens the released add-on in the browser, which asks to update it",
                        ),
                    );
                }
                let mut label = if a.full { format!("Full build {v}") } else { v };
                if a.active == Some(false) {
                    label.push_str(" · off");
                }
                if a.stopped {
                    label.push_str(" · agents stopped");
                }
                label
            }
            None => {
                buttons.push(widget::button::standard("Install").on_press_maybe(act.then(install)).into());
                String::new()
            }
        };
        let mut row = widget::row::with_capacity(5)
            .spacing(spacing.space_s)
            .align_y(Alignment::Center)
            .push(with_tip(name, p.path.clone()))
            .push(widget::space().width(Length::Fill));
        if connected {
            row = row.push(with_tip(connected_dot(8.0), "Connected to foxreach now"));
        }
        if !state.is_empty() {
            row = row.push(caption(state));
        }
        // The row's buttons and its ⋮ menu sit together, close to each other.
        let mut actions = widget::row::with_capacity(3).spacing(spacing.space_xxxs).align_y(Alignment::Center);
        for btn in buttons {
            actions = actions.push(btn);
        }
        if p.addon.is_some() {
            actions = actions.push(self.profile_menu(b, p));
        }
        row = row.push(actions);
        settings::item_row(vec![row.into()]).into()
    }

    /// The ⋮ button of a profile with foxreach, and its menu: actions used less often than Update.
    fn profile_menu<'a>(&'a self, b: &'a host::Browser, p: &'a host::Profile) -> Element<'a, Message> {
        let act = self.can_act();
        let item = |label: &'static str, msg: Option<Message>| -> Element<'a, Message> {
            widget::button::custom(widget::text::body(label))
                .class(cosmic::theme::Button::MenuItem)
                .padding([6, 16])
                .width(Length::Fixed(240.0))
                .on_press_maybe(msg)
                .into()
        };
        let remove = (act && !p.in_use).then(|| Message::RemoveAddon { spec: b.spec.clone(), profile: p.path.clone() });
        let stopped = p.addon.as_ref().is_some_and(|a| a.stopped);
        let toggle = act.then(|| Message::SetStopped { profile: p.path.clone(), stopped: !stopped });
        let is_default = self.snap.status.as_ref().is_some_and(|s| s.default_browser.as_deref() == Some(p.path.as_str()));
        let set_default = act.then(|| Message::SetDefault((!is_default).then(|| p.path.clone())));
        let mut items = widget::column::with_capacity(4)
            .push(item(if is_default { "Stop being the default for agents" } else { "Make default for agents" }, set_default))
            .push(item(if stopped { "Let agents use this profile" } else { "Stop agents in this profile" }, toggle))
            .push(item("Remove from this profile", remove));
        if p.in_use {
            items = items.push(widget::container(caption("Close the browser to remove")).padding([2, 16, 6, 16]));
        }
        let button = widget::button::icon(icon::from_name("view-more-symbolic"))
            // No padding on the right: the dots sit at the row's edge, beside Update.
            .padding([8, 0, 8, 4])
            .tooltip("More")
            .on_press(if self.menu.as_deref() == Some(p.path.as_str()) { Message::CloseMenu } else { Message::ToggleMenu(p.path.clone()) });
        let mut pop = widget::popover(button).position(widget::popover::Position::Bottom).on_close(Message::CloseMenu);
        if self.menu.as_deref() == Some(p.path.as_str()) {
            pop = pop.popup(widget::container(items).padding(4).class(cosmic::theme::Container::Dropdown));
        }
        pop.into()
    }

    /* ---------- foxreach ---------- */

    fn foxreach_tab(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let idle = self.busy.is_none();
        let act = self.can_act();
        let row = |label: Element<'static, Message>, control: Element<'static, Message>| -> Element<'static, Message> {
            settings::item_row(vec![
                widget::row::with_capacity(3).align_y(Alignment::Center).push(label).push(widget::space().width(Length::Fill)).push(control).into(),
            ])
            .into()
        };
        let mut program = settings::section().title("Program");
        let bundled = self.snap.bundled.as_ref().map(|(_, v)| v.clone());
        let title = |v: &str| -> Element<'static, Message> {
            widget::row::with_capacity(2)
                .spacing(spacing.space_s)
                .align_y(Alignment::Center)
                .push(logo(32))
                .push(widget::text::title4(format!("foxreach {v}")))
                .into()
        };
        let checkout = matches!(self.snap.in_use.as_ref().map(|u| &u.source), Some(Source::Checkout(_)));
        match &self.snap.in_use {
            Some(u) if u.source == Source::Release => {
                let control: Element<'static, Message> = match &bundled {
                    Some(b) if host::compare_versions(b, &u.version).is_gt() => widget::button::suggested(format!("Install {b}"))
                        .on_press_maybe(idle.then(|| Message::InstallProgram(self.set_agents())))
                        .into(),
                    _ => caption("Installed"),
                };
                program = program.add(row(with_tip(title(&u.version), u.command.join(" ")), control));
            }
            Some(u) => {
                let root = match &u.source {
                    Source::Checkout(r) => r.replacen(&host::home().to_string_lossy().into_owned(), "~", 1),
                    Source::Release => String::new(),
                };
                let label = widget::column::with_capacity(2)
                    .spacing(2)
                    .push(title(&u.version))
                    .push(caption(format!("Runs from the checkout in {root}")));
                let switch = with_tip(
                    widget::button::standard("Use the release program").on_press_maybe((idle && self.can_install()).then_some(Message::AskSwitch)),
                    format!("Installs the release as {} and points the browsers and agents at it", host::install_path().display()),
                );
                program = program.add(row(with_tip(label, u.command.join(" ")), switch));
            }
            None => {
                let label = match &self.snap.too_old {
                    Some(v) => format!("foxreach {v} · too old for this app"),
                    None => String::from("Not installed"),
                };
                let button = widget::button::suggested(if self.snap.too_old.is_some() { "Replace" } else { "Install" })
                    .on_press_maybe(self.can_install().then(|| Message::InstallProgram(self.set_agents())));
                program = program.add(row(widget::text::body(label).into(), with_tip(button, self.install_tip())));
            }
        }
        let upd: Element<'static, Message> = match &self.update {
            Some(u) if u.newer => widget::button::suggested(format!("Update to {}", u.latest)).on_press_maybe(act.then_some(Message::ApplyUpdate)).into(),
            _ => widget::button::standard("Check").on_press_maybe(act.then_some(Message::CheckUpdates)).into(),
        };
        let upd_label = match &self.update {
            Some(u) if !u.newer => format!("Updates · up to date ({})", u.latest),
            _ => String::from("Updates"),
        };
        if self.snap.status.as_ref().and_then(|s| s.setup.as_ref()).is_some_and(|s| s.mixed) {
            program = program.add(row(
                caption("Some agents or browsers run a different foxreach").into(),
                caption("See the Agents tab"),
            ));
        }
        // Updates and the browser connection belong to an installed foxreach.
        let installed = self.snap.in_use.is_some() || self.demo;
        if checkout {
            program = program.add(row(widget::text::body("Updates").into(), caption("With git: scripts/update.py in the checkout")));
        } else if installed {
            program = program.add(row(widget::text::body(upd_label).into(), upd));
        }
        if let Some(status) = self.snap.status.as_ref().filter(|_| installed) {
            let ready = !status.host.manifests.is_empty();
            program = program.add(row(
                with_tip(
                    widget::text::body(if ready { "Browser connection · ready" } else { "Browser connection · not set up" }),
                    status.host.wrapper_runs.clone().unwrap_or_default(),
                ),
                widget::button::standard(if ready { "Repair" } else { "Set up" }).on_press_maybe(act.then_some(Message::SetUpBrowsers)).into(),
            ));
        }

        let mut remove = settings::section().title("Remove");
        let mut options: Vec<(Part, String)> = Vec::new();
        if let Some(status) = &self.snap.status {
            if status.host.wrapper.is_some() || !status.host.manifests.is_empty() {
                options.push((Part::Host, String::from("Browser connection")));
            }
            for f in status.host.flatpaks.iter().filter(|f| f.prepared) {
                options.push((Part::Flatpak(f.id.clone()), format!("Copy inside {}", f.id)));
            }
            if status.agents.iter().any(|a| a.set) {
                options.push((Part::Agents, String::from("foxreach in all agents")));
            }
        }
        if self.snap.installed.is_some() {
            options.push((Part::Program, String::from("The foxreach program")));
        }
        if self.packaging != Packaging::Plain || self.demo {
            options.push((Part::App, String::from("This app")));
        }
        for (part, label) in options {
            let checked = self.parts.contains(&part);
            let p = part.clone();
            remove = remove.add(settings::item_row(vec![
                widget::checkbox(checked).label(label).on_toggle(move |on| Message::TogglePart(p.clone(), on)).into(),
            ]));
        }
        remove = remove.add(settings::item_row(vec![
            widget::row::with_capacity(3)
                .spacing(spacing.space_xs)
                .push(widget::space().width(Length::Fill))
                .push(widget::button::destructive("Remove selected").on_press_maybe((idle && !self.parts.is_empty()).then_some(Message::AskRemove { everything: false })))
                .push(widget::button::text("Remove everything").on_press_maybe(idle.then_some(Message::AskRemove { everything: true })))
                .into(),
        ]));
        widget::column::with_capacity(2).spacing(spacing.space_m).push(program).push(remove).into()
    }

    fn removal_parts(&self, everything: bool) -> Vec<Part> {
        if !everything {
            return self.parts.iter().cloned().collect();
        }
        let mut all = Vec::new();
        if self.remover().is_some() || self.demo {
            all.extend([Part::Host, Part::Agents]);
            if let Some(s) = &self.snap.status {
                all.extend(s.host.flatpaks.iter().filter(|f| f.prepared).map(|f| Part::Flatpak(f.id.clone())));
            }
        }
        if self.snap.installed.is_some() {
            all.push(Part::Program);
        }
        if self.packaging != Packaging::Plain {
            all.push(Part::App);
        }
        all
    }
}

fn describe(part: &Part) -> String {
    match part {
        Part::Host => String::from("the browser connection"),
        Part::Flatpak(id) => format!("the copy inside {id}"),
        Part::Agents => String::from("foxreach in every agent"),
        Part::Program => String::from("the foxreach program"),
        Part::App => String::from("this app"),
    }
}

/// Remove the parts in order: foxreach's own parts first (it needs the program), then the
/// program, then this app. Returns the log and whether the app should quit.
pub fn remove_parts(program: Option<Vec<String>>, parts: Vec<Part>) -> (Output, bool) {
    let mut log = Vec::new();
    let mut ok = true;
    let mut args: Vec<String> = vec![String::from("uninstall")];
    for p in &parts {
        match p {
            Part::Host => args.extend([String::from("--part"), String::from("host")]),
            Part::Flatpak(id) => args.extend([String::from("--part"), format!("flatpak:{id}")]),
            Part::Agents => args.extend([String::from("--part"), String::from("agents")]),
            _ => {}
        }
    }
    if args.len() > 1 {
        match &program {
            Some(program) => {
                let refs: Vec<&str> = args.iter().map(String::as_str).collect();
                let out = host::foxreach(program, &refs);
                ok &= out.ok;
                log.push(out.text);
            }
            None => {
                ok = false;
                log.push(String::from("No foxreach program to run the removal with."));
            }
        }
    }
    let program_there = host::run(&[String::from("test"), String::from("-e"), host::install_path().to_string_lossy().into_owned()]).ok;
    if ok && parts.contains(&Part::Program) && !program_there {
        log.push(format!("{} is not there; nothing to remove", host::install_path().display()));
    } else if ok && parts.contains(&Part::Program) {
        let out = host::trash(&host::install_path());
        ok &= out.ok;
        log.push(if out.ok { format!("moved {} to the trash", host::install_path().display()) } else { out.text });
    }
    let mut quit = false;
    if ok && parts.contains(&Part::App) {
        let out = host::remove_self();
        ok &= out.ok;
        quit = out.ok;
        log.push(if out.ok && out.text.is_empty() { String::from("moved this app to the trash") } else { out.text });
    }
    (Output { ok, text: log.join("\n") }, quit)
}

impl Application for App {
    type Executor = cosmic::executor::Default;
    type Flags = bool;
    type Message = Message;

    const APP_ID: &'static str = crate::APP_ID;

    fn core(&self) -> &cosmic::Core {
        &self.core
    }

    fn core_mut(&mut self) -> &mut cosmic::Core {
        &mut self.core
    }

    fn init(core: cosmic::Core, demo: bool) -> (Self, Task<cosmic::Action<Message>>) {
        let mut tabs = segmented_button::SingleSelectModel::default();
        let agents = tabs.insert().text("Agents").icon(icon::from_name("system-users-symbolic")).data(Tab::Agents).id();
        let browsers = tabs.insert().text("Browsers").icon(icon::from_name("internet-web-browser-symbolic")).data(Tab::Browsers).id();
        let foxreach = tabs.insert().text("foxreach").icon(logo(16)).data(Tab::Foxreach).id();
        // FOXREACH_MANAGER_TAB=browsers|foxreach opens on that tab: for screenshots and dev.sh, so a
        // test needs no click on the user's desktop.
        tabs.activate(match std::env::var("FOXREACH_MANAGER_TAB").as_deref() {
            Ok("browsers") => browsers,
            Ok("foxreach") => foxreach,
            _ => agents,
        });
        let mut app = App {
            core,
            demo,
            tabs,
            snap: Snapshot::default(),
            loaded: false,
            busy: None,
            error: None,
            toasts: widget::Toasts::new(Message::CloseToast),
            search: String::new(),
            browser_search: String::new(),
            tests: HashMap::new(),
            update: None,
            latest_addon: None,
            latest_full: None,
            asked_latest: false,
            parts: BTreeSet::new(),
            dialog: None,
            packaging: host::packaging(),
            menu: None,
        };
        app.set_header_title(String::from(if demo { "foxreach (demo)" } else { "foxreach" }));
        let mut tasks = vec![app.refresh(), app.start_test(None)];
        if let Some(id) = app.core.main_window_id() {
            tasks.push(app.set_window_title(String::from("foxreach"), id));
        }
        (app, Task::batch(tasks))
    }

    /// The header's close button.
    fn on_app_exit(&mut self) -> Option<Message> {
        Some(Message::Quit)
    }

    /// The compositor's close (Alt+F4, the task bar); this app has one window.
    fn on_close_requested(&self, _id: cosmic::iced::window::Id) -> Option<Message> {
        Some(Message::Quit)
    }

    fn header_end(&self) -> Vec<Element<'_, Message>> {
        vec![
            widget::button::icon(icon::from_name("view-refresh-symbolic"))
                .tooltip("Refresh")
                .on_press_maybe(self.busy.is_none().then_some(Message::Refresh))
                .into(),
        ]
    }

    fn dialog(&self) -> Option<Element<'_, Message>> {
        let (parts, everything) = match self.dialog.as_ref()? {
            Dialog::Remove { parts, everything } => (parts, everything),
            Dialog::ConnectRisky { name, spec, warning } => {
                return Some(
                    widget::dialog()
                        .title(format!("Connect {name} anyway?"))
                        .body(format!("{warning}\n\n{}", "Then install the add-on in it, and when it asks, allow foxreach in private windows: Tor Browser has no other kind."))
                        .icon(icon::from_name("dialog-warning-symbolic").size(64))
                        .primary_action(widget::button::destructive("Connect anyway").on_press(Message::ConnectBrowser(spec.clone())))
                        .secondary_action(widget::button::standard("Cancel").on_press(Message::CloseDialog))
                        .into(),
                );
            }
            Dialog::Switch { agents } => {
                let who = if agents.is_empty() { String::from("the browsers") } else { format!("the browsers and {} agents", agents.len()) };
                return Some(
                    widget::dialog()
                        .title("Use the release program?")
                        .body(format!(
                            "Installs the latest foxreach release as {} and points {who} at it instead of the checkout. \
                             The checkout stays as it is; scripts/build_install.py in it points the browsers back.",
                            host::install_path().display()
                        ))
                        .primary_action(widget::button::suggested("Use the release").on_press(Message::InstallProgram(agents.clone())))
                        .secondary_action(widget::button::standard("Cancel").on_press(Message::CloseDialog))
                        .into(),
                );
            }
        };
        let list = parts.iter().map(describe).collect::<Vec<_>>().join(", ");
        let mut body = format!("Removes {list}.");
        if parts.contains(&Part::Agents) {
            body.push_str(" Agents' settings are backed up first.");
        }
        Some(
            widget::dialog()
                .title(if *everything { "Remove everything?" } else { "Remove the selected parts?" })
                .body(body)
                .icon(icon::from_name("dialog-warning-symbolic").size(64))
                .primary_action(widget::button::destructive("Remove").on_press(Message::ConfirmRemove))
                .secondary_action(widget::button::standard("Cancel").on_press(Message::CloseDialog))
                .into(),
        )
    }

    fn update(&mut self, message: Message) -> Task<cosmic::Action<Message>> {
        match message {
            Message::Refresh => {
                if self.demo {
                    self.loaded = false;
                }
                return Task::batch([self.refresh(), self.start_test(None)]);
            }
            Message::Refreshed(snap) => {
                self.snap = *snap;
                self.loaded = true;
                self.busy = None;
                self.parts.clear();
                // Which browser answers: checked once there is a foxreach to ask with (at start the
                // app does not know yet).
                let mut tasks = Vec::new();
                // Which add-on versions are released, so profiles can offer Update without a Check.
                if !self.asked_latest && !self.demo
                    && let Some(reader) = self.reader()
                {
                    self.asked_latest = true;
                    tasks.push(background(
                        move || {
                            let a = host::foxreach(&reader, &["addon", "latest", "--json"]);
                            if a.ok { serde_json::from_str::<host::Latest>(&a.text).map_err(|e| e.to_string()) } else { Err(a.text) }
                        },
                        Message::LatestAddon,
                    ));
                }
                return Task::batch(tasks);
            }
            Message::TabSelected(entity) => self.tabs.activate(entity),
            Message::Search(q) => self.search = q,
            Message::SearchBrowsers(q) => self.browser_search = q,
            Message::Ran(label, out) => {
                self.busy = None;
                let task = if out.ok {
                    let first = out.text.lines().find(|l| !l.trim().is_empty()).unwrap_or("Done").trim().to_string();
                    self.toast(if self.demo || label.is_empty() { first } else { label.clone() })
                } else {
                    self.error = Some((label, out.text));
                    Task::none()
                };
                return Task::batch([task, self.refresh()]);
            }
            Message::DismissError => self.error = None,
            Message::CloseToast(id) => self.toasts.remove(id),
            Message::ToggleAgent(id, on) => {
                self.tests.remove(&Some(id.clone()));
                if self.demo {
                    let out = demo::set_agent(&mut self.snap, &id, on);
                    return self.update(Message::Ran(String::new(), out));
                }
                let name = self.snap.status.as_ref().and_then(|s| s.agents.iter().find(|a| a.id == id)).map(|a| a.name.clone()).unwrap_or(id.clone());
                return self.run_foxreach(
                    &format!("{} {name}", if on { "Added foxreach to" } else { "Removed foxreach from" }),
                    vec![String::from("agents"), String::from(if on { "add" } else { "remove" }), String::from("--agents"), id],
                );
            }
            Message::Test(agent) => return self.start_test(agent),
            Message::TestAll => {
                let ids: Vec<String> = self.snap.status.iter().flat_map(|s| s.agents.iter()).filter(|a| a.set).map(|a| a.id.clone()).collect();
                return Task::batch(ids.into_iter().map(|id| self.start_test(Some(id))).collect::<Vec<_>>());
            }
            Message::Tested(key, r) => {
                self.tests.insert(key, TestState::Done(r));
            }
            Message::UseThisEverywhere => {
                let ids: Vec<String> = self.snap.status.iter().flat_map(|s| s.agents.iter()).filter(|a| a.set).map(|a| a.id.clone()).collect();
                return self.run_foxreach("Agents point at this foxreach", vec![String::from("agents"), String::from("add"), String::from("--agents"), ids.join(",")]);
            }
            Message::SetUpBrowsers => {
                return self.run_foxreach("Browser connection set up", vec![String::from("install"), String::from("--agents"), String::from("none")]);
            }
            Message::AskConnect { name, spec, warning } => self.dialog = Some(Dialog::ConnectRisky { name, spec, warning }),
            Message::ConnectBrowser(spec) => {
                self.dialog = None;
                return self.run_foxreach("Connected Tor Browser", vec![
                    String::from("install"), String::from("--browser"), spec, String::from("--agents"), String::from("none"),
                ]);
            }
            Message::SetUpFlatpak(id) => {
                return self.run_foxreach(
                    &format!("Connected {id}"),
                    vec![String::from("install"), String::from("--browser"), format!("flatpak:{id}"), String::from("--agents"), String::from("none")],
                );
            }
            Message::OpenBrowser { spec, profile } => {
                if self.demo {
                    return self.start_test(None);
                }
                // The add-on connects a few seconds after the browser starts; the list is read then.
                self.busy = Some(String::from("Opening the browser…"));
                return background(
                    move || {
                        let out = host::open_browser(&spec, &profile);
                        if out.ok {
                            std::thread::sleep(std::time::Duration::from_secs(8));
                        }
                        out
                    },
                    Message::Opened,
                );
            }
            Message::Opened(out) => {
                if !out.ok {
                    self.busy = None;
                    self.error = Some((String::from("Open browser"), out.text));
                    return Task::none();
                }
                return self.refresh();
            }
            Message::AskSwitch => self.dialog = Some(Dialog::Switch { agents: self.set_agents() }),
            Message::InstallProgram(agents) => {
                self.dialog = None;
                if self.demo {
                    return self.update(Message::Ran(String::from("Installed"), Output { ok: true, text: String::from("demo: installed") }));
                }
                let bundled = self.snap.bundled.clone();
                let reader = self.snap.reader.clone();
                if bundled.is_none() && reader.is_none() {
                    return Task::none();
                }
                self.busy = Some(String::from("Installing…"));
                let agents = if agents.is_empty() { String::from("none") } else { agents.join(",") };
                return background(
                    move || {
                        let mut out = match (&bundled, &reader) {
                            (Some((src, _)), _) => host::install_bundled(src),
                            // No copy in this app: the foxreach it reads with downloads the release.
                            (None, Some(r)) => host::foxreach(r, &["install-program"]),
                            (None, None) => Output { ok: false, text: String::from("Nothing to install from") },
                        };
                        if out.ok {
                            // The new program registers itself with the browsers, and with these agents.
                            let set_up = host::foxreach(&host::program_command(), &["install", "--agents", &agents]);
                            out.ok = set_up.ok;
                            out.text = format!("{}\n{}", out.text, set_up.text);
                        }
                        out
                    },
                    |out| Message::Ran(String::from("Installed foxreach"), out),
                );
            }
            Message::CheckUpdates => {
                if self.demo {
                    return self.update(Message::Checked(
                        Ok(ProgramUpdate { latest: String::from("0.3.4"), newer: true }),
                        Ok(host::Latest { version: String::from("0.3.4"), full: None }),
                    ));
                }
                let Some(program) = self.program() else {
                    return Task::none();
                };
                self.busy = Some(String::from("Checking…"));
                return background(
                    move || {
                        let p = host::foxreach(&program, &["update", "--check", "--json"]);
                        let program_update = if p.ok { serde_json::from_str::<ProgramUpdate>(&p.text).map_err(|e| e.to_string()) } else { Err(p.text) };
                        let a = host::foxreach(&program, &["addon", "latest", "--json"]);
                        let addon = if a.ok {
                            serde_json::from_str::<host::Latest>(&a.text).map_err(|e| e.to_string())
                        } else {
                            Err(a.text)
                        };
                        (program_update, addon)
                    },
                    |(p, a)| Message::Checked(p, a),
                );
            }
            Message::Checked(p, a) => {
                self.busy = None;
                match p {
                    Ok(u) => self.update = Some(u),
                    Err(e) => self.error = Some((String::from("Cannot check for updates"), e)),
                }
                if let Ok(l) = a {
                    self.latest_full = l.full.map(|f| f.version);
                    self.latest_addon = Some(l.version);
                }
            }
            Message::LatestAddon(result) => {
                if let Ok(l) = result {
                    self.latest_full = l.full.map(|f| f.version);
                    self.latest_addon = Some(l.version);
                }
            }
            Message::ApplyUpdate => {
                self.update = None;
                return self.run_foxreach("Updated foxreach", vec![String::from("update")]);
            }
            Message::InstallAddon { spec, profile } => {
                return self.run_foxreach(
                    "Opened in the browser: confirm there",
                    vec![String::from("addon"), String::from("install"), String::from("--browser"), spec, String::from("--profile"), profile],
                );
            }
            Message::ToggleMenu(profile) => self.menu = Some(profile),
            Message::SetDefault(profile) => {
                self.menu = None;
                return match profile {
                    Some(p) => self.run_foxreach("Agents use that profile when several browsers are connected", vec![
                        String::from("browsers"), String::from("default"), String::from("--profile"), p,
                    ]),
                    None => self.run_foxreach("No default browser for agents", vec![String::from("browsers"), String::from("default"), String::from("--clear")]),
                };
            }
            Message::SetStopped { profile, stopped } => {
                self.menu = None;
                let (label, verb) = if stopped { ("Agents stopped in that profile", "stop") } else { ("Agents can use that profile again", "start") };
                return self.run_foxreach(label, vec![String::from("addon"), String::from(verb), String::from("--profile"), profile]);
            }
            Message::CloseMenu => self.menu = None,
            Message::RemoveAddon { spec, profile } => {
                self.menu = None;
                return self.run_foxreach(
                    "Removed the add-on",
                    vec![String::from("addon"), String::from("remove"), String::from("--browser"), spec, String::from("--profile"), profile],
                );
            }
            Message::TogglePart(part, on) => {
                if on {
                    self.parts.insert(part);
                } else {
                    self.parts.remove(&part);
                }
            }
            Message::AskRemove { everything } => {
                let parts = self.removal_parts(everything);
                if !parts.is_empty() {
                    self.dialog = Some(Dialog::Remove { parts, everything });
                }
            }
            Message::CloseDialog => self.dialog = None,
            Message::ConfirmRemove => {
                let Some(Dialog::Remove { parts, .. }) = self.dialog.take() else {
                    return Task::none();
                };
                if self.demo {
                    let text = format!("demo: would remove {}", parts.iter().map(describe).collect::<Vec<_>>().join(", "));
                    return self.update(Message::Ran(String::new(), Output { ok: true, text }));
                }
                let program = self.remover();
                self.busy = Some(String::from("Removing…"));
                return background(move || remove_parts(program, parts), |(out, quit)| Message::Removed(out, quit));
            }
            Message::Removed(out, quit) => {
                self.busy = None;
                if !out.ok {
                    self.error = Some((String::from("Remove"), out.text));
                    return self.refresh();
                }
                let toast = self.toast("Removed");
                if quit {
                    return Task::batch([
                        toast,
                        cosmic::task::future(async {
                            std::thread::sleep(std::time::Duration::from_millis(1500));
                            Message::Quit
                        }),
                    ]);
                }
                return Task::batch([toast, self.refresh()]);
            }
            // End the process here, before iced tears the window down. In that teardown iced-winit's
            // Wayland thread (detached, never joined) drops its objects while the main thread is
            // already in exit() with the connection gone: a use-after-free that crashed in
            // wl_proxy_destroy (core dump 2026-11-12 23:24, AppImage, on closing the window). Nothing
            // here needs a clean shutdown: settings are written as each action finishes.
            Message::Quit => std::process::exit(0),
        }
        Task::none()
    }

    fn view(&self) -> Element<'_, Message> {
        let spacing = cosmic::theme::spacing();
        let tabs = widget::container(widget::tab_bar::horizontal(&self.tabs).on_activate(Message::TabSelected).width(Length::Shrink))
            .center_x(Length::Fill);
        let mut page = widget::column::with_capacity(4).spacing(spacing.space_m).width(Length::Fill);
        if let Some((label, text)) = &self.error {
            let first: String = text.lines().filter(|l| !l.trim().is_empty()).take(3).collect::<Vec<_>>().join("\n");
            page = page.push(widget::warning(format!("{label}: {first}")).on_close(Message::DismissError));
        }
        if let Some(b) = &self.busy {
            page = page.push(caption(b.clone()));
        }
        if self.loaded {
            page = page.push(match self.tab() {
                Tab::Agents => self.agents_tab(),
                Tab::Browsers => self.browsers_tab(),
                Tab::Foxreach => self.foxreach_tab(),
            });
        }
        let body = widget::scrollable(
            widget::container(widget::container(page).max_width(760).padding([spacing.space_s, spacing.space_m])).center_x(Length::Fill),
        )
        .height(Length::Fill);
        let content = widget::column::with_capacity(2).spacing(spacing.space_xs).push(tabs).push(body);
        widget::toaster(&self.toasts, content)
    }
}

#[cfg(test)]
mod tests {
    use super::differs;

    #[test]
    fn a_checkout_is_one_foxreach_whichever_script_starts_it() {
        let ours = Some("/bun /c/bridge/cli.mjs mcp");
        assert!(!differs(Some("/bun /c/bridge/mcp.mjs"), ours));
        assert!(differs(Some("/bun /other/bridge/mcp.mjs"), ours));
        assert!(differs(Some("/h/.local/bin/foxreach mcp"), ours));
        assert!(!differs(Some("/h/.local/bin/foxreach mcp"), Some("/h/.local/bin/foxreach mcp")));
        assert!(!differs(None, ours));
        assert!(!differs(Some("x"), None));
    }
}
