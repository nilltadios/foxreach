/* Injected only into tabs agents may use. Reads the page and resolves element refs. Safe to inject twice. */
(() => {
  "use strict";
  if (globalThis.__foxreach) return;
  globalThis.__foxreach = true;

  const refs = new Map();
  const refOf = new WeakMap();
  let nextRef = 1;

  const SKIP = new Set(["script", "style", "noscript", "template", "head", "meta", "link"]);
  const INTERACTIVE_TAGS = new Set(["a", "button", "input", "select", "textarea", "summary", "option"]);
  const INTERACTIVE_ROLES = new Set([
    "button", "checkbox", "combobox", "link", "listbox", "menuitem", "menuitemcheckbox", "menuitemradio",
    "option", "radio", "searchbox", "slider", "spinbutton", "switch", "tab", "textbox", "treeitem",
  ]);
  const IMPLICIT_ROLES = {
    a: "link", article: "article", aside: "complementary", button: "button", dialog: "dialog", footer: "contentinfo",
    form: "form", h1: "heading", h2: "heading", h3: "heading", h4: "heading", h5: "heading", h6: "heading",
    header: "banner", iframe: "frame", frame: "frame", img: "image", li: "listitem", main: "main", nav: "navigation", ol: "list",
    option: "option", p: "paragraph", progress: "progressbar", section: "region", select: "combobox",
    summary: "button", table: "table", td: "cell", textarea: "textbox", th: "columnheader", tr: "row", ul: "list",
  };
  const INPUT_ROLES = {
    button: "button", checkbox: "checkbox", email: "textbox", image: "button", number: "spinbutton",
    password: "textbox", radio: "radio", range: "slider", reset: "button", search: "searchbox",
    submit: "button", tel: "textbox", text: "textbox", url: "textbox", file: "button",
  };

  const clean = (s, n = 200) => {
    const t = (s ?? "").replace(/\s+/g, " ").trim();
    return t.length > n ? `${t.slice(0, n)}…` : t;
  };
  const shadow = (el) => el.openOrClosedShadowRoot ?? el.shadowRoot ?? null;

  function roleOf(el) {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit.split(/\s+/)[0];
    const tag = el.localName;
    if (tag === "input") return INPUT_ROLES[el.type] ?? "textbox";
    if (el.isContentEditable && !el.parentElement?.isContentEditable) return "textbox";
    return IMPLICIT_ROLES[tag] ?? null;
  }

  function nameOf(el) {
    const aria = el.getAttribute("aria-label");
    if (aria) return clean(aria);
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const root = el.getRootNode();
      const text = labelledby.split(/\s+/).map((id) => root.getElementById?.(id)?.textContent ?? "").join(" ");
      if (clean(text)) return clean(text);
    }
    if (el.labels?.length) {
      const text = Array.from(el.labels, (l) => l.textContent).join(" ");
      if (clean(text)) return clean(text);
    }
    if (el.localName === "input" && ["button", "submit", "reset"].includes(el.type) && el.value) return clean(el.value);
    if (el.alt) return clean(el.alt);
    if (el.placeholder) return clean(el.placeholder);
    if (["a", "button", "summary", "option", "h1", "h2", "h3", "h4", "h5", "h6", "label", "li", "td", "th", "p"].includes(el.localName) ||
        INTERACTIVE_ROLES.has(el.getAttribute("role"))) {
      const text = clean(el.textContent);
      if (text) return text;
    }
    return clean(el.title ?? el.getAttribute("title"));
  }

  function isVisible(el) {
    if (el.checkVisibility && !el.checkVisibility({ checkVisibilityCSS: true, contentVisibilityAuto: true })) {
      return el.localName === "input" && el.type === "file"; // often hidden behind a styled button
    }
    return el.getAttribute("aria-hidden") !== "true";
  }

  function isInteractive(el, role) {
    return INTERACTIVE_TAGS.has(el.localName) || INTERACTIVE_ROLES.has(role) || el.isContentEditable ||
      el.hasAttribute("onclick") || (el.hasAttribute("tabindex") && el.tabIndex >= 0);
  }

  function refFor(el) {
    let id = refOf.get(el);
    if (!id) {
      id = `r${nextRef++}`;
      refOf.set(el, id);
      refs.set(id, new WeakRef(el));
    }
    return id;
  }

  function getRef(id) {
    const el = refs.get(id)?.deref();
    if (!el || !el.isConnected) throw new Error(`Ref ${id} is stale: the element is gone. Call read_page or find again.`);
    return el;
  }

  function stateOf(el) {
    const s = [];
    if (el.localName === "input" && ["checkbox", "radio"].includes(el.type)) s.push(el.checked ? "checked" : "unchecked");
    else if (el.getAttribute("aria-checked")) s.push(`checked=${el.getAttribute("aria-checked")}`);
    if (el.disabled || el.getAttribute("aria-disabled") === "true") s.push("disabled");
    if (el.getAttribute("aria-expanded")) s.push(`expanded=${el.getAttribute("aria-expanded")}`);
    if (el.selected) s.push("selected");
    if (el.required) s.push("required");
    if (el.type === "file") s.push("file input");
    if (el.type === "password") s.push("password");
    else if (el.value !== undefined && el.value !== "" && ["input", "textarea", "select"].includes(el.localName) &&
             !["button", "submit", "reset", "checkbox", "radio"].includes(el.type)) s.push(`value=${JSON.stringify(clean(el.value, 80))}`);
    if (el.href && el.localName === "a") s.push(`href=${clean(el.getAttribute("href"), 100)}`);
    if (/^h[1-6]$/.test(el.localName)) s.push(`level=${el.localName[1]}`);
    return s;
  }

  function line(el, role, depth) {
    const name = nameOf(el);
    const state = stateOf(el);
    const id = el.id ? ` #${clean(el.id, 40)}` : "";
    return `${"  ".repeat(depth)}- ${role ?? el.localName}${name ? ` "${name.replace(/"/g, "'")}"` : ""}${id}` +
      ` [${refFor(el)}]${state.length ? ` (${state.join(", ")})` : ""}`;
  }

  function childNodes(el) {
    const root = shadow(el);
    const light = el.localName === "slot" && el.assignedNodes().length ? el.assignedNodes() : el.childNodes;
    return root ? [...root.childNodes, ...light] : [...light];
  }

  function walk(el, opts, depth, out) {
    if (depth > opts.maxDepth) return;
    if (SKIP.has(el.localName) || !isVisible(el)) return;
    const role = roleOf(el);
    const interactive = isInteractive(el, role);
    const named = role && !["list", "listitem", "row", "region", "form", "paragraph", "cell"].includes(role);
    let emit = interactive || (opts.filter === "all" && (named || role));
    if (emit && opts.viewportOnly && !inViewport(el)) {
      out.offscreen++;
      emit = false;
    }
    let next = depth;
    if (emit) {
      out.lines.push(line(el, role, depth));
      next = depth + 1;
    }
    if (["a", "button", "option", "summary", "select"].includes(el.localName) && emit && el.localName !== "select") return;
    for (const node of childNodes(el)) {
      if (node.nodeType === Node.ELEMENT_NODE) walk(node, opts, next, out);
      else if (opts.filter === "all" && node.nodeType === Node.TEXT_NODE && !emit) {
        const t = clean(node.textContent, 300);
        if (t) {
          out.lines.push(`${"  ".repeat(next)}- text: ${t}`);
        }
      }
    }
  }

  function inViewport(el) {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
  }

  function readPage({ filter = "all", maxDepth = 40, ref, viewportOnly = false }) {
    const out = { lines: [], offscreen: 0 };
    const start = ref ? getRef(ref) : document.documentElement;
    walk(start, { filter, maxDepth, viewportOnly }, 0, out);
    return { lines: out.lines, offscreen: out.offscreen, url: location.href, title: document.title, ...viewport() };
  }

  // find understands plain descriptions without a language model, so it works for any agent.
  const FILLER = new Set(("a an and at by for from in into is it me my of on or our that the their this to " +
    "where which with you your").split(" "));
  const TEXT_FIELD = ["textbox", "searchbox", "combobox", "input", "textarea"];
  const SYNONYMS = {
    bar: TEXT_FIELD, box: TEXT_FIELD, field: TEXT_FIELD, input: TEXT_FIELD, typing: TEXT_FIELD, type: TEXT_FIELD,
    enter: TEXT_FIELD, write: TEXT_FIELD, text: TEXT_FIELD,
    button: ["button"], link: ["link"], image: ["image"], picture: ["image"], heading: ["heading"], title: ["heading"],
    dropdown: ["combobox", "listbox"], select: ["combobox", "listbox"], menu: ["combobox", "listbox", "menuitem"],
    checkbox: ["checkbox", "switch"], tick: ["checkbox"], toggle: ["switch", "checkbox"],
    slider: ["slider"], upload: ["file input"], attach: ["file input"], file: ["file input"],
    login: ["log in", "sign in", "signin"], signin: ["log in", "login"], logout: ["log out", "sign out"],
    signup: ["sign up", "create account", "register"], register: ["sign up", "create account"],
    submit: ["send", "save", "search", "go", "confirm"], send: ["submit"], save: ["submit"], close: ["dismiss", "cancel"],
    search: ["find"], find: ["search"],
  };

  function find({ query, limit = 20 }) {
    const q = query.toLowerCase().trim();
    const words = q.split(/[^\p{L}\p{N}]+/u).filter((w) => w && !FILLER.has(w));
    const tokens = words.length ? words : [q];
    const scored = [];
    const visit = (el) => {
      if (SKIP.has(el.localName) || !isVisible(el)) return;
      const role = roleOf(el);
      const interactive = isInteractive(el, role);
      if (role || interactive) {
        const text = line(el, role, 0);
        const hay = `${text} ${el.getAttribute("name") ?? ""} ${el.type ?? ""} ${el.title ?? ""}`.toLowerCase();
        let score = 0;
        for (const token of tokens) {
          if (hay.includes(token)) score += 1;
          else if (SYNONYMS[token]?.some((alt) => hay.includes(alt))) score += 0.7;
        }
        if (hay.includes(q)) score += 1;
        if (score >= 0.6 * tokens.length) scored.push({ score: score + (interactive ? 0.25 : 0), line: text });
      }
      for (const node of childNodes(el)) if (node.nodeType === Node.ELEMENT_NODE) visit(node);
    };
    visit(document.documentElement);
    scored.sort((a, b) => b.score - a.score);
    return { matches: scored.slice(0, limit), total: scored.length };
  }

  function pageText({ maxChars = 50000 }) {
    const root = document.querySelector("article, main, [role=main]") ?? document.body ?? document.documentElement;
    let text = (root.innerText ?? root.textContent ?? "").trim();
    if (root !== document.body && text.length < 200) text = (document.body?.innerText ?? text).trim();
    return { text: text.slice(0, maxChars), truncated: text.length > maxChars, url: location.href, title: document.title };
  }

  function point({ ref }) {
    const el = getRef(ref);
    el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) throw new Error(`Ref ${ref} has no size on screen.`);
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    let top = document.elementFromPoint(x, y);
    while (top && shadow(top)?.elementFromPoint(x, y) && shadow(top).elementFromPoint(x, y) !== top) top = shadow(top).elementFromPoint(x, y);
    const covered = top && top !== el && !el.contains(top) && !top.contains(el);
    return { x, y, coveredBy: covered ? `${top.localName}${top.id ? `#${top.id}` : ""}` : null };
  }

  function formInput({ ref, value }) {
    const el = getRef(ref);
    const fire = (type) => el.dispatchEvent(new Event(type, { bubbles: true }));
    if (el.localName === "select") {
      const want = String(value);
      const option = [...el.options].find((o) => o.value === want || clean(o.textContent) === want);
      if (!option) throw new Error(`No option "${want}". Options: ${[...el.options].map((o) => clean(o.textContent)).join(", ")}`);
      el.value = option.value;
    } else if (el.localName === "input" && ["checkbox", "radio"].includes(el.type)) {
      el.checked = value === true || value === "true";
    } else if ("value" in el) {
      el.focus();
      el.value = String(value);
    } else if (el.isContentEditable) {
      el.focus();
      el.textContent = String(value);
    } else {
      throw new Error(`Ref ${ref} (${el.localName}) is not a form field.`);
    }
    fire("input");
    fire("change");
    return { ok: true, tag: el.localName };
  }

  function setFiles({ ref, files }) {
    const el = getRef(ref);
    if (el.localName !== "input" || el.type !== "file") throw new Error(`Ref ${ref} is not a file input.`);
    const transfer = new DataTransfer();
    for (const f of files) {
      const bytes = Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0));
      transfer.items.add(new File([bytes], f.name, { type: f.type || "application/octet-stream" }));
    }
    el.files = transfer.files;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return { count: el.files.length };
  }

  function viewport() {
    return {
      width: window.innerWidth, height: window.innerHeight,
      scrollX: window.scrollX, scrollY: window.scrollY, dpr: window.devicePixelRatio,
    };
  }

  function waitCheck({ selector, text }) {
    if (selector && !document.querySelector(selector)) return { found: false };
    if (text && !(document.body?.innerText ?? "").includes(text)) return { found: false };
    return { found: true };
  }

  // Untrusted fallback, for builds without the Experiment. The events have isTrusted=false.
  function untrustedMouse({ action, x, y, button = 0, count = 1 }) {
    const el = document.elementFromPoint(x, y);
    if (!el) throw new Error(`Nothing at (${x}, ${y}).`);
    const init = { bubbles: true, cancelable: true, composed: true, clientX: x, clientY: y, button, view: window };
    if (action === "move") {
      el.dispatchEvent(new MouseEvent("mousemove", init));
      el.dispatchEvent(new MouseEvent("mouseover", init));
    } else {
      for (let n = 1; n <= count; n++) {
        el.dispatchEvent(new MouseEvent("mousedown", { ...init, detail: n }));
        el.focus?.();
        el.dispatchEvent(new MouseEvent("mouseup", { ...init, detail: n }));
        el.dispatchEvent(new MouseEvent(button === 2 ? "contextmenu" : "click", { ...init, detail: n }));
      }
    }
    return { target: el.localName, attempts: 1 };
  }

  function untrustedType({ text }) {
    const el = document.activeElement;
    if (!document.execCommand("insertText", false, text)) throw new Error("Could not type here without trusted input.");
    return { typed: text.length, target: el?.localName };
  }

  function untrustedKeys({ keys }) {
    const el = document.activeElement ?? document.body;
    for (const key of keys.trim().split(/\s+/)) {
      for (const type of ["keydown", "keyup"]) el.dispatchEvent(new KeyboardEvent(type, { key, bubbles: true, cancelable: true }));
    }
    return { pressed: keys };
  }

  function untrustedScroll({ x, y, dx = 0, dy = 0 }) {
    const el = document.elementFromPoint(x, y);
    let node = el;
    while (node && node.scrollHeight <= node.clientHeight && node.scrollWidth <= node.clientWidth) node = node.parentElement;
    (node ?? document.scrollingElement).scrollBy({ left: dx, top: dy, behavior: "instant" });
    return { method: "scrollBy", moved: true };
  }

  /**
   * Agent code for builds without the Experiment. It runs in the page's own world (window.eval),
   * never as this content script: a content script's requests cross sites with the add-on's
   * permissions and are not tied to the tab, so they would reach sites the user has blocked.
   */
  async function evaluate({ code }) {
    const source = `(async () => {
      const value = await (0, eval)(${JSON.stringify(code)});
      const text = JSON.stringify(value);
      return text === undefined ? "undefined" : text;
    })()`;
    try {
      return { text: String(await window.eval(source)) };
    } catch (e) {
      const message = String(e?.message ?? e);
      throw new Error(/content security policy|blocked by csp|unsafe-eval/i.test(message)
        ? `This page's security policy does not allow running scripts on it without trusted input. (${message})`
        : message);
    }
  }

  const TEXT_INPUTS = new Set(["text", "search", "email", "url", "tel", "password", "number", ""]);

  function isTextEntry(el) {
    if (!el) return false;
    if (el.isContentEditable) return true;
    if (el.localName === "textarea") return !el.readOnly && !el.disabled;
    if (el.localName === "input") return TEXT_INPUTS.has(el.type) && !el.readOnly && !el.disabled;
    return false;
  }

  /**
   * Where keys typed now would go in this frame: the deepest focused element, through shadow roots.
   * `focused` is false when this frame does not have focus, or hands it on to a child frame.
   */
  function focusInfo() {
    let el = document.activeElement;
    while (el && shadow(el)?.activeElement) el = shadow(el).activeElement;
    const inChild = !!el && ["iframe", "frame"].includes(el.localName);
    const focused = document.hasFocus() && !inChild;
    if (!el || el === document.body || el === document.documentElement) {
      return { focused, editable: false, desc: "the page body (no element has focus)", value: null };
    }
    const value = el.isContentEditable ? el.textContent
      : "value" in el && typeof el.value === "string" && el.type !== "password" ? el.value : null;
    const desc = line(el, roleOf(el), 0).replace(/^- /, "");
    return { focused, editable: isTextEntry(el), desc, value, length: el.type === "password" ? el.value.length : value?.length };
  }

  const OPS = {
    readPage, evaluate, find, pageText, point, formInput, setFiles, viewport, waitCheck, focusInfo,
    untrustedMouse, untrustedType, untrustedKeys, untrustedScroll,
    ping: () => ({ ok: true }),
    scrollTo: ({ ref }) => ({ ...point({ ref }), ...viewport() }),
  };

  browser.runtime.onMessage.addListener((msg) => {
    if (!msg?.foxreachOp) return undefined;
    return (async () => {
      const op = OPS[msg.foxreachOp];
      if (!op) throw new Error(`Unknown operation ${msg.foxreachOp}`);
      return op(msg.args ?? {});
    })();
  });
})();
