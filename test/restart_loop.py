#!/usr/bin/env python3
"""Does the add-on come back after every browser restart?

Installs once, then restarts the browser N times with no automation attached. Each start must
open the socket and answer a real tool call. --settle sets how long the first run stays up after
install, so a too-early quit can be told apart from a daily restart failing.

Run:      uv run --python 3.15 test/restart_loop.py [--visible] [--runs N] [--settle SECONDS]
Expected: every restart OK, exit 0.
"""
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from harness import ROOT, Browser, McpClient


def arg(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def main():
    runs, settle = arg("--runs", 10), arg("--settle", 3.0)
    subprocess.run(["uv", "run", "--python", "3.15", str(ROOT / "scripts/build_install.py")], check=True, capture_output=True)
    browser = Browser(ROOT / "dist/foxreach.xpi", headless="--visible" not in sys.argv)
    failed = 0
    try:
        browser.start(marionette=True)
        browser.install()
        browser.wait_for_socket()
        time.sleep(settle)
        browser.stop()
        for run in range(1, runs + 1):
            time.sleep(0.5)
            started = time.time()
            browser.start(marionette=False)
            try:
                browser.wait_for_socket(timeout=30)
                client = McpClient(browser.socket)
                status = json.loads(client.call("status"))
                client.close()
                print(f"restart {run}: OK in {time.time() - started:.1f} s, trusted={status['trustedInput']}", flush=True)
            except Exception as e:
                failed += 1
                print(f"restart {run}: FAIL {e}", flush=True)
            browser.stop()
    finally:
        browser.cleanup()
    print(f"{runs - failed}/{runs} restarts OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
