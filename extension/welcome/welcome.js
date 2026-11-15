// Shown in the tab that held the add-on file once foxreach is installed or updated from it
// (background.js turns that blank tab into this page). "?update" for an update.
const { version } = browser.runtime.getManifest();
if (location.search.includes("update")) {
  document.getElementById("title").textContent = `foxreach is updated to ${version}`;
  document.title = "foxreach is updated";
}
document.getElementById("version").textContent = `Version ${version}`;
document.getElementById("close").addEventListener("click", async () => {
  const tab = await browser.tabs.getCurrent();
  if (tab) browser.tabs.remove(tab.id);
});
