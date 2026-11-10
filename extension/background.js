"use strict";

const VERSION = browser.runtime.getManifest().version;
const HOST_NAME = "foxreach";
const RECONNECT_MS = 2000;
const RECONNECT_BUSY_MS = 30000;
const NET_LIMIT = 1000;
const LOG_LIMIT = 200;
const RESULT_LIMIT = 500000;
const CONTENT_TIMEOUT_MS = 15000;
const NAV_TIMEOUT_MS = 30000;
const GROUP_TITLE = "agent";

const INPUT_SETTLE_MS = 120;
const INPUT_NAV_TIMEOUT_MS = 10000;
const SCREENSHOT_LOAD_TIMEOUT_MS = 8000;
const RECORD_MAX_FRAMES = 150;
const RECORD_WIDTH = 900;
const RECORDED_TOOLS = new Set(["click", "hover", "drag", "type_text", "press_key", "scroll", "scroll_to", "form_input", "file_upload", "navigate", "tabs_select"]);

const trusted = typeof browser.foxreachInput !== "undefined";
const recordings = new Map();
const consoleCleared = new Map();
const openedBy = new Map();
const pointers = new Map();
const POINTER_SHAPE = [[0, 0], [0, 17], [4, 13], [7, 20], [10, 19], [7, 12], [12, 12]];
const agentTabs = new Set();
const network = new Map();
const activity = [];
const partial = new Map();
// siteAccess: "all", or "listed" for only the sites in allowedHosts. Set at first start.
let settings = { paused: false, blockedHosts: [], siteAccess: null, allowedHosts: [], askApproval: true, approvedAgents: [] };
let port = null;
let hostError = null;
let agentGroupId = null;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const privileged = (tabId, method, args) => browser.foxreachInput.call({ tabId, method, args });
const DIALOG_NOTE = " A dialog opened and is blocking the page; use handle_dialog.";

function withTimeout(promise, ms, message) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(message)), ms);
    }),
  ]).finally(() => clearTimeout(timer));
}

/* ---------- access policy ---------- */

// "bank.example." is the same site as "bank.example"; compare without the trailing dot.
const plainHost = (host) => host.toLowerCase().replace(/\.+$/, "");

function hostOf(url) {
  try {
    return plainHost(new URL(url).hostname);
  } catch {
    return "";
  }
}

/** A listed host covers its subdomains. */
const inList = (host, list) => !!host && list.some((b) => host === b || host.endsWith(`.${b}`));

const hostBlocked = (url) => inList(hostOf(url), settings.blockedHosts);

/* A request the user has to answer opens its own small window: the toolbar button may be tucked
   away in the browser's extensions menu, where a badge on it is never seen. One window at most. */
let requestWindow = null;

async function showRequests() {
  try {
    if (requestWindow !== null && (await browser.windows.get(requestWindow).catch(() => null))) {
      await browser.windows.update(requestWindow, { focused: true });
      return;
    }
    requestWindow = -1; // being opened: a second request in the same moment must not open another
    requestWindow = (await browser.windows.create({ type: "popup", url: "/popup/popup.html?requests", width: 420, height: 560 })).id;
  } catch (e) {
    requestWindow = null;
    console.error("foxreach: could not show the request window", e);
  }
}

/* Site access. The user either lets agents use every site, or only the ones on a list. Under that,
   the browser must have given this add-on the site: the full build has every site from the start,
   the build without privileged code asks the user for each one (or for all) from the popup. */

const SITE_REQUEST_LIMIT = 20;
const siteRequests = new Map(); // host -> when an agent was last refused there, for the popup
const wantedSites = new Set(); // hosts (or "*") the user is granting from the popup right now
const originsOf = (host) => (host === "*" ? ["<all_urls>"] : [`*://${host}/*`, `*://*.${host}/*`]);
const isWeb = (url) => /^https?:/.test(url);
const browserGrants = (host) => browser.permissions.contains({ origins: [originsOf(host)[0]] });

/** Other addresses (about:blank, a frame's srcdoc) belong to no site; reading them fails or not by itself. */
async function siteAllowed(url) {
  if (!isWeb(url)) return true;
  return siteListed(url) && browserGrants(hostOf(url));
}

async function assertSiteAllowed(url) {
  if (await siteAllowed(url)) return;
  const host = hostOf(url);
  if (!siteRequests.has(host)) showRequests();
  siteRequests.delete(host);
  siteRequests.set(host, Date.now());
  if (siteRequests.size > SITE_REQUEST_LIMIT) siteRequests.delete(siteRequests.keys().next().value);
  updateBadge();
  throw new Error(`Agents are not allowed on ${host} yet. It now shows as a request under the foxreach toolbar button: ask the user to allow it there, then try again.`);
}

/** Move what the user asked for into the settings, once the browser has granted it. */
async function applyWanted() {
  for (const host of [...wantedSites]) {
    if (!(await browserGrants(host))) continue;
    wantedSites.delete(host);
    if (host === "*") settings.siteAccess = "all";
    else if (!settings.allowedHosts.includes(host)) settings.allowedHosts.push(host);
    // What was asked for and is now allowed needs no answer any more.
    for (const asked of [...siteRequests.keys()]) if (siteListed(`http://${asked}/`)) siteRequests.delete(asked);
  }
  await browser.storage.local.set({ settings });
  updateBadge();
}
browser.permissions.onAdded.addListener(applyWanted);

/* Agents. Any program of this user can reach the socket, so a new one waits for the user's yes.
   The name is what the program calls itself: this guards against an agent the user did not mean to
   let in, not against a program that lies. */

const APPROVAL_WAIT_MS = 60000;
const allowedConns = new Set(); // connections allowed until they close
const deniedConns = new Set();
const agentRequests = new Map(); // connection -> { name, time, waiters }

async function assertAgentAllowed(conn, name) {
  if (!settings.askApproval || allowedConns.has(conn) || settings.approvedAgents.includes(name)) return;
  if (deniedConns.has(conn)) throw new Error(`The user refused the agent "${name}".`);
  let request = agentRequests.get(conn);
  if (!request) {
    request = { name, time: Date.now(), waiters: [] };
    agentRequests.set(conn, request);
    updateBadge();
    showRequests();
  }
  const answer = await new Promise((resolve) => {
    request.waiters.push(resolve);
    setTimeout(() => resolve("unanswered"), APPROVAL_WAIT_MS);
  });
  if (answer === "allow") return;
  if (answer === "deny") throw new Error(`The user refused the agent "${name}".`);
  throw new Error(`The user has not yet allowed the agent "${name}" to use the browser. A request is waiting under the foxreach toolbar button: ask the user to answer it, then try again.`);
}

