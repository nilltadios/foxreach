#!/usr/bin/env python3
"""`foxreach status --json`, `agents remove --agents`, and `uninstall --part`: what the manager app uses.

Run:      uv run --python 3.15 test/status_json.py
Expected: every line PASS, exit 0.
How:      the real program (bun bridge/cli.mjs) with HOME pointed at a throwaway folder; agent
          programs are not run (FOXREACH_AGENT_CLI=0). No real settings file is read or written.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUN = shutil.which("bun") or str(pathlib.Path.home() / ".bun/bin/bun")
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


def run(home, *args, extra=None):
    env = {**(extra or {}), "HOME": str(home), "PATH": f"{home}/bin:/usr/bin:/bin", "FOXREACH_ADDON_UPDATES": (home / "updates.json").as_uri(),
           "FOXREACH_ADDON_UPDATES_FULL": (home / "updates-full.json").as_uri(), "FOXREACH_AGENT_CLI": "0", "XDG_RUNTIME_DIR": str(home / "run"),
           "FOXREACH_PROFILES": str(home / "profiles")}
    r = subprocess.run([BUN, str(ROOT / "bridge/cli.mjs"), *args], env=env, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)
    (home / ".codex").mkdir()
    (home / ".codex/config.toml").write_text('model = "x"\n')
    (home / ".config/Claude").mkdir(parents=True)
    (home / ".config/Claude/claude_desktop_config.json").write_text('{"preferences": {}}\n')

    code, out, err = run(home, "status", "--json")
    s = json.loads(out) if code == 0 else {}
    check("status --json exits 0 with one JSON object", code == 0 and s.get("format") == 1, err)
    rows = {a["id"]: a for a in s.get("agents", [])}
    check("status lists every agent with found/set", {"codex", "claude-desktop", "claude-code"} <= rows.keys()
          and rows["codex"]["found"] and not rows["codex"]["set"] and not rows["claude-code"]["found"], rows.get("codex"))
    check("status: no browser found yet", s.get("browsers") == [], s.get("browsers"))
    check("status: nothing installed yet", s.get("host", {}).get("wrapper") is None and s["host"]["manifests"] == [], s.get("host"))
    check("setup: none in use yet", s.get("setup", {}).get("kind") == "none", s.get("setup"))

    run(home, "install", "--no-flatpak", "--agents", "codex,claude-desktop")
    s = json.loads(run(home, "status", "--json")[1])
    rows = {a["id"]: a for a in s["agents"]}
    check("after install: both agents set, with the MCP command", rows["codex"]["set"] and rows["claude-desktop"]["set"]
          and rows["claude-desktop"]["command"] == s["mcpCommand"], rows["claude-desktop"])
    check("after install: wrapper and manifest", s["host"]["wrapper"] and len(s["host"]["manifests"]) == 1
          and s["host"]["wrapperRuns"] in s["mcpCommand"], s["host"])
    setup = s.get("setup", {})
    check("setup: a checkout, found from what the browser runs", setup.get("kind") == "checkout" and setup.get("root") == str(ROOT)
          and setup.get("from") == "browser" and setup.get("command", [""])[-1] == str(ROOT / "bridge/cli.mjs")
          and not setup.get("mixed"), setup)

    code, out, err = run(home, "agents", "remove", "--agents", "codex")
    rows = {a["id"]: a for a in json.loads(run(home, "status", "--json")[1])["agents"]}
    check("agents remove --agents takes out only that one", code == 0 and not rows["codex"]["set"] and rows["claude-desktop"]["set"], out + err)
    code, _, err = run(home, "agents", "remove", "--agents", "nosuch")
    check("agents remove: unknown name is an error", code == 1 and "Unknown agent" in err, err)

    code, out, err = run(home, "uninstall", "--part", "host")
    s = json.loads(run(home, "status", "--json")[1])
    check("uninstall --part host keeps the agents", code == 0 and s["host"]["wrapper"] is None and s["host"]["manifests"] == []
          and {a["id"]: a for a in s["agents"]}["claude-desktop"]["set"], out + err)
    fp = home / ".var/app/org.example.Browser/foxreach"
    fp.mkdir(parents=True)
    for n in ("foxreach", "foxreach-host"):
        (fp / n).write_text("x")
    (fp / "profile").mkdir()
    s = json.loads(run(home, "status", "--json")[1])
    check("status: a prepared Flatpak browser is listed", {"id": "org.example.Browser", "installed": False, "prepared": True}
          in s["host"]["flatpaks"], s["host"]["flatpaks"])
    code, out, err = run(home, "uninstall", "--part", "flatpak:org.example.Browser")
    check("uninstall --part flatpak:<id> removes that copy, keeps a profile beside it", code == 0
          and not (fp / "foxreach").exists() and not (fp / "foxreach-host").exists() and (fp / "profile").is_dir(), out + err)
    code, _, err = run(home, "uninstall", "--part", "bogus")
    check("uninstall: unknown part is an error", code == 1 and "Unknown part" in err, err)
    code, out, err = run(home, "uninstall", "--part", "agents")
    s = json.loads(run(home, "status", "--json")[1])
    check("uninstall --part agents", code == 0 and not any(a["set"] for a in s["agents"]), out + err)
    cfg = json.loads((home / ".config/Claude/claude_desktop_config.json").read_text())
    check("other settings survive", cfg.get("preferences") == {}, cfg)

    # The add-on: a stand-in firefox that records how it was started, two profiles.
    (home / "bin").mkdir()
    (home / "bin/firefox").write_text(f'#!/bin/sh\necho "$@" > {home}/opened\n')
    (home / "bin/firefox").chmod(0o755)
    root = home / ".mozilla/firefox"
    plain, full = root / "a.default", root / "b.dev"
    for p in (plain, full):
        (p / "extensions").mkdir(parents=True)
    (root / "profiles.ini").write_text("[Profile0]\nName=default\nIsRelative=1\nPath=a.default\nDefault=1\n\n"
                                       "[Profile1]\nName=dev\nIsRelative=1\nPath=b.dev\n")
    (full / "extensions/foxreach@foxreach.local.xpi").write_text("x")
    (full / "extensions.json").write_text(json.dumps({"addons": [{"id": "foxreach@foxreach.local", "version": "0.3.2",
                                                                  "location": "app-profile", "active": True}]}))
    (full / "user.js").write_text('user_pref("a", 1);\nuser_pref("extensions.experiments.enabled", true);\n'
                                  'user_pref("xpinstall.signatures.required", false);\n')
    (home / "updates.json").write_text(json.dumps({"addons": {"foxreach@foxreach.local": {"updates": [
        {"version": "0.3.2", "update_link": "https://example.invalid/foxreach-0.3.2-signed.xpi"},
        {"version": "0.3.10", "update_link": "https://example.invalid/foxreach-0.3.10-signed.xpi"}]}}}))
    (home / "updates-full.json").write_text(json.dumps({"addons": {"foxreach@foxreach.local": {"updates": [
        {"version": "0.3.9", "update_link": "https://example.invalid/foxreach-0.3.9.xpi"}]}}}))
    s = json.loads(run(home, "status", "--json")[1])
    ff = s["browsers"][0] if s["browsers"] else {}
    profs = {p["name"]: p for p in ff.get("profiles", [])}
    check("addon: browser and both profiles found", ff.get("spec") == str(home / "bin/firefox") and profs.keys() == {"default", "dev"}, s["browsers"])
    check("addon: versions per profile, full build marked", profs["default"]["addon"] is None and profs["dev"]["addon"]["version"] == "0.3.2"
          and profs["dev"]["addon"]["full"] and profs["default"]["isDefault"], profs)
    code, out, _ = run(home, "addon", "latest", "--json")
    check("addon latest: newest by version, not by order", code == 0 and json.loads(out)["version"] == "0.3.10", out)
    check("addon latest: the full build's release channel too", code == 0 and json.loads(out)["full"]["version"] == "0.3.9", out)
    code, out, err = run(home, "addon", "install", "--browser", str(home / "bin/firefox"), "--profile", str(plain))
    import time
    for _ in range(50):
        if (home / "opened").exists():
            break
        time.sleep(0.1)
    opened = (home / "opened").read_text().split() if (home / "opened").exists() else []
    check("addon install opens the signed file in that profile", code == 0 and opened == ["--profile", str(plain),
          "https://example.invalid/foxreach-0.3.10-signed.xpi"], f"{out}{err} {opened}")
    (home / "opened").unlink()
    code, out, err = run(home, "addon", "install", "--browser", str(home / "bin/firefox"), "--profile", str(full))
    for _ in range(50):
        if (home / "opened").exists():
            break
        time.sleep(0.1)
    opened = (home / "opened").read_text().split() if (home / "opened").exists() else []
    check("addon install on a full-build profile opens the full build of the latest release, not the signed one",
          code == 0 and opened == ["--profile", str(full), "https://example.invalid/foxreach-0.3.9.xpi"], f"{out}{err} {opened}")
    code, _, err = run(home, "addon", "install", "--browser", str(home / "bin/firefox"), "--profile", str(home))
    check("addon install refuses a folder that is no profile", code == 1 and "is not a profile" in err, err)
    (full / "prefs.js").write_text("")
    code, out, err = run(home, "addon", "stop", "--profile", str(full))
    stopped = {p["name"]: p for p in json.loads(run(home, "status", "--json")[1])["browsers"][0]["profiles"]}
    check("addon stop: agents stopped in that profile, the add-on kept", code == 0 and (full / "foxreach-agents-stopped").exists()
          and stopped["dev"]["addon"]["stopped"] and (full / "extensions/foxreach@foxreach.local.xpi").exists(), out + err)
    code, out, err = run(home, "addon", "start", "--profile", str(full))
    stopped = {p["name"]: p for p in json.loads(run(home, "status", "--json")[1])["browsers"][0]["profiles"]}
    check("addon start: allowed again", code == 0 and not (full / "foxreach-agents-stopped").exists()
          and not stopped["dev"]["addon"]["stopped"], out + err)
    code, _, err = run(home, "addon", "stop", "--profile", str(home / "bin"))
    check("addon stop refuses a folder that is no profile", code == 1 and "not a browser profile" in err, err)
    (full / "lock").symlink_to(f"127.0.0.1:+{os.getpid()}")
    code, _, err = run(home, "addon", "remove", "--profile", str(full))
    check("addon remove refuses an open profile", code == 1 and "in use" in err and (full / "extensions/foxreach@foxreach.local.xpi").exists(), err)
    (full / "lock").unlink()
    (home / "profiles").write_text(f"{full}\t0.3.2\t{home}/bin/firefox\n{plain}\t\t\n")
    (home / "AppImages").mkdir()
    (home / "AppImages/firefox.AppImage").symlink_to(home / "nowhere")
    code, out, _ = run(home, "status", "--json")
    check("status: a dangling AppImage link is skipped, not fatal", code == 0 and json.loads(out)["browsers"], out[-300:])
    code, out, err = run(home, "addon", "remove", "--profile", str(full))
    check("addon remove drops the profile from the record", f"{plain}" in (home / "profiles").read_text()
          and str(full) not in (home / "profiles").read_text(), (home / "profiles").read_text())
    js = (full / "user.js").read_text()
    check("addon remove: file and the two prefs gone, other prefs kept, backup made", code == 0
          and not (full / "extensions/foxreach@foxreach.local.xpi").exists() and js == 'user_pref("a", 1);\n'
          and any(full.glob("user.js.bak-*")), out + err + js)
    after = {p["name"]: p for p in json.loads(run(home, "status", "--json")[1])["browsers"][0]["profiles"]}
    check("addon remove: status shows it gone although the browser's extensions.json still lists it",
          after["dev"]["addon"] is None and "foxreach@foxreach.local" in (full / "extensions.json").read_text(), after["dev"])

    # foxreach test: a stand-in MCP server that answers status, and the real server with no browser.
    fake = home / "bin/fake-mcp"
    fake.write_text("""#!/usr/bin/env python3
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if m.get("id") == 1:
        print(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-06-18", "capabilities": {}}}), flush=True)
    elif m.get("id") == 2:
        open(sys.argv[0] + ".args", "w").write(json.dumps(m["params"].get("arguments")))
        text = json.dumps({"version": "9.9.9", "browser": {"name": "Firefox", "version": "160.0"}})
        print(json.dumps({"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": text}]}}), flush=True)
