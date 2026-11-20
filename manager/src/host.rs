// SPDX-License-Identifier: MIT
//! Running the foxreach program on the host, from a plain build, an AppImage or a Flatpak.
//!
//! Every change goes through `foxreach` itself (`status --json`, `agents add`, `uninstall
//! --part`, `addon install`, ...), so the app and the command line do the same thing. The program
//! must sit at a path the host keeps (`~/.local/bin/foxreach`), because agents and browsers are
//! given that path: never one inside an AppImage's mount or a Flatpak.

use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use serde::Deserialize;

/// Where this app runs.
#[derive(Clone, Debug, PartialEq)]
pub enum Packaging {
    Plain,
    /// The AppImage file, from `$APPIMAGE`.
    AppImage(PathBuf),
    /// Inside a Flatpak; `app_path` is where the host sees the app's /app.
    Flatpak { app_path: PathBuf },
}

pub fn packaging() -> Packaging {
    if let Ok(info) = std::fs::read_to_string("/.flatpak-info") {
        let app_path = ini_value(&info, "Instance", "app-path").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/app"));
        return Packaging::Flatpak { app_path };
    }
    // APPIMAGE and APPDIR are inherited by everything an AppImage starts (Claude's own AppImage
    // passes them on), so they describe this process only when its executable lies inside APPDIR.
    if let (Some(file), Some(appdir), Ok(exe)) = (std::env::var_os("APPIMAGE"), std::env::var_os("APPDIR"), std::env::current_exe())
        && inside(&exe, Path::new(&appdir))
    {
        return Packaging::AppImage(PathBuf::from(file));
    }
    Packaging::Plain
}

/// True when `path` is `dir` or below it (both resolved), so a prefix such as /tmp/.mount_ab does
/// not match /tmp/.mount_abc.
fn inside(path: &Path, dir: &Path) -> bool {
    match (path.canonicalize(), dir.canonicalize()) {
        (Ok(p), Ok(d)) => p.starts_with(d),
        _ => false,
    }
}

pub fn ini_value(text: &str, section: &str, key: &str) -> Option<String> {
    let mut inside = false;
    for line in text.lines().map(str::trim) {
        if line.starts_with('[') {
            inside = line == format!("[{section}]");
        } else if inside
            && let Some((k, v)) = line.split_once('=')
            && k.trim() == key
        {
            return Some(v.trim().to_string());
        }
    }
    None
}

/// What a command printed, and whether it succeeded.
#[derive(Clone, Debug)]
pub struct Output {
    pub ok: bool,
    pub text: String,
}

/// A command for the host: through `flatpak-spawn --host` in a Flatpak, and without the variables
/// an AppImage sets for itself (its libraries must not reach foxreach, `claude` or `flatpak`).
fn host_command(argv: &[String]) -> Command {
    let mut cmd = match packaging() {
        Packaging::Flatpak { .. } => {
            let mut c = Command::new("flatpak-spawn");
            c.arg("--host");
            c.args(argv);
            c
        }
        _ => {
            let mut c = Command::new(&argv[0]);
            c.args(&argv[1..]);
            c
        }
    };
    // Also outside our own AppImage: a plain build started from another AppImage inherits its
    // variables, which must not reach foxreach, `claude` or `flatpak` either.
    if let Some(appdir) = std::env::var_os("APPDIR") {
        let appdir = appdir.to_string_lossy().into_owned();
        for (key, value) in std::env::vars_os() {
            let k = key.to_string_lossy();
            // uruntime keeps the caller's home folders as HOST_<name> and points some of them
            // into the AppImage (XDG_CACHE_HOME=~/.cache/AppImage-Cache): put the originals back.
            if let Some(original) = k.strip_prefix("HOST_")
                && (original == "HOME" || (original.starts_with("XDG_") && original.ends_with("_HOME")))
            {
                cmd.env(original, &value);
                cmd.env_remove(&key);
                continue;
            }
            if matches!(
                k.as_ref(),
                "LD_LIBRARY_PATH" | "LD_PRELOAD" | "APPDIR" | "APPIMAGE" | "ARGV0" | "ARG0" | "OWD" | "APPOFFSET"
                    | "URUNTIME" | "URUNTIME_DIR" | "HOSTPATH" | "HOST_KERNEL_VERSION" | "REUSE_CHECK_DELAY" | "DWARFS_CACHESIZE"
            ) || k.starts_with("SHARUN")
                || k.starts_with("APPIMAGE_")
            {
                cmd.env_remove(&key);
                continue;
            }
            // A search path the AppImage extended: keep the host's part of it.
            let v = value.to_string_lossy();
            if v.contains(&appdir) {
                let kept: Vec<&str> = v.split(':').filter(|p| !p.contains(&appdir)).collect();
                if kept.is_empty() {
                    cmd.env_remove(&key);
                } else {
                    cmd.env(&key, kept.join(":"));
                }
            }
        }
    }
    cmd
}

