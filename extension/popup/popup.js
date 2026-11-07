"use strict";

const $ = (id) => document.getElementById(id);
const ask = (msg) => browser.runtime.sendMessage(msg);

function render(state) {
  const status = $("status");
  status.textContent = !state.connected
    ? `Bridge not connected${state.hostError ? `: ${state.hostError}` : ""}`
    : `Connected, ${state.trusted ? "trusted input" : "untrusted input only"}, v${state.version}`;
  status.className = state.connected ? "ok" : "bad";
  $("paused").checked = state.paused;
  if (document.activeElement !== $("blocked")) $("blocked").value = state.blockedHosts.join(", ");

  $("tabs").replaceChildren(
    ...state.tabs.map((tab) => {
      const li = document.createElement("li");
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = tab.shared;
      box.dataset.tabId = tab.id;
      box.addEventListener("change", async () => {
        render(await ask({ popup: "share", tabId: tab.id, value: box.checked }));
      });
      const title = document.createElement("span");
      title.textContent = tab.title || tab.url;
      title.title = tab.url;
      label.append(box, title);
      li.append(label);
      return li;
    })
  );

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

$("paused").addEventListener("change", async (e) => render(await ask({ popup: "setPaused", value: e.target.checked })));
$("save-blocked").addEventListener("click", async () => render(await ask({ popup: "setBlocked", value: $("blocked").value })));

ask({ popup: "state" }).then(render);
setInterval(async () => render(await ask({ popup: "state" })), 2000);
