"use strict";

const $ = (id) => document.getElementById(id);
const ask = (msg) => browser.runtime.sendMessage(msg);
const originsOf = (host) => (host === "*" ? ["<all_urls>"] : [`*://${host}/*`, `*://*.${host}/*`]);
let state = null;

function item(text, title, ...buttons) {
  const li = document.createElement("li");
  li.className = "line";
  const span = document.createElement("span");
  span.textContent = text;
  span.title = title ?? text;
  li.append(span, ...buttons);
  return li;
}

function button(label, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}

let lastSend = Promise.resolve(); // the newest message to the background page still awaiting its answer
const send = (msg) => (lastSend = ask(msg).then(render));

const browserGrants = (host) => browser.permissions.contains({ origins: [originsOf(host)[0]] });

/**
 * Let agents use a site ("*": every site). Where the browser hands out sites one by one, it has to
 * be asked, from a click (it accepts the question from nowhere else); the background page finishes
 * the job when the answer arrives. A window or tab of foxreach's own asks at once. The toolbar
 * panel does not: the browser may open its prompt behind the panel, and a click there closes the
 * panel, after which the prompt stays up with nobody to take its answer (Allow does nothing). So
 * the panel opens a tab that asks instead (grantPage).
 */
function allowSite(host) {
  if (!state?.asksBrowser) return send({ popup: "wantSite", host });
  if (inPanel) {
    return browserGrants(host).then((granted) => {
      // Granted by the browser already: only foxreach's own list changes.
      if (granted) return send({ popup: "wantSite", host });
      // Otherwise the grant tab asks, and records the site only once the browser has granted it,
      // so closing that tab any way (Cancel or its close button) leaves nothing behind.
      return openGrantTab([host]);
    });
  }
  const sent = send({ popup: "wantSite", host });
  browser.permissions.request({ origins: originsOf(host) }).then(() => send({ popup: "state" }), () => {});
  return sent;
}

/** From the toolbar panel: the grant tab for these sites (["*"]: every site), then close the panel. */
async function openGrantTab(hosts) {
  const url = browser.runtime.getURL(`/popup/popup.html?grant=${encodeURIComponent(hosts.join(","))}`);
  // A grant tab for this site already open: bring it forward rather than open another.
  const open = (await browser.tabs.query({})).find((t) => t.url === url);
  const [here] = await browser.tabs.query({ active: true, currentWindow: true });
  if (open) {
    // Asked again from another tab: that is now the tab to go back to.
    const from = here && here.id !== open.id && here.windowId === open.windowId ? { openerTabId: here.id } : {};
    await browser.tabs.update(open.id, { active: true, ...from });
    await browser.windows.update(open.windowId, { focused: true });
  } else {
    // Next to the tab the user is on; closeTab goes back to it.
    await browser.tabs.create({ url, openerTabId: here?.id });
  }
  // A message still on its way (sharing the ticked tab) gets its answer first: closing under it
  // makes the browser log an error.
  await lastSend.catch(() => {});
  window.close(); // the browser leaves the panel open over the new tab otherwise
}

/**
 * "Only these sites". A site added while every site was granted is covered by that grant and holds
 * none of its own: the browser does not store a grant for a site it already covers. Withdrawing
 * every site would leave it listed but unusable (issue #12). So once every site is withdrawn, the
 * listed sites it took along are asked for again, in one prompt from the grant tab.
 */
async function listedOnly() {
  const before = new Set((state?.allowedHosts ?? []).filter((s) => s.granted).map((s) => s.host));
  await send({ popup: "listedOnly" });
  if (!state?.asksBrowser || !inPanel) return;
  const lost = state.allowedHosts.filter((s) => !s.granted && before.has(s.host)).map((s) => s.host);
  if (lost.length) await openGrantTab(lost);
}

// Opened by the background page as a window of its own, to show a request. It closes once
// nothing is left to answer.
const requestWindow = location.search === "?requests";
if (requestWindow) {
  document.documentElement.classList.add("request-window");
  $("request-view").hidden = false;
  document.title = "foxreach: a request needs your answer";
}

