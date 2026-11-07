// Privileged code. Content-process half of trusted input: one actor per frame.

const BUTTON_MASK = { 0: 1, 1: 4, 2: 2 };
const DELIVERY_ATTEMPTS = 6;
const DELIVERY_RETRY_MS = 150;
const WHEEL_START_MS = 200;
const WHEEL_POLL_MS = 60;
const WHEEL_MAX_POLLS = 40;
const DRAG_STEPS = 8;

const NAMED_KEYS = {
  Enter: 13, Tab: 9, Escape: 27, Backspace: 8, Delete: 46, Insert: 45,
  ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40,
  Home: 36, End: 35, PageUp: 33, PageDown: 34,
};
for (let i = 1; i <= 12; i++) NAMED_KEYS[`F${i}`] = 111 + i;

const KEY_ALIASES = {
  return: "Enter", esc: "Escape", del: "Delete", ins: "Insert",
  left: "ArrowLeft", up: "ArrowUp", right: "ArrowRight", down: "ArrowDown",
  pgup: "PageUp", pgdn: "PageDown",
};
const MODIFIERS = {
  ctrl: ["Control", "ControlLeft"], control: ["Control", "ControlLeft"],
  shift: ["Shift", "ShiftLeft"], alt: ["Alt", "AltLeft"],
  meta: ["Meta", "MetaLeft"], cmd: ["Meta", "MetaLeft"], super: ["Meta", "MetaLeft"],
};

const sleep = (win, ms) => new Promise((r) => win.setTimeout(r, ms));

function modifierFlags(win, names = []) {
  const u = win.windowUtils;
  const flags = {
    shift: u.MODIFIER_SHIFT, ctrl: u.MODIFIER_CONTROL, control: u.MODIFIER_CONTROL,
    alt: u.MODIFIER_ALT, meta: u.MODIFIER_META, cmd: u.MODIFIER_META, super: u.MODIFIER_META,
  };
  return names.reduce((all, n) => all | (flags[n.toLowerCase()] ?? 0), 0);
}

function fire(win, type, x, y, { button = 0, buttons = 0, clickCount = 0, modifiers = 0 } = {}) {
  win.synthesizeMouseEvent(
    type, x, y,
    { button, buttons, clickCount, modifiers, inputSource: win.MouseEvent.MOZ_SOURCE_MOUSE },
    { isDOMEventSynthesized: false, isWidgetEventSynthesized: false }
  );
}

/**
 * Press, retrying only if the press was never delivered. Input can be dropped silently right after
 * a page starts; retrying until a real mousedown is seen cannot double-click, because a delivered
 * press stops the loop.
 */
async function pressConfirmed(win, x, y, opts) {
  const root = win.windowRoot;
  let seen = false;
  const onDown = () => (seen = true);
  root.addEventListener("mousedown", onDown, { capture: true, mozSystemGroup: true });
  try {
    for (let attempt = 1; attempt <= DELIVERY_ATTEMPTS; attempt++) {
      fire(win, "mousemove", x, y);
      fire(win, "mousedown", x, y, opts);
      if (!seen) await sleep(win, 0);
      if (seen) return attempt;
      await sleep(win, DELIVERY_RETRY_MS);
    }
  } finally {
    root.removeEventListener("mousedown", onDown, { capture: true, mozSystemGroup: true });
  }
  throw new Error(`The page never received the press at (${x}, ${y}).`);
}

function deepElementFromPoint(doc, x, y) {
  let el = doc.elementFromPoint(x, y);
  while (el?.openOrClosedShadowRoot) {
    const inner = el.openOrClosedShadowRoot.elementFromPoint(x, y);
    if (!inner || inner === el) break;
    el = inner;
  }
  return el;
}

function describe(el) {
  if (!el) return null;
  const id = el.id ? `#${el.id}` : "";
  return `${el.localName}${id}`;
}

