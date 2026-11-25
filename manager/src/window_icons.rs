// SPDX-License-Identifier: MIT
//! The six icons libcosmic's own widgets look up by name in the icon theme, with no way for the
//! app to pass its own picture: the header bar's minimize, maximize, restore and close buttons,
//! the close button of an error banner and of a toast, and the tab bar's scroll arrows. They are
//! drawn for the app (resources/ui/), compiled in, and at start written out as a tiny private icon
//! theme that libcosmic is pointed at, so they look the same on every desktop and are never blank.
//!
//! How libcosmic finds a named icon (libcosmic `src/widget/icon/named.rs`, `Named::path`, and
//! pop-os/freedesktop-icons, both at the commits in Cargo.lock):
//! - it asks the theme set as libcosmic's default (`Settings::default_icon_theme`), then "Cosmic";
//! - the folders holding themes are read once, from XDG_DATA_DIRS, XDG_DATA_HOME and HOME, at the
//!   first lookup, and kept for the life of the process (`BASE_PATHS` and `THEMES` are LazyLocks);
//! - a theme's `Inherits` is followed one level only, then Cosmic, hicolor, gnome and Yaru.
//!
//! So the theme is written to a folder of the app's own, that folder is put in front of
//! XDG_DATA_DIRS only while the six names are looked up once (which makes the lookup read its
//! folders), and the variable is then put back exactly as it was, before anything else runs: no
//! program the app starts ever sees the change. The theme inherits the desktop's theme and that
//! theme's own parents, so any other name libcosmic asks for is found where it was before.

use std::ffi::{OsStr, OsString};
use std::io;
use std::path::{Path, PathBuf};

use crate::host;

/// The private theme's name and folder name: the app ID, so no installed theme has it.
const THEME: &str = crate::APP_ID;

/// The one folder of icons in the theme, as named in index.theme.
const DIR: &str = "scalable/actions";

/// The six drawings, by the file name the lookup expects (the icon name plus ".svg").
const ICONS: [(&str, &[u8]); 6] = [
    ("window-minimize-symbolic.svg", include_bytes!("../resources/ui/window-minimize-symbolic.svg")),
    ("window-maximize-symbolic.svg", include_bytes!("../resources/ui/window-maximize-symbolic.svg")),
    ("window-restore-symbolic.svg", include_bytes!("../resources/ui/window-restore-symbolic.svg")),
    ("window-close-symbolic.svg", include_bytes!("../resources/ui/window-close-symbolic.svg")),
    ("go-previous-symbolic.svg", include_bytes!("../resources/ui/go-previous-symbolic.svg")),
    ("go-next-symbolic.svg", include_bytes!("../resources/ui/go-next-symbolic.svg")),
];

/// What `prepare` did: the theme to give libcosmic, and one line for `--check`.
pub struct Prepared {
    pub theme: Option<String>,
    pub summary: String,
}