function answerAgent(conn, mode) {
  const request = agentRequests.get(conn);
  if (!request) return;
  agentRequests.delete(conn);
  if (mode === "deny") deniedConns.add(conn);
  else allowedConns.add(conn);
  if (mode === "always" && !settings.approvedAgents.includes(request.name)) settings.approvedAgents.push(request.name);
  for (const done of request.waiters) done(mode === "deny" ? "deny" : "allow");
}

function forgetConnection(conn) {
  agentRequests.get(conn)?.waiters.forEach((done) => done("unanswered"));
  agentRequests.delete(conn);
  allowedConns.delete(conn);
  deniedConns.delete(conn);
  updateBadge();
}

/** What the user typed, as host names: "https://Bank.example:443/login" and "*.bank.example" both mean bank.example. */
function parseBlocked(value) {
  const hosts = String(value).split(/[\s,]+/).map((entry) => {
    const bare = entry.trim().replace(/^\*\./, "");
    if (!bare) return "";
    try {
      return plainHost(new URL(bare.includes("://") ? bare : `http://${bare}`).hostname);
    } catch {
      return plainHost(bare);
    }
  });
  return [...new Set(hosts.filter(Boolean))];
}

/** Whether the user's own list lets agents on this address (the browser's grant is asked separately). */
const siteListed = (url) => !isWeb(url) || settings.siteAccess === "all" || inList(hostOf(url), settings.allowedHosts);

/**
 * A tab as shown to agents. A title can itself be private ("Inbox (3) - name@..."), so a site
 * agents may not use shows without it: a blocked one not at all, an unlisted one by host, which
 * is what the agent has to ask the user for.
 */
function tabLine(tab) {
  if (hostBlocked(tab.url)) return "a site the user has blocked for agents";
  if (!siteListed(tab.url)) return `a site agents are not allowed on yet (${hostOf(tab.url)})`;
  return `${tab.title} <${tab.url}>`;
}

/** A frame keeps its own site: a blocked one stays unreadable inside a page that is allowed. */
async function assertFrameAllowed(tabId, frameId) {
  if (!frameId) return;
  const frame = await browser.webNavigation.getFrame({ tabId, frameId }).catch(() => null);
  if (!frame) return;
  if (hostBlocked(frame.url)) throw new Error("The user has blocked agents on this frame's site.");
  await assertSiteAllowed(frame.url);
}

async function accessibleTab(tabId) {
  if (!Number.isInteger(tabId)) throw new Error("tabId is required. Use tabs_list or tabs_create.");
  if (!agentTabs.has(tabId)) {
    throw new Error(`Tab ${tabId} is not available to agents. Use tabs_create, or ask the user to share it from the foxreach toolbar button.`);
  }
  const tab = await browser.tabs.get(tabId).catch(() => null);
  if (!tab) {
    agentTabs.delete(tabId);
    throw new Error(`Tab ${tabId} no longer exists.`);
  }
  if (hostBlocked(tab.url)) throw new Error(`The user has blocked agents on this site (${new URL(tab.url).hostname}).`);
  await assertSiteAllowed(tab.url);
  return tab;
}

async function assertNoDialog(tabId) {
  if (!trusted) return;
  const state = await privileged(tabId, "dialogState");
  if (state.open) {
    throw new Error(`A ${state.type} dialog is open and is blocking the page: ${JSON.stringify(state.message)}. Use handle_dialog.`);
  }
}

async function adoptTab(tab) {
  agentTabs.add(tab.id);
  updateBadge();
  if (tab.incognito) return;
  try {
    if (agentGroupId !== null) {
      const exists = await browser.tabGroups.get(agentGroupId).catch(() => null);
      if (!exists || exists.windowId !== tab.windowId) agentGroupId = null;
    }
    const groupId = await browser.tabs.group(
      // A new group goes into the tab's own window: the default is the window in front, which may
      // be foxreach's request window, and the tab would move there and close with it.
      agentGroupId === null ? { tabIds: [tab.id], createProperties: { windowId: tab.windowId } } : { tabIds: [tab.id], groupId: agentGroupId }
    );
    if (agentGroupId === null) {
      agentGroupId = groupId;
      await browser.tabGroups.update(groupId, { title: GROUP_TITLE, color: "orange" });
    }
  } catch {
    // Grouping only helps the user see which tabs are agent tabs; access does not depend on it.
  }
}

function updateBadge() {
  browser.browserAction.setBadgeBackgroundColor({ color: settings.paused ? "#c92a2a" : "#d9480f" });
  const asking = agentRequests.size || siteRequests.size;
  browser.browserAction.setBadgeText({ text: settings.paused ? "||" : asking ? "?" : agentTabs.size ? String(agentTabs.size) : "" });
}

/* ---------- content script calls ---------- */

async function ensureInjected(tabId) {
  try {
    await browser.tabs.executeScript(tabId, { file: "/content/content.js", allFrames: true, runAt: "document_start" });
  } catch (e) {
    const tab = await browser.tabs.get(tabId);
    throw new Error(`Cannot read this page (${tab.url}): ${e.message}`);
  }
}

async function contentCall(tabId, frameId, op, args) {
  await assertFrameAllowed(tabId, frameId);
  await assertNoDialog(tabId);
  await ensureInjected(tabId);
  return withTimeout(
    browser.tabs.sendMessage(tabId, { foxreachOp: op, args }, { frameId: frameId ?? 0 }),
    CONTENT_TIMEOUT_MS,
    `The page did not answer within ${CONTENT_TIMEOUT_MS / 1000} s; it may be busy.`
  );
}

const parseRef = (ref) => {
  const m = /^(?:f(\d+):)?(r\d+)$/.exec(String(ref));
  if (!m) throw new Error(`Bad ref "${ref}". Refs look like r12 or f123:r12.`);
  return { frameId: m[1] ? Number(m[1]) : 0, ref: m[2] };
};

const frameRef = (frameId, text) => (frameId ? text.replace(/\[(r\d+)\]/g, `[f${frameId}:$1]`) : text);

async function allFrames(tabId) {
  return (await browser.webNavigation.getAllFrames({ tabId })) ?? [{ frameId: 0, url: "" }];
}

