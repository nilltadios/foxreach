"use strict";

const BASE = "http://127.0.0.1:47831";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const report = (step, payload) =>
  fetch(`${BASE}/report`, {
    method: "POST",
    body: JSON.stringify({ step, ...payload }),
  });

const pageEvents = async (tabId) =>
  (await browser.tabs.executeScript(tabId, { code: "document.body.dataset.events" }))[0];

async function run() {
  const tab = await browser.tabs.create({ url: `${BASE}/page`, active: true });
  for (let i = 0; i < 50; i++) {
    if ((await browser.tabs.get(tab.id)).status === "complete") break;
    await sleep(100);
  }
  await sleep(500);

  await report("environment", {
    experimentApi: typeof browser.foxinput,
    webdriver: (await browser.tabs.executeScript(tab.id, { code: "navigator.webdriver" }))[0],
  });

  // Control: an event dispatched from a content script.
  await browser.tabs.executeScript(tab.id, {
    code: `document.getElementById("btn").dispatchEvent(new MouseEvent("click", {bubbles: true}));`,
  });
  await report("content_script_dispatch", { events: await pageEvents(tab.id) });
  await browser.tabs.executeScript(tab.id, { code: `document.body.dataset.events = "[]";` });

  // How long after load does the first synthesized click get delivered?
  const t0 = performance.now();
  const probe = [];
  for (let attempt = 1; attempt <= 12; attempt++) {
    await browser.foxinput.click(tab.id, 100, 70);
    await sleep(250);
    const seen = JSON.parse((await pageEvents(tab.id)) || "[]").some((e) => e.type === "click");
    probe.push({ attempt, ms: Math.round(performance.now() - t0), seen });
    if (seen) break;
  }
  await report("first_click_probe", { probe });
  await browser.tabs.executeScript(tab.id, { code: `document.body.dataset.events = "[]";` });

  // Experiment: click the button, click the input, then type into it.
  for (const [step, action] of [
    ["experiment_click_button", () => browser.foxinput.click(tab.id, 100, 70)],
    ["experiment_click_input", () => browser.foxinput.click(tab.id, 100, 170)],
    ["experiment_type", () => browser.foxinput.typeText(tab.id, "abc")],
  ]) {
    try {
      await action();
      await sleep(300);
      await report(step, { events: await pageEvents(tab.id) });
    } catch (e) {
      await report(step, { error: String(e) });
    }
    await browser.tabs.executeScript(tab.id, { code: `document.body.dataset.events = "[]";` });
  }

  const value = (
    await browser.tabs.executeScript(tab.id, { code: `document.getElementById("inp").value` })
  )[0];
  await report("final_input_value", { value });
  await report("done", {});
}

run().catch((e) => report("fatal", { error: String(e) }));