/// Writes the private theme and makes libcosmic's lookup find it. Must run before any thread is
/// started, since it changes XDG_DATA_DIRS for a moment. When anything fails, the result is what
/// the app did before these icons were built in: the desktop's theme, found by
/// `fallback_icon_theme`, with the six names blank where that theme lacks them.
pub fn prepare() -> Prepared {
    let fallback = fallback_icon_theme();
    let fall_back = |why: String| Prepared { summary: format!("the desktop's icon theme ({}): {why}", fallback.as_deref().unwrap_or("libcosmic's default")), theme: fallback.clone() };
    let Some(base) = private_base() else {
        return fall_back(String::from("no absolute XDG_DATA_HOME or HOME"));
    };
    let theme_dir = base.join("icons").join(THEME);
    // What libcosmic would have asked for without this theme: the desktop's theme outside COSMIC,
    // else COSMIC's configured one (Cosmic unless changed).
    let desktop = fallback.clone().unwrap_or_else(cosmic::config::icon_theme);
    let inherits = inherits(&desktop, &theme_bases());
    if let Err(e) = write_theme(&theme_dir, &theme_files(&inherits)) {
        return fall_back(format!("cannot write {}: {e}", theme_dir.display()));
    }
    // The theme becomes libcosmic's default only once with_data_dir has seen that this process has
    // one thread: when it has more, the desktop's theme stays, as it does for every other failure.
    let probed = match with_data_dir(&base, || {
        cosmic::icon_theme::set_default(THEME);
        probe()
    }) {
        Ok(found) => found,
        Err(threads) => return fall_back(format!("{threads} threads running at start")),
    };
    let missed: Vec<String> = probed
        .into_iter()
        .filter(|(_, path)| !path.as_deref().is_some_and(|p| p.starts_with(&theme_dir)))
        .map(|(name, path)| format!("{name} -> {}", path.map_or_else(|| String::from("nothing"), |p| p.display().to_string())))
        .collect();
    if !missed.is_empty() {
        return fall_back(format!("the lookup did not use {}: {}", theme_dir.display(), missed.join(", ")));
    }
    Prepared { theme: Some(THEME.to_string()), summary: format!("{} (then {})", theme_dir.display(), inherits.join(", ")) }
}

/// Looks each of the six names up as libcosmic's widgets do (the header bar, banner and toast at
/// no size, the tab bar's arrows at 16), and returns where each was found.
fn probe() -> Vec<(String, Option<PathBuf>)> {
    let mut found = Vec::new();
    for (file, _) in ICONS {
        let name = file.trim_end_matches(".svg");
        for size in [None, Some(16)] {
            let mut named = cosmic::widget::icon::from_name(name);
            if let Some(size) = size {
                named = named.size(size);
            }
            found.push((name.to_string(), named.path()));
        }
    }
    found
}

/// Runs `f` with `base` in front of XDG_DATA_DIRS, then puts the variable back as it was (also
/// when `f` panics). Enforces the single-thread precondition internally so callers cannot mutate
/// the environment while another thread may read it.
fn with_data_dir<T>(base: &Path, f: impl FnOnce() -> T) -> Result<T, usize> {
    let threads = std::fs::read_dir("/proc/self/task").map(Iterator::count).unwrap_or(0);
    if threads != 1 {
        return Err(threads);
    }
    struct Restore(Option<OsString>);
    impl Drop for Restore {
        fn drop(&mut self) {
            // SAFETY: checked `threads == 1` above before mutating the environment, and `f`
            // (icon lookup) does not spawn threads.
            unsafe {
                match &self.0 {
                    Some(v) => std::env::set_var("XDG_DATA_DIRS", v),
                    None => std::env::remove_var("XDG_DATA_DIRS"),
                }
            }
        }
    }
    let original = std::env::var_os("XDG_DATA_DIRS");
    let mut dirs = vec![base.to_path_buf()];
    dirs.extend(data_dirs(original.as_deref()));
    // A folder with a colon in its path cannot be listed; the lookup then just misses the theme.
    let Ok(joined) = std::env::join_paths(dirs) else {
        return Ok(f());
    };
    let _restore = Restore(original);
    // SAFETY: setting a variable is only unsound while another thread may read the environment;
    // verified `threads == 1` directly above, and `Restore` puts the original value back on exit.
    unsafe { std::env::set_var("XDG_DATA_DIRS", &joined) };
    Ok(f())
}

/// XDG_DATA_DIRS as the lookup reads it (the xdg crate): its absolute entries, or
/// /usr/local/share and /usr/share when it is unset or has none.
fn data_dirs(value: Option<&OsStr>) -> Vec<PathBuf> {
    let dirs: Vec<PathBuf> = value.map(|v| std::env::split_paths(v).filter(|p| p.is_absolute()).collect()).unwrap_or_default();
    if dirs.is_empty() { vec![PathBuf::from("/usr/local/share"), PathBuf::from("/usr/share")] } else { dirs }
}

