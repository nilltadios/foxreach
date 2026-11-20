#!/usr/bin/env python3
"""The MCP server's icon (serverInfo.icons) is foxreach's icon, under Bun and under Node.js.

Run:      uv run --python 3.15 test/server_icon.py [--binary dist/foxreach]
Checks:   bridge/icon.mjs carries extension/icons/icon.svg unchanged; an initialize at protocol
          2025-11-25 returns one icon, a data: URI that decodes to that SVG, with mimeType
          image/svg+xml; an initialize at 2025-06-18 returns no icons field. With --binary, the
          same checks run against that single-file build too. No browser needed; HOME is a
          throwaway folder.
Expected: one "ok" line per runtime and protocol, then "server_icon: ok", exit 0.
"""
import os
if os.name == "nt":
    raise SystemExit("This test runs on Linux and macOS only.")

import argparse, base64, json, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SVG = (ROOT / "extension/icons/icon.svg").read_text().strip()
SEARCH = f"{pathlib.Path.home()}/.bun/bin:/usr/bin:" + os.environ["PATH"]


def initialize(command, protocol, home):
    msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
           "params": {"protocolVersion": protocol, "capabilities": {}, "clientInfo": {"name": "server-icon-test", "version": "1"}}}
    env = {**os.environ, "HOME": home, "XDG_RUNTIME_DIR": home}
    p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
    try:
        p.stdin.write(json.dumps(msg) + "\n"); p.stdin.flush()
        line = p.stdout.readline()
    finally:
        p.stdin.close(); p.kill(); p.wait(timeout=5)
    return json.loads(line)["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary", help="also check this single-file build (from scripts/build_binary.py)")
    args = parser.parse_args()

    module = (ROOT / "bridge/icon.mjs").read_text()
    assert f"export const ICON_SVG = '{SVG}';" in module, "bridge/icon.mjs no longer matches extension/icons/icon.svg"

    runs = [(name, [shutil.which(name, path=SEARCH), str(ROOT / "bridge/mcp.mjs")]) for name in ("bun", "node")]
    if args.binary:
        runs.append(("binary", [str(pathlib.Path(args.binary).resolve()), "mcp"]))
    failed = False
    with tempfile.TemporaryDirectory() as home:
        for name, command in runs:
            if command[0] is None:
                print(f"{name}: not found, skipped"); continue
            new = initialize(command, "2025-11-25", home)
            icons = new["serverInfo"].get("icons")
            ok = (new["protocolVersion"] == "2025-11-25" and isinstance(icons, list) and len(icons) == 1
                  and icons[0].get("mimeType") == "image/svg+xml" and icons[0].get("sizes") == ["any"]
                  and icons[0]["src"].startswith("data:image/svg+xml;base64,")
                  and base64.b64decode(icons[0]["src"].split(",", 1)[1]).decode() == SVG)
            print(f"{'ok' if ok else 'FAIL'}  {name} 2025-11-25: icon {'is' if ok else 'is not'} the add-on's SVG")
            old = initialize(command, "2025-06-18", home)
            ok_old = old["protocolVersion"] == "2025-06-18" and "icons" not in old["serverInfo"]
            print(f"{'ok' if ok_old else 'FAIL'}  {name} 2025-06-18: {'no' if ok_old else 'unexpected'} icons field")
            failed |= not (ok and ok_old)
    if failed:
        sys.exit("server_icon: FAILED")
    print("server_icon: ok")


if __name__ == "__main__":
    main()