function textInputProcessor(win) {
  const tip = Cc["@mozilla.org/text-input-processor;1"].createInstance(Ci.nsITextInputProcessor);
  if (!tip.beginInputTransactionForTests(win)) throw new Error("Could not start keyboard input.");
  return tip;
}

function keyEvent(win, key, code, keyCode) {
  return new win.KeyboardEvent("", { key, code, keyCode });
}

function printable(win, ch) {
  const upper = ch.toUpperCase();
  let code = "";
  let keyCode = 0;
  if (/^[a-z]$/i.test(ch)) {
    code = `Key${upper}`;
    keyCode = upper.charCodeAt(0);
  } else if (/^[0-9]$/.test(ch)) {
    code = `Digit${ch}`;
    keyCode = ch.charCodeAt(0);
  } else if (ch === " ") {
    code = "Space";
    keyCode = 32;
  }
  return keyEvent(win, ch, code, keyCode);
}

function namedKey(win, name) {
  const key = KEY_ALIASES[name.toLowerCase()] ?? name;
  if (Object.hasOwn(NAMED_KEYS, key)) return keyEvent(win, key, key, NAMED_KEYS[key]);
  if (key.toLowerCase() === "space") return printable(win, " ");
  if ([...key].length === 1) return printable(win, key);
  throw new Error(`Unknown key "${name}".`);
}

function pressCombo(win, tip, combo) {
  const parts = combo.split("+").filter(Boolean);
  const main = parts.pop() ?? combo;
  const held = parts.map((p) => {
    const m = MODIFIERS[p.toLowerCase()];
    if (!m) throw new Error(`Unknown modifier "${p}".`);
    return new win.KeyboardEvent("", { key: m[0], code: m[1] });
  });
  for (const ev of held) tip.keydown(ev);
  const ev = namedKey(win, main);
  tip.keydown(ev);
  tip.keyup(ev);
  for (const ev2 of held.reverse()) tip.keyup(ev2);
}

function scrollableAncestor(el, win) {
  for (let node = el; node; node = node.parentElement) {
    const s = win.getComputedStyle(node);
    const can = (o, a, b) => (o === "auto" || o === "scroll") && a > b;
    if (
      can(s.overflowY, node.scrollHeight, node.clientHeight) ||
      can(s.overflowX, node.scrollWidth, node.clientWidth)
    ) {
      return node;
    }
  }
  return win.document.scrollingElement;
}

/** Split off the last top-level statement, so "const a = await f(); a * 2" can return a * 2. */
function splitLastStatement(code) {
  let depth = 0;
  let quote = null;
  let last = 0;
  for (let i = 0; i < code.length; i++) {
    const ch = code[i];
    if (quote) {
      if (ch === "\\") i++;
      else if (ch === quote) quote = null;
    } else if (ch === '"' || ch === "'" || ch === "`") {
      quote = ch;
    } else if (ch === "/" && code[i + 1] === "/") {
      while (i < code.length && code[i] !== "\n") i++;
    } else if (ch === "/" && code[i + 1] === "*") {
      i = code.indexOf("*/", i + 2);
      if (i < 0) return null;
      i++;
    } else if ("([{".includes(ch)) {
      depth++;
    } else if (")]}".includes(ch)) {
      depth--;
    } else if (depth === 0 && (ch === ";" || ch === "\n") && code.slice(i + 1).trim()) {
      last = i + 1;
    }
  }
  return last ? [code.slice(0, last), code.slice(last).trim()] : null;
}

/** Returns whether the page itself accepted: the browser's own drop handling also prevents default. */
function dispatchDrag(win, el, ev) {
  win.windowUtils.dispatchDOMEventViaPresShellForTesting(el, ev);
  return ev.defaultPreventedByContent;
}