/// The app's own data folder, which only this app writes to: $XDG_DATA_HOME/<app id>
/// (~/.local/share/dev.nilltadios.FoxreachManager; in the Flatpak ~/.var/app/<app id>/data/<app id>).
fn private_base() -> Option<PathBuf> {
    let data_home = std::env::var_os("XDG_DATA_HOME").map(PathBuf::from).filter(|p| p.is_absolute()).or_else(|| {
        let home = host::home();
        (home != Path::new("/")).then(|| home.join(".local/share"))
    })?;
    Some(data_home.join(crate::APP_ID))
}

/// The folders the lookup takes themes from, in its order (data dirs, data home, ~/.icons), for
/// reading the desktop theme's own index.theme.
fn theme_bases() -> Vec<PathBuf> {
    let home = host::home();
    let data_home = std::env::var_os("XDG_DATA_HOME").map(PathBuf::from).filter(|p| p.is_absolute()).unwrap_or_else(|| home.join(".local/share"));
    let mut bases: Vec<PathBuf> = data_dirs(std::env::var_os("XDG_DATA_DIRS").as_deref()).iter().map(|d| d.join("icons")).collect();
    bases.push(data_home.join("icons"));
    bases.push(home.join(".icons"));
    bases
}

/// The private theme's Inherits: the desktop's theme, then the themes that one inherits (read from
/// the first index.theme found, as the lookup does), because the lookup follows one level only.
fn inherits(desktop: &str, bases: &[PathBuf]) -> Vec<String> {
    let usable = |n: &str| !n.is_empty() && n != THEME && !n.contains([',', '/', '\n', '\r']);
    let mut list: Vec<String> = Vec::new();
    if usable(desktop) {
        list.push(desktop.to_string());
    }
    let parents = bases
        .iter()
        .find_map(|b| std::fs::read_to_string(b.join(desktop).join("index.theme")).ok())
        .and_then(|text| host::ini_value(&text, "Icon Theme", "Inherits"))
        .unwrap_or_default();
    for parent in parents.split(',').map(str::trim) {
        if usable(parent) && !list.iter().any(|n| n == parent) {
            list.push(parent.to_string());
        }
    }
    list
}

/// The theme's files, by path inside its folder: index.theme and the six SVGs.
fn theme_files(inherits: &[String]) -> Vec<(String, Vec<u8>)> {
    // [Icon Theme] first, a Size in every folder section and a newline at the end: the lookup's
    // parser skips a theme or a folder otherwise (freedesktop-icons src/theme/parse.rs).
    let index = format!(
        "[Icon Theme]\nName=foxreach manager window icons\nComment=Written by foxreach manager at start; changed files are written again\n\
         Inherits={}\nDirectories={DIR}\nHidden=true\n\n[{DIR}]\nContext=Actions\nSize=16\nMinSize=8\nMaxSize=512\nType=Scalable\n",
        inherits.join(",")
    );
    let mut files = vec![(String::from("index.theme"), index.into_bytes())];
    files.extend(ICONS.iter().map(|(name, svg)| (format!("{DIR}/{name}"), svg.to_vec())));
    files
}

/// Writes `files` under `dir`: only those whose content differs (through a temporary file and a
/// rename, so a lookup never reads half a file), and removes anything else in `dir`, such as an
/// icon an older version had. Returns whether anything changed.
fn write_theme(dir: &Path, files: &[(String, Vec<u8>)]) -> io::Result<bool> {
    let mut changed = false;
    for (rel, data) in files {
        let path = dir.join(rel);
        if std::fs::read(&path).is_ok_and(|old| old == *data) {
            continue;
        }
        std::fs::create_dir_all(path.parent().unwrap_or(dir))?;
        let tmp = path.with_extension(format!("tmp-{}", std::process::id()));
        std::fs::write(&tmp, data)?;
        std::fs::rename(&tmp, &path)?;
        changed = true;
    }
    changed |= remove_others(dir, Path::new(""), files)?;
    Ok(changed)
}

