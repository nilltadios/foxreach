#!/usr/bin/env python3
"""browser_open: an agent starts a recorded profile's browser and waits for foxreach to connect.

Run:      uv run --python 3.15 test/browser_open.py
Browser:  FOXREACH_BROWSER (a path or a command), as for test/e2e.py
Expected: every line PASS, exit 0. Only throwaway profiles: a record file of the test's own
          (FOXREACH_PROFILES) lists them; the real record and profiles are never read or opened.
          The browser starts headless (MOZ_HEADLESS=1).
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, Browser, McpClient, ToolError  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'} {name}{'' if ok else f' -- {detail}'}", flush=True)


def call(mcp, tool, **args):
    try:
        return True, mcp.call(tool, **args)
    except ToolError as e:
        return False, str(e)


def main():
    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    b = Browser(ROOT / "dist/foxreach.xpi")
    work = pathlib.Path(tempfile.mkdtemp(prefix="foxreach-open-"))
    record = work / "profiles"
    saved = dict(os.environ)
    try:
        b.start(marionette=True)
        b.install()
        b.wait_for_socket()
        McpClient(b.socket).call("status")
        time.sleep(3)
        b.stop()

        os.environ.update({"FOXREACH_PROFILES": str(record), "FOXREACH_SOCKET": b.socket, "MOZ_HEADLESS": "1",
                           "FOXREACH_HOST_LOG": str(pathlib.Path(b.dir) / "host.log")})
        record.write_text(f"{b.profile}\t0\t{b.browser.spec}\n")
        mcp = McpClient(b.socket)
        ok, text = call(mcp, "status")
        check("before: not connected, and the error names the profile", not ok and str(b.profile) in text, text)

        started = time.monotonic()
        ok, text = call(mcp, "browser_open")
        check("browser_open with one recorded profile starts it and waits for foxreach", ok and "connected" in text, text)
        print(f"     (took {time.monotonic() - started:.1f} s)")
        ok, text = call(mcp, "tabs_list")
        check("...and tools work afterwards", ok, text)
        ok, text = call(mcp, "browser_open")
        check("browser_open when connected starts nothing", ok and "already connected" in text, text)
        mcp.close()
        b.stop()
        time.sleep(1)

        # Fake profiles, recorded with programs that cannot start a browser.
        busy = work / "busy-profile"
        busy.mkdir()
        os.symlink(f"127.0.0.1:+{os.getpid()}", busy / "lock")  # held by a live process: this one
        missing = work / "missing-program"
        missing.mkdir()
        record.write_text(f"{busy}\t0\t/bin/false\n{missing}\t0\t{work}/no-such-browser\n")
        mcp = McpClient(b.socket)
        ok, text = call(mcp, "browser_open")
        check("several recorded profiles and no default: asks which", not ok and "several profiles" in text and str(busy) in text, text)
        ok, text = call(mcp, "browser_open", profile=str(work / "elsewhere"))
        check("a profile that is not recorded is refused", not ok and "No single recorded profile" in text, text)
        ok, text = call(mcp, "browser_open", profile="busy-profile")
        check("a profile a running browser holds is refused, by folder name too", not ok and "already has" in text, text)
        ok, text = call(mcp, "browser_open", profile=str(missing))
        check("a recorded program that does not exist is reported", not ok and "Could not start" in text, text)
        mcp.close()
    finally:
        os.environ.clear()
        os.environ.update(saved)
        b.cleanup()
        subprocess.run(["rm", "-rf", str(work)], check=False)
    failed = results.count(False)
    print(f"\n{len(results) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
