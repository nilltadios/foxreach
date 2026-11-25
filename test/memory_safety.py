#!/usr/bin/env python3
"""What foxreach keeps in memory stays bounded: buffers have limits, and listeners, timers and
recordings are let go.

Run:      uv run --python 3.15 test/memory_safety.py
Expected: every line PASS, "N passed, 0 failed", exit 0 (about 15 s).
How:      a real native host (bridge/host.mjs) and a real MCP server (bridge/mcp.mjs) are started on
          sockets in throwaway folders and sent too much: too many clients, an oversized message, a
          batch of uploads over the limit, an oversized line. The add-on's scripts are loaded into
          the runtime with stand-ins for the browser's objects, and their bookkeeping is counted.
          Last, the sources are read: no native-memory primitive in the JavaScript, `unsafe` in two
          Rust files only. No browser is started and no real setting is touched.
Not shown: that the JavaScript engines, the browser or the Rust crates are memory safe. JavaScript
          has no way to corrupt memory by itself; what this test covers is how much it holds on to.
"""
import json
import os
import pathlib
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "test"))
from harness import RUNTIME  # noqa: E402

fails = 0
passes = 0


def check(name, ok, detail=""):
    global fails, passes
    if ok:
        passes += 1
        print(f"PASS {name}")
    else:
        fails += 1
        print(f"FAIL {name}: {detail}")


def own_folders(tmp):
    """Environment for a host or MCP server this test starts: its home, state and runtime folders
    are the throwaway one, so it reads and writes nothing of the real foxreach setup."""
    return {"HOME": tmp, "XDG_RUNTIME_DIR": tmp, "XDG_STATE_HOME": os.path.join(tmp, "state"),
            "XDG_CONFIG_HOME": os.path.join(tmp, "config"), "FOXREACH_PROFILES": os.path.join(tmp, "profiles")}


def check_static_invariants():
    forbidden_js = re.compile(r"\b(allocUnsafe|allocUnsafeSlow|SharedArrayBuffer|WebAssembly|bun:ffi|ctypes|dlopen)\b")
    hits = []
    for folder in ("bridge", "extension"):
        for p in sorted((ROOT / folder).rglob("*")):
            if p.suffix not in (".js", ".mjs"):
                continue
            for idx, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if forbidden_js.search(line):
                    hits.append(f"{p.relative_to(ROOT)}:{idx}: {line.strip()}")
    check("Zero unsafe/FFI/WASM/allocUnsafe primitives in bridge/ and extension/", not hits, hits)

    forbidden_py = re.compile(r"\b(import\s+(ctypes|cffi|mmap)|from\s+(ctypes|cffi|mmap)\s+import)\b")
    py_hits = []
    for p in sorted((ROOT / "scripts").glob("*.py")):
        if forbidden_py.search(p.read_text(encoding="utf-8")):
            py_hits.append(str(p.relative_to(ROOT)))
    check("Zero ctypes/cffi/mmap usage in scripts/", not py_hits, py_hits)

    unsafe_rs_files = sorted(
        str(p.relative_to(ROOT))
        for p in (ROOT / "manager/src").glob("*.rs")
        if re.search(r"\bunsafe\b", p.read_text(encoding="utf-8"))
    )
    check(
        "Rust `unsafe` in manager/src/ is restricted strictly to x11_window.rs and window_icons.rs",
        unsafe_rs_files == ["manager/src/window_icons.rs", "manager/src/x11_window.rs"],
        unsafe_rs_files,
    )
    x11 = (ROOT / "manager/src/x11_window.rs").read_text(encoding="utf-8")
    check(
        "manager/src/x11_window.rs enforces compile-time size/align checks and RAII Drop guards",
        "struct DlHandle" in x11 and "struct OwnedDisplay" in x11
        and "size_of::<T>() == std::mem::size_of::<*mut c_void>()" in x11
        and "align_of::<T>() == std::mem::align_of::<*mut c_void>()" in x11,
    )
    wi = (ROOT / "manager/src/window_icons.rs").read_text(encoding="utf-8")
    check(
        "manager/src/window_icons.rs checks single-threaded /proc/self/task inside with_data_dir with Restore Drop",
        "fn with_data_dir" in wi and "/proc/self/task" in wi.split("fn with_data_dir", 1)[1]
        and "impl Drop for Restore" in wi,
    )


