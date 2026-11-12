// The MCP tool list. Behaviour lives in extension/background.js under the same names.
const tabId = { type: "integer", description: "Tab id from tabs_list or tabs_create." };
const ref = { type: "string", description: "Element ref from read_page or find, such as r12 or f123:r12." };
const x = { type: "number", description: "X in CSS pixels from the viewport's left edge." };
const y = { type: "number", description: "Y in CSS pixels from the viewport's top edge." };
const modifiers = {
  type: "array",
  items: { type: "string", enum: ["ctrl", "shift", "alt", "meta"] },
  description: "Modifier keys held during the action.",
};

const tool = (name, description, properties = {}, required = []) => ({
  name,
  description,
  inputSchema: { type: "object", properties, required, additionalProperties: false },
});

export const TOOLS = [
  tool("status", "Report whether the browser is connected, the add-on and bridge versions (with a warning if they differ), whether input is trusted (isTrusted=true), and whether the user allows agents on every site (siteAccess \"all\") or only on listed ones. Works before the user has allowed this agent; other tools then wait for the user's answer."),
  tool("tabs_list", "List the tabs agents may use: ones created by agents or shared by the user. Other tabs stay private."),
  tool("tabs_create", "Open a new tab, wait for it to load, and return its id.", {
    url: { type: "string", description: "http(s) URL. Default about:blank." },
    active: { type: "boolean", description: "Bring the tab to the front, in place of the tab the user is looking at. Default false: it opens in the background, where every tool works. Use true, or tabs_select later, only when the user should see it." },
  }),
  tool("tabs_close", "Close a tab.", { tabId }, ["tabId"]),
  tool("tabs_select", "Bring a tab to the front.", { tabId }, ["tabId"]),
  tool("navigate", "Load a URL and wait for it, or go back, forward or reload. With no tabId, opens a new tab in the background.", {
    tabId,
    url: { type: "string", description: 'A URL, or "back", "forward", "reload".' },
  }, ["url"]),
  tool("read_page", "Read the page as an accessibility-style tree, across iframes and shadow DOM. Each element has a ref for other tools. Prefer this over screenshots.", {
    tabId,
    filter: { type: "string", enum: ["all", "interactive"], description: "Default all." },
    scope: { type: "string", enum: ["viewport", "page"], description: "Default viewport for the interactive filter, otherwise page." },
    max_depth: { type: "integer", description: "Default 40." },
    ref: { ...ref, description: "Read only under this element." },
    max_chars: { type: "integer", description: "Default 50000." },
  }, ["tabId"]),
  tool("find", 'Find elements by plain description or text, across frames, best match first: "search bar", "the field for typing your name", "login button".', {
    tabId,
    query: { type: "string" },
    limit: { type: "integer", description: "Default 20." },
  }, ["tabId", "query"]),
  tool("get_page_text", "Get the visible text of the page, main content first.", {
    tabId,
    max_chars: { type: "integer", description: "Default 50000." },
  }, ["tabId"]),
  tool("click", "Click an element or a viewport point with real input (isTrusted=true), including inside cross-site iframes. The press is confirmed delivered before this returns.", {
    tabId, ref, x, y,
    button: { type: "string", enum: ["left", "middle", "right"], description: "Default left." },
    count: { type: "integer", description: "2 for a double click, 3 for a triple click. Default 1." },
    modifiers,
  }, ["tabId"]),
  tool("hover", "Move the pointer over an element or point.", { tabId, ref, x, y }, ["tabId"]),
  tool("drag", "Press at one viewport point, move, and release at another. Works for sliders, pointer-driven dragging and HTML5 drag-and-drop.", {
    tabId,
    from_x: x, from_y: y, to_x: x, to_y: y,
    modifiers,
  }, ["tabId", "from_x", "from_y", "to_x", "to_y"]),
  tool("type_text", "Type text with real key events into the focused element, or click ref first. Use \\n for Enter. Non-Latin text and emoji are supported. " +
    "The reply names the element that received the text and warns when its value did not change. Refuses when focus is not on a text field.", {
    tabId,
    text: { type: "string" },
    ref: { ...ref, description: "Click this element first." },
    allow_non_text: { type: "boolean", description: "Type even though focus is not on a text field, for keyboard-driven pages. Default false." },
  }, ["tabId", "text"]),
  tool("press_key", 'Press keys with real events. Space-separated sequence, + for combos: "Enter", "ctrl+a Delete", "shift+Tab". ' +
    "The reply names the focused element. Refuses Delete and Backspace when focus is not on a text field.", {
    tabId,
    keys: { type: "string" },
    repeat: { type: "integer", description: "Default 1." },
    allow_non_text: { type: "boolean", description: "Send Delete or Backspace even though focus is not on a text field. Default false." },
  }, ["tabId", "keys"]),
  tool("scroll", "Scroll at a point or the viewport centre by a pixel amount, and return a screenshot of the result.", {
    tabId, ref, x, y,
    dx: { type: "number", description: "Pixels right. Default 0." },
    dy: { type: "number", description: "Pixels down. Default 0." },
    screenshot: { type: "boolean", description: "Return a screenshot of the result. Default true." },
  }, ["tabId"]),
  tool("scroll_to", "Scroll an element to the centre of the viewport.", { tabId, ref }, ["tabId", "ref"]),
  tool("form_input", "Set a form field's value directly and fire input and change. For keystroke-sensitive fields use type_text instead.", {
    tabId, ref,
    value: { description: "String, or boolean for a checkbox or radio. For a select, the option's value or text." },
  }, ["tabId", "ref", "value"]),
  tool("file_upload", "Attach local files to a file input (ref), or drop them on a drop zone (x and y). The agent's machine reads the files. Hidden files and folders (a name starting with a dot) are refused.", {
    tabId, ref, x, y,
    paths: { type: "array", items: { type: "string" }, description: "Absolute file paths." },
  }, ["tabId", "paths"]),
  tool("screenshot", "Capture a tab, even in the background. At scale 1 with no region, image pixels equal viewport coordinates.", {
    tabId,
    region: {
      type: "object",
      description: "Viewport region to capture, to zoom in on detail.",
      properties: { x: { type: "number" }, y: { type: "number" }, width: { type: "number" }, height: { type: "number" } },
      required: ["x", "y", "width", "height"],
    },
    format: { type: "string", enum: ["jpeg", "png"], description: "Default jpeg." },
    quality: { type: "integer", description: "JPEG quality 1-100. Default 75." },
    scale: { type: "number", description: "Image pixels per CSS pixel. Default 1." },
    save_to: { type: "string", description: "Also save the image to this absolute path, which must end in .png, .jpg, .jpeg or .webp, for example to upload it with file_upload." },
    show_pointer: { type: "boolean", description: "Draw the agent's pointer, with its tip at the last position used. Default true." },
  }, ["tabId"]),
  tool("wait", "Wait a number of seconds, at most 60.", { seconds: { type: "number" } }),
  tool("wait_for", "Wait until a selector matches or text appears in the top frame.", {
    tabId,
    selector: { type: "string" },
    text: { type: "string" },
    timeout: { type: "number", description: "Seconds. Default 15, at most 120." },
  }, ["tabId"]),
  tool("javascript", "Run JavaScript in the page's own world and return the last expression as JSON. Top-level await works, and page CSP does not block it. let/const are local to the call; a bare assignment or var sets a global on the page's window, which later calls can read.", {
    tabId,
    code: { type: "string", description: "The code. Needs code, file, or both." },
    file: { type: "string", description: "Absolute path of a .js/.mjs file on this machine to run first (before code), for example a helper library. Hidden files and folders are refused." },
    args: { description: "Any JSON value, available to the code as the constant `args`, so data need not be pasted into code." },
    save_to: { type: "string", description: "Write the full result to this absolute path (.json, .txt, .csv, .tsv, .md or .log) and reply with only its size and start. Results up to 16 MiB; without save_to, replies stop at 500 kB." },
    timeout: { type: "number", description: "Seconds to wait for the result. Default 30, at most 600." },
    frame_id: { type: "integer", description: "Frame id from read_page. Default: the top frame." },
  }, ["tabId"]),
  tool("read_console", "Read console messages and script errors from all frames. Works without injecting anything into the page.", {
    tabId,
    pattern: { type: "string", description: "Keep messages matching this regular expression." },
    only_errors: { type: "boolean" },
    limit: { type: "integer", description: "Default 50." },
    clear: { type: "boolean", description: "Do not show these messages again." },
  }, ["tabId"]),
  tool("read_network", "List requests made by a tab since agents gained access to it. Headers and bodies are not captured.", {
    tabId,
    url_pattern: { type: "string", description: "Keep URLs containing this text." },
    limit: { type: "integer", description: "Default 50." },
    clear: { type: "boolean", description: "Forget this tab's list after reading." },
  }, ["tabId"]),
  tool("resize_window", "Resize the window containing a tab.", {
    tabId,
    width: { type: "integer" },
    height: { type: "integer" },
  }, ["tabId", "width", "height"]),
  tool("record", "Record a tab's actions as an animated GIF, with click marks and action labels. Start, act, then stop with a path to save it.", {
    tabId,
    action: { type: "string", enum: ["start", "stop", "discard"] },
    save_to: { type: "string", description: "With stop: absolute path for the GIF, ending in .gif." },
  }, ["tabId", "action"]),
  tool("batch", "Run several tools in one round trip, in order, stopping at the first error. Much faster than separate calls when the next steps are predictable.", {
    actions: {
      type: "array",
      description: "Steps to run, each with the tool's name and its arguments.",
      items: {
        type: "object",
        properties: { tool: { type: "string" }, args: { type: "object" } },
        required: ["tool"],
      },
    },
    stop_on_error: { type: "boolean", description: "Default true." },
  }, ["actions"]),
  tool("handle_dialog", "Read, accept or dismiss a page's alert, confirm, prompt or beforeunload dialog.", {
    tabId,
    action: { type: "string", enum: ["status", "accept", "dismiss"], description: "Default status." },
    text: { type: "string", description: "Text to enter into a prompt before accepting." },
  }, ["tabId"]),
];
