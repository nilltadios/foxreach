// Like a password manager's in-page prompt: the extension's own page in an iframe, over the site,
// inside a closed shadow root so the site cannot reach it.
const host = document.createElement("div");
host.style.cssText = "position:fixed;left:40px;top:300px;width:360px;height:120px;z-index:2147483647";
const root = host.attachShadow({ mode: "closed" });
const frame = document.createElement("iframe");
frame.src = browser.runtime.getURL("box.html");
frame.style.cssText = "width:360px;height:120px;border:0";
root.append(frame);
document.documentElement.append(host);
// The box reports what it received, so the test can read it from the page.
addEventListener("message", (e) => { if (e.data?.pinbox) document.documentElement.dataset.pin = e.data.pinbox; });