/// Run a command on the host and wait for it.
pub fn run(argv: &[String]) -> Output {
    match host_command(argv).stdin(Stdio::null()).output() {
        Ok(out) => {
            let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
            text.push_str(&String::from_utf8_lossy(&out.stderr));
            Output { ok: out.status.success(), text: text.trim_end().to_string() }
        }
        Err(e) => Output { ok: false, text: format!("Cannot run {}: {e}", argv[0]) },
    }
}

pub fn home() -> PathBuf {
    std::env::var_os("HOME").map_or_else(|| PathBuf::from("/"), PathBuf::from)
}

/// Where the app installs the program, and where it looks first.
pub fn install_path() -> PathBuf {
    home().join(".local/bin/foxreach")
}

/// The program to run: FOXREACH_MANAGER_PROGRAM (tests, a checkout), else ~/.local/bin/foxreach.
/// A list, because a checkout is `bun bridge/cli.mjs`.
pub fn program_command() -> Vec<String> {
    match std::env::var("FOXREACH_MANAGER_PROGRAM") {
        Ok(cmd) if !cmd.trim().is_empty() => cmd.split_whitespace().map(String::from).collect(),
        _ => vec![install_path().to_string_lossy().into_owned()],
    }
}

/// Words of one shell command line, with double- and single-quoted parts kept whole, as `words` in
/// bridge/setup.mjs splits them.
fn words(line: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut quote = None;
    let mut any = false;
    for ch in line.trim().chars() {
        if let Some(q) = quote {
            if ch == q {
                quote = None;
            } else {
                cur.push(ch);
            }
        } else if ch == '"' || ch == '\'' {
            quote = Some(ch);
            any = true;
        } else if ch.is_whitespace() {
            if !cur.is_empty() || any {
                out.push(std::mem::take(&mut cur));
            }
            any = false;
        } else {
            cur.push(ch);
        }
    }
    if !cur.is_empty() || any {
        out.push(cur);
    }
    out
}

/// What the file a host manifest names makes the browser run, as words: a script's `exec` line, or
/// the file itself when it is no script (`manifestRuns` in bridge/setup.mjs). `head` is its start.
fn wrapper_runs(target: &str, head: &str) -> Option<Vec<String>> {
    if !head.starts_with("#!") {
        return Some(vec![target.to_string()]);
    }
    let line = head.split('\n').map(str::trim).find(|l| l.starts_with("exec "))?;
    Some(words(&line[5..]))
}

/// True for a path ending in bridge/cli.mjs, bridge/mcp.mjs or bridge/host.mjs (either separator).
fn is_bridge_script(word: &str) -> bool {
    ["cli", "mcp", "host"].iter().any(|name| {
        let file = format!("{name}.mjs");
        word.strip_suffix(file.as_str())
            .and_then(|rest| rest.strip_suffix('/').or_else(|| rest.strip_suffix('\\')))
            .and_then(|rest| rest.strip_suffix("bridge"))
            .is_some_and(|rest| rest.ends_with('/') || rest.ends_with('\\'))
    })
}

/// How to run the command line of the foxreach a command starts, as `classify` in bridge/setup.mjs
/// tells: a checkout is `[runtime] <root>/bridge/cli.mjs`, the compiled program (a file named
/// foxreach...) is itself. None for anything else, which is no foxreach to read with.
fn cli_command(argv: &[String]) -> Option<Vec<String>> {
    let first = argv.first()?;
    if let Some(script) = argv.iter().find(|w| is_bridge_script(w)) {
        let root = Path::new(script).parent()?.parent()?;
        let mut cmd: Vec<String> = if first != script { vec![first.clone()] } else { Vec::new() };
        cmd.push(root.join("bridge/cli.mjs").to_string_lossy().into_owned());
        return Some(cmd);
    }
    let name = Path::new(first).file_name()?.to_string_lossy();
    name.starts_with("foxreach").then(|| vec![first.clone()])
}

/// The start of a file on the host (a Flatpak cannot read the host's home itself).
fn host_file_head(path: &str) -> Option<String> {
    let out = run(&["head".into(), "-c".into(), "8192".into(), path.to_string()]);
    out.ok.then_some(out.text)
}

