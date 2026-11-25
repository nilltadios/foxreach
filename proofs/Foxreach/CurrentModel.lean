import Foxreach.Policy

/-!
# Faithful Model of Current `foxreach` (`v0.4.1`)

Direct transcription of the security gates and tool dispatch in:
- `extension/background.js` (`handleRequest`, `runTool`, `accessibleTab`, `assertAgentAllowed`,
  `agentAccess`, `assertFrameAllowed`, `eachFrame`, `assertNoFilledSecret`, `secretBoxes`,
  `capture`, `tabLine`, `TOOLS.*`, `webRequest.onBeforeRequest`)
- `extension/experiment/parent.js` (`resolvePoint`, `resolveFocus`, `console`)
- `bridge/host.mjs` (`findProfile`, `agentsStopped`)
- `bridge/mcp.mjs` (`callTool`, `browser_open`)

Where the real code has a fail-open path or skips a check, this model preserves that exact
behavior so Lean 4 can both verify what currently holds (`ProvedToday.lean`) and construct
machine-checked counterexamples for what fails (`Counterexamples.lean`).
-/

namespace Foxreach

abbrev TabId := Nat
abbrev ConnId := Nat
abbrev ProfileId := Nat

inductive BuildMode where
  | full    -- LibreWolf / Dev Edition / Nightly: trusted = true, asksBrowser = false
  | signed  -- AMO signed build for Release Firefox: trusted = false, asksBrowser = true
  deriving DecidableEq, Repr

inductive HostPlatform where
  | linuxWithProc
  | macNoProc
  | windows
  deriving DecidableEq, Repr

inductive AgentAccess where
  | allowed
  | waiting
  | refused
  | unasked
  deriving DecidableEq, Repr

structure Settings where
  paused : Bool := false
  blockedHosts : List Host := []
  siteAccess : SiteAccess := .listed
  allowedHosts : List Host := []
  askApproval : Bool := true
  approvedAgents : List String := []
  deriving DecidableEq, Repr

/-- Who opened the dialog that is open over a tab, as the browser tells the add-on
(`promptPrincipal`, read by `dialogState` in `extension/experiment/parent.js`): a page, by its
address, or not told. Any frame can open a dialog over the whole tab. -/
inductive DialogOpener where
  | page (url : Url)
  | unknown
  deriving DecidableEq, Repr

structure Tab where
  tabId : TabId
  url : Url
  title : String
  frames : List Frame := []
  /-- The dialog open over the tab (`alert`, `confirm`, `prompt`, leaving a page), if any. -/
  dialog : Option DialogOpener := none
  networkLog : List Url := []
  /-- Models `eachFrame(...)` rejecting (e.g. `ensureInjected` failing). -/
  eachFrameRejects : Bool := false
  /-- For modeling coordinate hit-testing (`parent.js:68-78`) and keyboard focus (`parent.js:81-89`). -/
  pointHitFrameId : Nat := 0
  focusedFrameId : Nat := 0
  deriving DecidableEq, Repr

structure BrowserState where
  build : BuildMode
  settings : Settings
  /-- Whether Firefox granted `<all_urls>` to this add-on (always true in `.full`). -/
  browserGrantsAll : Bool
  /-- Per-host browser permission grants (`browser.permissions.contains`). -/
  browserGrantedHosts : List Host := []
  /-- `agentTabs`: tabs opened by agents, popups of agent tabs, or explicitly shared by user. -/
  agentTabs : List TabId := []
  /-- `createdBy`: maps agent-opened tabs to the connection that opened them. User-shared tabs have `none`. -/
  createdBy : List (TabId × ConnId) := []
  allowedConns : List ConnId := []
  deniedConns : List ConnId := []
  tabs : List Tab := []
  /-- Native host state (`bridge/host.mjs:30-97`). -/
  hostPlatform : HostPlatform := .linuxWithProc
  hostProfile : Option ProfileId := some 0
  hostCandidates : List ProfileId := []
  stoppedProfiles : List ProfileId := []
  deriving DecidableEq, Repr

/-! ## State Helpers Matching `extension/background.js` and `bridge/host.mjs` -/

def browserGrants (s : BrowserState) (h : Host) : Bool :=
  s.build == .full || s.browserGrantsAll || inList h s.browserGrantedHosts

def siteAllowedIn (s : BrowserState) (u : Url) : Bool :=
  siteAllowed s.settings.siteAccess s.settings.allowedHosts (browserGrants s) u