/// Removes what lies under `dir`/`rel` and is neither one of `files` nor a folder holding one.
fn remove_others(dir: &Path, rel: &Path, files: &[(String, Vec<u8>)]) -> io::Result<bool> {
    let mut changed = false;
    for entry in std::fs::read_dir(dir.join(rel))? {
        let entry = entry?;
        let rel = rel.join(entry.file_name());
        let wanted_file = files.iter().any(|(f, _)| Path::new(f) == rel);
        let wanted_dir = files.iter().any(|(f, _)| Path::new(f).starts_with(&rel) && Path::new(f) != rel);
        // symlink_metadata, so a link is removed as a link and never followed out of the folder.
        let is_dir = entry.path().symlink_metadata()?.is_dir();
        if is_dir && wanted_dir {
            changed |= remove_others(dir, &rel, files)?;
        } else if !(wanted_file && !is_dir) {
            if is_dir { std::fs::remove_dir_all(entry.path())? } else { std::fs::remove_file(entry.path())? }
            changed = true;
        }
    }
    Ok(changed)
}

/// The theme libcosmic used before the app had its own: the desktop's when COSMIC's is missing
/// (KDE's, GTK's, Breeze, Adwaita), or none, which leaves COSMIC's configured one. The private
/// theme inherits it, and it is used as before when the private theme cannot be set up.
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

#[cfg(test)]
mod tests {
    use super::{DIR, ICONS, THEME, data_dirs, inherits, theme_files, write_theme};
    use crate::host::ini_value;
    use std::path::{Path, PathBuf};