async function eachFrame(tabId, op, args) {
  await assertNoDialog(tabId);
  await ensureInjected(tabId);
  const frames = await allFrames(tabId);
  frames.sort((a, b) => a.frameId - b.frameId);
  const results = await Promise.all(
    frames.map(async (f) => {
      if (f.frameId && hostBlocked(f.url)) return { frame: { frameId: f.frameId, url: "blocked site" }, error: "the user has blocked agents on this frame's site" };
      if (f.frameId && !(await siteAllowed(f.url))) return { frame: f, error: "agents are not allowed on this frame's site" };
      try {
        const value = await withTimeout(
          browser.tabs.sendMessage(tabId, { foxreachOp: op, args }, { frameId: f.frameId }),
          CONTENT_TIMEOUT_MS,
          "frame timed out"
        );
        return { frame: f, value };
      } catch (e) {
        return { frame: f, error: e.message };
      }
    })
  );
  return results;
}

/** Point for an input action: an explicit viewport point, or the centre of a ref. */
async function targetPoint(tabId, args) {
  if (args.ref) {
    const { frameId, ref } = parseRef(args.ref);
    const p = await contentCall(tabId, frameId, "point", { ref });
    return { x: p.x, y: p.y, frameId, coveredBy: p.coveredBy };
  }
  if (typeof args.x !== "number" || typeof args.y !== "number") throw new Error("Give either ref, or x and y.");
  return { x: args.x, y: args.y, frameId: 0, coveredBy: null };
}

const BUTTONS = { left: 0, middle: 1, right: 2 };

/** Remember where the pointer is in viewport coordinates. A frame's own point can't be used directly. */
function notePointer(tabId, point, result) {
  const place = result?.viewportPoint ?? (point.frameId ? null : point);
  if (place) pointers.set(tabId, { x: place.x, y: place.y });
  else pointers.delete(tabId);
}

async function dispatchMouse(tabId, point, { action, button = 0, count = 1, modifiers }) {
  const result = trusted
    ? await privileged(tabId, "mouse", { frameId: point.frameId, x: point.x, y: point.y, action, button, count, modifiers })
    : await contentCall(tabId, point.frameId, "untrustedMouse", { x: point.x, y: point.y, action, button, count });
  notePointer(tabId, point, result);
  return result;
}

const inputNote = (point, res) =>
  (trusted ? "" : " (untrusted event: sites can ignore it)") +
  (point.coveredBy ? ` Warning: the element is covered by ${point.coveredBy}, which took the input.` : "") +
  (res?.attempts > 1 ? ` Delivered on attempt ${res.attempts}.` : "");

/* ---------- navigation ---------- */

/**
 * Resolves with true on timeout. A "complete" only counts after this navigation's "loading", and,
 * when leaving about:blank, once the tab has left it: the blank page's own late events look the same.
 */
function waitForLoad(tabId, { leavingBlank = false, timeoutMs = NAV_TIMEOUT_MS } = {}) {
  return new Promise((resolve) => {
    let loading = false;
    const finish = (timedOut) => {
      clearTimeout(timer);
      browser.tabs.onUpdated.removeListener(listener);
      resolve(timedOut);
    };
    const listener = async (id, change, tab) => {
      if (id !== tabId) return;
      if (change.status === "loading") loading = true;
      else if (change.status === "complete" && loading && !(leavingBlank && tab.url === "about:blank")) finish(false);
    };
    const timer = setTimeout(() => finish(true), timeoutMs);
    browser.tabs.onUpdated.addListener(listener);
  });
}

async function untilIdle(tabId, timeoutMs = 2500) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if ((await browser.tabs.get(tabId)).status === "complete") return true;
    if (trusted && (await privileged(tabId, "dialogState")).open) return false;
    await sleep(50);
  }
  return false;
}

/** After input: if it started a navigation, wait for the new page, so the next read is not stale. */
async function afterInput(tabId, before) {
  await sleep(INPUT_SETTLE_MS);
  const tab = await browser.tabs.get(tabId).catch(() => null);
  if (!tab) return " The tab closed.";
  if (tab.status !== "complete") await untilIdle(tabId, INPUT_NAV_TIMEOUT_MS);
  const now = await browser.tabs.get(tabId).catch(() => null);
  const opened = openedBy.get(tabId) ?? [];
  openedBy.delete(tabId);
  return (now && now.url !== before.url ? ` The page is now ${tabLine(now)}.` : "") +
    (opened.length ? ` This opened new tab ${opened.join(", ")}.` : "");
}

async function settle(tabId, options) {
  // A dialog during load would otherwise hold this up for the whole timeout.
  const load = waitForLoad(tabId, options);
  const stopWatching = { done: false };
  const dialog = (async () => {
    while (!stopWatching.done) {
      await sleep(250);
      const state = await privileged(tabId, "dialogState").catch(() => ({ open: false }));
      if (state.open) return "dialog";
    }
    return null;
  })();
  const outcome = await (trusted ? Promise.race([load, dialog]) : load);
  stopWatching.done = true;
  const tab = await browser.tabs.get(tabId);
  const suffix = outcome === "dialog" ? " A dialog is open; use handle_dialog." : outcome === true ? " The page is still loading." : "";
  return `Tab ${tabId}: ${tabLine(tab)}.${suffix}`;
}

async function checkUrl(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    parsed = new URL(`https://${url}`);
  }
  if (!["http:", "https:", "about:"].includes(parsed.protocol) || (parsed.protocol === "about:" && parsed.href !== "about:blank")) {
    throw new Error(`Agents may only open http, https and about:blank, not ${parsed.protocol}`);
  }
  if (hostBlocked(parsed.href)) throw new Error(`The user has blocked agents on this site (${parsed.hostname}).`);
  await assertSiteAllowed(parsed.href);
  return parsed.href;
}

/** A picture of a tab. The browser offers this only to an add-on that holds every site. */
async function capture(tabId, options) {
  if (!browser.tabs.captureTab) {
    throw new Error("The browser gives pictures of pages only to an add-on that is allowed on every site, and the user has allowed single sites. read_page and find work without pictures; for pictures, ask the user to choose \"Every site\" under the foxreach toolbar button.");
  }
  return browser.tabs.captureTab(tabId, options);
}

/* ---------- tools ---------- */