def findTab (s : BrowserState) (tid : TabId) : Option Tab :=
  s.tabs.find? (fun t => t.tabId == tid)

def tabCreatedBy (s : BrowserState) (tid : TabId) : Option ConnId :=
  (s.createdBy.find? (fun p => p.1 == tid)).map Prod.snd

/-- `agentsStopped()` in `bridge/host.mjs:93-97`.
Note: on `macNoProc`, `findProfile()` returns `none` and `hostCandidates` is empty (`[]`). -/
def agentsStopped_current (s : BrowserState) : Bool :=
  match s.hostProfile with
  | none => s.hostCandidates.any (fun c => s.stoppedProfiles.contains c)
  | some p => s.stoppedProfiles.contains p

/-- `agentAccess(conn, name, ask)` in `extension/background.js:178-185`. -/
def agentAccess_current (s : BrowserState) (conn : ConnId) (name : String) (ask : Bool) : AgentAccess :=
  if !s.settings.askApproval then .allowed
  else if s.allowedConns.contains conn || s.settings.approvedAgents.contains name then .allowed
  else if s.deniedConns.contains conn then .refused
  else if !ask then .unasked
  else .waiting

/-- Whether `assertAgentAllowed(conn, name)` passes immediately (without interactive user click).
Models `extension/background.js:190-201`. -/
def agentAllowedNow (s : BrowserState) (conn : ConnId) (name : String) : Bool :=
  !s.settings.askApproval || s.allowedConns.contains conn || s.settings.approvedAgents.contains name

/-- `tabLine(tab)` in `extension/background.js:243-247`. -/
inductive TabLineOutput where
  | blockedPlaceholder
  | unlistedHostOnly (h : Host)
  | fullTitleAndUrl (title : String) (url : Url)
  deriving DecidableEq, Repr

def tabLine (s : BrowserState) (t : Tab) : TabLineOutput :=
  if hostBlocked s.settings.blockedHosts t.url then
    .blockedPlaceholder
  else if !siteListed s.settings.siteAccess s.settings.allowedHosts t.url then
    .unlistedHostOnly t.url.host
  else
    .fullTitleAndUrl t.title t.url

/-- `accessibleTab(tabId)` in `extension/background.js:260-273`. -/
def accessibleTab (s : BrowserState) (tid : TabId) : Except String Tab :=
  if !s.agentTabs.contains tid then
    .error "Tab is not available to agents"
  else
    match findTab s tid with
    | none => .error "Tab no longer exists"
    | some t =>
      if hostBlocked s.settings.blockedHosts t.url then
        .error "Blocked site"
      else if !siteAllowedIn s t.url then
        .error "Site not allowed"
      else
        .ok t

/-- `assertFrameAllowed(tabId, frameId)` in `extension/background.js:250-258`. -/
def assertFrameAllowed (s : BrowserState) (t : Tab) (frameId : Nat) : Except String Unit :=
  if frameId == 0 then
    .ok ()
  else
    match findFrame t.frames frameId with
    | none => .ok ()
    | some f =>
      let owner := ownerUrl t.frames f
      if hostBlocked s.settings.blockedHosts owner then
        .error "Blocked frame site"
      else if !siteAllowedIn s owner then
        .error "Frame site not allowed"
      else
        .ok ()

/-- Whether a frame is permitted by `eachFrame` (`extension/background.js:364-366`). -/
def framePermitted (s : BrowserState) (t : Tab) (f : Frame) : Bool :=
  if f.frameId == 0 then
    true
  else
    let owner := ownerUrl t.frames f
    !hostBlocked s.settings.blockedHosts owner && siteAllowedIn s owner

/-- `filledSecret` check on a single frame (`extension/content/content.js:665-678`). -/
def frameHasFilledSecret (f : Frame) : Bool :=
  f.fields.any (fun fld => fld.isSecret && !fld.value.isEmpty)

def frameFilledSecrets (f : Frame) : List String :=
  (f.fields.filter (fun fld => fld.isSecret && !fld.value.isEmpty)).map (·.value)

/-- `assertNoFilledSecret` in `extension/background.js:418-426`.
Faithfully models `eachFrame`'s `try/catch` (`L367-376`): a frame with `scriptOutcome = .timeoutOrError`
returns no `value`, so `results.find(r => r.value?.field)` misses it (fail-open!). -/
def assertNoFilledSecret_current (s : BrowserState) (t : Tab) : Except String Unit :=
  if t.eachFrameRejects then
    .error "eachFrame failed"
  else
    let detected := t.frames.any fun f =>
      framePermitted s t f &&
      f.scriptOutcome == .ok &&
      frameHasFilledSecret f
    if detected then
      .error "Filled secret field blocks javascript"
    else
      .ok ()

