#!/usr/bin/env python3
"""Find Firefox-based browsers on this machine, however they were installed, and start them.

Run:      uv run --python 3.15 scripts/browsers.py          lists what was found, one per line
Expected: lines of "<name>  <packaging>  <spec>", exit 0; exit 1 if none was found.

A browser is named by a spec, which --browser and FOXREACH_BROWSER accept:
  /path/to/binary-or.AppImage    a distro package, a tarball, an AppImage, a wrapper script
  firefox-developer-edition      a command on PATH
  flatpak:<application id>       a Flatpak, for example flatpak:io.gitlab.librewolf-community

Found here: commands on PATH, AppImages in ~/AppImages, ~/Applications and ~/.local/bin, and installed
Flatpaks; on macOS, commands on PATH and apps in /Applications and ~/Applications (the spec is the
executable inside the app, such as /Applications/LibreWolf.app/Contents/MacOS/librewolf). Nothing
is executed to find them.
"""
import dataclasses
import os
import pathlib
import re
import shutil
import subprocess
import sys

HOME = pathlib.Path.home()
# FOXREACH_PLATFORM (linux or darwin) is for tests, which check each platform's rules on any machine.
MAC = (os.environ.get("FOXREACH_PLATFORM") or sys.platform) == "darwin"
# On macOS, profiles live in a folder of Application Support, and every build reads native-messaging
# manifests from Mozilla's folder there (LibreWolf too: checked with LibreWolf 160).
SUPPORT = "Library/Application Support"
MAC_HOSTS = f"{SUPPORT}/Mozilla/NativeMessagingHosts"


@dataclasses.dataclass(frozen=True)
class Kind:
    """One browser family: how its builds are named and where they keep their files."""

    name: str
    commands: tuple       # executable names, as on PATH and in AppImage file names
    flatpaks: tuple       # Flatpak application ids
    profile_roots: tuple  # folders holding profiles.ini, relative to the home folder
    host_dirs: tuple      # extra folders read for native-messaging manifests, relative to home
    mac_roots: tuple = () # on macOS: folders holding profiles.ini, relative to Application Support

    def roots(self, home=HOME):
        """Folders holding this family's profiles.ini, under home (a Flatpak's own, on Linux)."""
        if MAC:
            return [home / SUPPORT / r for r in self.mac_roots]
        return [home / r for r in self.profile_roots]


# Every build reads ~/.mozilla/native-messaging-hosts (toolkit/xre/nsXREDirProvider.cpp hardcodes
# it, also for forks). host_dirs lists what a fork reads besides.
KINDS = (
    Kind("Firefox",
         ("firefox", "firefox-developer-edition", "firefox-devedition", "firefox-nightly", "firefox-beta",
          "firefox-esr", "firefox-bin"),
         ("org.mozilla.firefox",), (".mozilla/firefox", ".config/mozilla/firefox"), (), ("Firefox",)),
    Kind("LibreWolf", ("librewolf", "librewolf-bin"), ("io.gitlab.librewolf-community",),
         (".librewolf", ".config/librewolf/librewolf"), (".librewolf",), ("librewolf",)),
    Kind("Floorp", ("floorp",), ("one.ablaze.floorp",), (".floorp",), (), ("Floorp",)),
    Kind("Waterfox", ("waterfox",), ("net.waterfox.waterfox",), (".waterfox",), (), ("Waterfox",)),
    Kind("Zen", ("zen", "zen-browser", "zen-bin"), ("app.zen_browser.zen",), (".zen",), (), ("zen",)),
)
APPIMAGE_DIRS = ("AppImages", "Applications", ".local/bin")
MAC_APP_DIRS = (pathlib.Path("/Applications"), HOME / "Applications")


@dataclasses.dataclass(frozen=True)
class Browser:
    kind: Kind | None  # None: a path we were given and do not recognize
    packaging: str     # "native", "appimage", "flatpak", or "app" (a macOS app)
    spec: str          # what --browser takes to name this browser again

    @property
    def name(self):
        return self.kind.name if self.kind else pathlib.Path(self.spec).name

    @property
    def app_id(self):
        return self.spec.removeprefix("flatpak:") if self.packaging == "flatpak" else None

    @property
    def home(self):
        """The folder this build treats as home: a Flatpak keeps its files in its own."""
        return HOME / ".var/app" / self.app_id if self.app_id else HOME

    def profile_roots(self):
        return self.kind.roots(self.home) if self.kind else []

    def host_dirs(self):
        """Folders where this build looks for native-messaging manifests."""
        if MAC:
            return [HOME / MAC_HOSTS]
        extra = self.kind.host_dirs if self.kind else ()
        return [self.home / d / "native-messaging-hosts" for d in (".mozilla", *extra)]

    def default_profile(self):
        """The dedicated foxreach profile. A Flatpak can only open one inside its own folder."""
        return (self.home / "foxreach/profile") if self.app_id else HOME / ".local/share/foxreach/profile"

    def command(self, args, env=None):
        """argv that starts this browser with args; env reaches the browser and its native host."""
        env = env or {}
        if self.app_id:
            return ["flatpak", "run", *[f"--env={k}={v}" for k, v in env.items()], self.app_id, *map(str, args)]
        return [self.spec, *map(str, args)]

    def popen(self, args, env=None, **kwargs):
        # LD_PRELOAD from the caller's session can crash the browser; it is never meant for it.
        full = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"} | (env or {})
        return subprocess.Popen(self.command(args, env), env=full, **kwargs)

    def socket_dir(self):
        """Where a socket made by this browser's native host is also visible to agents: a Flatpak
        shares only this one folder with the rest of the session (see build_install.py)."""
        if not self.app_id:
            return None
        return pathlib.Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / "foxreach"