function runInPage(win, code) {
  // A sandbox on the page's own principal: real page world, and page CSP does not block it. Its
  // JSON and Promise belong to the sandbox, so a page that replaces those cannot alter results.
  const sandbox = Cu.Sandbox(win, { sandboxPrototype: win, wantXrays: false, sameZoneAs: win });
  const serialize = `.then((v) => {
      if (v === undefined) return "undefined";
      if (typeof v === "function" || typeof v === "symbol") return String(v);
      if (v instanceof Node) return v.outerHTML ?? v.nodeName;
      const seen = new WeakSet();
      return JSON.stringify(v, (k, x) => {
        if (typeof x === "object" && x !== null) {
          if (seen.has(x)) return "[circular]";
          seen.add(x);
        }
        return typeof x === "bigint" ? String(x) : x;
      }) ?? String(v);
    })
    .catch((e) => "Error: " + (e && e.stack ? e + "\\n" + e.stack : e))`;
  const inAsync = (body) => `(async () => ${body}\n)()${serialize}`;
  const body = code.trim().replace(/;+$/, "");
  // Each form below fails with SyntaxError before running anything, so code never runs twice.
  const compileOnly = (source) => {
    try {
      return Cu.evalInSandbox(source, sandbox);
    } catch (e) {
      if (e.name !== "SyntaxError") return `Error: ${e}`;
      return undefined;
    }
  };
  const asExpression = compileOnly(inAsync(`(${body})`));
  if (asExpression !== undefined) return asExpression;
  if (!/\bawait\b/.test(code)) {
    // A plain script: the language's own completion value is its last expression.
    let done = false;
    try {
      sandbox.__foxreachValue = Cu.evalInSandbox(code, sandbox);
      done = true;
    } catch (e) {
      if (e.name !== "SyntaxError") return `Error: ${e}`;
    }
    if (done) return Cu.evalInSandbox(`Promise.resolve(__foxreachValue)${serialize}`, sandbox);
  }
  const split = splitLastStatement(body);
  if (split) {
    const returned = compileOnly(inAsync(`{ ${split[0]}\nreturn (${split[1].replace(/;+$/, "")}\n); }`));
    if (returned !== undefined) return returned;
  }
  return Cu.evalInSandbox(inAsync(`{ ${code}\n}`), sandbox);
}

function consoleEntries(win, { includeLogs = true, includeErrors = true }) {
  const out = [];
  const innerId = win.windowGlobalChild.innerWindowId;
  const render = (v) => {
    try {
      return typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);
    } catch {
      return String(v);
    }
  };
  if (includeLogs) {
    const storage = Cc["@mozilla.org/consoleAPI-storage;1"].getService(Ci.nsIConsoleAPIStorage);
    for (const e of storage.getEvents(innerId)) {
      out.push({
        time: e.timeStamp,
        level: e.level,
        text: Array.from(e.arguments ?? [], render).join(" "),
        source: `${e.filename}:${e.lineNumber}`,
      });
    }
  }
  if (includeErrors) {
    for (const m of Services.console.getMessageArray() ?? []) {
      if (m instanceof Ci.nsIScriptError && m.innerWindowID === innerId) {
        out.push({
          time: m.timeStamp,
          level: m.flags & Ci.nsIScriptError.warningFlag ? "warn" : "error",
          text: m.errorMessage,
          source: `${m.sourceName}:${m.lineNumber}`,
        });
      }
    }
  }
  return out;
}