    /// A fresh empty folder for one test, removed by the test at its end.
    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("fxm-window-icons-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// Every file under `dir`, as paths inside it, sorted.
    fn listing(dir: &Path) -> Vec<String> {
        fn walk(dir: &Path, rel: &Path, out: &mut Vec<String>) {
            for e in std::fs::read_dir(dir.join(rel)).unwrap() {
                let rel = rel.join(e.unwrap().file_name());
                if dir.join(&rel).is_dir() { walk(dir, &rel, out) } else { out.push(rel.to_string_lossy().into_owned()) }
            }
        }
        let mut out = Vec::new();
        walk(dir, Path::new(""), &mut out);
        out.sort();
        out
    }

    #[test]
    fn the_theme_holds_the_six_icons_and_an_index_the_lookup_reads() {
        let files = theme_files(&[String::from("breeze-dark"), String::from("breeze")]);
        let mut names: Vec<&str> = files.iter().map(|(f, _)| f.as_str()).collect();
        names.sort_unstable();
        let mut expected: Vec<String> = ["window-minimize", "window-maximize", "window-restore", "window-close", "go-previous", "go-next"]
            .iter()
            .map(|n| format!("{DIR}/{n}-symbolic.svg"))
            .collect();
        expected.push(String::from("index.theme"));
        expected.sort();
        assert_eq!(names, expected);
        for (name, svg) in ICONS {
            let text = std::str::from_utf8(svg).unwrap();
            assert!(text.starts_with("<svg") && text.contains("viewBox=\"0 0 16 16\""), "{name} is not a 16 px SVG");
        }
        let index = String::from_utf8(files[0].1.clone()).unwrap();
        // The lookup's parser: [Icon Theme] first, every folder section with a Size, and the last
        // line ended by a newline (an unterminated last line is never read).
        assert!(index.starts_with("[Icon Theme]\n") && index.ends_with('\n'));
        assert_eq!(ini_value(&index, "Icon Theme", "Inherits").as_deref(), Some("breeze-dark,breeze"));
        assert_eq!(ini_value(&index, "Icon Theme", "Directories").as_deref(), Some(DIR));
        assert_eq!(ini_value(&index, DIR, "Size").as_deref(), Some("16"));
        assert_eq!(ini_value(&index, DIR, "Type").as_deref(), Some("Scalable"));
        let sections: Vec<&str> = index.lines().filter(|l| l.starts_with('[')).collect();
        assert_eq!(sections, ["[Icon Theme]", "[scalable/actions]"]);
    }

    #[test]
    fn writing_twice_changes_nothing() {
        let dir = scratch("twice").join(THEME);
        let files = theme_files(&[String::from("breeze")]);
        assert!(write_theme(&dir, &files).unwrap(), "the first write wrote nothing");
        let stamp = |p: &str| std::fs::metadata(dir.join(p)).unwrap().modified().unwrap();
        let before: Vec<_> = files.iter().map(|(f, _)| stamp(f)).collect();
        assert!(!write_theme(&dir, &files).unwrap(), "the second write changed something");
        let after: Vec<_> = files.iter().map(|(f, _)| stamp(f)).collect();
        assert_eq!(before, after, "a file was rewritten although it was the same");
        for (f, data) in &files {
            assert_eq!(&std::fs::read(dir.join(f)).unwrap(), data);
        }
        std::fs::remove_dir_all(dir.parent().unwrap()).unwrap();
    }

    #[test]
    fn a_newer_set_replaces_an_older_one() {
        let dir = scratch("newer").join(THEME);
        // An older version: other content, another parent theme, an icon no longer drawn, a folder
        // no longer used and a temporary file left by a crash.
        let mut old = theme_files(&[String::from("Adwaita")]);
        old[1].1 = b"<svg/>".to_vec();
        old.push((format!("{DIR}/gone-symbolic.svg"), b"<svg/>".to_vec()));
        old.push((String::from("16x16/actions/gone-symbolic.svg"), b"<svg/>".to_vec()));
        old.push((String::from("index.tmp-1"), Vec::new()));
        write_theme(&dir, &old).unwrap();
        let new = theme_files(&[String::from("breeze")]);
        assert!(write_theme(&dir, &new).unwrap());
        let mut expected: Vec<String> = new.iter().map(|(f, _)| f.clone()).collect();
        expected.sort();
        assert_eq!(listing(&dir), expected);
        for (f, data) in &new {
            assert_eq!(&std::fs::read(dir.join(f)).unwrap(), data, "{f} is still the old one");
        }
        std::fs::remove_dir_all(dir.parent().unwrap()).unwrap();
    }

    #[test]
    fn an_unwritable_folder_is_an_error_not_a_panic() {
        let root = scratch("unwritable");
        // A file where the theme's folder would go.
        std::fs::write(root.join(THEME), b"").unwrap();
        assert!(write_theme(&root.join(THEME), &theme_files(&[])).is_err());
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn the_desktop_theme_and_its_own_parents_are_inherited() {
        let root = scratch("inherits");
        let (first, second) = (root.join("a"), root.join("b"));
        for (base, inherits) in [(&first, "breeze,hicolor"), (&second, "Adwaita")] {
            std::fs::create_dir_all(base.join("breeze-dark")).unwrap();
            std::fs::write(base.join("breeze-dark/index.theme"), format!("[Icon Theme]\nName=x\nInherits={inherits}\n")).unwrap();
        }
        // The first index.theme found counts, as in the lookup.
        assert_eq!(inherits("breeze-dark", &[first.clone(), second.clone()]), ["breeze-dark", "breeze", "hicolor"]);
        assert_eq!(inherits("breeze-dark", &[root.join("none"), second]), ["breeze-dark", "Adwaita"]);
        assert_eq!(inherits("Cosmic", &[first]), ["Cosmic"]);
        assert_eq!(inherits(THEME, &[]), Vec::<String>::new());
        std::fs::remove_dir_all(&root).unwrap();
    }

    #[test]
    fn data_dirs_are_read_as_the_lookup_reads_them() {
        let default = [PathBuf::from("/usr/local/share"), PathBuf::from("/usr/share")];
        assert_eq!(data_dirs(None), default);
        assert_eq!(data_dirs(Some("".as_ref())), default);
        assert_eq!(data_dirs(Some("relative:also/relative".as_ref())), default);
        assert_eq!(data_dirs(Some("/a:rel:/b".as_ref())), [PathBuf::from("/a"), PathBuf::from("/b")]);
    }
}
