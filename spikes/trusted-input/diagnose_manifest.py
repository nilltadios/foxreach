#!/usr/bin/env python3
"""Ask LibreWolf's own parser why an add-on directory is rejected.

Run: uv run --python 3.15 diagnose_manifest.py [addon_dir]
Output: errors and warnings from ExtensionData.parseManifest and the console.
"""
import zipfile, json, os, pathlib, shutil, signal, socket, subprocess, sys, tempfile, time

PORT = 47833
APP = pathlib.Path.home() / "AppImages/librewolf.appimage"
addon = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).parent / "ext").resolve()
profile = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-diag-"))
(profile / "user.js").write_text(f'user_pref("marionette.port", {PORT});\nuser_pref("extensions.experiments.enabled", true);\nuser_pref("xpinstall.signatures.required", false);\n')
env = {k: v for k, v in os.environ.items() if k != "LD_PRELOAD"} | {"MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}
proc = subprocess.Popen([APP, "--headless", "--no-remote", "--new-instance", "--profile", profile,
                         "--marionette", "--remote-allow-system-access"],
                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
shutil.copytree(addon, profile / "addon")
xpi_copy = profile / "addon.xpi"
JS = """
const [path, done] = arguments;
const { ExtensionData } = ChromeUtils.importESModule("resource://gre/modules/Extension.sys.mjs");
const { FileUtils } = ChromeUtils.importESModule("resource://gre/modules/FileUtils.sys.mjs");
const { AddonManager } = ChromeUtils.importESModule("resource://gre/modules/AddonManager.sys.mjs");
(async () => {
  const out = {};
  const file = new FileUtils.File(path);
  const data = new ExtensionData(Services.io.newFileURI(file));
  try { await data.loadManifest(); } catch (e) { out.loadError = String(e); }
  out.errors = data.errors; out.warnings = data.warnings;
  out.experimentsPref = Services.prefs.getBoolPref("extensions.experiments.enabled");
  try { const a = await AddonManager.installTemporaryAddon(file); out.temporary = {ok: true, active: a.isActive}; await a.uninstall(); }
  catch (e) { out.temporary = String(e); }
  // Permanent install, the route about:addons "Install Add-on From File" takes.
  try {
    const xpi = new FileUtils.File(path + ".xpi");
    const i = await AddonManager.getInstallForFile(xpi);
    out.permanentState = i.state; out.permanentError = i.error;
    const a = await i.install();
    out.permanent = {ok: true, active: a.isActive, signedState: a.signedState, temporarilyInstalled: a.temporarilyInstalled};
  } catch (e) { out.permanent = String(e); }
  out.console = Services.console.getMessageArray().map(m => String(m.message || m)).filter(m => /addon|manifest|extension|xpi/i.test(m)).slice(-12);
  done(out);
})();
"""
with zipfile.ZipFile(xpi_copy, "w") as z:
    for f in addon.iterdir():
        z.write(f, f.name)
try:
    for _ in range(120):
        try:
            sock = socket.create_connection(("127.0.0.1", PORT), timeout=60); break
        except OSError:
            time.sleep(0.25)
    stream = sock.makefile("rb"); seq = 0
    def recv():
        n = b""
        while (c := stream.read(1)) != b":":
            n += c
        return json.loads(stream.read(int(n)))
    def cmd(name, params):
        global seq; seq += 1
        d = json.dumps([0, seq, name, params]).encode(); sock.sendall(str(len(d)).encode() + b":" + d)
        _, _, err, res = recv()
        if err: raise RuntimeError(f"{name}: {err['error']}: {err['message']}")
        return res
    recv(); cmd("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {"moz:firefoxOptions": {}}}})
    cmd("Marionette:SetContext", {"value": "chrome"})
    r = cmd("WebDriver:ExecuteAsyncScript", {"script": JS, "args": [str(profile / "addon")], "scriptTimeout": 30000})
    print(json.dumps(r["value"], indent=1))
finally:
    for d in pathlib.Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if str(profile).encode() in (d / "cmdline").read_bytes() and not os.readlink(d / "exe").endswith(".appimage"): os.kill(int(d.name), signal.SIGTERM)
            except OSError: pass
    proc.wait(timeout=20); time.sleep(1); shutil.rmtree(profile, ignore_errors=True)