const TOOLS = {
  async status() {
    return {
      text: JSON.stringify({
        version: VERSION,
        browser: await browser.runtime.getBrowserInfo(),
        trustedInput: trusted,
        paused: settings.paused,
        siteAccess: settings.siteAccess,
        agentTabs: agentTabs.size,
      }),
    };
  },

  async tabs_list() {
    const all = await browser.tabs.query({});
    const mine = all.filter((t) => agentTabs.has(t.id));
    const lines = mine.map((t) => `${t.id}${t.active ? " (active)" : ""}: ${tabLine(t)}`);
    return { text: `${lines.join("\n") || "No tabs yet. Use tabs_create."}\n(${all.length - mine.length} other tabs are not available to agents.)` };
  },

  async tabs_create({ url = "about:blank", active = true }) {
    const target = await checkUrl(url);
    // In an ordinary browser window: the window in front may be foxreach's own request window,
    // and a tab put there would close with it.
    const windows = (await browser.windows.getAll()).filter((w) => w.type === "normal"); // the windowTypes filter is ignored
    const home = windows.find((w) => w.focused) ?? windows[0];
    const tab = await browser.tabs.create({ url: "about:blank", active, ...(home ? { windowId: home.id } : {}) });
    // Adopt before loading, so network capture sees the first request.
    await adoptTab(tab);
    await untilIdle(tab.id);
    if (target === "about:blank") return { text: `Tab ${tab.id}: about:blank.` };
    const done = settle(tab.id, { leavingBlank: true });
    await browser.tabs.update(tab.id, { url: target });
    return { text: await done };
  },

  async tabs_close({ tabId }) {
    await accessibleTab(tabId);
    await browser.tabs.remove(tabId);
    return { text: `Closed tab ${tabId}.` };
  },

  async tabs_select({ tabId }) {
    const tab = await accessibleTab(tabId);
    await browser.tabs.update(tabId, { active: true });
    await browser.windows.update(tab.windowId, { focused: true });
    // The browser ignores the request while a prompt of its own covers the window (a first-run
    // or terms-of-use prompt in a new profile, for one). Tools still work on the tab meanwhile.
    if (!(await browser.tabs.get(tabId)).active) {
      return { text: `Tab ${tabId} was not brought to the front: a prompt in the browser window is in the way, and the user has to answer it. Tools still work on the tab in the background.` };
    }
    return { text: `Selected tab ${tabId}.` };
  },

  async navigate({ tabId, url }) {
    if (tabId === undefined) {
      if (["back", "forward", "reload"].includes(url)) throw new Error("tabId is required for back, forward and reload.");
      return TOOLS.tabs_create({ url });
    }
    await accessibleTab(tabId);
    if (url === "back" || url === "forward" || url === "reload") {
      const done = settle(tabId);
      await { back: browser.tabs.goBack, forward: browser.tabs.goForward, reload: browser.tabs.reload }[url](tabId);
      return { text: await done };
    }
    const target = await checkUrl(url);
    const done = settle(tabId, { leavingBlank: target !== "about:blank" });
    await browser.tabs.update(tabId, { url: target });
    return { text: await done };
  },

  async read_page({ tabId, filter = "all", max_depth = 40, ref, max_chars = 50000, scope }) {
    await accessibleTab(tabId);
    // The whole of a long page's links can be 30 times what is on screen, and costs an agent as much.
    const viewportOnly = (scope ?? (filter === "interactive" ? "viewport" : "page")) === "viewport";
    let sections = [];
    let offscreen = 0;
    let view = null;
    if (ref) {
      const parsed = parseRef(ref);
      const r = await contentCall(tabId, parsed.frameId, "readPage", { filter, maxDepth: max_depth, ref: parsed.ref });
      sections = [frameRef(parsed.frameId, r.lines.join("\n"))];
    } else {
      const results = await eachFrame(tabId, "readPage", { filter, maxDepth: max_depth, viewportOnly });
      for (const { frame, value, error } of results) {
        if (error) {
          sections.push(`--- frame ${frame.frameId} <${frame.url}>: could not read (${error})`);
          continue;
        }
        offscreen += value.offscreen;
        if (frame.frameId === 0) view = value;
        const header = frame.frameId === 0 ? `Page: ${value.title} <${value.url}>` : `--- frame ${frame.frameId} <${value.url}>`;
        sections.push(`${header}\n${frameRef(frame.frameId, value.lines.join("\n"))}`);
      }
    }
    const full = sections.join("\n");
    let text = full;
    if (full.length > max_chars) {
      const cut = full.lastIndexOf("\n", max_chars);
      text = `${full.slice(0, cut > 0 ? cut : max_chars)}\n[truncated at ${cut > 0 ? cut : max_chars} of ${full.length} characters. Pass a larger max_chars, or read part of the page with ref.]`;
    }
    if (offscreen) text += `\n[${offscreen} more elements are off screen: scroll, use find, or pass scope "page".]`;
    if (view) text += `\nViewport: ${view.width}x${view.height}, scrolled to (${view.scrollX}, ${view.scrollY}).`;
    return { text };
  },

  async find({ tabId, query, limit = 20 }) {
    await accessibleTab(tabId);
    if (!query) throw new Error("query is required.");
    const results = await eachFrame(tabId, "find", { query, limit });
    const scored = results.flatMap(({ frame, value }) =>
      (value?.matches ?? []).map((m) => ({ score: m.score, line: frameRef(frame.frameId, m.line) })));
    scored.sort((a, b) => b.score - a.score);
    const total = results.reduce((n, r) => n + (r.value?.total ?? 0), 0);
    if (!scored.length) return { text: `Nothing matches "${query}".` };
    const lines = scored.slice(0, limit).map((m) => m.line);
    return { text: lines.join("\n") + (total > limit ? `\n[${total} matches, best ${limit} shown. Be more specific.]` : "") };
  },

  async get_page_text({ tabId, max_chars = 50000 }) {
    await accessibleTab(tabId);
    const r = await contentCall(tabId, 0, "pageText", { maxChars: max_chars });
    return { text: `${r.title} <${r.url}>\n\n${r.text}${r.truncated ? "\n[truncated]" : ""}` };
  },

  async click(args) {
    const { tabId, button = "left", count = 1, modifiers } = args;
    const before = await accessibleTab(tabId);
    await assertNoDialog(tabId);
    if (!(button in BUTTONS)) throw new Error(`button must be left, middle or right.`);
    const point = await targetPoint(tabId, args);
    const res = await dispatchMouse(tabId, point, { action: "click", button: BUTTONS[button], count, modifiers });
    recordPoint(tabId, point);
    let dialog = trusted ? await privileged(tabId, "dialogState") : {};
    const moved = dialog.open ? "" : await afterInput(tabId, before);
    if (!dialog.open && trusted) dialog = await privileged(tabId, "dialogState").catch(() => ({}));
    return {
      text: `Clicked ${res.target ?? "the element"} at (${Math.round(point.x)}, ${Math.round(point.y)}).${inputNote(point, res)}${moved}` +
        (dialog.open ? ` A ${dialog.type} dialog opened: ${JSON.stringify(dialog.message)}. Use handle_dialog.` : ""),
    };
  },

  async hover(args) {
    await accessibleTab(args.tabId);
    await assertNoDialog(args.tabId);
    const point = await targetPoint(args.tabId, args);
    const res = await dispatchMouse(args.tabId, point, { action: "move" });
    return { text: `Moved the pointer over ${res.target ?? "the page"}.${inputNote(point, res)}${res.blockedByDialog ? DIALOG_NOTE : ""}` };
  },

  async drag({ tabId, from_x, from_y, to_x, to_y, modifiers }) {
    await accessibleTab(tabId);
    await assertNoDialog(tabId);
    if (!trusted) throw new Error("drag needs trusted input, which this build does not have.");
    const res = await privileged(tabId, "drag", { from: { x: from_x, y: from_y }, to: { x: to_x, y: to_y }, modifiers });
    pointers.set(tabId, { x: to_x, y: to_y });
    if (res.blockedByDialog) return { text: `Dragged.${DIALOG_NOTE}` };
    const kind = res.htmlDrag ? (res.dropped ? " The page accepted the drop." : " The page did not accept a drop here.") : "";
    return { text: `Dragged to ${res.target ?? "the release point"} at (${to_x}, ${to_y}).${kind}` };
  },

  async type_text({ tabId, text, ref }) {
    await accessibleTab(tabId);
    await assertNoDialog(tabId);
    if (typeof text !== "string") throw new Error("text is required.");
    if (ref) {
      const point = await targetPoint(tabId, { ref });
      await dispatchMouse(tabId, point, { action: "click" });
    }
    const res = trusted
      ? await privileged(tabId, "type", { text })
      : await contentCall(tabId, 0, "untrustedType", { text });
    if (res.blockedByDialog) return { text: `Typing was interrupted.${DIALOG_NOTE}` };
    return { text: `Typed ${res.typed} characters.${trusted ? "" : " (untrusted)"}` };
  },

  async press_key({ tabId, keys, repeat = 1 }) {
    const before = await accessibleTab(tabId);
    await assertNoDialog(tabId);
    if (!keys) throw new Error("keys is required, for example \"Enter\" or \"ctrl+a Delete\".");
    const res = trusted
      ? await privileged(tabId, "keys", { keys, repeat })
      : await contentCall(tabId, 0, "untrustedKeys", { keys });
    const moved = res.blockedByDialog ? "" : await afterInput(tabId, before);
    return { text: `Pressed ${keys}${repeat > 1 ? ` x${repeat}` : ""}.${trusted ? "" : " (untrusted)"}${res.blockedByDialog ? DIALOG_NOTE : moved}` };
  },

  async scroll(args) {
    const { tabId, dx = 0, dy = 0, screenshot = true } = args;
    await accessibleTab(tabId);
    await assertNoDialog(tabId);
    const explicit = !!args.ref || typeof args.x === "number";
    let point;
    if (explicit) point = await targetPoint(tabId, args);
    else {
      const v = await contentCall(tabId, 0, "viewport", {});
      point = { x: v.width / 2, y: v.height / 2, frameId: 0 };
    }
    const res = trusted
      ? await privileged(tabId, "wheel", { frameId: point.frameId, x: point.x, y: point.y, dx, dy })
      : await contentCall(tabId, point.frameId, "untrustedScroll", { x: point.x, y: point.y, dx, dy });
    if (res.blockedByDialog) return { text: `Scrolling was interrupted.${DIALOG_NOTE}` };
    if (explicit) notePointer(tabId, point, res);
    const text = res.handledByPage
      ? "The page handled the wheel event itself, so it did not scroll."
      : res.moved
        ? `Scrolled to (${res.scrollLeft}, ${res.scrollTop}).`
        : "Nothing scrolled: already at the limit, or nothing scrollable here.";
    if (!screenshot || !browser.tabs.captureTab) return { text };
    // The scroll happened, so a failed picture must not turn it into a failed call.
    const shot = await TOOLS.screenshot({ tabId }).catch((e) => ({ text: `Screenshot failed: ${e.message}` }));
    return { image: shot.image, text: `${text} ${shot.text}` };
  },

  async scroll_to({ tabId, ref }) {
    await accessibleTab(tabId);
    const parsed = parseRef(ref);
    const p = await contentCall(tabId, parsed.frameId, "scrollTo", { ref: parsed.ref });
    return { text: `Ref ${ref} is centred at (${Math.round(p.x)}, ${Math.round(p.y)}).` };
  },

  async form_input({ tabId, ref, value }) {
    await accessibleTab(tabId);
    const parsed = parseRef(ref);
    const r = await contentCall(tabId, parsed.frameId, "formInput", { ref: parsed.ref, value });
    return { text: `Set ${r.tag} ${ref}.` };
  },

  async file_upload({ tabId, ref, x, y, files }) {
    await accessibleTab(tabId);
    if (!ref) {
      if (typeof x !== "number" || typeof y !== "number") throw new Error("Give ref for a file input, or x and y for a drop zone.");
      if (!trusted) throw new Error("Dropping files needs trusted input, which this build does not have.");
      await assertNoDialog(tabId);
      const dropped = await privileged(tabId, "dropFiles", { x, y, files });
      if (dropped.blockedByDialog) return { text: `Dropping was interrupted.${DIALOG_NOTE}` };
      if (!dropped.accepted) throw new Error(`${dropped.target} at (${x}, ${y}) does not accept dropped files.`);
      return { text: `Dropped ${dropped.count} file(s) on ${dropped.target}.` };
    }
    const parsed = parseRef(ref);
    const r = await contentCall(tabId, parsed.frameId, "setFiles", { ref: parsed.ref, files });
    return { text: `Attached ${r.count} file(s) to ${ref}.` };
  },

  async screenshot({ tabId, region, format = "jpeg", quality = 75, scale = 1, show_pointer = true }) {
    await accessibleTab(tabId);
    await assertNoDialog(tabId);
    // The page's styles and images may still be arriving; a half-loaded picture misleads.
    await untilIdle(tabId, SCREENSHOT_LOAD_TIMEOUT_MS);
    const v = await contentCall(tabId, 0, "viewport", {}).catch(() => null);
    const options = { format, scale };
    if (format === "jpeg") options.quality = quality;
    if (region) {
      if (!v) throw new Error("region needs a page that can be read.");
      // captureTab's rect is document-relative; agents think in viewport coordinates.
      options.rect = { x: region.x + v.scrollX, y: region.y + v.scrollY, width: region.width, height: region.height };
    }
    let dataUrl = await capture(tabId, options);
    const pointer = pointers.get(tabId);
    if (show_pointer && pointer) dataUrl = await withPointer(dataUrl, pointer, region, scale, format, quality);
    const [header, data] = dataUrl.split(",", 2);
    return {
      image: { data, mimeType: header.slice(5).split(";")[0] },
      text: v
        ? `Viewport ${v.width}x${v.height} CSS px, scrolled to (${v.scrollX}, ${v.scrollY}). At scale 1 without region, image pixels are viewport coordinates.` +
          (pointer ? ` Pointer at (${Math.round(pointer.x)}, ${Math.round(pointer.y)}).` : "")
        : "Viewport size unavailable.",
    };
  },

  async wait({ seconds = 1 }) {
    const s = Math.min(Math.max(seconds, 0), 60);
    await sleep(s * 1000);
    return { text: `Waited ${s} s.` };
  },

  async wait_for({ tabId, selector, text, timeout = 15 }) {
    await accessibleTab(tabId);
    if (!selector && !text) throw new Error("Give selector or text.");
    const deadline = Date.now() + Math.min(timeout, 120) * 1000;
    while (Date.now() < deadline) {
      await accessibleTab(tabId); // the page may have moved to a blocked site while we wait
      await assertNoDialog(tabId);
      const r = await contentCall(tabId, 0, "waitCheck", { selector, text }).catch(() => ({ found: false }));
      if (r.found) return { text: `Found ${selector ?? JSON.stringify(text)}.` };
      await sleep(200);
    }
    throw new Error(`Not found within ${timeout} s: ${selector ?? JSON.stringify(text)}.`);
  },

  async javascript({ tabId, code, frame_id = 0 }) {
    await accessibleTab(tabId);
    await assertNoDialog(tabId);
    if (!code) throw new Error("code is required.");
    await assertFrameAllowed(tabId, frame_id);
    if (!trusted) return { text: (await contentCall(tabId, frame_id, "evaluate", { code })).text };
    const value = await privileged(tabId, "evaluate", { frameId: frame_id, code });
    if (value?.blockedByDialog) return { text: `The script has not finished.${DIALOG_NOTE}` };
    const text = String(value);
    return { text: text.length > RESULT_LIMIT ? `${text.slice(0, RESULT_LIMIT)}\n[truncated]` : text };
  },

  async read_console({ tabId, pattern, only_errors = false, limit = 50, clear = false }) {
    await accessibleTab(tabId);
    await assertNoDialog(tabId);
    if (!trusted) throw new Error("read_console needs trusted input, which this build does not have.");
    let entries = await privileged(tabId, "console", {});
    const clearedAt = consoleCleared.get(tabId) ?? 0;
    entries = entries.filter((e) => e.time > clearedAt);
    if (clear && entries.length) consoleCleared.set(tabId, entries[entries.length - 1].time);
    if (only_errors) entries = entries.filter((e) => e.level === "error");
    if (pattern) {
      let matcher;
      try {
        matcher = new RegExp(pattern);
      } catch (e) {
        throw new Error(`Bad pattern: ${e.message}`);
      }
      entries = entries.filter((e) => matcher.test(e.text));
    }
    const lines = entries.slice(-limit).map((e) => `[${e.level}] ${e.text} (${e.source})`);
    return { text: lines.join("\n") || "No console messages." };
  },

  async read_network({ tabId, url_pattern, limit = 50, clear = false }) {
    await accessibleTab(tabId);
    let entries = network.get(tabId) ?? [];
    if (url_pattern) entries = entries.filter((e) => e.url.includes(url_pattern));
    const lines = entries.slice(-limit).map((e) => `${e.method} ${e.status ?? e.error ?? "pending"} ${e.type} ${e.ms ?? "?"}ms ${e.url}`);
    if (clear) network.delete(tabId);
    return { text: lines.join("\n") || "No requests captured." };
  },

  async resize_window({ tabId, width, height }) {
    const tab = await accessibleTab(tabId);
    await browser.windows.update(tab.windowId, { state: "normal", width, height });
    const v = await contentCall(tabId, 0, "viewport", {}).catch(() => null);
    return { text: `Window resized.${v ? ` Viewport is ${v.width}x${v.height}.` : ""}` };
  },

  async record({ tabId, action }) {
    await accessibleTab(tabId);
    if (action === "start") {
      recordings.set(tabId, { frames: [], point: null, label: "start" });
      await captureFrame(tabId);
      return { text: "Recording. A frame is captured after each action in this tab." };
    }
    const rec = recordings.get(tabId);
    if (!rec) throw new Error("This tab is not recording.");
    recordings.delete(tabId);
    if (action === "discard") return { text: "Recording discarded." };
    if (action !== "stop") throw new Error("action must be start, stop or discard.");
    rec.label = "end";
    recordings.set(tabId, rec);
    await captureFrame(tabId);
    recordings.delete(tabId);
    return { gif: bytesToBase64(encodeGif(rec.frames)), frames: rec.frames.length, text: `Recorded ${rec.frames.length} frames.` };
  },

  async batch({ actions, stop_on_error = true }, client) {
    if (!Array.isArray(actions) || !actions.length) throw new Error("actions must be a non-empty list.");
    const parts = [];
    for (const [index, action] of actions.entries()) {
      if (action.tool === "batch") throw new Error("batch cannot be nested.");
      try {
        const result = await runTool(action.tool, action.args ?? {}, client);
        parts.push({ index, tool: action.tool, ...result });
      } catch (e) {
        parts.push({ index, tool: action.tool, error: e.message });
        if (stop_on_error) {
          parts.push({ index, tool: action.tool, text: `Stopped: ${actions.length - index - 1} actions not run.` });
          return { parts, failed: true };
        }
      }
    }
    return { parts };
  },

  async handle_dialog({ tabId, action = "status", text }) {
    await accessibleTab(tabId);
    if (!trusted) throw new Error("handle_dialog needs trusted input, which this build does not have.");
    if (action === "status") {
      const s = await privileged(tabId, "dialogState");
      return { text: s.open ? `${s.type}: ${JSON.stringify(s.message)}` : "No dialog is open." };
    }
    if (action !== "accept" && action !== "dismiss") throw new Error("action must be status, accept or dismiss.");
    const s = await privileged(tabId, "dialogRespond", { action, text });
    return { text: `${action === "accept" ? "Accepted" : "Dismissed"} ${s.type}: ${JSON.stringify(s.message)}.` };
  },
};

