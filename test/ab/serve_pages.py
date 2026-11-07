#!/usr/bin/env python3
"""Serve the end-to-end test pages for comparing foxreach with another browser tool.

Run: uv run --python 3.15 test/ab/serve_pages.py
Both tools then get the same pages, at http://127.0.0.1:47852/<page> (main, frames, dialogs, ...).
"""
import http.server
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import e2e  # noqa: E402

server = http.server.ThreadingHTTPServer(("127.0.0.1", e2e.PORT), e2e.Handler)
print("serving on", e2e.MAIN, flush=True)
server.serve_forever()