/-- `secretBoxes` + `capture` in `extension/background.js:707-780`.
Returns the list of frames that contain a filled secret YET go unredacted in the returned image.
Faithfully models:
1. `browser.tabs.captureTab` check (`L750`): requires `build == .full || browserGrantsAll`.
2. `eachFrame(...).catch(() => [])` (`L708`): if `eachFrameRejects = true`, `results` becomes `[]`,
   so ZERO boxes are redacted and ALL filled secrets are exposed!
3. `results.filter(r => r.frame.frameId && !r.value)` (`L714`): skips `frameId == 0` on timeout/error! -/
def captureUnredactedSecrets_current (s : BrowserState) (t : Tab) : Except String (List (Nat × Url)) :=
  if s.build == .signed && !s.browserGrantsAll then
    .error "captureTab requires Every site"
  else if t.eachFrameRejects then
    -- Line 708: `.catch(() => [])` returns empty boxes, so every filled secret in the tab is unredacted!
    let leaked := t.frames.filter frameHasFilledSecret
    .ok (leaked.map fun f => (f.frameId, ownerUrl t.frames f))
  else
    -- Line 714: `r.frame.frameId && !r.value` skips `frameId == 0` when its query fails/times out.
    -- Subframes (`frameId != 0`) that fail/are blocked/unlisted ARE placed in `unchecked` and covered whole.
    let leaked := t.frames.filter fun f =>
      f.frameId == 0 && f.scriptOutcome == .timeoutOrError && frameHasFilledSecret f
    .ok (leaked.map fun f => (f.frameId, ownerUrl t.frames f))

/-! ## Tool Requests and Observable Effects -/

inductive ToolRequest where
  | status (ask : Bool) (tabId : Option TabId)
  | tabs_list
  | tabs_create (url : Url)
  | tabs_close_one (tabId : TabId)
  | tabs_close_mine
  | read_page (tabId : TabId)
  | click_ref (tabId : TabId) (frameId : Nat)
  | click_xy (tabId : TabId)
  | type_text (tabId : TabId)
  | javascript (tabId : TabId) (frameId : Nat)
  | screenshot (tabId : TabId)
  | read_console (tabId : TabId)
  | read_network (tabId : TabId)
  | navigate_url (tabId : TabId) (url : Url)
  | navigate_back (tabId : TabId) (destUrl : Url)
  /-- A tool that needs the tab and at most its top page: `tabs_select`, `resize_window`,
  `get_page_text`, `wait_for`, `navigate` with "reload". -/
  | top_page (tabId : TabId)
  | handle_dialog (tabId : TabId)
  /-- `wait`: passes time, touches nothing. -/
  | wait
  | batch (actions : List ToolRequest)
  | browser_open (profile : ProfileId)
  deriving Repr

inductive Effect where
  | statusOnly (paused : Bool) (access : AgentAccess)
  | statusWithTabProbe (tabId : TabId) (tabUrl : Url)
  | listTabs (entries : List (TabId × TabLineOutput)) (hiddenCount : Nat)
  | openTab (url : Url)
  | closeTabs (tabIds : List TabId)
  | readFrames (tabId : TabId) (framesRead : List (Nat × Url × List FieldOutput))
  | actOnFrame (tabId : TabId) (frameId : Nat) (owner : Url) (trustedInput : Bool)
  | evalJs (tabId : TabId) (frameId : Nat) (owner : Url) (exposedSecrets : List String)
  | takeScreenshot (tabId : TabId) (unredactedSecretFrames : List (Nat × Url))
  | readConsole (tabId : TabId) (entries : List (Nat × Url × String))
  | readNetwork (tabId : TabId) (urls : List Url)
  | navigateHistory (tabId : TabId) (destUrl : Url)
  /-- A dialog's text read, or the dialog answered: `sources` are the pages it can have come from. -/
  | readDialog (tabId : TabId) (sources : List Url)
  | nothing
  | batchEffects (effects : List Effect)
  | hostLaunchBrowser (profile : ProfileId)
  deriving Repr