/* ---------- host connection ---------- */

// Log enough to see what an agent did, but not typed text, scripts or file contents.
const LOGGED_ARGS = ["tabId", "url", "ref", "x", "y", "keys", "query", "selector", "action", "button", "host"];

function logActivity(client, tool, args, ok, ms, error) {
  const details = Object.fromEntries(LOGGED_ARGS.filter((k) => args?.[k] !== undefined).map((k) => [k, args[k]]));
  // Which local files an agent handed to a page is something the user should be able to see.
  if (Array.isArray(args?.files)) details.files = args.files.map((f) => f?.name);
  activity.push({ time: Date.now(), client, tool, details, ok, ms, error });
  if (activity.length > LOG_LIMIT) activity.shift();
}

async function runTool(tool, args, client) {
  const started = Date.now();
  try {
    if (settings.paused && tool !== "status") throw new Error("The user has paused agent access from the foxreach toolbar button.");
    if (!Object.hasOwn(TOOLS, tool)) throw new Error(`Unknown tool ${tool}.`);
    const result = await TOOLS[tool](args, client);
    logActivity(client, tool, args, true, Date.now() - started);
    if (RECORDED_TOOLS.has(tool) && recordings.has(args.tabId)) {
      recordings.get(args.tabId).label = tool;
      await captureFrame(args.tabId).catch(() => {});
    }
    return result;
  } catch (e) {
    logActivity(client, tool, args, false, Date.now() - started, e.message);
    throw e;
  }
}