def test_host_bounds():
    with tempfile.TemporaryDirectory(prefix="foxreach-memsafe-host-") as tmp:
        sock_path = os.path.join(tmp, "browser.sock")
        env = dict(os.environ, FOXREACH_SOCKET=sock_path, FOXREACH_HOST_PROFILE=tmp, **own_folders(tmp))
        proc = subprocess.Popen(
            [RUNTIME, str(ROOT / "bridge/cli.mjs"), "host"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # A host that never answers would hold a read below for ever: it is stopped instead, the
        # read ends, and the part fails.
        watchdog = threading.Timer(60, proc.kill)
        watchdog.start()
        try:
            # Wait for socket to listen.
            deadline = time.time() + 10
            while time.time() < deadline and not os.path.exists(sock_path):
                time.sleep(0.02)
            check("host.mjs starts listening on private socket", os.path.exists(sock_path))

            # Connect 64 clients and verify the 65th is closed immediately (MAX_CLIENTS = 64).
            conns = []
            for _ in range(64):
                c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                c.connect(sock_path)
                conns.append(c)
            time.sleep(0.15)
            extra = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            extra.settimeout(2.0)
            extra.connect(sock_path)
            try:
                data = extra.recv(16)
                rejected = data == b""
            except (ConnectionResetError, BrokenPipeError):
                rejected = True
            except TimeoutError:
                rejected = False  # still open after 2 s: the host took it
            extra.close()
            for c in conns:
                c.close()
            check("host.mjs rejects 65th concurrent client (MAX_CLIENTS=64)", rejected)

            # Send oversized native messaging length prefix (> 64 MiB) -> host must fail closed (exit 5), not OOM.
            proc.stdin.write(struct.pack("<I", 64 * 1024 * 1024 + 1))
            proc.stdin.flush()
            head = proc.stdout.read(4)
            msg = json.loads(proc.stdout.read(struct.unpack("<I", head)[0])) if len(head) == 4 else {}
            rc = proc.wait(timeout=10)
            check(
                "host.mjs rejects >64 MiB browser frame header cleanly without buffering",
                rc == 5 and "exceeded" in str(msg.get("hostError", "")),
                f"rc={rc}, msg={msg}",
            )
        finally:
            watchdog.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


def test_mcp_batch_and_single_flight():
    with tempfile.TemporaryDirectory(prefix="foxreach-memsafe-mcp-") as tmp:
        sock_path = os.path.join(tmp, "browser.sock")
        connect_count = 0
        lock = threading.Lock()
        stop = threading.Event()

        def serve():
            nonlocal connect_count
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(sock_path)
            srv.listen(16)
            srv.settimeout(0.2)
            try:
                while not stop.is_set():
                    try:
                        conn, _ = srv.accept()
                    except TimeoutError:
                        continue
                    with lock:
                        connect_count += 1
                    threading.Thread(target=handle_conn, args=(conn,), daemon=True).start()
            finally:
                srv.close()

        def handle_conn(conn):
            with conn, conn.makefile("rw") as f:
                for line in f:
                    req = json.loads(line)
                    if req.get("tool") == "status":
                        text = json.dumps({"version": json.loads((ROOT / "extension/manifest.json").read_text())["version"]})
                    else:
                        text = f"ok:{req['id']}"
                    f.write(json.dumps({"id": req["id"], "result": {"text": text}}) + "\n")
                    f.flush()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        try:
            time.sleep(0.1)
            env = dict(os.environ, FOXREACH_SOCKET=sock_path, **own_folders(tmp))
            proc = subprocess.Popen(
                [RUNTIME, str(ROOT / "bridge/cli.mjs"), "mcp"],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            watchdog = threading.Timer(90, proc.kill)
            watchdog.start()
            try:
                # Fire 5 concurrent tools/call requests before conn is opened -> must share 1 socket!
                proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "clientInfo": {"name": "test"}}}) + "\n")
                for rid in range(2, 7):
                    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": "tabs_list", "arguments": {}}}) + "\n")
                proc.stdin.flush()
                replies = {}
                for _ in range(6):
                    r = json.loads(proc.stdout.readline())
                    replies[r["id"]] = r
                check(
                    "mcp.mjs single-flight connect opens only 1 socket for 5 concurrent calls",
                    connect_count == 1 and all("result" in replies[i] and not replies[i]["result"].get("isError") for i in range(2, 7)),
                    f"connect_count={connect_count}",
                )

                # Batch upload aggregate cap: 3 x 25 MiB = 75 MiB > 64 MiB must be rejected before browser call!
                f1 = os.path.join(tmp, "a.bin")
                f2 = os.path.join(tmp, "b.bin")
                f3 = os.path.join(tmp, "c.bin")
                for fp in (f1, f2, f3):
                    with open(fp, "wb") as fh:
                        fh.truncate(25 * 1024 * 1024)
                proc.stdin.write(json.dumps({
                    "jsonrpc": "2.0",
                    "id": 99,
                    "method": "tools/call",
                    "params": {
                        "name": "batch",
                        "arguments": {
                            "actions": [
                                {"tool": "file_upload", "args": {"tabId": 1, "ref": "r1", "paths": [f1]}},
                                {"tool": "file_upload", "args": {"tabId": 1, "ref": "r1", "paths": [f2]}},
                                {"tool": "file_upload", "args": {"tabId": 1, "ref": "r1", "paths": [f3]}},
                            ]
                        },
                    },
                }) + "\n")
                proc.stdin.flush()
                batch_reply = json.loads(proc.stdout.readline())
                txt = batch_reply.get("result", {}).get("content", [{}])[0].get("text", "")
                check(
                    "mcp.mjs batch enforces aggregate 64 MiB upload cap across actions",
                    batch_reply.get("result", {}).get("isError") is True and "64 MiB" in txt,
                    batch_reply,
                )

                # Oversized stdin line (>16 MiB) must fail closed with JSON-RPC error and exit 1.
                proc.stdin.write("x" * (16 * 1024 * 1024 + 100) + "\n")
                proc.stdin.flush()
                err_reply = json.loads(proc.stdout.readline())
                rc = proc.wait(timeout=10)
                check(
                    "mcp.mjs rejects >16 MiB stdin line cleanly",
                    rc == 1 and "too large" in err_reply.get("error", {}).get("message", "").lower(),
                    f"rc={rc}, reply={err_reply}",
                )
            finally:
                watchdog.cancel()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
        finally:
            stop.set()
            t.join(timeout=5)


