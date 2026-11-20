// Shown in the tab that held the add-on file once foxreach is installed from it (background.js
// turns that blank tab into this page). An update shows nothing.
const { version } = browser.runtime.getManifest();
document.getElementById("version").textContent = `Version ${version}`;
document.getElementById("close").addEventListener("click", async () => {
  const tab = await browser.tabs.getCurrent();
  if (tab) browser.tabs.remove(tab.id);
});