async function handleRequest({ id, client, tool, args }) {
  try {
    // The part of the id before the colon is the connection, numbered by the host, not by the agent.
    if (tool !== "status") {
      await assertAgentAllowed(String(id).split(":")[0], client).catch((e) => {
        logActivity(client, tool, args, false, 0, e.message);
        throw e;
      });
    }
    send({ id, result: await runTool(tool, args ?? {}, client) });
  } catch (e) {
    send({ id, error: e.message });
  }
}

/* ---------- recording ---------- */

function recordPoint(tabId, point) {
  const rec = recordings.get(tabId);
  if (rec) rec.point = point;
}

function decodeImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("Could not decode a captured image."));
    img.src = url;
  });
}

/** An arrow with its tip exactly on the point. Its size stays readable at any image scale. */
function drawPointer(g, x, y) {
  g.beginPath();
  POINTER_SHAPE.forEach(([dx, dy], i) => (i ? g.lineTo(x + dx, y + dy) : g.moveTo(x + dx, y + dy)));
  g.closePath();
  g.fillStyle = "#000000";
  g.strokeStyle = "#ffffff";
  g.lineWidth = 1.5;
  g.fill();
  g.stroke();
}

async function withPointer(dataUrl, pointer, region, scale, format, quality) {
  const x = (pointer.x - (region?.x ?? 0)) * scale;
  const y = (pointer.y - (region?.y ?? 0)) * scale;
  const image = await decodeImage(dataUrl);
  if (x < 0 || y < 0 || x >= image.width || y >= image.height) return dataUrl;
  const canvas = document.createElement("canvas");
  canvas.width = image.width;
  canvas.height = image.height;
  const g = canvas.getContext("2d");
  g.drawImage(image, 0, 0);
  drawPointer(g, x, y);
  return canvas.toDataURL(`image/${format}`, quality / 100);
}