// Opened by the toolbar panel as a tab of its own, to ask the browser for one site, several
// (?grant=<host>,<host>: the listed sites "Only these sites" would leave without a grant) or every
// site ("*"): see allowSite and listedOnly.
const grantHost = new URLSearchParams(location.search).get("grant");
const grantHosts = grantHost === null ? [] : grantHost === "*" ? ["*"] : grantHost.split(",");
// Host names, IP addresses ([::1] too) and names with underscores, as the panel passes them.
const grantPage = grantHost !== null && (grantHost === "*" || grantHosts.every((h) => /^(\[[0-9a-f:.]+\]|[a-z0-9._-]+)$/.test(h)));
const grantOrigins = () => grantHosts.flatMap(originsOf);
const grantName = grantHosts.join(", ");
/** Each site of this grant page onto foxreach's list (wantSite), or off the wanted ones (unwantSite). */
const grantTell = (popup) => Promise.all(grantHosts.map((host) => ask({ popup, host })));
const inPanel = !requestWindow && grantHost === null;
const closeTab = async () => {
  const t = await browser.tabs.getCurrent();
  // Back to the tab the user asked from, if this tab is still the one in front. (The browser's own
  // return to the opener is lost as soon as any tab opens or is selected meanwhile.)
  if (t.active && t.openerTabId !== undefined) await browser.tabs.update(t.openerTabId, { active: true }).catch(() => {});
  return browser.tabs.remove(t.id);
};
let grantDone = false; // granted here, or seen granted (render), so the tab is closing
if (grantHost !== null) {
  document.documentElement.classList.add("grant-page");
  $("grant-view").hidden = false;
  document.title = !grantPage ? "foxreach" : grantHost === "*" ? "foxreach: allow every site" : `foxreach: allow ${grantName}`;
  if (grantPage) showGrant();
  else {
    $("grant-card").replaceChildren(card(
      "foxreach can't ask the browser for this", grantHost || "(nothing)", "Not a site name foxreach can grant.",
      "Allow sites from the foxreach toolbar button.",
      actionButton("Close", "primary", closeTab),
    ));
  }
}

