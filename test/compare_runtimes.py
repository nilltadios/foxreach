#!/usr/bin/env python3
"""Measure the bridge's cost under each runtime: MCP server start-up time and memory.

Run:      uv run --python 3.15 test/compare_runtimes.py
Measures: time from launch to the initialize reply, and PSS once idle, 10 runs each. No browser needed.
"""
import json, os, pathlib, shutil, statistics, subprocess, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
SEARCH = f"{pathlib.Path.home()}/.bun/bin:/usr/bin:" + os.environ["PATH"]
MSG = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "m", "version": "1"}}}) + "\n"
for name in ("node", "bun"):
    exe = shutil.which(name, path=SEARCH)
    version = subprocess.run([exe, "--version"], capture_output=True, text=True).stdout.strip()
    times, pss = [], []
    for _ in range(10):
        start = time.perf_counter()
        p = subprocess.Popen([exe, str(ROOT / "bridge/mcp.mjs")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        p.stdin.write(MSG); p.stdin.flush(); p.stdout.readline()
        times.append((time.perf_counter() - start) * 1000)
        time.sleep(0.3)
        rollup = pathlib.Path(f"/proc/{p.pid}/smaps_rollup").read_text()
        pss.append(int(next(l for l in rollup.splitlines() if l.startswith("Pss:")).split()[1]) / 1024)
        p.stdin.close(); p.wait(timeout=5)
    print(f"{name} {version}: start-up {statistics.median(times):.0f} ms, memory {statistics.median(pss):.0f} MiB PSS (medians of 10)")