/// The foxreach the browsers' host manifest runs (`~/.mozilla`, then `~/.librewolf`, the Linux
/// folders of bridge/install.mjs), as a command line to read with: how a packaged app sees a setup
/// made from a checkout or with the program at another path.
fn manifest_reader() -> Option<Vec<String>> {
    [".mozilla", ".librewolf"].iter().find_map(|dir| {
        let manifest = home().join(dir).join("native-messaging-hosts/foxreach.json");
        let text = host_file_head(&manifest.to_string_lossy())?;
        let value = serde_json::from_str::<serde_json::Value>(&text).ok()?;
        let target = value.get("path")?.as_str().filter(|p| !p.is_empty())?.to_string();
        cli_command(&wrapper_runs(&target, &host_file_head(&target)?)?)
    })
}

/// The checkout a plain build was compiled from, run with Bun (or Node): a development build's own
/// reader when no foxreach is set up. None for packaged builds, or when the folder or a runtime is
/// missing.
pub fn dev_checkout() -> Option<Vec<String>> {
    if packaging() != Packaging::Plain {
        return None;
    }
    let cli = Path::new(env!("CARGO_MANIFEST_DIR")).join("../bridge/cli.mjs").canonicalize().ok()?;
    let runtime = Some(home().join(".bun/bin/bun"))
        .filter(|p| p.is_file())
        .or_else(|| on_path("bun"))
        .or_else(|| on_path("node"))?;
    Some(vec![runtime.to_string_lossy().into_owned(), cli.to_string_lossy().into_owned()])
}

fn on_path(cmd: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|p| std::env::split_paths(&p).map(|d| d.join(cmd)).find(|f| f.is_file()))
}

/// Run foxreach with arguments.
pub fn foxreach(program: &[String], args: &[&str]) -> Output {
    let mut argv = program.to_vec();
    argv.extend(args.iter().map(|a| a.to_string()));
    run(&argv)
}

/// The version a program says it is, or None when it does not run.
pub fn version_of(program: &[String]) -> Option<String> {
    let out = foxreach(program, &["version"]);
    let v = out.text.lines().last()?.trim().to_string();
    (out.ok && v.chars().next()?.is_ascii_digit()).then_some(v)
}

/// -1, 0 or 1 for dotted numeric versions.
pub fn compare_versions(a: &str, b: &str) -> std::cmp::Ordering {
    let parse = |s: &str| s.split('.').map(|p| p.parse::<u64>().unwrap_or(0)).collect::<Vec<_>>();
    let (pa, pb) = (parse(a), parse(b));
    for i in 0..pa.len().max(pb.len()) {
        let o = pa.get(i).unwrap_or(&0).cmp(pb.get(i).unwrap_or(&0));
        if o.is_ne() {
            return o;
        }
    }
    std::cmp::Ordering::Equal
}

/// The release file of the foxreach program for a processor (`std::env::consts::ARCH`), as the
/// release names it.
fn release_asset(arch: &str) -> Option<&'static str> {
    match arch {
        "x86_64" => Some("foxreach-linux-x64"),
        "aarch64" => Some("foxreach-linux-arm64"),
        _ => None,
    }
}

/// The version a release's BUILDINFO.txt names on its `foxreach <version>` line (`latestVersion`
/// in bridge/update.mjs).
fn buildinfo_version(text: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let v = line.strip_prefix("foxreach ")?.trim_end();
        (!v.is_empty() && !v.starts_with('.') && !v.ends_with('.') && !v.contains("..") && v.chars().all(|c| c.is_ascii_digit() || c == '.'))
            .then(|| v.to_string())
    })
}

/// The checksum SHA256SUMS lists for a file: its lines are `<sha256>  <name>`.
fn listed_sha256(sums: &str, asset: &str) -> Option<String> {
    sums.lines().find_map(|line| {
        let mut parts = line.split_whitespace();
        let (hash, name) = (parts.next()?, parts.next()?);
        (name == asset).then(|| hash.to_string())
    })
}

/// Where releases are downloaded from: FOXREACH_RELEASE_URL (tests, a mirror; install.sh and
/// bridge/update.mjs read it too), else the latest GitHub release. Read here, because host
/// commands run with a cleaned environment.
fn release_base() -> String {
    match std::env::var("FOXREACH_RELEASE_URL") {
        Ok(url) if !url.trim().is_empty() => url.trim().trim_end_matches('/').to_string(),
        _ => String::from("https://github.com/nilltadios/foxreach/releases/latest/download"),
    }
}

/// A host command that downloads `url` to `to` (`-` for its output), with curl or else wget,
/// whichever the host has. A stalled download ends by itself, because the window waits for it.
fn download_command(curl: bool, url: &str, to: &str) -> Vec<String> {
    let mut argv: Vec<String> = if curl {
        ["curl", "-fsSL", "--connect-timeout", "20", "--speed-limit", "1024", "--speed-time", "60", "-o"].map(String::from).to_vec()
    } else {
        ["wget", "-q", "--tries=1", "--connect-timeout=20", "--read-timeout=60", "-O"].map(String::from).to_vec()
    };
    argv.push(to.to_string());
    argv.push(url.to_string());
    argv
}

