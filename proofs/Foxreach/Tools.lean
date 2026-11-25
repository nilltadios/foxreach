import Foxreach.Repaired

/-!
# Every Tool an Agent Can Call

`bridge/tools.mjs` lists the tools (31). The model in `CurrentModel.lean` has fewer requests than
that, because several tools pass the same checks: `hover` at a point is checked as `click` at a
point is, `find` reads the frames `read_page` reads. This file is the table that says, for each
tool and each way of calling it that changes what is checked, which modelled request stands for
it, so that `foxreach_promise_proved` can be read as a statement about every tool.

`test/proofs.py` compares the names in `SimpleTool.name` with the names in `bridge/tools.mjs`: a
tool added to the code and not to this table fails that test.

What this table is not: a proof that each tool's code makes the checks of the request it is
mapped to. That was read from `extension/background.js` (each tool's first lines). `test/e2e.py`
tests it in the browser for the tools that read, point, type or take a ref into a frame of a site
not allowed; for `tabs_select`, `resize_window`, `get_page_text`, `wait_for` and `record` the
mapping rests on the reading alone.
-/

namespace Foxreach

/-- A tool call other than `batch`, with the arguments that decide what is checked. -/
inductive SimpleTool where
  | status (ask : Bool) (tabId : Option TabId)
  | browser_open (profile : ProfileId)
  | browsers_list
  | browser_select
  | tabs_list
  | tabs_create (url : Url)
  | tabs_close (tabId : TabId)
  | tabs_close_mine
  | tabs_select (tabId : TabId)
  /-- `navigate` without a tab opens one, as `tabs_create` does. -/
  | navigate_new (url : Url)
  | navigate_url (tabId : TabId) (url : Url)
  /-- `navigate` with "back" or "forward": `dest` is where the tab's history leads. -/
  | navigate_history (tabId : TabId) (dest : Url)
  | navigate_reload (tabId : TabId)
  | read_page (tabId : TabId)
  /-- `read_page` from a ref down: the ref's frame is checked, as for a click on a ref. -/
  | read_page_ref (tabId : TabId) (frameId : Nat)
  | find (tabId : TabId)
  | get_page_text (tabId : TabId)
  | click_ref (tabId : TabId) (frameId : Nat)
  | click_point (tabId : TabId)
  | hover_ref (tabId : TabId) (frameId : Nat)
  | hover_point (tabId : TabId)
  /-- Both ends of a drag are points, each checked as a click at a point is. -/
  | drag (tabId : TabId)
  /-- With a ref it clicks the element first (`click_ref`'s check), then types where focus is. -/
  | type_text (tabId : TabId)
  | press_key (tabId : TabId)
  | scroll_ref (tabId : TabId) (frameId : Nat)
  | scroll_point (tabId : TabId)
  | scroll_to (tabId : TabId) (frameId : Nat)
  | form_input (tabId : TabId) (frameId : Nat)
  | file_upload_ref (tabId : TabId) (frameId : Nat)
  /-- Files dropped at a point. -/
  | file_upload_point (tabId : TabId)
  | screenshot (tabId : TabId)
  | wait
  | wait_for (tabId : TabId)
  | javascript (tabId : TabId) (frameId : Nat)
  | read_console (tabId : TabId)
  | read_network (tabId : TabId)
  | resize_window (tabId : TabId)
  /-- Each frame of a recording is taken as a screenshot is, with the same covering of secret fields. -/
  | record (tabId : TabId)
  | handle_dialog (tabId : TabId)
  deriving Repr

/-- The tool's name in `bridge/tools.mjs`. -/
def SimpleTool.name : SimpleTool → String
  | .status _ _ => "status"
  | .browser_open _ => "browser_open"
  | .browsers_list => "browsers_list"
  | .browser_select => "browser_select"
  | .tabs_list => "tabs_list"
  | .tabs_create _ => "tabs_create"
  | .tabs_close _ => "tabs_close"
  | .tabs_close_mine => "tabs_close"
  | .tabs_select _ => "tabs_select"
  | .navigate_new _ => "navigate"
  | .navigate_url _ _ => "navigate"
  | .navigate_history _ _ => "navigate"
  | .navigate_reload _ => "navigate"
  | .read_page _ => "read_page"
  | .read_page_ref _ _ => "read_page"
  | .find _ => "find"
  | .get_page_text _ => "get_page_text"
  | .click_ref _ _ => "click"
  | .click_point _ => "click"
  | .hover_ref _ _ => "hover"
  | .hover_point _ => "hover"
  | .drag _ => "drag"
  | .type_text _ => "type_text"
  | .press_key _ => "press_key"
  | .scroll_ref _ _ => "scroll"
  | .scroll_point _ => "scroll"
  | .scroll_to _ _ => "scroll_to"
  | .form_input _ _ => "form_input"
  | .file_upload_ref _ _ => "file_upload"
  | .file_upload_point _ => "file_upload"
  | .screenshot _ => "screenshot"
  | .wait => "wait"
  | .wait_for _ => "wait_for"
  | .javascript _ _ => "javascript"
  | .read_console _ => "read_console"
  | .read_network _ => "read_network"
  | .resize_window _ => "resize_window"
  | .record _ => "record"
  | .handle_dialog _ => "handle_dialog"

/-- The modelled request that stands for the call, or `none` for the two tools the MCP server
answers from its own files without asking any browser: `browsers_list` and `browser_select`. They
touch no tab, site or field; they do tell an agent which browsers are connected (name, profile
folder), whatever pause or agent approval say, since those live in a browser. -/
def SimpleTool.request : SimpleTool → Option ToolRequest
  | .status ask tabId => some (.status ask tabId)
  | .browser_open p => some (.browser_open p)
  | .browsers_list => none
  | .browser_select => none
  | .tabs_list => some .tabs_list
  | .tabs_create url => some (.tabs_create url)
  | .tabs_close tid => some (.tabs_close_one tid)
  | .tabs_close_mine => some .tabs_close_mine
  | .tabs_select tid => some (.top_page tid)
  | .navigate_new url => some (.tabs_create url)
  | .navigate_url tid url => some (.navigate_url tid url)
  | .navigate_history tid dest => some (.navigate_back tid dest)
  | .navigate_reload tid => some (.top_page tid)
  | .read_page tid => some (.read_page tid)
  | .read_page_ref tid fid => some (.click_ref tid fid)
  | .find tid => some (.read_page tid)
  | .get_page_text tid => some (.top_page tid)
  | .click_ref tid fid => some (.click_ref tid fid)
  | .click_point tid => some (.click_xy tid)
  | .hover_ref tid fid => some (.click_ref tid fid)
  | .hover_point tid => some (.click_xy tid)
  | .drag tid => some (.click_xy tid)
  | .type_text tid => some (.type_text tid)
  | .press_key tid => some (.type_text tid)
  | .scroll_ref tid fid => some (.click_ref tid fid)
  | .scroll_point tid => some (.click_xy tid)
  | .scroll_to tid fid => some (.click_ref tid fid)
  | .form_input tid fid => some (.click_ref tid fid)
  | .file_upload_ref tid fid => some (.click_ref tid fid)
  | .file_upload_point tid => some (.click_xy tid)
  | .screenshot tid => some (.screenshot tid)
  | .wait => some .wait
  | .wait_for tid => some (.top_page tid)
  | .javascript tid fid => some (.javascript tid fid)
  | .read_console tid => some (.read_console tid)
  | .read_network tid => some (.read_network tid)
  | .resize_window tid => some (.top_page tid)
  | .record tid => some (.screenshot tid)
  | .handle_dialog tid => some (.handle_dialog tid)

/-- A tool call: one tool, or `batch` with its steps (a batch holds no batch). -/
inductive Tool where
  | one (tool : SimpleTool)
  | batch (steps : List SimpleTool)
  deriving Repr

/-- A step of a batch as the add-on sees it. The add-on runs the steps and knows only its own
tools: one the MCP server answers by itself is, there, an unknown tool, and fails the batch. -/
def SimpleTool.asStep (t : SimpleTool) : ToolRequest :=
  match t.request with
  | some (.browser_open p) => .browser_open p
  | some req => req
  | none => .browser_open 0

def Tool.request : Tool → Option ToolRequest
  | .one t => t.request
  | .batch steps => some (.batch (steps.map SimpleTool.asStep))

/-- Only the two lists of browsers are answered without a request to a browser. -/
theorem no_request_only_for_the_browser_lists (t : SimpleTool) :
    t.request = none ↔ t.name = "browsers_list" ∨ t.name = "browser_select" := by
  cases t <;> simp [SimpleTool.request, SimpleTool.name]

/-- A batch never contains a batch, whatever its steps. -/
theorem a_step_is_never_a_batch (t : SimpleTool) : ∀ actions, t.asStep ≠ .batch actions := by
  intro actions
  cases t <;> simp [SimpleTool.asStep, SimpleTool.request]

/-- **The promise, for every tool.** Whatever tool an agent calls, in whatever reachable state: if
the call reaches a browser and succeeds, what it did keeps the promise of
`foxreach_promise_proved`. -/
theorem every_tool_keeps_the_promise
    {s : BrowserState} (hreach : Reachable s)
    {conn : ConnId} {client : String} {tool : Tool} {req : ToolRequest} {eff : Effect}
    (_hreq : tool.request = some req)
    (hok : handleRequest_repaired s conn client req = .ok eff) :
    (∀ p, req = .browser_open p → s.stoppedProfiles.contains p = false) ∧
    ((∀ p, req ≠ .browser_open p) → agentsStopped_repaired s = false) ∧
    (eff.touchesTabOrPage = true →
      s.settings.paused = false ∧ agentAllowedNow s conn client = true) ∧
    (∀ tid ∈ eff.tabsAccessed, s.agentTabs.contains tid = true) ∧
    eff.respectsSitePolicy s = true ∧
    eff.leaksSecret = false :=
  foxreach_promise_proved hreach hok

end Foxreach
