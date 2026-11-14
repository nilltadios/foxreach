#!/usr/bin/env python3
"""What an agent is told when the bridge cannot reach the browser, for each likely cause.

Run:      uv run --python 3.15 test/connect_errors.py
Expected: every line PASS, exit 0. Needs no browser: the bridge is pointed at a socket that does not
exist, and at fake process lists (FOXREACH_PROC) that show no browser, one, two, or none readable,
and at a fake profile record (FOXREACH_PROFILES).
One check uses the real process list (/proc, or ps on macOS), with a stand-in process whose
executable is named "librewolf".
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import McpClient, ToolError  # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("PASS" if ok else "FAIL", name, "" if ok else detail)


def message(socket, proc):
    """The error text of a first call, as an agent sees it."""
    if proc:
        os.environ["FOXREACH_PROC"] = str(proc)
    client = McpClient(str(socket))
    try:
        client.call("status")
        return "(no error)"
    except ToolError as e:
        return str(e)
    finally:
        client.close()
        os.environ.pop("FOXREACH_PROC", None)


def fake_proc(root, exes):
    root.mkdir()
    (root / "self").mkdir()  # not a pid: must be skipped
    for pid, exe in enumerate(exes, start=100):
        (root / str(pid)).mkdir()
        (root / str(pid) / "exe").symlink_to(exe)
    return root


def main():
    work = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-connect-"))
    socket = work / "missing.sock"
    try:
        out = message(socket, fake_proc(work / "none", ["/usr/bin/bash", "/usr/bin/kwin_wayland"]))
        check("no browser running: says so", out.startswith("No Firefox-based browser is running"), out)
        check("no browser running: says what to open", "with the profile that has the foxreach add-on" in out, out)
        check("keeps the technical detail for debugging", f"on {socket})" in out, out)

        out = message(socket, fake_proc(work / "firefox", ["/usr/lib64/firefox/firefox"]))
        check("Firefox running: names it", out.startswith("Firefox is running, but the foxreach add-on has not connected"), out)
        check("Firefox running: says what to check", "about:addons" in out and "profile" in out, out)

        out = message(socket, fake_proc(work / "two", [
            "/tmp/.mount_librewXXXX/usr/bin/librewolf",  # an AppImage build
            "/app/lib/floorp/floorp",                     # a Flatpak build
        ]))
        check("two browsers running: names both", "Floorp and LibreWolf are running" in out, out)

        # A profile record (setup_profile.py writes one): the message names the profile and how to open it.
        profile = work / "my profile"
        profile.mkdir()
        record = work / "profiles"
        record.write_text(f"{profile}\t0.3.0\t/opt/libre wolf/librewolf\n{work / 'gone'}\t0.2.0\t/x\n"
                          f"{work}\t0.3.0\tflatpak:io.example.Browser\n")
        os.environ["FOXREACH_PROFILES"] = str(record)
        try:
            out = message(socket, fake_proc(work / "none2", ["/usr/bin/bash"]))
        finally:
            del os.environ["FOXREACH_PROFILES"]
        check("profile record: names the profile and quotes the command",
              f"'/opt/libre wolf/librewolf' --profile '{profile}'" in out, out)
        check("profile record: flatpak profile opens with flatpak run",
              f"flatpak run io.example.Browser --profile {work}" in out, out)
        check("profile record: a profile that no longer exists is left out", str(work / "gone") not in out, out)
        os.environ["FOXREACH_PROFILES"] = str(work / "no-record")
        try:
            out = message(socket, fake_proc(work / "none3", ["/usr/bin/bash"]))
        finally:
            del os.environ["FOXREACH_PROFILES"]
        check("no profile record: no hint", "set up in" not in out, out)

        out = message(socket, work / "no-proc-here")
        check("no process list: generic advice", out.startswith("The browser is not connected. Open the Firefox-based browser"), out)

        # macOS lists processes with ps; FOXREACH_PS stands in for its output, on any machine.
        def ps_message(lines):
            listing = work / "ps.txt"
            if lines is None:
                listing.unlink(missing_ok=True)
            else:
                listing.write_text("".join(f"{line}\n" for line in lines))
            os.environ["FOXREACH_PS"] = str(listing)
            try:
                return message(socket, None)
            finally:
                del os.environ["FOXREACH_PS"]
        out = ps_message(["/usr/sbin/cfprefsd", "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder"])
        check("ps (macOS): no browser running: says so", out.startswith("No Firefox-based browser is running"), out)
        out = ps_message(["/usr/sbin/cfprefsd", "/Applications/LibreWolf.app/Contents/MacOS/librewolf",
                          "/Applications/LibreWolf.app/Contents/MacOS/plugin-container.app/Contents/MacOS/plugin-container"])
        check("ps (macOS): finds LibreWolf by the executable inside its app", out.startswith("LibreWolf is running"), out)
        out = ps_message(None)
        check("ps (macOS): no listing: generic advice", out.startswith("The browser is not connected"), out)

        # The real process list, with a stand-in process whose executable is named like a browser.
        # (copy, not copy2: macOS refuses to copy the system flags of /bin/sleep.)
        stand_in = work / "bin" / "librewolf"
        stand_in.parent.mkdir()
        shutil.copy(shutil.which("sleep"), stand_in)
        if sys.platform == "darwin":
            # macOS kills a copy of one of its own programs that still carries Apple's signature.
            subprocess.run(["codesign", "--force", "--sign", "-", str(stand_in)], check=True, capture_output=True)
        proc = subprocess.Popen([str(stand_in), "30"])
        try:
            out = message(socket, None if sys.platform == "darwin" else "/proc")
            check("real process list: finds a running browser by its executable", "LibreWolf is running" in out
                  or ("LibreWolf and" in out or "and LibreWolf" in out), out)
        finally:
            proc.kill()
            proc.wait()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("failed:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