/-- The pages the open dialog's text can have come from: the page that opened it when the browser
names a web page, else any frame of the tab (each by the site it belongs to); none without a dialog. -/
def dialogSources (t : Tab) : List Url :=
  match t.dialog with
  | none => []
  | some (.page u) => if isWeb u then [u] else t.frames.map (ownerUrl t.frames)
  | some .unknown => t.frames.map (ownerUrl t.frames)

/-! ## Faithful Execution Semantics of Current `foxreach` (`v0.4.1`) -/

def execSingleTool_current (s : BrowserState) (conn : ConnId) (access : AgentAccess)
    (req : ToolRequest) : Except String Effect :=
  match req with
  | .status _ tabOpt =>
    -- Matches `TOOLS.status` (`extension/background.js:785-795`):
    -- When `tabId` is given, calls `accessibleTab(tabId)` + `paintState(tabId)`!
    match tabOpt with
    | none => .ok (.statusOnly s.settings.paused access)
    | some tid =>
      match accessibleTab s tid with
      | .error e => .error e
      | .ok t => .ok (.statusWithTabProbe tid t.url)

  | .tabs_list =>
    let mine := s.tabs.filter (fun t => s.agentTabs.contains t.tabId)
    let entries := mine.map (fun t => (t.tabId, tabLine s t))
    .ok (.listTabs entries (s.tabs.length - mine.length))

  | .tabs_create url =>
    if checkUrlOk s.settings.siteAccess s.settings.allowedHosts s.settings.blockedHosts (browserGrants s) url then
      .ok (.openTab url)
    else
      .error "URL rejected by checkUrl"

  | .tabs_close_one tid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok _ => .ok (.closeTabs [tid])

  | .tabs_close_mine =>
    let own := (s.createdBy.filter (fun p => p.2 == conn)).map Prod.fst
    .ok (.closeTabs own)

  | .read_page tid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      if t.eachFrameRejects then
        .error "eachFrame failed"
      else
        let readable := t.frames.filter (fun f => framePermitted s t f && f.scriptOutcome == .ok)
        let out := readable.map fun f => (f.frameId, ownerUrl t.frames f, f.fields.map stateOfField)
        .ok (.readFrames tid out)

  | .click_ref tid fid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      match assertFrameAllowed s t fid with
      | .error e => .error e
      | .ok () =>
        let owner := match findFrame t.frames fid with
          | some f => ownerUrl t.frames f
          | none => t.url
        .ok (.actOnFrame tid fid owner (s.build == .full))

  | .click_xy tid =>
    -- Matches `TOOLS.click` with `(x,y)` (`extension/background.js:954-959`)
    -- + `parent.js:68-78, 186-193`:
    -- In `.full` build, `resolvePoint` descends into `pointHitFrameId` WITHOUT `assertFrameAllowed`!
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      if s.build == .full then
        let hitId := t.pointHitFrameId
        let owner := match findFrame t.frames hitId with
          | some f => ownerUrl t.frames f
          | none => t.url
        .ok (.actOnFrame tid hitId owner true)
      else
        -- Signed build runs `untrustedMouse` in frame 0 only (`contentCall(tabId, 0, ...)`).
        .ok (.actOnFrame tid 0 t.url false)

  | .type_text tid =>
    -- Matches `TOOLS.type_text` (`extension/background.js:995-1024`)
    -- + `parent.js:81-89, 222-224`:
    -- In `.full` build, `resolveFocus` follows focus into `focusedFrameId` WITHOUT subframe check!
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      if s.build == .full then
        let focId := t.focusedFrameId
        let owner := match findFrame t.frames focId with
          | some f => ownerUrl t.frames f
          | none => t.url
        .ok (.actOnFrame tid focId owner true)
      else
        .ok (.actOnFrame tid 0 t.url false)

  | .javascript tid fid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      match assertFrameAllowed s t fid with
      | .error e => .error e
      | .ok () =>
        match assertNoFilledSecret_current s t with
        | .error e => .error e
        | .ok () =>
          let targetFrame := findFrame t.frames fid
          let owner := match targetFrame with
            | some f => ownerUrl t.frames f
            | none => t.url
          -- Page JS in `fid` can read any filled secret in `fid` (or same-origin frames).
          let exposed := match targetFrame with
            | some f => frameFilledSecrets f
            | none => []
          .ok (.evalJs tid fid owner exposed)

  | .screenshot tid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      match captureUnredactedSecrets_current s t with
      | .error e => .error e
      | .ok unredacted => .ok (.takeScreenshot tid unredacted)

  | .read_console tid =>
    -- Matches `TOOLS.read_console` (`extension/background.js:1186-1190`)
    -- + `parent.js:229-235`: queries ALL frames in tab subtree without site/block check!
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      if s.build != .full then
        .error "read_console needs trusted input"
      else
        let msgs := t.frames.flatMap fun f =>
          f.consoleMsgs.map fun m => (f.frameId, ownerUrl t.frames f, m)
        .ok (.readConsole tid msgs)

  | .read_network tid =>
    -- Matches `TOOLS.read_network` (`extension/background.js:1208-1215`):
    -- returns `network.get(tabId)` unfiltered.
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t => .ok (.readNetwork tid t.networkLog)

  | .navigate_url tid url =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok _ =>
      if checkUrlOk s.settings.siteAccess s.settings.allowedHosts s.settings.blockedHosts (browserGrants s) url then
        .ok (.openTab url)
      else
        .error "URL rejected by checkUrl"

  | .navigate_back tid destUrl =>
    -- Matches `TOOLS.navigate` with `"back"` (`extension/background.js:877-880`):
    -- does not call `checkUrl` before calling `browser.tabs.goBack`.
    match accessibleTab s tid with
    | .error e => .error e
    | .ok _ => .ok (.navigateHistory tid destUrl)

  | .top_page tid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t => .ok (.actOnFrame tid 0 t.url false)

  | .handle_dialog tid =>
    -- `TOOLS.handle_dialog` and `dialogState` (v0.4.1): the dialog's text goes to the agent, and
    -- the agent may answer it, whichever frame opened it.
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      if s.build != .full then
        .error "handle_dialog needs trusted input"
      else
        .ok (.readDialog tid (dialogSources t))

  | .wait => .ok .nothing

  | .batch _ => .error "batch cannot be nested"
  -- The add-on has no such tool (`TOOLS`, `runTool`: "Unknown tool"): `bridge/mcp.mjs` answers
  -- `browser_open` itself, and only as a call of its own, so one inside `batch` ends here.
  | .browser_open _ => .error "Unknown tool browser_open"