async function captureFrame(tabId) {
  const rec = recordings.get(tabId);
  if (!rec || rec.frames.length >= RECORD_MAX_FRAMES) return;
  const url = await capture(tabId, { format: "png", scale: 1 });
  const image = await decodeImage(url);
  const scale = Math.min(1, RECORD_WIDTH / image.width);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(image.width * scale);
  canvas.height = Math.round(image.height * scale);
  const g = canvas.getContext("2d");
  g.drawImage(image, 0, 0, canvas.width, canvas.height);
  if (rec.point) {
    g.strokeStyle = "#d9480f";
    g.lineWidth = 3;
    g.beginPath();
    g.arc(rec.point.x * scale, rec.point.y * scale, 14, 0, 2 * Math.PI);
    g.stroke();
    rec.point = null;
  }
  const pointer = pointers.get(tabId);
  if (pointer) drawPointer(g, pointer.x * scale, pointer.y * scale);
  g.font = "14px sans-serif";
  const width = g.measureText(rec.label).width + 12;
  g.fillStyle = "#1a1a1a";
  g.fillRect(0, canvas.height - 24, width, 24);
  g.fillStyle = "#ffffff";
  g.fillText(rec.label, 6, canvas.height - 7);
  rec.frames.push({ width: canvas.width, height: canvas.height, rgba: g.getImageData(0, 0, canvas.width, canvas.height).data });
}

function send(message) {
  try {
    port?.postMessage(message);
  } catch (e) {
    console.error("foxreach: could not send to host", e);
  }
}

function onHostMessage(message) {
  if (message.chunk) {
    const { id, i, n, data } = message.chunk;
    const parts = partial.get(id) ?? [];
    parts[i] = data;
    partial.set(id, parts);
    if (parts.filter((p) => p !== undefined).length === n) {
      partial.delete(id);
      handleRequest(JSON.parse(parts.join("")));
    }
    return;
  }
  if (message.hostError) {
    hostError = message.hostError;
    return;
  }
  if (message.closed !== undefined) {
    forgetConnection(String(message.closed));
    return;
  }
  handleRequest(message);
}

