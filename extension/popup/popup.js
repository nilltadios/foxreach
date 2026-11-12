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

const send = async (msg) => render(await ask(msg));

/**
 * Let agents use a site ("*": every site). Where the browser hands out sites one by one, it is
 * asked here, in the click itself: it accepts the question only from a click, and its own prompt
 * may close this popup, so the background page finishes the job when the answer arrives.
 */
function allowSite(host) {
  const sent = send({ popup: "wantSite", host });
  if (state?.asksBrowser) browser.permissions.request({ origins: originsOf(host) }).then(() => send({ popup: "state" }), () => {});
  return sent;
}

// Opened by the background page as a window of its own, to show a request. It closes once
// nothing is left to answer.
const requestWindow = location.search === "?requests";
if (requestWindow) {
  document.documentElement.classList.add("request-window");
  $("request-view").hidden = false;
  document.title = "foxreach: a request needs your answer";
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

$("paused").addEventListener("change", (e) => send({ popup: "setPaused", value: e.target.checked }));
$("ask").addEventListener("change", (e) => send({ popup: "setAsk", value: e.target.checked }));
$("save-blocked").addEventListener("click", () => send({ popup: "setBlocked", value: $("blocked").value }));
$("access-all").addEventListener("click", () => allowSite("*"));
$("access-listed").addEventListener("click", () => send({ popup: "listedOnly" }));
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