/-- Matches `runTool` (`extension/background.js:1289-1305`) for non-batch steps. -/
def runToolSingle_current (s : BrowserState) (conn : ConnId) (access : AgentAccess)
    (req : ToolRequest) : Except String Effect :=
  match req with
  | .status _ _ => execSingleTool_current s conn access req
  | _ =>
    if s.settings.paused then
      .error "The user has paused agent access"
    else
      execSingleTool_current s conn access req

def runBatchList_current (s : BrowserState) (conn : ConnId) : List ToolRequest → Except String (List Effect)
  | [] => .ok []
  | a :: rest =>
    match a with
    | .batch _ => .error "batch cannot be nested"
    | _ =>
      match runToolSingle_current s conn .allowed a with
      | .error e => .error e
      | .ok eff =>
        match runBatchList_current s conn rest with
        | .error e => .error e
        | .ok effs => .ok (eff :: effs)

/-- Full request pipeline matching `bridge/mcp.mjs:527-530`, `bridge/host.mjs:229-233`,
and `extension/background.js:1289-1323`. -/
def handleRequest_current (s : BrowserState) (conn : ConnId) (client : String)
    (req : ToolRequest) : Except String Effect :=
  match req with
  | .browser_open p =>
    -- Executed directly in `bridge/mcp.mjs:529` before connecting to any browser host or approval.
    .ok (.hostLaunchBrowser p)
  | _ =>
    -- Native host stop check (`bridge/host.mjs:229-232`).
    if agentsStopped_current s then
      .error "The user stopped agents in this browser profile"
    else
      match req with
      | .status ask _ =>
        -- `background.js:1312`: skips `assertAgentAllowed`, and `runTool` skips `paused`.
        let access := agentAccess_current s conn client ask
        runToolSingle_current s conn access req
      | .batch actions =>
        if !agentAllowedNow s conn client then
          .error "Agent not allowed"
        else if s.settings.paused then
          .error "The user has paused agent access"
        else if actions.isEmpty then
          .error "actions must be non-empty"
        else
          match runBatchList_current s conn actions with
          | .error e => .error e
          | .ok effs => .ok (.batchEffects effs)
      | _ =>
        if !agentAllowedNow s conn client then
          .error "Agent not allowed"
        else
          runToolSingle_current s conn .allowed req

/-! ## `webRequest.onBeforeRequest` & Signed-Build Popup Transitions -/