function connectHost() {
  port = browser.runtime.connectNative(HOST_NAME);
  port.onMessage.addListener(onHostMessage);
  port.onDisconnect.addListener((p) => {
    // hostError set here means another browser holds the socket: retry slowly, keep the message.
    const busy = hostError !== null;
    hostError = hostError ?? p.error?.message ?? "The native host exited.";
    port = null;
    // A new host numbers its connections from 1 again.
    for (const conn of [...agentRequests.keys(), ...allowedConns, ...deniedConns]) forgetConnection(conn);
    setTimeout(() => {
      hostError = null;
      connectHost();
    }, busy ? RECONNECT_BUSY_MS : RECONNECT_MS);
  });
  send({ hello: { version: VERSION, trustedInput: trusted } });
}

/* ---------- popup ---------- */

async function popupState() {
  const tabs = await browser.tabs.query({});
  const shown = tabs.filter((t) => /^https?:|^about:blank/.test(t.url));
  return {
    version: VERSION,
    trusted,
    connected: !!port && !hostError,
    hostError,
    paused: settings.paused,
    blockedHosts: settings.blockedHosts,
    // Whether the browser hands out sites one by one (the build without privileged code), so the
    // popup has to ask it for each.
    asksBrowser: (browser.runtime.getManifest().optional_permissions ?? []).includes("<all_urls>"),
    siteAccess: settings.siteAccess,
    allowedHosts: await Promise.all(settings.allowedHosts.map(async (host) => ({ host, granted: await browserGrants(host) }))),
    everySiteGranted: await browserGrants("*"),
    siteRequests: [...siteRequests.keys()].reverse(),
    askApproval: settings.askApproval,
    approvedAgents: settings.approvedAgents,
    agentRequests: [...agentRequests].map(([conn, r]) => ({ conn, name: r.name, time: r.time })),
    tabs: await Promise.all(shown.map(async (t) => ({
      id: t.id, title: t.title, url: t.url, shared: agentTabs.has(t.id), active: t.active,
      host: isWeb(t.url) ? hostOf(t.url) : "", allowed: await siteAllowed(t.url),
    }))),
    activity: activity.slice(-40).reverse(),
  };
}

browser.runtime.onMessage.addListener(async (msg, sender) => {
  if (sender.id !== browser.runtime.id || sender.tab?.url?.startsWith("moz-extension:") === false) return undefined;
  switch (msg?.popup) {
    case "state":
      return popupState();
    case "setPaused":
      settings.paused = !!msg.value;
      break;
    case "setBlocked":
      settings.blockedHosts = parseBlocked(msg.value);
      break;
    case "share":
      if (msg.value) await adoptTab(await browser.tabs.get(msg.tabId));
      else agentTabs.delete(msg.tabId);
      break;
    case "wantSite": {
      // The popup asks the browser for the site at the same moment; applyWanted runs again once
      // the user has answered that.
      const host = msg.host === "*" ? "*" : parseBlocked(msg.host)[0];
      if (host) wantedSites.add(host);
      await applyWanted();
      break;
    }
    case "removeSite":
      settings.allowedHosts = settings.allowedHosts.filter((h) => h !== msg.host);
      await browser.permissions.remove({ origins: originsOf(msg.host) }).catch(() => {}); // not ours to drop in the full build
      break;
    case "listedOnly":
      settings.siteAccess = "listed";
      await browser.permissions.remove({ origins: originsOf("*") }).catch(() => {});
      break;
    case "dismissSite":
      siteRequests.delete(msg.host);
      break;
    case "setAsk":
      settings.askApproval = !!msg.value;
      if (!settings.askApproval) for (const conn of [...agentRequests.keys()]) answerAgent(conn, "once");
      break;
    case "answerAgent":
      answerAgent(String(msg.conn), msg.mode);
      break;
    case "forgetAgent":
      settings.approvedAgents = settings.approvedAgents.filter((n) => n !== msg.name);
      break;
    default:
      return undefined;
  }
  await browser.storage.local.set({ settings });
  updateBadge();
  return popupState();
});

/* ---------- tab and network bookkeeping ---------- */

browser.tabs.onCreated.addListener((tab) => {
  // A page's popup or target=_blank stays with the agent that caused it.
  if (tab.openerTabId !== undefined && agentTabs.has(tab.openerTabId)) {
    adoptTab(tab);
    openedBy.set(tab.openerTabId, [...(openedBy.get(tab.openerTabId) ?? []), tab.id]);
  }
});
browser.tabs.onRemoved.addListener((tabId) => {
  agentTabs.delete(tabId);
  network.delete(tabId);
  openedBy.delete(tabId);
  recordings.delete(tabId);
  consoleCleared.delete(tabId);
  pointers.delete(tabId);
  updateBadge();
});

const FILTER = { urls: ["<all_urls>"] };
const pending = new Map();

browser.webRequest.onBeforeRequest.addListener((d) => {
  if (!agentTabs.has(d.tabId)) return undefined;
  // A tab agents can use never loads anything from a blocked site: not as a page, a redirect, a
  // frame, or a request made by a script. Checking the tab's address alone would miss the last three.
  if (hostBlocked(d.url)) {
    if (d.type === "main_frame") logActivity("foxreach", "blocked a page load", { tabId: d.tabId, host: new URL(d.url).hostname }, false, 0, "This site is on the blocked list, and the tab is one agents can use.");
    return { cancel: true };
  }
  const entry = { method: d.method, url: d.url, type: d.type, start: d.timeStamp };
  pending.set(d.requestId, entry);
  const list = network.get(d.tabId) ?? [];
  list.push(entry);
  if (list.length > NET_LIMIT) list.shift();
  network.set(d.tabId, list);
  return undefined;
}, FILTER, ["blocking"]);

browser.webRequest.onCompleted.addListener((d) => {
  const entry = pending.get(d.requestId);
  if (!entry) return;
  entry.status = d.statusCode;
  entry.ms = Math.round(d.timeStamp - entry.start);
  pending.delete(d.requestId);
}, FILTER);

browser.webRequest.onErrorOccurred.addListener((d) => {
  const entry = pending.get(d.requestId);
  if (!entry) return;
  entry.error = d.error;
  entry.ms = Math.round(d.timeStamp - entry.start);
  pending.delete(d.requestId);
}, FILTER);

/* ---------- startup ---------- */

(async () => {
  const stored = await browser.storage.local.get("settings");
  settings = { ...settings, ...stored.settings };
  settings.blockedHosts = parseBlocked((settings.blockedHosts ?? []).join(" ")); // entries saved by older versions
  // First start: every site if the browser already grants that (the full build), else a list to fill.
  settings.siteAccess ??= (await browserGrants("*")) ? "all" : "listed";
  updateBadge();
  connectHost();
})();
