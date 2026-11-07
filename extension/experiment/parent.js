/* global ExtensionAPI, ChromeUtils, Services */
"use strict";

// Privileged code. Parent-process half of trusted input. Keep this file and child.sys.mjs small:
// they run with full browser privileges, and they are what a reviewer must read.

const { ExtensionError } = ChromeUtils.importESModule(
  "resource://gre/modules/ExtensionUtils.sys.mjs"
);

const { setTimeout, clearTimeout } = ChromeUtils.importESModule("resource://gre/modules/Timer.sys.mjs");

const ACTOR = "FoxreachInput";
const RES_HOST = "foxreach";
const QUERY_TIMEOUT_MS = 30000;
const FRAME_ELEMENT_DEPTH = 8;
const DIALOG_POLL_MS = 100;

const resHandler = () =>
  Services.io.getProtocolHandler("resource").QueryInterface(Components.interfaces.nsIResProtocolHandler);

let registered = false;

function register(extension) {
  if (registered) return;
  resHandler().setSubstitution(RES_HOST, extension.rootURI);
  ChromeUtils.registerWindowActor(ACTOR, {
    child: { esModuleURI: `resource://${RES_HOST}/experiment/child.sys.mjs` },
    allFrames: true,
    // Runs in web processes. Commands only flow from parent to child; the parent treats replies
    // as untrusted, and the one thing it acts on (a frame id) is checked by childFrame().
    safeForUntrustedWebProcess: true,
  });
  registered = true;
}

function query(bc, name, data) {
  const wgp = bc?.currentWindowGlobal;
  if (!wgp) throw new ExtensionError("The page is navigating; try again.");
  let actor;
  try {
    actor = wgp.getActor(ACTOR);
  } catch (e) {
    throw new ExtensionError(`This page does not accept agent input (${wgp.documentURI?.spec}): ${e.message ?? e}`);
  }
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(
      () => reject(new ExtensionError(`Timed out in the page after ${QUERY_TIMEOUT_MS} ms (${name}).`)),
      QUERY_TIMEOUT_MS
    );
  });
  return Promise.race([actor.sendQuery(name, data), timeout]).finally(() => clearTimeout(timer));
}

/** Find a frame by id inside one tab's own tree, so an id can never reach another tab. */
const frameInTab = (top, id) => top.getAllBrowsingContextsInSubtree().find((bc) => bc.id === id);

/** A frame id reported by a web process, accepted only if it is in the same tab. */
function childFrame(top, id) {
  const bc = frameInTab(top, id);
  if (!bc) throw new ExtensionError("A page reported a frame outside its tab.");
  return bc;
}

/** Follow iframes under a viewport point, so input reaches cross-process frames too. */
async function resolvePoint(top, x, y) {
  let bc = top;
  for (let depth = 0; depth < FRAME_ELEMENT_DEPTH; depth++) {
    const hit = await query(bc, "hitTest", { x, y });
    if (hit.bc === undefined) break;
    bc = childFrame(top, hit.bc);
    x = hit.x;
    y = hit.y;
  }
  return { bc, x, y };
}

/** Follow focus down the frame tree to the frame whose element has it. */
async function resolveFocus(top) {
  let bc = top;
  for (let depth = 0; depth < FRAME_ELEMENT_DEPTH; depth++) {
    const hit = await query(bc, "focusedFrame", {});
    if (hit.bc === undefined) break;
    bc = childFrame(top, hit.bc);
  }
  return bc;
}

/** Convert a point in a frame to the tab's viewport. Null if a frame cannot be placed. */
async function toViewport(bc, x, y) {
  for (let frame = bc; frame.parent; frame = frame.parent) {
    const rect = await query(frame.parent, "frameRect", { bc: frame.id });
    if (!rect || !Number.isFinite(rect.x) || !Number.isFinite(rect.y)) return null;
    x += rect.x;
    y += rect.y;
  }
  return { x, y };
}

function openDialog(browser) {
  const box = browser.tabDialogBox;
  if (!box) return null;
  for (const manager of [box.getTabDialogManager(), box.getContentDialogManager()]) {
    for (const dialog of manager.dialogs) {
      const prompt = dialog.frameContentWindow?.Dialog;
      if (prompt?.ui?.button0) return prompt;
    }
  }
  return null;
}

/**
 * A page's alert() runs inside the event that caused it, so that event's query cannot finish until
 * the dialog closes. Report the dialog rather than waiting for it.
 */
function orDialog(browser, work) {
  let timer;
  const watch = new Promise((resolve) => {
    const poll = () => {
      if (openDialog(browser)) resolve({ blockedByDialog: true });
      else timer = setTimeout(poll, DIALOG_POLL_MS);
    };
    timer = setTimeout(poll, DIALOG_POLL_MS);
  });
  return Promise.race([work, watch]).finally(() => clearTimeout(timer));
}