/// Download the latest release's program for this machine to ~/.local/bin/foxreach (the old one
/// kept as foxreach.previous), checked against the release's SHA256SUMS and BUILDINFO.txt, with
/// host tools so it works the same from a Flatpak. Returns what was done, line by line.
pub fn install_download() -> Output {
    let fail = |text: String| Output { ok: false, text: format!("{text}\nNothing was changed.") };
    let arch = std::env::consts::ARCH;
    let Some(asset) = release_asset(arch) else {
        return fail(format!("There is no foxreach build for {arch}."));
    };
    let curl = if run(&["sh".into(), "-c".into(), "command -v curl".into()]).ok {
        true
    } else if run(&["sh".into(), "-c".into(), "command -v wget".into()]).ok {
        false
    } else {
        return fail(String::from("curl or wget is needed."));
    };
    let base = release_base();
    let fetch = |name: &str| {
        let out = run(&download_command(curl, &format!("{base}/{name}"), "-"));
        if out.ok { Ok(out.text) } else { Err(format!("Cannot download {base}/{name}: {}", out.text)) }
    };
    let version = match fetch("BUILDINFO.txt").map(|t| buildinfo_version(&t)) {
        Ok(Some(v)) => v,
        Ok(None) => return fail(String::from("The latest release does not say its version (BUILDINFO.txt).")),
        Err(e) => return fail(e),
    };
    let sums = match fetch("SHA256SUMS") {
        Ok(t) => t,
        Err(e) => return fail(e),
    };

    let dest = install_path();
    let dir = dest.parent().unwrap_or(Path::new("/")).to_string_lossy().into_owned();
    let dest_s = dest.to_string_lossy().into_owned();
    let tmp = format!("{dir}/.foxreach-manager-new");
    let mut log = Vec::new();
    // Every failure from here on removes the half-made file, and the installed program stays.
    let fail = |text: String| {
        run(&["rm".into(), "-f".into(), tmp.clone()]);
        fail(text)
    };
    for step in [vec!["mkdir".into(), "-p".into(), dir.clone()], download_command(curl, &format!("{base}/{asset}"), &tmp)] {
        let out = run(&step);
        if !out.ok {
            return fail(format!("{}: {}", step.join(" "), out.text));
        }
    }
    // Checked before the file is made executable or run.
    let summed = run(&["sha256sum".into(), tmp.clone()]);
    let got = summed.text.split_whitespace().next().filter(|_| summed.ok).unwrap_or_default().to_string();
    let want = listed_sha256(&sums, asset);
    if !summed.ok || want.as_deref() != Some(got.as_str()) {
        run(&["rm".into(), "-f".into(), tmp.clone()]);
        let got = if summed.ok { got } else { format!("no checksum ({})", summed.text) };
        return Output {
            ok: false,
            text: format!("Checksum mismatch for {asset}: expected {}, got {got}. Nothing was changed.", want.as_deref().unwrap_or("nothing listed")),
        };
    }
    log.push(format!("downloaded {base}/{asset}, checksum checked against SHA256SUMS"));
    let out = run(&["chmod".into(), "755".into(), tmp.clone()]);
    if !out.ok {
        return fail(format!("chmod 755 {tmp}: {}", out.text));
    }
    match version_of(std::slice::from_ref(&tmp)) {
        Some(v) if v == version => {}
        Some(v) => return fail(format!("The downloaded {asset} says it is foxreach {v}, the release says {version}.")),
        None => return fail(format!("{tmp} version: the downloaded program does not run.")),
    }
    if run(&["test".into(), "-e".into(), dest_s.clone()]).ok {
        let prev = format!("{dest_s}.previous");
        let out = run(&["cp".into(), "-f".into(), dest_s.clone(), prev.clone()]);
        if !out.ok {
            run(&["rm".into(), "-f".into(), tmp.clone()]);
            return Output { ok: false, text: format!("Cannot keep the old program as {prev}: {}", out.text) };
        }
        log.push(format!("kept the old program as {prev}"));
    }
    let out = run(&["mv".into(), "-f".into(), tmp, dest_s.clone()]);
    if !out.ok {
        return Output { ok: false, text: out.text };
    }
    log.push(format!("installed {dest_s} (foxreach {version})"));
    Output { ok: true, text: log.join("\n") }
}