/-- Models `webRequest.onBeforeRequest` (`extension/background.js:1631-1646`).
Cancels `hostBlocked` requests in `agentTabs`; otherwise records `url` into `tab.networkLog`
even if `url` is NOT in `allowedHosts`. -/
def onBeforeRequest_current (s : BrowserState) (tid : TabId) (url : Url) : BrowserState :=
  if !s.agentTabs.contains tid then
    s
  else if hostBlocked s.settings.blockedHosts url then
    s
  else
    { s with
      tabs := s.tabs.map fun t =>
        if t.tabId == tid then { t with networkLog := t.networkLog ++ [url] } else t }

/-- Models popup `"listedOnly"` (`extension/background.js:1579-1582`):
sets `siteAccess := .listed` AND removes `<all_urls>` in the signed build (`asksBrowser = true`). -/
def popupListedOnly (s : BrowserState) : BrowserState :=
  { s with
    settings := { s.settings with siteAccess := .listed }
    browserGrantsAll := if s.build == .signed then false else s.browserGrantsAll }

/-- Models popup `"wantSite"` for `"*"` + `applyWanted` (`extension/background.js:138-149`):
granting `<all_urls>` sets `siteAccess := .all`. -/
def popupGrantAllSites (s : BrowserState) : BrowserState :=
  { s with
    settings := { s.settings with siteAccess := .all }
    browserGrantsAll := true }

/-- Models popup `"share"` with `value = false` (`extension/background.js:1557-1560`):
deletes `tid` from `agentTabs`, but does NOT delete `tid` from `createdBy`! -/
def popupUnshareTab_current (s : BrowserState) (tid : TabId) : BrowserState :=
  { s with agentTabs := s.agentTabs.filter (· != tid) }

/-! ## Security Predicates on Effects -/

def Effect.touchesTabOrPage : Effect → Bool
  | .statusOnly _ _ => false
  | .nothing => false
  | .listTabs _ _ => false
  | .hostLaunchBrowser _ => false
  | .batchEffects effs => effs.any Effect.touchesTabOrPage
  | _ => true

def Effect.tabsAccessed : Effect → List TabId
  | .statusOnly _ _ => []
  | .statusWithTabProbe tid _ => [tid]
  | .listTabs entries _ => entries.map Prod.fst
  | .openTab _ => []
  | .closeTabs tids => tids
  | .readFrames tid _ => [tid]
  | .actOnFrame tid _ _ _ => [tid]
  | .evalJs tid _ _ _ => [tid]
  | .takeScreenshot tid _ => [tid]
  | .readConsole tid _ => [tid]
  | .readNetwork tid _ => [tid]
  | .navigateHistory tid _ => [tid]
  | .readDialog tid _ => [tid]
  | .nothing => []
  | .batchEffects effs => effs.flatMap Effect.tabsAccessed
  | .hostLaunchBrowser _ => []

def Effect.urlsAccessed : Effect → List Url
  | .statusOnly _ _ => []
  | .statusWithTabProbe _ u => [u]
  | .listTabs _ _ => []
  | .openTab u => [u]
  | .closeTabs _ => []
  | .readFrames _ fs => fs.map (fun x => x.2.1)
  | .actOnFrame _ _ u _ => [u]
  | .evalJs _ _ u _ => [u]
  | .takeScreenshot _ _ => []
  | .readConsole _ entries => entries.map (fun x => x.2.1)
  | .readNetwork _ urls => urls
  | .navigateHistory _ u => [u]
  | .readDialog _ sources => sources
  | .nothing => []
  | .batchEffects effs => effs.flatMap Effect.urlsAccessed
  | .hostLaunchBrowser _ => []

def Effect.leaksSecret : Effect → Bool
  | .readFrames _ fs =>
    fs.any fun x => x.2.2.any fun out =>
      match out with
      | .redacted _ _ => false
      | .plainValue _ => false -- `stateOfField` only emits `.plainValue` for non-secret fields
      | .empty => false
  | .evalJs _ _ _ exposed => !exposed.isEmpty
  | .takeScreenshot _ unredacted => !unredacted.isEmpty
  | .batchEffects effs => effs.any Effect.leaksSecret
  | _ => false

/-- Whether every URL read or acted on by `eff` obeys `s`'s blocklist and site allowlist. -/
def Effect.respectsSitePolicy (s : BrowserState) (eff : Effect) : Bool :=
  (eff.urlsAccessed).all fun u =>
    !hostBlocked s.settings.blockedHosts u && siteAllowedIn s u

end Foxreach
