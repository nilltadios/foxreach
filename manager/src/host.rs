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

/// The foxreach program shipped inside this app: (path the host sees, path this process sees).
pub fn bundled_program() -> Option<(PathBuf, PathBuf)> {
    if let Some(p) = std::env::var_os("FOXREACH_MANAGER_BUNDLED") {
        let p = PathBuf::from(p);
        return p.is_file().then(|| (p.clone(), p));
    }
    const REL: &str = "share/foxreach-manager/foxreach";
    match packaging() {
        Packaging::Flatpak { app_path } => {
            let local = Path::new("/app").join(REL);
            local.is_file().then(|| (app_path.join(REL), local))
        }
        Packaging::AppImage(_) => {
            let appdir = std::env::var_os("APPDIR").map(PathBuf::from)?;
            let local = appdir.join(REL);
            local.is_file().then(|| (local.clone(), local))
        }
        Packaging::Plain => None,
    }
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

/// Put the bundled program at ~/.local/bin/foxreach (the old one kept as foxreach.previous), with
/// host tools so it works the same from a Flatpak. Returns what was done, line by line.
pub fn install_bundled(host_src: &Path) -> Output {
    let dest = install_path();
    let dir = dest.parent().unwrap_or(Path::new("/")).to_string_lossy().into_owned();
    let dest_s = dest.to_string_lossy().into_owned();
    let tmp = format!("{dir}/.foxreach-manager-new");
    let mut log = Vec::new();
    let steps: Vec<Vec<String>> = {
        let mut s = vec![vec!["mkdir".into(), "-p".into(), dir.clone()]];
        s.push(vec!["cp".into(), "-f".into(), host_src.to_string_lossy().into_owned(), tmp.clone()]);
        s.push(vec!["chmod".into(), "755".into(), tmp.clone()]);
        s.push(vec![tmp.clone(), "version".into()]);
        s
    };
    for step in &steps {
        let out = run(step);
        if !out.ok {
            run(&["rm".into(), "-f".into(), tmp.clone()]);
            return Output { ok: false, text: format!("{}: {}\nNothing was changed.", step.join(" "), out.text) };
        }
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
    log.push(format!("installed {dest_s}"));
    Output { ok: true, text: log.join("\n") }
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
    pub mcp_command: String,
    pub agents: Vec<Agent>,
    pub host: HostSide,
    #[serde(default)]
    pub browsers: Vec<Browser>,
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
}

#[derive(Clone, Debug, Deserialize)]
pub struct Latest {
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
    /// The installed program and its version, when it runs.
    pub installed: Option<(Vec<String>, String)>,
    /// The bundled program: host path and version.
    pub bundled: Option<(PathBuf, String)>,
    pub status: Option<Status>,
    /// Why there is no status, or what it came from.
    pub note: Option<String>,
}

pub fn snapshot() -> Snapshot {
    let program = program_command();
    let installed = version_of(&program).map(|v| (program.clone(), v));
    // Run by its host path, like every other command: the host sees an AppImage's mount and a
    // Flatpak's files, and the environment is cleaned the same way.
    let bundled = bundled_program().and_then(|(host, _)| version_of(&[host.to_string_lossy().into_owned()]).map(|v| (host, v)));
    let mut snap = Snapshot { installed: installed.clone(), bundled: bundled.clone(), status: None, note: None };
    // Read with the installed program; without one (or one too old for `status`), with the
    // bundled one, which only reads.
    let mut readers: Vec<(Vec<String>, &str)> = Vec::new();
    if installed.is_some() {
        readers.push((program, "installed"));
    }
    if let Some((host, _)) = &bundled {
        readers.push((vec![host.to_string_lossy().into_owned()], "bundled"));
    }
    for (reader, which) in readers {
        let out = foxreach(&reader, &["status", "--json"]);
        match out.ok.then(|| serde_json::from_str::<Status>(&out.text)) {
            Some(Ok(status)) => {
                if which == "bundled" {
                    snap.note = Some(match &installed {
                        Some((_, v)) => format!("The installed foxreach ({v}) is too old for this app; shown with the bundled one."),
                        None => String::from("foxreach is not installed yet; shown with the bundled one."),
                    });
                }
                snap.status = Some(status);
                return snap;
            }
            Some(Err(e)) => snap.note = Some(format!("foxreach status --json: {e}")),
            None => snap.note = Some(format!("foxreach status --json failed: {}", out.text)),
        }
    }
    if snap.note.is_none() {
        snap.note = Some(String::from("No foxreach program found, and none is bundled with this build."));
    }
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