/// Start a browser on a profile and leave it running: detached in its own process group, with the
/// environment cleaned as for every host command (an AppImage's libraries must not reach it).
pub fn open_browser(spec: &str, profile: &str) -> Output {
    use std::os::unix::process::CommandExt;
    let argv: Vec<String> = match spec.strip_prefix("flatpak:") {
        Some(id) => ["flatpak", "run", id, "--profile", profile].map(String::from).to_vec(),
        None => [spec, "--profile", profile].map(String::from).to_vec(),
    };
    match host_command(&argv).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0).spawn() {
        Ok(mut child) => {
            // Reap it when it exits, so it never lingers as a zombie of this app.
            std::thread::spawn(move || child.wait());
            Output { ok: true, text: format!("Started {}", argv.join(" ")) }
        }
        Err(e) => Output { ok: false, text: format!("Cannot start {}: {e}", argv[0]) },
    }
}

/// Move a file to the trash on the host (reversible), as `gio trash` does.
pub fn trash(path: &Path) -> Output {
    run(&["gio".into(), "trash".into(), path.to_string_lossy().into_owned()])
}

/// Remove this app itself, after it has quit: the AppImage file goes to the trash; a Flatpak is
/// uninstalled by the host's flatpak a moment after this process exits.
pub fn remove_self() -> Output {
    match packaging() {
        Packaging::AppImage(file) => trash(&file),
        Packaging::Flatpak { .. } => {
            let id = crate::APP_ID;
            let spawned = Command::new("flatpak-spawn")
                .args(["--host", "setsid", "sh", "-c"])
                .arg(format!("sleep 2; flatpak uninstall --noninteractive -y {id}"))
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .spawn();
            match spawned {
                Ok(_) => Output { ok: true, text: format!("{id} is uninstalled once this window closes") },
                Err(e) => Output { ok: false, text: format!("Cannot run flatpak-spawn: {e}") },
            }
        }
        Packaging::Plain => Output { ok: false, text: String::from("This build is not installed as an app; delete it yourself") },
    }
}

/* ---------- foxreach status --json ---------- */

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    /// The report's layout (bridge/status.mjs); 1 when absent. A higher one is not read.
    #[serde(default = "first_format")]
    pub format: u64,
    pub mcp_command: String,
    /// The foxreach the browsers and agents actually run (absent from programs before 0.3.4).
    #[serde(default)]
    pub setup: Option<Setup>,
    pub agents: Vec<Agent>,
    pub host: HostSide,
    #[serde(default)]
    pub browsers: Vec<Browser>,
    /// The browsers serving agents now (several can be connected at once).
    #[serde(default)]
    pub connected: Vec<Connected>,
    /// The profile agents use when several browsers are connected.
    #[serde(default)]
    pub default_browser: Option<String>,
}

/// The newest `foxreach status --json` layout this app reads.
const STATUS_FORMAT: u64 = 1;
/// How the note about a newer layout starts: `snapshot` tells it from a program that is too old.
const NEWER_NOTE: &str = "This foxreach is newer than this app reads";

fn first_format() -> u64 {
    1
}

