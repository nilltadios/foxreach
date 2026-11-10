#!/usr/bin/env python3
"""Find Firefox-based browsers on this machine, however they were installed, and start them.

Run:      uv run --python 3.15 scripts/browsers.py          lists what was found, one per line
Expected: lines of "<name>  <packaging>  <spec>", exit 0; exit 1 if none was found.

A browser is named by a spec, which --browser and FOXREACH_BROWSER accept:
  /path/to/binary-or.AppImage    a distro package, a tarball, an AppImage, a wrapper script
  firefox-developer-edition      a command on PATH
  flatpak:<application id>       a Flatpak, for example flatpak:io.gitlab.librewolf-community

Found here: commands on PATH, AppImages in ~/AppImages, ~/Applications and ~/.local/bin, and installed
Flatpaks. Nothing is executed to find them. Linux only.
"""
import dataclasses
import os
import pathlib
import shutil
import subprocess
import sys

HOME = pathlib.Path.home()


@dataclasses.dataclass(frozen=True)
class Kind:
    """One browser family: how its builds are named and where they keep their files."""

    name: str
    commands: tuple       # executable names, as on PATH and in AppImage file names
    flatpaks: tuple       # Flatpak application ids
    profile_roots: tuple  # folders holding profiles.ini, relative to the home folder
    host_dirs: tuple      # extra folders read for native-messaging manifests, relative to home


# Every build reads ~/.mozilla/native-messaging-hosts (toolkit/xre/nsXREDirProvider.cpp hardcodes
# it, also for forks). host_dirs lists what a fork reads besides.
KINDS = (
    Kind("Firefox",
         ("firefox", "firefox-developer-edition", "firefox-devedition", "firefox-nightly", "firefox-beta",
          "firefox-esr", "firefox-bin"),
         ("org.mozilla.firefox",), (".mozilla/firefox", ".config/mozilla/firefox"), ()),
    Kind("LibreWolf", ("librewolf", "librewolf-bin"), ("io.gitlab.librewolf-community",),
         (".librewolf", ".config/librewolf/librewolf"), (".librewolf",)),
    Kind("Floorp", ("floorp",), ("one.ablaze.floorp",), (".floorp",), ()),
    Kind("Waterfox", ("waterfox",), ("net.waterfox.waterfox",), (".waterfox",), ()),
    Kind("Zen", ("zen", "zen-browser", "zen-bin"), ("app.zen_browser.zen",), (".zen",), ()),
)
APPIMAGE_DIRS = ("AppImages", "Applications", ".local/bin")


@dataclasses.dataclass(frozen=True)
class Browser:
    kind: Kind | None  # None: a path we were given and do not recognize
    packaging: str     # "native", "appimage" or "flatpak"
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
        return [self.home / r for r in (self.kind.profile_roots if self.kind else ())]

    def host_dirs(self):
        """Folders where this build looks for native-messaging manifests."""
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


def kind_of(name):
    """The family whose command name this file name carries, longest name first."""
    name = name.lower()
    pairs = sorted(((c, k) for k in KINDS for c in k.commands), key=lambda p: -len(p[0]))
    return next((k for c, k in pairs if name == c or name.startswith((c + ".", c + "-", c + "_"))), None)


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
    for folder in APPIMAGE_DIRS:
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
    path = shutil.which(os.path.expanduser(spec))
    if not path:
        raise SystemExit(f"No browser at {spec}: not a file that can be run, and not a command on PATH.")
    path = os.path.abspath(path)
    return Browser(kind_of(os.path.basename(path)), "appimage" if path.lower().endswith(".appimage") else "native", path)


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