def test_js_units_in_runtime():
    script = r"""
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { probe } from "./bridge/agenttest.mjs";

const ROOT = process.cwd();
let background = null;
async function section(name, fn) {
  try {
    await fn();
    console.log(`UNIT PASS ${name}`);
  } catch (e) {
    console.log(`UNIT FAIL ${name}: ${String(e?.message ?? e).split("\n")[0]}`);
  }
}

await section("an agent command that floods its output is stopped at 1 MiB (foxreach test)", async () => {
  const res = await probe([process.execPath, "-e", 'process.stdout.write("A".repeat(1200000)); setTimeout(() => {}, 5000);'], { timeoutMs: 5000 });
  assert.equal(res.ok, false);
  assert.match(res.error, /1 MiB/i);
});

await section("a recording's frames are kept at one byte a pixel, and the GIF is the same as from full colour", async () => {
  const src = fs.readFileSync(path.join(ROOT, "extension/gif.js"), "utf8");
  const ctx = vm.createContext({ Uint8Array, Map, Math, String, btoa, Error });
  vm.runInContext(src + "\nthis.__exports = { encodeGif, quantizeRgba, bytesToBase64 };", ctx);
  const { encodeGif, quantizeRgba, bytesToBase64 } = ctx.__exports;
  const rgba = new Uint8ClampedArray(4 * 4 * 4);
  for (let i = 0; i < rgba.length; i += 4) { rgba[i] = 255; rgba[i + 1] = 128; rgba[i + 2] = 0; rgba[i + 3] = 255; }
  const g1 = encodeGif([{ width: 4, height: 4, rgba }]);
  const g2 = encodeGif([{ width: 4, height: 4, pixels: quantizeRgba(rgba, 4, 4) }]);
  assert.equal(Buffer.from(g1.subarray(0, 6)).toString("ascii"), "GIF89a");
  assert.deepEqual(Array.from(g1), Array.from(g2));
  assert.ok(bytesToBase64(g2).length > 10);
});

await section("the page script keeps one paint probe and one press watcher at a time, and at most 20,000 element refs", async () => {
  const src = fs.readFileSync(path.join(ROOT, "extension/content/content.js"), "utf8");
  let onMessage = null;
  const activeMouseDown = new Set();
  const activeRafs = new Map();
  let nextRaf = 1;
  const fakeElements = [];
  for (let i = 0; i < 20500; i++) {
    fakeElements.push({
      nodeType: 1, localName: "button", id: `b${i}`, type: "button", isConnected: true,
      textContent: `btn ${i}`, childNodes: [],
      getAttribute: () => null, hasAttribute: () => false,
      getRootNode: () => ({}), contains: () => false,
      checkVisibility: () => true,
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 10, height: 10, right: 10, bottom: 10 }),
    });
  }
  const docEl = {
    nodeType: 1, localName: "div", id: "root", isConnected: true, childNodes: fakeElements,
    getAttribute: () => null, hasAttribute: () => false, checkVisibility: () => true,
  };
  const ctx = vm.createContext({
    globalThis: {},
    Map, Set, WeakMap, WeakSet, WeakRef, FinalizationRegistry, Math, Array, Number, String, JSON, Error, RegExp, Object, Promise, Uint8Array,
    Node: { ELEMENT_NODE: 1, TEXT_NODE: 3 },
    ShadowRoot: class {},
    performance: { now: () => 100 },
    setTimeout, clearTimeout,
    requestAnimationFrame: (cb) => { const id = nextRaf++; activeRafs.set(id, cb); return id; },
    cancelAnimationFrame: (id) => { activeRafs.delete(id); },
    addEventListener: (type, fn) => { if (type === "mousedown") activeMouseDown.add(fn); },
    removeEventListener: (type, fn) => { if (type === "mousedown") activeMouseDown.delete(fn); },
    location: { href: "https://example.test/", host: "example.test" },
    window: { innerWidth: 800, innerHeight: 600, scrollX: 0, scrollY: 0, devicePixelRatio: 1 },
    document: {
      title: "test", hidden: false, baseURI: "https://example.test/", documentElement: docEl,
      addEventListener: () => {},
      querySelectorAll: () => [],
    },
    browser: {
      storage: { local: { get: async () => ({}) }, onChanged: { addListener: () => {} } },
      runtime: { onMessage: { addListener: (fn) => { onMessage = fn; } } },
    },
  });
  ctx.globalThis = ctx;
  vm.runInContext(src, ctx);
  assert.ok(onMessage);

  // Overlapping paintStart x5 -> at most 1 live rAF, and paintRead leaves 0
  for (let i = 0; i < 5; i++) await onMessage({ foxreachOp: "paintStart", args: {} });
  assert.equal(activeRafs.size, 1, `expected 1 live rAF, got ${activeRafs.size}`);
  await onMessage({ foxreachOp: "paintRead", args: {} });
  assert.equal(activeRafs.size, 0, `expected 0 live rAF after paintRead, got ${activeRafs.size}`);

  // readPage over 20,500 buttons -> refs capped at 20,000 (oldest evicted -> r1 stale, r20500 alive)
  await onMessage({ foxreachOp: "readPage", args: { filter: "interactive", maxDepth: 5 } });
  let r1Stale = false;
  try { await onMessage({ foxreachOp: "watchPress", args: { ref: "r1" } }); } catch { r1Stale = true; }
  assert.equal(r1Stale, true, "expected r1 to be evicted once refs exceeded MAX_REFS");

  // The element that lost r1 is still in the page. Read again (a reading that fits the limit:
  // one of more elements than the limit cannot give them all a ref), it gets a new ref, and that
  // one works; it is not handed the ref that was given up.
  docEl.childNodes = fakeElements.slice(0, 10);
  const again = await onMessage({ foxreachOp: "readPage", args: { filter: "interactive", maxDepth: 5 } });
  docEl.childNodes = fakeElements;
  const firstButton = again.lines.find((l) => l.includes("#b0 ["));
  assert.ok(firstButton, "the first button is not in the second reading");
  const newRef = /\[(r\d+)\]/.exec(firstButton)[1];
  assert.notEqual(newRef, "r1", "the element kept the ref that was given up");
  await onMessage({ foxreachOp: "watchPress", args: { ref: newRef } });

  // watchPress x3 + pressResult without mousedown firing -> 0 leaked mousedown listeners
  for (let i = 0; i < 3; i++) await onMessage({ foxreachOp: "watchPress", args: { ref: "r20500" } });
  assert.equal(activeMouseDown.size, 1, `expected 1 mousedown listener, got ${activeMouseDown.size}`);
  await onMessage({ foxreachOp: "pressResult", args: {} });
  assert.equal(activeMouseDown.size, 0, `expected 0 mousedown listeners after pressResult, got ${activeMouseDown.size}`);
});

await section("the privileged part removes its quit observers and timers when the add-on shuts down", async () => {
  const src = fs.readFileSync(path.join(ROOT, "extension/experiment/parent.js"), "utf8");
  const obs = new Set();
  const resSubs = new Map();
  let actorRegistered = false;
  class ExtensionError extends Error {}
  class ExtensionAPI {}
  const ctx = vm.createContext({
    ExtensionAPI, Map, Set, Promise, Error, Object, Number, Math, String, console,
    Components: { interfaces: { nsIResProtocolHandler: {} } },
    ChromeUtils: {
      importESModule: (u) => {
        if (u.includes("ExtensionUtils")) return { ExtensionError };
        if (u.includes("Timer")) return { setTimeout, clearTimeout };
        throw new Error(u);
      },
      registerWindowActor: () => { actorRegistered = true; },
      unregisterWindowActor: () => { actorRegistered = false; },
    },
    Services: {
      io: { getProtocolHandler: () => ({ QueryInterface: () => ({ setSubstitution: (k, v) => resSubs.set(k, v) }) }) },
      obs: {
        addObserver: (o, t) => obs.add(`${t}:${obs.size}:${o}`),
        removeObserver: (o, t) => {
          for (const k of [...obs]) if (k.startsWith(`${t}:`)) { obs.delete(k); break; }
        },
      },
    },
  });
  vm.runInContext(src, ctx);
  const inst = new ctx.foxreachInput();
  const api = inst.getAPI({ extension: { rootURI: "file:///ext", tabManager: {} } });
  assert.equal(actorRegistered, true);
  const quitPromise = api.foxreachInput.call({ tabId: null, method: "untilQuit", args: {} });
  assert.equal(obs.size, 2, "expected 2 quit observers registered");
  inst.onShutdown();
  assert.equal(obs.size, 0, `expected 0 quit observers after onShutdown, got ${obs.size}`);
  assert.equal(actorRegistered, false);
  const res = await quitPromise;
  assert.equal(res.quitting, false);
});

await section("the add-on forgets partial requests when the host goes, and a closed tab's pending requests", async () => {
  const gifSrc = fs.readFileSync(path.join(ROOT, "extension/gif.js"), "utf8");
  const bgSrc = fs.readFileSync(path.join(ROOT, "extension/background.js"), "utf8");
  let onMsg = null;
  let onDisc = null;
  let onTabRemoved = null;
  let onBeforeReq = null;
  let posted = [];
  const ctx = vm.createContext({
    Map, Set, Array, Object, Number, String, JSON, Math, Date, Error, RegExp, Promise, Uint8Array, Uint8ClampedArray,
    setTimeout, clearTimeout, console, btoa, TextEncoder,
    URL,
    browser: {
      runtime: {
        id: "foxreach@foxreach.local",
        getManifest: () => ({ version: "0.4.1" }),
        getBrowserInfo: async () => ({ name: "Firefox", version: "159" }),
        getPlatformInfo: async () => ({ os: "linux" }),
        connectNative: () => ({
          onMessage: { addListener: (fn) => { onMsg = fn; } },
          onDisconnect: { addListener: (fn) => { onDisc = fn; } },
          postMessage: (m) => posted.push(m),
        }),
        onInstalled: { addListener: () => {} },
        onMessage: { addListener: () => {} },
      },
      storage: { local: { get: async () => ({}), set: async () => {}, remove: async () => {} } },
      permissions: { onAdded: { addListener: () => {} }, contains: async () => true, remove: async () => {} },
      browserAction: { setBadgeBackgroundColor: () => {}, setBadgeText: () => {} },
      tabs: {
        onCreated: { addListener: () => {} },
        onRemoved: { addListener: (fn) => { onTabRemoved = fn; } },
        onUpdated: { addListener: () => {}, removeListener: () => {} },
        query: async () => [],
      },
      webRequest: {
        onBeforeRequest: { addListener: (fn) => { onBeforeReq = fn; } },
        onCompleted: { addListener: () => {} },
        onErrorOccurred: { addListener: () => {} },
      },
    },
  });
  vm.runInContext(gifSrc + "\n" + bgSrc + "\nthis.__fits = typeof assertReplyFits === 'function' ? assertReplyFits : null;\nthis.__state = () => ({ partialSize: partial.size, partialBytes, pendingSize: pending.size, recordingsSize: recordings.size, recordingBytes, agentTabs });", ctx);
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(onMsg && onDisc && onTabRemoved && onBeforeReq);

  // Feed 1 partial chunk, then fire onDisconnect -> partial must be 0
  onMsg({ chunk: { id: "1:1", i: 0, n: 2, data: "a".repeat(1000) } });
  assert.equal(ctx.__state().partialSize, 1);
  assert.equal(ctx.__state().partialBytes, 1000);
  onDisc({});
  assert.equal(ctx.__state().partialSize, 0, "partial must be cleared on port.onDisconnect");
  assert.equal(ctx.__state().partialBytes, 0, "partialBytes must be 0 on port.onDisconnect");

  // Add agent tab + pending webRequest -> tabRemoved must purge pending
  ctx.__state().agentTabs.add(42);
  onBeforeReq({ tabId: 42, requestId: "req-1", method: "GET", url: "https://example.test/", type: "main_frame", timeStamp: 1 });
  assert.equal(ctx.__state().pendingSize, 1);
  onTabRemoved(42);
  assert.equal(ctx.__state().pendingSize, 0, "pending webRequests for closed tab must be purged");
  background = ctx;
});

await section("a result over the native host's limit for one message is refused in the add-on, not sent", async () => {
  assert.ok(background?.__fits, "the add-on has no check of a result.s size");
  background.__fits({ text: "plain" });
  background.__fits({ gif: "A".repeat(30 * 1024 * 1024) });
  assert.throws(() => background.__fits({ gif: "A".repeat(61 * 1024 * 1024) }), /too large to return \(61 MiB/);
  // 31 Mi two-byte characters are 62 MiB once encoded, though under the limit when counted as characters.
  assert.throws(() => background.__fits({ results: [{ text: "\u00e9".repeat(31 * 1024 * 1024) }] }), /too large to return \(62 MiB/);
});

await section("a dropped file's data is taken off the page's sandbox and a wheel listener is removed, also on failure", async () => {
  const src = fs.readFileSync(path.join(ROOT, "extension/experiment/child.sys.mjs"), "utf8");
  // Load child.sys.mjs as an ES module with Gecko globals stubbed on globalThis
  let lastSandbox = null;
  let throwEval = false;
  globalThis.JSWindowActorChild = class {};
  globalThis.ChromeUtils = { importESModule: () => ({ setTimeout }) };
  globalThis.Cu = {
    Sandbox: () => { lastSandbox = {}; return lastSandbox; },
    cloneInto: (v) => structuredClone(v),
    evalInSandbox: (code, sb) => {
      if (throwEval) throw new Error("boom");
      const fn = new Function("__files", "DataTransfer", "File", "atob", `return ${code}`);
      class FakeDT { constructor() { this.items = { add: () => {} }; } }
      class FakeFile { constructor(parts, name) { this.parts = parts; this.name = name; } }
      return fn(sb.__files, FakeDT, FakeFile, atob);
    },
  };
  const mod = await import(path.join(ROOT, "extension/experiment/child.sys.mjs"));
  const child = new mod.FoxreachInputChild();
  const wheelListeners = new Set();
  const fakeWin = {
    document: { elementFromPoint: () => ({ localName: "div", id: "drop" }), scrollingElement: { scrollLeft: 0, scrollTop: 0, scrollTo: () => {} } },
    getComputedStyle: () => ({ overflowX: "visible", overflowY: "visible" }),
    DragEvent: class { constructor(t) { this.type = t; this.defaultPreventedByContent = true; } },
    WheelEvent: { DOM_DELTA_PIXEL: 0 },
    windowRoot: {
      addEventListener: (t, fn) => { if (t === "wheel") wheelListeners.add(fn); },
      removeEventListener: (t, fn) => { if (t === "wheel") wheelListeners.delete(fn); },
    },
    windowUtils: {
      dispatchDOMEventViaPresShellForTesting: () => {},
      sendWheelEvent: () => { throw new Error("wheel boom"); },
    },
  };
  child.contentWindow = fakeWin;

  // dropFiles normal: sandbox.__files deleted
  await child.receiveMessage({ name: "dropFiles", data: { x: 1, y: 1, files: [{ name: "a.txt", type: "text/plain", data: btoa("hello") }] } });
  assert.equal("__files" in lastSandbox, false, "sandbox.__files must be deleted after normal dropFiles");

  // dropFiles throw: sandbox.__files still deleted
  throwEval = true;
  let threw = false;
  try {
    await child.receiveMessage({ name: "dropFiles", data: { x: 1, y: 1, files: [{ name: "a.txt", type: "text/plain", data: btoa("hello") }] } });
  } catch { threw = true; }
  assert.equal(threw, true);
  assert.equal("__files" in lastSandbox, false, "sandbox.__files must be deleted when dropFiles throws");

  // wheel throw: listener still removed
  let wheelThrew = false;
  try { await child.receiveMessage({ name: "wheel", data: { x: 1, y: 1, dx: 0, dy: 100 } }); } catch { wheelThrew = true; }
  assert.equal(wheelThrew, true);
  assert.equal(wheelListeners.size, 0, `expected 0 wheel listeners after throw, got ${wheelListeners.size}`);
});

"""
    r = subprocess.run([RUNTIME, "-e", script], cwd=ROOT, capture_output=True, text=True, timeout=120)
    units = re.findall(r"(?m)^UNIT (PASS|FAIL) (.*)$", r.stdout)
    for outcome, line in units:
        name, _, detail = line.partition(": ") if outcome == "FAIL" else (line, "", "")
        check(name, outcome == "PASS", detail)
    check("every unit ran (7)", r.returncode == 0 and len(units) == 7, (r.stdout + r.stderr)[-2000:])


def main():
    # A part that cannot finish (a host without the limits answers nothing, say) is one FAIL, and
    # the other parts still run.
    for part in (check_static_invariants, test_host_bounds, test_mcp_batch_and_single_flight, test_js_units_in_runtime):
        try:
            part()
        except Exception as e:  # noqa: BLE001
            check(f"{part.__name__} ran to its end", False, f"{type(e).__name__}: {e}")
    print(f"\n{passes} passed, {fails} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