/// A browser connected to foxreach now: `foxreach browsers --json`, `connected` in status.
#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Connected {
    pub id: String,
    pub name: String,
    pub packaging: String,
    pub profile: Option<String>,
    pub profile_name: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Agent {
    pub id: String,
    pub name: String,
    pub file: String,
    pub found: bool,
    pub set: bool,
    pub command: Option<String>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct HostSide {
    pub wrapper: Option<String>,
    pub wrapper_runs: Option<String>,
    pub manifests: Vec<String>,
    pub flatpaks: Vec<FlatpakBrowser>,
}

/// `setup` in `foxreach status --json`: what the browsers' host and the agents run.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Setup {
    /// "release", "checkout", "other" or "none".
    pub kind: String,
    pub root: Option<String>,
    /// How to run that foxreach's command line.
    pub command: Option<Vec<String>>,
    #[serde(default)]
    pub mixed: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct FlatpakBrowser {
    pub id: String,
    pub prepared: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Browser {
    pub name: String,
    pub packaging: String,
    pub spec: String,
    pub profiles: Vec<Profile>,
    /// Why this browser is not recommended (Tor Browser).
    #[serde(default)]
    pub warning: Option<String>,
    /// A browser foxreach is connected to only by name (Tor Browser): whether it is, and the spec.
    #[serde(default)]
    pub connect: Option<ConnectBrowser>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ConnectBrowser {
    pub ready: bool,
    pub spec: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    pub name: String,
    pub path: String,
    pub is_default: bool,
    pub in_use: bool,
    pub addon: Option<Addon>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Addon {
    pub version: Option<String>,
    pub active: Option<bool>,
    pub full: bool,
    /// The user stopped agents in this profile; the add-on stays (absent before 0.3.4).
    #[serde(default)]
    pub stopped: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Latest {
    pub version: String,
    /// The full build's latest release (profiles set up from a checkout); absent before 0.3.4.
    #[serde(default)]
    pub full: Option<LatestFull>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct LatestFull {
    pub version: String,
}

#[derive(Clone, Debug, Deserialize)]
pub struct ProgramUpdate {
    pub latest: String,
    pub newer: bool,
}

/// Everything the window shows, gathered on a thread.
#[derive(Clone, Debug, Default)]
pub struct Snapshot {
    /// The program at ~/.local/bin/foxreach (or FOXREACH_MANAGER_PROGRAM) and its version, when it runs.
    pub installed: Option<(Vec<String>, String)>,
    /// The foxreach this computer uses, which the app acts through: the installed program, or the
    /// checkout the browsers and agents run. None when nothing is set up.
    pub in_use: Option<InUse>,
    /// What read the status: the program in use, else what the browsers' host runs or this build's
    /// checkout. None when there is nothing to read with (the Install button downloads the release).
    pub reader: Option<Vec<String>>,
    /// The installed program's version when it is too old for this app (no `status --json`).
    pub too_old: Option<String>,
    /// The installed program's version when its status is in a newer layout than this app reads:
    /// the app needs the update, not the program, so no Install or Replace is offered.
    pub too_new: Option<String>,
    pub status: Option<Status>,
    /// Why there is no status.
    pub note: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Source {
    /// The compiled program.
    Release,
    /// A checkout, by folder.
    Checkout(String),
}

#[derive(Clone, Debug)]
pub struct InUse {
    pub command: Vec<String>,
    pub version: String,
    pub source: Source,
}

fn read_status(program: &[String]) -> Result<Status, String> {
    let out = foxreach(program, &["status", "--json"]);
    if !out.ok {
        return Err(format!("foxreach status --json failed: {}", out.text));
    }
    parse_status(&out.text)
}

/// A status report, unless its format is newer than this app reads: that is said instead, also
/// when the newer layout does not fit `Status` at all (no parse error is shown for it).
fn parse_status(text: &str) -> Result<Status, String> {
    let newer = |format: u64| {
        format!("{NEWER_NOTE} (status format {format}, this app reads {STATUS_FORMAT}). Update foxreach manager.")
    };
    match serde_json::from_str::<Status>(text) {
        Ok(status) if status.format > STATUS_FORMAT => Err(newer(status.format)),
        Ok(status) => Ok(status),
        Err(e) => match serde_json::from_str::<serde_json::Value>(text).ok().and_then(|v| v.get("format")?.as_u64()) {
            Some(format) if format > STATUS_FORMAT => Err(newer(format)),
            _ => Err(format!("foxreach status --json: {e}")),
        },
    }
}

pub fn snapshot() -> Snapshot {
    let program = program_command();
    let mut snap = Snapshot { installed: version_of(&program).map(|v| (program.clone(), v)), ..Default::default() };

    // The installed program, when it is current, is what the app uses and reads with.
    if let Some((cmd, v)) = snap.installed.clone() {
        match read_status(&cmd) {
            Ok(status) => {
                snap.in_use = Some(InUse { command: cmd.clone(), version: v, source: Source::Release });
                snap.reader = Some(cmd);
                snap.status = Some(status);
                return snap;
            }
            Err(e) => {
                if e.starts_with(NEWER_NOTE) {
                    snap.too_new = Some(v);
                } else {
                    snap.too_old = Some(v);
                }
                snap.note = Some(e);
            }
        }
    }
    // Otherwise read with what the browsers' host runs (a checkout, or the program at another
    // path), else the checkout a development build came from, then look at what the browsers and
    // agents actually run. A host that runs the installed program, which failed above or is gone,
    // is not tried again.
    let readers = manifest_reader().filter(|r| *r != program).into_iter().chain(dev_checkout());
    for reader in readers {
        let status = match read_status(&reader) {
            Ok(s) => s,
            Err(e) => {
                snap.note = Some(e);
                continue;
            }
        };
        snap.note = None;
        snap.reader = Some(reader.clone());
        let in_use = status.setup.as_ref().filter(|s| s.kind == "checkout" || s.kind == "release").and_then(|setup| {
            let cmd = setup.command.clone()?;
            let version = version_of(&cmd)?;
            let source = match setup.kind.as_str() {
                "checkout" => Source::Checkout(setup.root.clone().unwrap_or_default()),
                _ => Source::Release,
            };
            Some(InUse { command: cmd, version, source })
        });
        // Read again with the foxreach in use, so its MCP command is the one agents are compared with.
        snap.status = match &in_use {
            Some(u) if u.command != reader => read_status(&u.command).ok().or(Some(status)),
            _ => Some(status),
        };
        if in_use.is_some() {
            snap.too_old = None;
            snap.too_new = None;
        }
        snap.in_use = in_use;
        return snap;
    }
    // Nothing installed and nothing to read with is no error: the window offers Install, which
    // downloads the release.
    snap
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn inside_needs_a_whole_folder_name() {
        let base = std::env::temp_dir().join(format!("fxm-inside-{}", std::process::id()));
        std::fs::create_dir_all(base.join("abc/bin")).unwrap();
        std::fs::create_dir_all(base.join("ab")).unwrap();
        assert!(inside(&base.join("abc/bin"), &base.join("abc")));
        assert!(!inside(&base.join("abc/bin"), &base.join("ab")));
        std::fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn versions_compare_numerically() {
        assert!(compare_versions("0.3.10", "0.3.9").is_gt());
        assert!(compare_versions("0.3", "0.3.0").is_eq());
        assert!(compare_versions("0.2.9", "0.3").is_lt());
    }

    #[test]
    fn release_files_are_read() {
        assert_eq!(release_asset("x86_64"), Some("foxreach-linux-x64"));
        assert_eq!(release_asset("aarch64"), Some("foxreach-linux-arm64"));
        assert_eq!(release_asset("riscv64"), None);
        assert_eq!(buildinfo_version("foxreach 0.4.1\ncommit abc\n").as_deref(), Some("0.4.1"));
        assert_eq!(buildinfo_version("built 2026-11-20\nfoxreach 10.2 \r\n").as_deref(), Some("10.2"));
        assert_eq!(buildinfo_version("foxreach v0.4.1\nfoxreach 0..1\nfoxreach \n"), None);
        let a = "a".repeat(64);
        let b = "b".repeat(64);
        let sums = format!("{a}  foxreach-linux-x64\n{b}  foxreach-linux-arm64\n{a}  foxreach-linux-x64.sha256\n");
        assert_eq!(listed_sha256(&sums, "foxreach-linux-arm64"), Some(b));
        assert_eq!(listed_sha256(&sums, "foxreach-linux-x64"), Some(a));
        assert_eq!(listed_sha256(&sums, "foxreach-darwin-arm64"), None);
        let curl = download_command(true, "file:///r/SHA256SUMS", "-");
        assert_eq!(curl[0], "curl");
        assert!(curl.windows(2).any(|w| w == ["--speed-time", "60"]) && curl.ends_with(&[String::from("-"), String::from("file:///r/SHA256SUMS")]));
        let wget = download_command(false, "https://x/a", "/t");
        assert!(wget[0] == "wget" && wget.contains(&String::from("--tries=1")) && wget.ends_with(&[String::from("/t"), String::from("https://x/a")]));
    }

    #[test]
    fn host_wrappers_give_a_command_to_read_with() {
        let s = |v: &[&str]| v.iter().map(|w| w.to_string()).collect::<Vec<_>>();
        let release = "#!/bin/sh\nunset LD_PRELOAD\nexec \"/home/u/.local/bin/foxreach\" host\n";
        let runs = wrapper_runs("/w", release).unwrap();
        assert_eq!(runs, s(&["/home/u/.local/bin/foxreach", "host"]));
        assert_eq!(cli_command(&runs), Some(s(&["/home/u/.local/bin/foxreach"])));
        let checkout = "#!/bin/sh\nunset LD_PRELOAD\nexec \"/home/u/.bun/bin/bun\" \"/home/u/src/foxreach/bridge/host.mjs\"\n";
        let runs = wrapper_runs("/w", checkout).unwrap();
        assert_eq!(cli_command(&runs), Some(s(&["/home/u/.bun/bin/bun", "/home/u/src/foxreach/bridge/cli.mjs"])));
        // Quoted parts stay whole; a checkout run without a runtime word is the script alone.
        assert_eq!(words(r#"'/a b/foxreach' "" host"#), s(&["/a b/foxreach", "", "host"]));
        assert_eq!(cli_command(&s(&["/c d/bridge/mcp.mjs"])), Some(s(&["/c d/bridge/cli.mjs"])));
        // The manifest naming the program itself, not a script.
        assert_eq!(wrapper_runs("/opt/foxreach-x", "\x7fELF..."), Some(s(&["/opt/foxreach-x"])));
        // Not a foxreach: another program, a script without an exec line, a lookalike folder.
        assert_eq!(cli_command(&s(&["/usr/bin/python3", "host.py"])), None);
        assert_eq!(wrapper_runs("/w", "#!/bin/sh\necho hi\n"), None);
        assert_eq!(cli_command(&s(&["/x/node", "/c/mybridge/cli.mjs"])), None);
        assert_eq!(cli_command(&[]), None);
    }

    #[test]
    fn a_newer_status_format_asks_for_a_newer_app() {
        // Shaped so it would not parse as this app's Status: the format must be what is reported.
        let err = parse_status(r#"{"format":2,"mcp":{"command":"x"}}"#).unwrap_err();
        assert!(err.contains("newer than this app reads") && err.contains("format 2") && err.contains("Update foxreach manager"), "{err}");
        let fits = r#"{"format":2,"mcpCommand":"x","agents":[],"host":{"wrapper":null,"wrapperRuns":null,"manifests":[],"flatpaks":[]}}"#;
        assert!(parse_status(fits).unwrap_err().contains("format 2"), "a newer report that happens to parse is not read either");
        assert!(err.starts_with(NEWER_NOTE) && parse_status(fits).unwrap_err().starts_with(NEWER_NOTE), "snapshot tells newer from too old by this");
        let ok = r#"{"mcpCommand":"x","agents":[],"host":{"wrapper":null,"wrapperRuns":null,"manifests":[],"flatpaks":[]}}"#;
        assert_eq!(parse_status(ok).unwrap().format, 1, "absent means 1");
        assert!(parse_status(r#"{"format":1}"#).unwrap_err().starts_with("foxreach status --json: "));
    }

    #[test]
    fn flatpak_info_app_path() {
        let info = "[Application]\nname=x\n\n[Instance]\ninstance-id=1\napp-path=/var/lib/flatpak/app/x/current/active/files\n";
        assert_eq!(ini_value(info, "Instance", "app-path").as_deref(), Some("/var/lib/flatpak/app/x/current/active/files"));
        assert_eq!(ini_value(info, "Application", "app-path"), None);
    }

    #[test]
    fn status_json_parses() {
        let text = r#"{"format":1,"version":"0.3.3","compiled":true,"program":"/h/.local/bin/foxreach","mcpCommand":"/h/.local/bin/foxreach mcp",
          "agents":[{"id":"codex","name":"Codex","also":null,"file":"/h/.codex/config.toml","found":true,"set":true,"command":"x","error":null}],
          "host":{"wrapper":null,"wrapperRuns":null,"manifests":[],"flatpaks":[{"id":"org.mozilla.firefox","installed":true,"prepared":false}]},
          "browsers":[{"name":"Firefox","packaging":"flatpak","spec":"flatpak:org.mozilla.firefox","profiles":[
            {"name":"default","path":"/p","isDefault":true,"inUse":false,"addon":{"version":"0.3.2","active":true,"file":true,"full":false}}]}]}"#;
        let s: Status = serde_json::from_str(text).unwrap();
        assert_eq!(s.agents[0].id, "codex");
        assert!(s.setup.is_none(), "a program from before setup still parses");
        let with_setup = text.replacen(r#""agents":"#, r#""setup":{"kind":"checkout","root":"/c","runtime":"/bun","command":["/bun","/c/bridge/cli.mjs"],"from":"browser","mixed":true},"agents":"#, 1);
        let s: Status = serde_json::from_str(&with_setup).unwrap();
        let setup = s.setup.unwrap();
        assert_eq!((setup.kind.as_str(), setup.root.as_deref(), setup.mixed), ("checkout", Some("/c"), true));
        assert_eq!(setup.command.unwrap(), ["/bun", "/c/bridge/cli.mjs"]);
        assert_eq!(s.browsers[0].profiles[0].addon.as_ref().unwrap().version.as_deref(), Some("0.3.2"));
    }
}

/* ---------- foxreach test --json ---------- */

/// Can an agent (or this program, `--self`) reach a browser right now?
#[derive(Clone, Debug, Default, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TestResult {
    pub ok: bool,
    pub browser: Option<String>,
    pub addon: Option<String>,
    pub warning: Option<String>,
    pub error: Option<String>,
}

/// `foxreach test --agents ID --json`, or `--self` when `agent` is None.
pub fn test(program: &[String], agent: Option<&str>) -> TestResult {
    let out = match agent {
        Some(id) => foxreach(program, &["test", "--agents", id, "--json"]),
        None => foxreach(program, &["test", "--self", "--json"]),
    };
    let fail = |e: String| TestResult { error: Some(e), ..Default::default() };
    if !out.ok {
        return fail(out.text.lines().last().unwrap_or("The test failed.").to_string());
    }
    match agent {
        Some(_) => serde_json::from_str::<Vec<TestResult>>(&out.text)
            .map_err(|e| e.to_string())
            .and_then(|v| v.into_iter().next().ok_or_else(|| String::from("No result.")))
            .unwrap_or_else(fail),
        None => serde_json::from_str::<TestResult>(&out.text).unwrap_or_else(|e| fail(e.to_string())),
    }
}