/** The grant page's card. outcome: null (not asked yet), "granted" or "refused". */
function showGrant(outcome = null) {
  const every = grantHost === "*";
  const several = grantHosts.length > 1;
  const cancel = () => grantTell("unwantSite").then(closeTab, closeTab);
  const meta = outcome === "granted" ? "Granted. This tab closes by itself."
    : outcome === "refused" ? "The browser did not grant it. Try again, or cancel."
    : "Next, the browser asks you to confirm.";
  const actions = outcome === "granted" ? [] : [
    actionButton("Cancel", "ghost", cancel),
    actionButton(outcome === "refused" ? "Try again" : "Continue", "primary", () => {
      browser.permissions.request({ origins: grantOrigins() }).then(async (granted) => {
        // Onto foxreach's list only once granted: a refusal must leave nothing behind, however the
        // tab is closed afterwards.
        if (granted) await grantTell("wantSite");
        if (granted && grantDone) return; // render saw it first and is closing the tab
        grantDone ||= granted;
        showGrant(granted ? "granted" : "refused");
        if (granted) setTimeout(closeTab, 1500);
      }, () => showGrant("refused"));
    }),
  ];
  $("grant-card").replaceChildren(card(
    "Let agents use", every ? "Every site" : grantName, meta,
    every ? "Agents may then use any site, except the ones you block, in tabs they open or you share."
      : several ? "These sites are on your list, and the browser had granted them only as part of every site. " +
        "Agents may then use them and their subdomains, in tabs they open or you share."
      : "Agents may then use this site and its subdomains, in tabs they open or you share.",
    ...actions,
  ));
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function actionButton(label, kind, onClick) {
  const b = button(label, onClick);
  b.className = `action ${kind}`;
  return b;
}

/** One question as a card: what is asked, by whom, what an answer means, and the answers. */
function card(kicker, subject, meta, explain, ...actions) {
  const c = el("section", "card");
  const row = el("div", "actions");
  row.append(...actions);
  c.append(el("p", "kicker", kicker), el("p", "subject", subject), el("p", "meta", meta), el("p", "explain", explain), row);
  return c;
}

function renderRequests() {
  const time = (t) => new Date(t).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
  $("request-cards").replaceChildren(
    ...state.agentRequests.map((r) => card(
      "An agent wants to use your browser", r.name, `Asked at ${time(r.time)}`,
      "It could open tabs, read and act on pages it opens or you share, on the sites you allow. " +
        "The name is what the program calls itself: allow only an agent you started.",
      actionButton("Refuse", "ghost", () => send({ popup: "answerAgent", conn: r.conn, mode: "deny" })),
      actionButton("Always allow", "quiet", () => send({ popup: "answerAgent", conn: r.conn, mode: "always" })),
      actionButton("Allow", "primary", () => send({ popup: "answerAgent", conn: r.conn, mode: "once" })),
    )),
    ...state.siteRequests.map((host) => card(
      "An agent asks to use a site", host, "Not on your list of sites yet",
      "Allowing adds it, with its subdomains, to the sites agents may use.",
      actionButton("Dismiss", "ghost", () => send({ popup: "dismissSite", host })),
      actionButton("Allow site", "primary", () => allowSite(host)),
    )),
  );
  $("request-status").textContent = state.connected ? "" : "· bridge not connected";
  fitWindow();
}

/* The browser may open the window larger than asked (LibreWolf rounds window sizes), so size it
   to the card once it is drawn. */
let fittedTo = 0;
async function fitWindow() {
  await new Promise(requestAnimationFrame);
  const content = document.getElementById("request-view").scrollHeight;
  if (Math.abs(content - fittedTo) < 4) return; // only when the cards change, not on every refresh
  fittedTo = content;
  const w = await browser.windows.getCurrent();
  const frame = window.outerHeight - window.innerHeight;
  await browser.windows.update(w.id, { width: 460, height: Math.ceil(content + frame) }).catch(() => {});
}

function render(next) {
  state = next;
  // A grant tab whose site got granted some other way (another grant tab, the request window)
  // has nothing left to ask: say so and close.
  if (grantPage && !grantDone) {
    Promise.all(grantHosts.map(browserGrants)).then((each) => {
      if (!each.every(Boolean) || grantDone) return;
      grantDone = true;
      grantTell("wantSite").catch(() => {}); // onto foxreach's list, as a grant here would
      showGrant("granted");
      setTimeout(closeTab, 1500);
    });
  }
  if (requestWindow && !state.agentRequests.length && !state.siteRequests.length) {
    browser.windows.getCurrent().then((w) => browser.windows.remove(w.id)); // window.close() is refused here
  }
  if (requestWindow) renderRequests();
  const status = $("status");
  status.textContent = !state.connected
    ? `Bridge not connected${state.hostError ? `: ${state.hostError}` : ""}`
    : `Connected, ${state.trusted ? "trusted input" : "untrusted input only"}, v${state.version}`;
  status.className = state.connected ? "ok" : "bad";
  $("paused").checked = state.paused;
  $("ask").checked = state.askApproval;
  renderHidden();
  if (document.activeElement !== $("blocked")) $("blocked").value = state.blockedHosts.join(", ");

  $("agent-requests").replaceChildren(
    ...state.agentRequests.map((r) => item(
      `${r.name} asks to use the browser`, `Asked at ${new Date(r.time).toLocaleTimeString()}. The name is what the program calls itself.`,
      button("Allow", () => send({ popup: "answerAgent", conn: r.conn, mode: "once" })),
      button("Always", () => send({ popup: "answerAgent", conn: r.conn, mode: "always" })),
      button("Refuse", () => send({ popup: "answerAgent", conn: r.conn, mode: "deny" })),
    ))
  );
  $("agents").replaceChildren(
    ...state.approvedAgents.map((name) => item(`${name}: always allowed`, null, button("Remove", () => send({ popup: "forgetAgent", name }))))
  );

  $("tabs").replaceChildren(
    ...state.tabs.map((tab) => {
      const li = document.createElement("li");
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = tab.shared;
      box.dataset.tabId = tab.id;
      box.addEventListener("change", () => {
        // Sharing a tab says the agent may use its site.
        if (box.checked && tab.host && !tab.allowed) allowSite(tab.host);
        send({ popup: "share", tabId: tab.id, value: box.checked });
      });
      const title = document.createElement("span");
      title.textContent = tab.title || tab.url;
      title.title = tab.url;
      label.append(box, title);
      li.append(label);
      return li;
    })
  );

  const all = state.siteAccess === "all";
  $("access-all").checked = all;
  $("access-listed").checked = !all;
  $("site-requests").replaceChildren(
    ...state.siteRequests.map((host) => item(
      `An agent asks for ${host}`, null,
      button("Allow", () => allowSite(host)),
      button("Dismiss", () => send({ popup: "dismissSite", host })),
    ))
  );
  $("sites").replaceChildren(
    ...state.allowedHosts.map(({ host, granted }) => item(
      granted ? host : `${host}: the browser has not granted it`, all ? "Used when only listed sites are allowed." : null,
      ...(granted ? [] : [button("Grant", () => allowSite(host))]),
      button("Remove", () => send({ popup: "removeSite", host })),
    ))
  );
  $("sites").className = all ? "unused" : "";
  if (all && !state.everySiteGranted) $("sites").replaceChildren(item("The browser has not granted every site.", null, button("Grant", () => allowSite("*"))));

  $("activity").replaceChildren(
    ...state.activity.map((a) => {
      const li = document.createElement("li");
      const time = document.createElement("time");
      time.textContent = new Date(a.time).toLocaleTimeString();
      li.append(time, `${a.client ?? "agent"}: ${a.tool} ${JSON.stringify(a.details)}`);
      if (!a.ok) {
        li.className = "fail";
        li.title = a.error;
      }
      return li;
    })
  );
}

const HIDDEN_LABELS = [
  ["typed", "Anything I type or autofill myself, whatever the field (agents still see what they typed)"],
  ["cards", "Payment cards (number, security code, expiry)"],
  ["ids", "ID numbers (SSN, ITIN, national, state or tax ID, passport, driver's license)"],
  ["answers", "Security question answers (mother's maiden name, first pet...): only when the box's own label names the question"],
  ["address", "Addresses"],
  ["phone", "Phone numbers"],
  ["email", "Email addresses"],
  ["name", "Names"],
  ["birthday", "Birth dates"],
];

function renderHidden() {
  const box = $("hidden-fields");
  if (!box.childElementCount) {
    for (const [kind, text] of HIDDEN_LABELS) {
      const label = el("label", "row");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.dataset.kind = kind;
      input.addEventListener("change", () => send({ popup: "setHidden", kind, value: input.checked }));
      label.append(input, ` ${text}`);
      box.append(label);
    }
  }
  for (const input of box.querySelectorAll("input")) input.checked = !!state.hiddenFields?.[input.dataset.kind];
}

$("paused").addEventListener("change", (e) => send({ popup: "setPaused", value: e.target.checked }));
$("ask").addEventListener("change", (e) => send({ popup: "setAsk", value: e.target.checked }));
$("save-blocked").addEventListener("click", () => send({ popup: "setBlocked", value: $("blocked").value }));
$("access-all").addEventListener("click", () => allowSite("*"));
$("access-listed").addEventListener("click", listedOnly);
$("add-site").addEventListener("submit", (e) => {
  e.preventDefault();
  // What the user typed, as a host name, the way the background page will store it.
  const typed = $("site").value.trim().replace(/^\*\./, "");
  if (!typed) return;
  let host = typed;
  try {
    host = new URL(typed.includes("://") ? typed : `http://${typed}`).hostname;
  } catch {}
  $("site").value = "";
  allowSite(host.toLowerCase().replace(/\.+$/, ""));
});

ask({ popup: "state" }).then(render);
setInterval(async () => render(await ask({ popup: "state" })), 2000);
