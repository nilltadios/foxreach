/* global ExtensionAPI, ChromeUtils */
"use strict";

// Runs in the content process of the tab; has chrome privileges there.
function frameScript() {
  const respond = (id, error) =>
    sendAsyncMessage("foxinput:done", { id, error: error ? String(error) : null });

  addMessageListener("foxinput:click", ({ data: { id, x, y } }) => {
    try {
      // Window.synthesizeMouseEvent replaced windowUtils.sendMouseEvent (present in 159).
      const fire = (type, clickCount, buttons) =>
        content.synthesizeMouseEvent(
          type, x, y,
          { button: 0, buttons, clickCount, inputSource: content.MouseEvent.MOZ_SOURCE_MOUSE },
          { isDOMEventSynthesized: false, isWidgetEventSynthesized: false }
        );
      fire("mousemove", 0, 0);
      fire("mousedown", 1, 1);
      fire("mouseup", 1, 0);
      respond(id);
    } catch (e) {
      respond(id, e);
    }
  });

  addMessageListener("foxinput:type", ({ data: { id, text } }) => {
    try {
      const tip = Cc["@mozilla.org/text-input-processor;1"].createInstance(
        Ci.nsITextInputProcessor
      );
      if (!tip.beginInputTransactionForTests(content)) throw new Error("no TIP transaction");
      for (const ch of text) {
        const ev = new content.KeyboardEvent("", {
          key: ch,
          code: "Key" + ch.toUpperCase(),
          keyCode: ch.toUpperCase().charCodeAt(0),
        });
        tip.keydown(ev);
        tip.keyup(ev);
      }
      respond(id);
    } catch (e) {
      respond(id, e);
    }
  });
}

const FRAME_SCRIPT = `data:application/javascript;charset=utf-8,${encodeURIComponent(
  `(${frameScript.toString()})();`
)}`;

this.foxinput = class extends ExtensionAPI {
  getAPI(context) {
    const loaded = new WeakSet();
    let seq = 0;

    const call = (tabId, name, args) => {
      const browser = context.extension.tabManager.get(tabId).nativeTab.linkedBrowser;
      const mm = browser.messageManager;
      if (!loaded.has(browser)) {
        mm.loadFrameScript(FRAME_SCRIPT, true);
        loaded.add(browser);
      }
      return new Promise((resolve, reject) => {
        const id = ++seq;
        const listener = ({ data }) => {
          if (data.id !== id) return;
          mm.removeMessageListener("foxinput:done", listener);
          data.error ? reject(new context.cloneScope.Error(data.error)) : resolve();
        };
        mm.addMessageListener("foxinput:done", listener);
        mm.sendAsyncMessage(`foxinput:${name}`, { id, ...args });
      });
    };

    return {
      foxinput: {
        click: (tabId, x, y) => call(tabId, "click", { x, y }),
        typeText: (tabId, text) => call(tabId, "type", { text }),
      },
    };
  }
};