""")
    fake.chmod(0o755)
    (home / ".config/Claude/claude_desktop_config.json").write_text(json.dumps({"mcpServers": {"foxreach": {"command": str(fake), "args": []}}}))
    (home / ".codex/config.toml").write_text(f'[mcp_servers.foxreach]\ncommand = "{BUN}"\nargs = ["{ROOT}/bridge/cli.mjs", "mcp"]\n')
    env_sock = {"FOXREACH_SOCKET": str(home / "run/none.sock")}
    r = subprocess.run([BUN, str(ROOT / "bridge/cli.mjs"), "test", "--json"], capture_output=True, text=True, timeout=60,
                       env={"HOME": str(home), "PATH": f"{home}/bin:/usr/bin:/bin", "FOXREACH_AGENT_CLI": "0", **env_sock})
    rows = {x["id"]: x for x in json.loads(r.stdout)} if r.returncode == 0 else {}
    check("test: an agent whose server answers status reaches a browser", rows.get("claude-desktop", {}).get("ok") is True
          and rows["claude-desktop"]["addon"] == "9.9.9" and rows["claude-desktop"]["browser"] == "Firefox", r.stdout + r.stderr)
    check("test: the real server with no browser says why (TOML entry read)", rows.get("codex", {}).get("ok") is False
          and rows["codex"]["command"].endswith("cli.mjs mcp") and rows["codex"]["error"], rows.get("codex"))
    check("test: agents without foxreach are left out", set(rows) == {"claude-desktop", "codex"}, list(rows))
    sent = json.loads((home / "bin/fake-mcp.args").read_text()) if (home / "bin/fake-mcp.args").exists() else None
    check("test: asks status with ask false, so the browser prompts no one", sent == {"ask": False}, sent)

    # Grok Build (TOML like Codex) and Muse Code (JSON that must keep "schema_version": 1).
    (home / ".grok").mkdir()
    (home / ".grok/config.toml").write_text('model = "grok-4"\n')
    (home / ".config/muse").mkdir(parents=True)
    code, out, err = run(home, "agents", "add", "--agents", "grok,muse")
    grok = (home / ".grok/config.toml").read_text()
    muse = json.loads((home / ".config/muse/settings.json").read_text())
    check("grok: [mcp_servers.foxreach] added, other settings kept", code == 0 and 'model = "grok-4"' in grok
          and "[mcp_servers.foxreach]" in grok, out + err + grok)
    check("muse: a new file gets schema_version 1 and an optional stdio entry", muse.get("schema_version") == 1
          and muse["mcp_servers"]["foxreach"]["transport"] == "stdio" and muse["mcp_servers"]["foxreach"]["mode"] == "optional", muse)
    (home / ".config/muse/settings.json").write_text(json.dumps({"schema_version": 1, "model": "x", "mcp_servers": {"other": {"transport": "stdio", "command": "y"}}}))
    run(home, "agents", "add", "--agents", "muse")
    muse = json.loads((home / ".config/muse/settings.json").read_text())
    check("muse: an existing file keeps its keys and other servers", muse.get("model") == "x" and "other" in muse["mcp_servers"]
          and "foxreach" in muse["mcp_servers"], muse)
    run(home, "agents", "remove", "--agents", "grok,muse")
    muse = json.loads((home / ".config/muse/settings.json").read_text())
    check("grok, muse: remove takes out only foxreach", "foxreach" not in (home / ".grok/config.toml").read_text()
          and muse.get("schema_version") == 1 and list(muse["mcp_servers"]) == ["other"], muse)

# macOS (FOXREACH_PLATFORM=darwin takes that path here): browsers are apps, profiles live in
# Application Support, and an open profile holds .parentlock open.
with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)
    mac = {"FOXREACH_PLATFORM": "darwin"}
    exe = home / "Applications/LibreWolf.app/Contents/MacOS/librewolf"
    exe.parent.mkdir(parents=True)
    exe.write_text(f'#!/bin/sh\necho "$@" > {home}/opened\n')
    exe.chmod(0o755)
    (home / "Applications/Notes.app/Contents/MacOS").mkdir(parents=True)
    root = home / "Library/Application Support/librewolf"
    prof = root / "Profiles/x.default-default"
    prof.mkdir(parents=True)
    (root / "profiles.ini").write_text("[Profile0]\nName=default-default\nIsRelative=1\nPath=Profiles/x.default-default\n\n"
                                       "[Install1]\nDefault=Profiles/x.default-default\n")
    (home / ".librewolf").mkdir()  # a Linux profile folder, which macOS does not use
    (home / ".librewolf/profiles.ini").write_text("[Profile0]\nName=linux\nIsRelative=1\nPath=.\n")
    code, out, err = run(home, "status", "--json", extra=mac)
    rows = json.loads(out)["browsers"] if code == 0 else []
    lw = rows[0] if rows else {}
    check("macOS: the app in ~/Applications is found by its executable, other apps are not", len(rows) == 1
          and lw.get("name") == "LibreWolf" and lw.get("spec") == str(exe) and lw.get("packaging") == "app", out + err)
    check("macOS: profiles come from Application Support only", [p["path"] for p in lw.get("profiles", [])] == [str(prof)]
          and lw["profiles"][0]["isDefault"] and lw["profiles"][0]["inUse"] is False, lw.get("profiles"))
    with open(prof / ".parentlock", "w"):
        s = json.loads(run(home, "status", "--json", extra=mac)[1])
        check("macOS: a profile whose .parentlock is held open is in use", s["browsers"][0]["profiles"][0]["inUse"] is True, s["browsers"])
    (prof / "extensions").mkdir()
    (prof / "extensions/foxreach@foxreach.local.xpi").write_text("x")
    with open(prof / ".parentlock", "w"):
        code, _, err = run(home, "addon", "remove", "--profile", str(prof), extra=mac)
        check("macOS: addon remove refuses an open profile", code == 1 and "in use" in err, err)
    code, out, err = run(home, "addon", "remove", "--profile", str(prof), extra=mac)
    check("macOS: addon remove works once it is closed", code == 0 and not (prof / "extensions/foxreach@foxreach.local.xpi").exists(), out + err)

# The program in use when the browsers run the release program, and agents run something else.
with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)
    hosts = home / ".mozilla/native-messaging-hosts"
    hosts.mkdir(parents=True)
    wrapper = home / ".local/share/foxreach/foxreach-host"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(f'#!/bin/sh\nunset LD_PRELOAD\nexec "{home}/.local/bin/foxreach" host\n')
    (hosts / "foxreach.json").write_text(json.dumps({"name": "foxreach", "path": str(wrapper), "type": "stdio"}))
    s = json.loads(run(home, "status", "--json")[1])
    check("setup: the release program, from the browser's wrapper", s["setup"].get("kind") == "release"
          and s["setup"].get("command") == [f"{home}/.local/bin/foxreach"] and not s["setup"]["mixed"], s["setup"])
    (home / ".codex").mkdir()
    (home / ".codex/config.toml").write_text('[mcp_servers.foxreach]\ncommand = "/bun"\nargs = ["/elsewhere/bridge/mcp.mjs"]\n')
    s = json.loads(run(home, "status", "--json")[1])
    check("setup: an agent running another foxreach makes it mixed", s["setup"].get("kind") == "release" and s["setup"]["mixed"], s["setup"])

# install-program: the release for this machine, checksum checked, the old file kept.
import hashlib, platform
asset = {"x86_64": "foxreach-linux-x64", "aarch64": "foxreach-linux-arm64"}.get(platform.machine())
with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)
    rel = home / "release"
    rel.mkdir()
    (rel / "BUILDINFO.txt").write_text("foxreach 9.9.9\n")
    (rel / asset).write_text("#!/bin/sh\necho 9.9.9\n")
    (rel / "SHA256SUMS").write_text(f"{hashlib.sha256((rel / asset).read_bytes()).hexdigest()}  {asset}\n")
    env = {"FOXREACH_RELEASE_URL": rel.as_uri(), "FOXREACH_BIN_DIR": str(home / "bin2")}
    code, out, err = run(home, "install-program", "--json", extra=env)
    prog = home / "bin2/foxreach"
    check("install-program puts the release in FOXREACH_BIN_DIR and says so", code == 0 and json.loads(out or "{}") == {"program": str(prog), "version": "9.9.9"}
          and subprocess.run([prog, "version"], capture_output=True, text=True).stdout.strip() == "9.9.9", out + err)
    code, out, err = run(home, "install-program", extra=env)
    check("install-program again keeps the old file as foxreach.previous", code == 0 and (home / "bin2/foxreach.previous").exists()
          and "kept the old program" in out, out + err)
    (rel / "SHA256SUMS").write_text(f"{'0' * 64}  {asset}\n")
    before = prog.read_bytes()
    code, out, err = run(home, "install-program", extra=env)
    check("install-program refuses a checksum mismatch and changes nothing", code == 1 and "Checksum mismatch" in err
          and prog.read_bytes() == before and not list((home / "bin2").glob(".foxreach-install-*")), err)

# Browsers behind wrapper scripts: a wrapper counts as what it starts, a link opener is no browser.
with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)
    (home / "bin").mkdir()
    (home / "AppImages").mkdir()
    appimage = home / "AppImages/librewolf.appimage"
    appimage.write_text("#!/bin/sh\n")
    appimage.chmod(0o755)
    (home / "bin/librewolf").write_text('#!/bin/sh\nunset LD_PRELOAD\nexec "$HOME/AppImages/librewolf.appimage" "$@"\n')
    (home / "bin/firefox").write_text('#!/usr/bin/env bash\ncase "$1" in --version) exec /usr/bin/firefox-real "$@" ;; esac\n'
                                      'target="${1:-about:blank}"\nexec xdg-open "$target"\n')
    (home / "bin/zen").write_text('#!/bin/sh\nMOZ=/opt/zen/zen\nexec $MOZ "$@"\n')  # says neither: kept as it is
    for n in ("librewolf", "firefox", "zen"):
        (home / "bin" / n).chmod(0o755)
    # A Flatpak browser whose profiles live in XDG config, which is ~/.var/app/<id>/config inside it.
    (home / "bin/flatpak").write_text('#!/bin/sh\n[ "$1" = list ] && echo org.mozilla.firefox\nexit 0\n')
    (home / "bin/flatpak").chmod(0o755)
    froot = home / ".var/app/org.mozilla.firefox/config/mozilla/firefox"
    (froot / "x.default-release").mkdir(parents=True)
    (froot / "y.default").mkdir(parents=True)
    (froot / "profiles.ini").write_text("[Profile0]\nName=default-release\nIsRelative=1\nPath=x.default-release\n\n"
                                        "[InstallABC]\nDefault=x.default-release\nLocked=1\n\n"
                                        "[Profile1]\nName=default\nIsRelative=1\nPath=y.default\nDefault=1\n")
    rows = json.loads(run(home, "addon", "--json")[1] or "[]")
    flat = next((r for r in rows if r["packaging"] == "flatpak"), {})
    check("browsers: a Flatpak's profiles in config/ (XDG inside the sandbox) are found",
          sorted(p["path"] for p in flat.get("profiles", [])) == [str(froot / "x.default-release"), str(froot / "y.default")], flat)
    check("browsers: the install's default wins over an old Default=1",
          [p["name"] for p in flat.get("profiles", []) if p["isDefault"]] == ["default-release"], flat.get("profiles"))
    rows = [r for r in rows if r["packaging"] != "flatpak"]
    found = sorted((r["name"], r["packaging"], r["spec"]) for r in rows)
    check("browsers: a wrapper is the AppImage it starts (once), a link opener is dropped, an unknown launcher is kept",
          found == [("LibreWolf", "appimage", str(appimage)), ("Zen", "native", str(home / "bin/zen"))], found)

# Tor Browser: found by its install folder, offered with a warning, connected only when named.
with tempfile.TemporaryDirectory() as tmp:
    home = pathlib.Path(tmp)
    tor = home / ".local/share/torbrowser/tbb/x86_64/tor-browser/Browser"
    data = tor / "TorBrowser/Data/Browser"
    (data / "profile.default").mkdir(parents=True)
    (data / "profiles.ini").write_text("[Profile0]\nName=default\nIsRelative=1\nPath=profile.default\nDefault=1\n")
    (tor / "start-tor-browser").write_text("#!/bin/sh\n")
    (tor / "start-tor-browser").chmod(0o755)
    manifest = data / ".mozilla/native-messaging-hosts/foxreach.json"
    s = json.loads(run(home, "status", "--json")[1])
    row = next((b for b in s["browsers"] if b["name"] == "Tor Browser"), {})
    check("Tor Browser: found, with its profile, marked not recommended, not connected",
          [p["path"] for p in row.get("profiles", [])] == [str(data / "profile.default")]
          and "Not recommended" in (row.get("warning") or "") and row.get("connect", {}).get("ready") is False
          and row["connect"]["spec"] == f"tor:{tor}", row)
    run(home, "install", "--no-flatpak", "--agents", "none")
    check("Tor Browser: a plain install leaves it alone", not manifest.exists())
    code, out, err = run(home, "install", "--browser", f"tor:{tor}", "--agents", "none")
    s = json.loads(run(home, "status", "--json")[1])
    row = next((b for b in s["browsers"] if b["name"] == "Tor Browser"), {})
    check("Tor Browser: install --browser tor: writes the manifest in its own data folder, with the warning",
          code == 0 and manifest.exists() and row["connect"]["ready"] and "Not recommended" in out
          and "private windows" in out and str(manifest) in s["host"]["manifests"], out + err)
    code, _, err = run(home, "install", "--browser", f"tor:{home}", "--agents", "none")
    check("Tor Browser: a folder that is no Tor Browser install is refused", code == 1 and "not a Tor Browser install" in err, err)
    code, out, err = run(home, "uninstall", "--part", "host")
    check("Tor Browser: uninstall --part host removes its manifest too", code == 0 and not manifest.exists(), out + err)

sys.exit(1 if fails else 0)