function dialogState(browser) {
  const prompt = openDialog(browser);
  if (!prompt) return { open: false };
  return {
    open: true,
    type: prompt.args?.inPermitUnload ? "beforeunload" : prompt.args?.promptType,
    message: prompt.ui.infoBody?.textContent ?? "",
  };
}

function dialogRespond(browser, { action, text }) {
  const prompt = openDialog(browser);
  if (!prompt) throw new ExtensionError("No dialog is open in this tab.");
  const state = dialogState(browser);
  if (text !== undefined && prompt.ui.loginTextbox) prompt.ui.loginTextbox.value = text;
  // Skip the anti-clickjacking delay on the buttons: this is not a racing human.
  prompt.ui.button0.disabled = false;
  if (action === "accept") prompt.ui.button0.click();
  else (prompt.ui.button1?.hidden ? prompt.ui.button0 : prompt.ui.button1 ?? prompt.ui.button0).click();
  return state;
}

this.foxreachInput = class extends ExtensionAPI {
  onShutdown() {
    if (!registered) return;
    ChromeUtils.unregisterWindowActor(ACTOR);
    resHandler().setSubstitution(RES_HOST, null);
    registered = false;
  }

  getAPI(context) {
    const { extension } = context;
    register(extension);
    const browserOf = (tabId) => extension.tabManager.get(tabId).nativeTab.linkedBrowser;
    const frameOf = (browser, frameId) => {
      if (!frameId) return browser.browsingContext;
      const bc = frameInTab(browser.browsingContext, frameId);
      if (!bc) throw new ExtensionError(`Frame ${frameId} is not in this tab.`);
      return bc;
    };

    const handlers = {
      async mouse(browser, { frameId, x, y, ...rest }) {
        const target = frameId
          ? { bc: frameOf(browser, frameId), x, y }
          : await resolvePoint(browser.browsingContext, x, y);
        const result = await query(target.bc, "mouse", { x: target.x, y: target.y, ...rest });
        if (frameId) result.viewportPoint = await toViewport(target.bc, x, y);
        return result;
      },
      // A press and release share one target, so a drag that leaves a frame still finishes.
      async drag(browser, { frameId, from, to, modifiers }) {
        const start = frameId
          ? { bc: frameOf(browser, frameId), ...from }
          : await resolvePoint(browser.browsingContext, from.x, from.y);
        const dx = start.x - from.x;
        const dy = start.y - from.y;
        return query(start.bc, "drag", {
          from: { x: start.x, y: start.y },
          to: { x: to.x + dx, y: to.y + dy },
          modifiers,
        });
      },
      async wheel(browser, { frameId, x, y, ...rest }) {
        const target = frameId
          ? { bc: frameOf(browser, frameId), x, y }
          : await resolvePoint(browser.browsingContext, x, y);
        const result = await query(target.bc, "wheel", { x: target.x, y: target.y, ...rest });
        if (frameId) result.viewportPoint = await toViewport(target.bc, x, y);
        return result;
      },
      async dropFiles(browser, { x, y, files }) {
        const target = await resolvePoint(browser.browsingContext, x, y);
        return query(target.bc, "dropFiles", { x: target.x, y: target.y, files });
      },
      async keys(browser, args) {
        return query(await resolveFocus(browser.browsingContext), "keys", args);
      },
      async type(browser, args) {
        return query(await resolveFocus(browser.browsingContext), "type", args);
      },
      async evaluate(browser, { frameId, code }) {
        return query(frameOf(browser, frameId), "evaluate", { code });
      },
      async console(browser, args) {
        const frames = browser.browsingContext.getAllBrowsingContextsInSubtree();
        const results = await Promise.allSettled(frames.map((bc) => query(bc, "console", args)));
        return results
          .flatMap((r) => (r.status === "fulfilled" ? r.value : []))
          .sort((a, b) => a.time - b.time);
      },
      dialogState: (browser) => dialogState(browser),
      dialogRespond: (browser, args) => dialogRespond(browser, args),
    };

    return {
      foxreachInput: {
        async call({ tabId, method, args }) {
          if (!Object.hasOwn(handlers, method)) throw new ExtensionError(`Unknown method ${method}`);
          try {
            const browser = browserOf(tabId);
            const work = handlers[method](browser, args ?? {});
            return await (method.startsWith("dialog") ? work : orDialog(browser, work));
          } catch (e) {
            if (e instanceof ExtensionError) throw e;
            throw new ExtensionError(`${method}: ${e?.message ?? e}`);
          }
        },
      },
    };
  }
};