def mac_pids(profile):
    """The processes of macOS apps running this profile, of this user. A macOS build may relaunch
    itself (on the first start with a new add-on, say): the new process belongs to launchd, not to
    whoever started the browser, and finds its profile in XRE_PROFILE_PATH rather than its
    arguments. So an app's executable is matched by the profile it runs, as ps shows it with the
    environment (-E)."""
    out = subprocess.run(["ps", "-E", "-x", "-o", "pid=,command="], capture_output=True, text=True).stdout
    # The path ends at the end of the line, before the next variable (NAME=) or the next option:
    # "/p/a" must not match a profile at "/p/a b".
    end = r"(?=$| [A-Za-z_][A-Za-z0-9_]*=| -)"
    marks = [re.compile(re.escape(m) + end) for m in (f"XRE_PROFILE_PATH={profile}", f"-profile {profile}")]
    pids = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if not pid.isdigit() or int(pid) == os.getpid() or ".app/Contents/MacOS/" not in command.split(" -", 1)[0]:
            continue
        if any(m.search(command) for m in marks):
            pids.append(int(pid))
    return pids


def kind_of(name):
    """The family whose command name this file name carries, longest name first."""
    name = name.lower()
    pairs = sorted(((c, k) for k in KINDS for c in k.commands), key=lambda p: -len(p[0]))
    return next((k for c, k in pairs if name == c or name.startswith((c + ".", c + "-", c + "_"))), None)


def mac_app(path):
    """The executable of a macOS app bundle and its family, or None if it is not a known browser."""
    folder = path / "Contents/MacOS"
    for exe in sorted(folder.iterdir()) if folder.is_dir() else ():
        kind = kind_of(exe.name)
        if kind and exe.is_file() and os.access(exe, os.X_OK):
            return kind, exe
    return None


def flatpak_ids():
    if not shutil.which("flatpak"):
        return []
    out = subprocess.run(["flatpak", "list", "--app", "--columns=application"], capture_output=True, text=True)
    return out.stdout.split() if out.returncode == 0 else []


def find():
    """Every browser found, in the order PATH, AppImages, Flatpaks. One entry per file."""
    found, seen = [], set()

    def add(kind, packaging, spec):
        real = os.path.realpath(spec) if packaging != "flatpak" else spec
        if real not in seen:
            seen.add(real)
            found.append(Browser(kind, packaging, str(spec)))

    for kind in KINDS:
        for command in kind.commands:
            path = shutil.which(command)
            if path:
                add(kind, "appimage" if path.lower().endswith(".appimage") else "native", path)
    for folder in MAC_APP_DIRS if MAC else ():
        for path in sorted(folder.glob("*.app")):
            if found_app := mac_app(path):
                add(found_app[0], "app", found_app[1])
    for folder in () if MAC else APPIMAGE_DIRS:
        for path in sorted((HOME / folder).glob("*")):
            if path.name.lower().endswith(".appimage") and path.is_file() and kind_of(path.name):
                add(kind_of(path.name), "appimage", path)
    installed = flatpak_ids()
    for kind in KINDS:
        for app in kind.flatpaks:
            if app in installed:
                add(kind, "flatpak", f"flatpak:{app}")
    return found


def resolve(spec):
    """The browser a spec names. Exits with the reason if it names nothing usable."""
    spec = str(spec)
    if spec.startswith("flatpak:"):
        app = spec.removeprefix("flatpak:")
        if app not in flatpak_ids():
            raise SystemExit(f"No Flatpak {app} is installed.")
        return Browser(next((k for k in KINDS if app in k.flatpaks), None), "flatpak", spec)
    if MAC and spec.rstrip("/").endswith(".app") and (found_app := mac_app(pathlib.Path(spec).expanduser())):
        return Browser(found_app[0], "app", str(found_app[1]))
    path = shutil.which(os.path.expanduser(spec))
    if not path:
        raise SystemExit(f"No browser at {spec}: not a file that can be run, and not a command on PATH.")
    path = os.path.abspath(path)
    packaging = "app" if ".app/Contents/MacOS/" in path else "appimage" if path.lower().endswith(".appimage") else "native"
    return Browser(kind_of(os.path.basename(path)), packaging, path)


def choose(spec=None, profile=None):
    """The browser to use: the one named, else FOXREACH_BROWSER, else the one this profile belongs
    to, else the only one installed. Exits listing the choices when that is not decided."""
    spec = spec or os.environ.get("FOXREACH_BROWSER")
    if spec:
        return resolve(spec)
    found = find()
    if not found:
        raise SystemExit("No Firefox-based browser was found. Name one with --browser (see scripts/browsers.py).")
    if profile:
        owners = [b for b in found if any(profile.is_relative_to(r) for r in [*b.profile_roots(), b.home / "foxreach"])
                  and (b.app_id or not profile.is_relative_to(HOME / ".var/app"))]
        kinds = {b.kind for b in owners}
        if len(kinds) == 1:
            return owners[0]
    if len({(b.kind, b.app_id) for b in found}) == 1:
        return found[0]
    raise SystemExit("More than one browser was found. Name one with --browser:\n" + listing(found))


def listing(found):
    return "\n".join(f"  {b.name:10} {b.packaging:9} {b.spec}" for b in found)


if __name__ == "__main__":
    browsers = find()
    print(listing(browsers) if browsers else "No Firefox-based browser was found.")
    sys.exit(0 if browsers else 1)