const HANDLERS = {
  hitTest(win, { x, y }) {
    const el = deepElementFromPoint(win.document, x, y);
    if (!el || !["iframe", "frame"].includes(el.localName) || !el.browsingContext) return {};
    const r = el.getBoundingClientRect();
    return { bc: el.browsingContext.id, x: x - r.left - el.clientLeft, y: y - r.top - el.clientTop };
  },

  frameRect(win, { bc }) {
    for (const el of win.document.querySelectorAll("iframe, frame")) {
      if (el.browsingContext?.id === bc) {
        const r = el.getBoundingClientRect();
        return { x: r.left + el.clientLeft, y: r.top + el.clientTop };
      }
    }
    return null;
  },

  focusedFrame(win) {
    let el = win.document.activeElement;
    while (el?.openOrClosedShadowRoot?.activeElement) el = el.openOrClosedShadowRoot.activeElement;
    return ["iframe", "frame"].includes(el?.localName) && el.browsingContext
      ? { bc: el.browsingContext.id }
      : {};
  },

  async mouse(win, { action, x, y, button = 0, count = 1, modifiers }) {
    const flags = modifierFlags(win, modifiers);
    const target = describe(deepElementFromPoint(win.document, x, y));
    if (action === "move") {
      fire(win, "mousemove", x, y, { modifiers: flags });
      return { target, attempts: 1 };
    }
    const mask = BUTTON_MASK[button];
    let attempts = 0;
    for (let n = 1; n <= count; n++) {
      const opts = { button, buttons: mask, clickCount: n, modifiers: flags };
      attempts = Math.max(attempts, await pressConfirmed(win, x, y, opts));
      if (button === 2) fire(win, "contextmenu", x, y, opts);
      fire(win, "mouseup", x, y, { ...opts, buttons: 0 });
    }
    return { target, attempts };
  },

  async drag(win, { from, to, modifiers }) {
    const flags = modifierFlags(win, modifiers);
    const opts = { button: 0, buttons: 1, clickCount: 1, modifiers: flags };
    // If the page starts an HTML5 drag, run it here. A native drag session would take over the
    // real pointer, and synthesized moves cannot drive it.
    const root = win.windowRoot;
    let dragged = null;
    const onDragStart = (e) => {
      if (e.defaultPrevented) return;
      dragged = { source: e.originalTarget, data: e.dataTransfer };
      e.preventDefault();
    };
    root.addEventListener("dragstart", onDragStart, { mozSystemGroup: true });
    let attempts;
    const steps = [];
    try {
      attempts = await pressConfirmed(win, from.x, from.y, opts);
      for (let i = 1; i <= DRAG_STEPS; i++) {
        const t = i / DRAG_STEPS;
        const point = { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t };
        fire(win, "mousemove", point.x, point.y, { buttons: 1, modifiers: flags });
        await sleep(win, 16);
        if (dragged) steps.push(point);
      }
    } finally {
      root.removeEventListener("dragstart", onDragStart, { mozSystemGroup: true });
    }
    fire(win, "mouseup", to.x, to.y, { ...opts, buttons: 0 });
    const target = describe(deepElementFromPoint(win.document, to.x, to.y));
    if (!dragged) return { target, attempts, htmlDrag: false };

    const send = (type, el, point, data) => {
      const ev = new win.DragEvent(type, {
        bubbles: true, cancelable: true, composed: true, view: win,
        clientX: point.x, clientY: point.y, button: 0,
        dataTransfer: data.mozCloneForEvent ? data.mozCloneForEvent(type) : data,
      });
      return dispatchDrag(win, el, ev);
    };
    let over = null;
    let accepted = false;
    for (const point of steps.length ? steps : [to]) {
      const el = deepElementFromPoint(win.document, point.x, point.y);
      if (!el) continue;
      if (el !== over) {
        if (over) send("dragleave", over, point, dragged.data);
        send("dragenter", el, point, dragged.data);
        over = el;
      }
      accepted = send("dragover", el, point, dragged.data);
      await sleep(win, 16);
    }
    if (over && accepted) send("drop", over, to, dragged.data);
    send("dragend", dragged.source, to, dragged.data);
    return { target, attempts, htmlDrag: true, dropped: !!(over && accepted) };
  },

  async wheel(win, { x, y, dx = 0, dy = 0 }) {
    const el = deepElementFromPoint(win.document, x, y);
    const scroller = scrollableAncestor(el, win);
    const before = { left: scroller?.scrollLeft, top: scroller?.scrollTop };
    // In the system group's bubble phase, so this sees what the page decided.
    let handledByPage = false;
    const onWheel = (e) => (handledByPage ||= e.defaultPrevented);
    const root = win.windowRoot;
    root.addEventListener("wheel", onWheel, { mozSystemGroup: true });
    win.windowUtils.sendWheelEvent(x, y, dx, dy, 0, win.WheelEvent.DOM_DELTA_PIXEL, 0, 0, 0, 0, null);
    await sleep(win, WHEEL_START_MS);
    root.removeEventListener("wheel", onWheel, { mozSystemGroup: true });
    if (handledByPage) return { method: "page", handledByPage: true, moved: false };
    // Wheel scrolling is animated. Report where it ends, not a position part-way through.
    for (let i = 0, last = null; scroller && i < WHEEL_MAX_POLLS; i++) {
      const now = `${scroller.scrollLeft},${scroller.scrollTop}`;
      if (now === last) break;
      last = now;
      await sleep(win, WHEEL_POLL_MS);
    }
    // The wheel does not land exactly on the distance, and does nothing in a background tab.
    scroller?.scrollTo({ left: before.left + dx, top: before.top + dy, behavior: "instant" });
    return {
      method: "wheel",
      scrollLeft: scroller?.scrollLeft,
      scrollTop: scroller?.scrollTop,
      moved: !!scroller && (before.left !== scroller.scrollLeft || before.top !== scroller.scrollTop),
    };
  },

  keys(win, { keys, repeat = 1 }) {
    const tip = textInputProcessor(win);
    for (let i = 0; i < repeat; i++) {
      for (const combo of keys.trim().split(/\s+/)) pressCombo(win, tip, combo);
    }
    return { pressed: keys, repeat };
  },

  async type(win, { text }) {
    const tip = textInputProcessor(win);
    let typed = 0;
    for (const ch of text.replace(/\r\n?/g, "\n")) {
      if (ch === "\n" || ch === "\t") {
        const ev = namedKey(win, ch === "\n" ? "Enter" : "Tab");
        tip.keydown(ev);
        tip.keyup(ev);
      } else if (/^[\x20-\x7e]$/.test(ch)) {
        const ev = printable(win, ch);
        tip.keydown(ev);
        tip.keyup(ev);
      } else {
        // No physical key produces this character here; deliver it the way an IME would.
        tip.commitCompositionWith(ch);
      }
      if (++typed % 40 === 0) await sleep(win, 0);
    }
    return { typed };
  },

  dropFiles(win, { x, y, files }) {
    const el = deepElementFromPoint(win.document, x, y);
    if (!el) throw new Error(`Nothing at (${x}, ${y}) to drop on.`);
    // A page cannot see DataTransfer items added under another principal, so build it under its own.
    const sandbox = Cu.Sandbox(win, { sandboxPrototype: win, wantXrays: false, sameZoneAs: win });
    sandbox.__files = Cu.cloneInto(files, sandbox);
    const data = Cu.evalInSandbox(
      `(() => {
        const transfer = new DataTransfer();
        for (const f of __files) {
          const bytes = Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0));
          transfer.items.add(new File([bytes], f.name, { type: f.type || "application/octet-stream" }));
        }
        return transfer;
      })()`,
      sandbox
    );
    const send = (type) => {
      const ev = new win.DragEvent(type, {
        bubbles: true, cancelable: true, composed: true, view: win, clientX: x, clientY: y, dataTransfer: data,
      });
      return dispatchDrag(win, el, ev);
    };
    send("dragenter");
    const accepted = send("dragover");
    if (accepted) send("drop");
    return { count: files.length, target: describe(el), accepted };
  },

  evaluate: (win, { code }) => runInPage(win, code),

  console: (win, args) => consoleEntries(win, args),
};

export class FoxreachInputChild extends JSWindowActorChild {
  receiveMessage({ name, data }) {
    if (!Object.hasOwn(HANDLERS, name)) throw new Error(`Unknown message ${name}`);
    return HANDLERS[name](this.contentWindow, data);
  }
}
