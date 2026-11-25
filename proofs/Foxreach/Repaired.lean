import Foxreach.ProvedToday
import Foxreach.Counterexamples

/-!
# Repaired Enforcement Specification & End-to-End Proof of the One Promise

Models the enforcement pipeline with the fixes for CE-1 through CE-9 and CE-11 as the code has them, defines
the reachable-state transition system (user popup actions, browser lifecycle events, secret-field
input), and machine-checks the unified promise theorem `foxreach_promise_proved` over all reachable
states and every modelled tool call (including `batch`), with zero `sorry`.

CE-10 (`browser_open`) is closed only as far as the code can close it: the bridge answers that call
before any browser runs, so it can honour a profile's stop mark but not pause or agent approval,
which only the add-on knows. `browser_open_not_gated_by_pause_or_approval` states that limit.
-/

namespace Foxreach

/-! ## Repaired Gates (closing CE-1 through CE-9, and CE-10 as far as the bridge can) -/

/-- Fix for CE-7 (`agentsStopped`, `bridge/host.mjs`): with the profile unknown, a stop in any
candidate counts; with no candidate either, a stop in any profile foxreach knows of does
(`allProfiles`, `bridge/addon.mjs`). `stoppedProfiles` is the set of known profiles holding the mark. -/
def agentsStopped_repaired (s : BrowserState) : Bool :=
  match s.hostProfile with
  | some p => s.stoppedProfiles.contains p
  | none =>
    if s.hostCandidates.isEmpty then
      !s.stoppedProfiles.isEmpty
    else
      s.hostCandidates.any (fun c => s.stoppedProfiles.contains c)

/-- A frame whose document has an origin of its own, which a script in another frame cannot read:
another add-on's page (`moz-extension:`) or a `data:` document. The add-on's script does not run in
such a frame, so it never answers the check. -/
def opaqueToOtherFrames (f : Frame) : Bool :=
  f.url.scheme == .mozExtension || f.url.scheme == .data

/-- The frames `assertNoFilledSecret` must hear from before `javascript` runs in frame `targetFid`:
the top frame, the target itself, and every frame that is not `opaqueToOtherFrames`
(`extension/background.js`, the `unchecked` test in `assertNoFilledSecret`). -/
def mustAnswer (targetFid : Nat) (f : Frame) : Bool :=
  f.frameId == 0 || f.frameId == targetFid || !opaqueToOtherFrames f

/-- Fix for CE-4: `assertNoFilledSecret(tabId, targetFrameId)` as the code has it. It refuses when
1. `eachFrame` rejects,
2. a permitted frame that answered holds a filled secret,
3. the top frame did not answer, or
4. a permitted frame that `mustAnswer` did not. -/
def assertNoFilledSecret_repaired (s : BrowserState) (t : Tab) (targetFid : Nat) : Except String Unit :=
  if t.eachFrameRejects then
    .error "eachFrame failed (fail-closed)"
  else if t.frames.any (fun f => framePermitted s t f && (f.scriptOutcome == .ok && frameHasFilledSecret f)) then
    .error "Filled secret blocks javascript"
  else if !t.frames.any (fun f => f.frameId == 0 && f.scriptOutcome == .ok) then
    .error "The page could not be checked for filled secrets"
  else if t.frames.any (fun f => framePermitted s t f && (f.scriptOutcome != .ok && mustAnswer targetFid f)) then
    .error "A frame could not be checked for filled secrets"
  else
    .ok ()

/-- Fix for CE-5a & CE-5b: `secretBoxes` is FAIL-CLOSED:
1. Do NOT swallow `eachFrame` rejection with `.catch(() => [])`.
2. If top frame (`frameId = 0`) fails/times out, refuse the screenshot instead of skipping it.
With every non-refused frame either cleanly checked & redacted or covered whole as `unchecked`,
zero secret frames go unredacted (`[]`). -/
def captureUnredactedSecrets_repaired (s : BrowserState) (t : Tab) : Except String (List (Nat × Url)) :=
  if s.build == .signed && !s.browserGrantsAll then
    .error "captureTab requires Every site"
  else if t.eachFrameRejects then
    .error "No picture: secretBoxes could not inspect tab (fail-closed)"
  else if t.frames.any (fun f => f.frameId == 0 && f.scriptOutcome != .ok) then
    .error "No picture: top frame could not be inspected for secret fields (fail-closed)"
  else
    .ok []

/-- Helper: check that a resolved frame's owner URL is both unblocked and allowed
(Fix for CE-2a, CE-2b, CE-3). -/
def resolveAllowedFrame (s : BrowserState) (t : Tab) (fid : Nat) : Except String Url :=
  let owner := match findFrame t.frames fid with
    | some f => ownerUrl t.frames f
    | none => t.url
  if hostBlocked s.settings.blockedHosts owner then
    .error "Blocked frame site"
  else if !siteAllowedIn s owner then
    .error "Frame site not allowed"
  else
    .ok owner

/-- Repaired single-tool execution. -/
def execSingleTool_repaired (s : BrowserState) (conn : ConnId) (access : AgentAccess)
    (req : ToolRequest) : Except String Effect :=
  match req with
  | .status _ tabOpt =>
    -- Fix for CE-1: `status(tabId)` requires unpaused AND approved agent before touching any tab!
    match tabOpt with
    | none => .ok (.statusOnly s.settings.paused access)
    | some tid =>
      if s.settings.paused then
        .error "The user has paused agent access"
      else if access != .allowed then
        .error "Agent not allowed to inspect tabs"
      else
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
    -- Fix for CE-9: filter by BOTH `createdBy` AND `s.agentTabs.contains`!
    let own := (s.createdBy.filter (fun p => p.2 == conn && s.agentTabs.contains p.1)).map Prod.fst
    .ok (.closeTabs own)

  | .read_page tid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      if t.eachFrameRejects then
        .error "eachFrame failed"
      else
        let readable := t.frames.filter fun f =>
          let owner := ownerUrl t.frames f
          !hostBlocked s.settings.blockedHosts owner &&
          siteAllowedIn s owner &&
          f.scriptOutcome == .ok
        let out := readable.map fun f => (f.frameId, ownerUrl t.frames f, f.fields.map stateOfField)
        .ok (.readFrames tid out)

  | .click_ref tid fid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      match resolveAllowedFrame s t fid with
      | .error e => .error e
      | .ok owner => .ok (.actOnFrame tid fid owner (s.build == .full))

  | .click_xy tid =>
    -- Fix for CE-2a: check target frame's site policy BEFORE delivering input!
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      let targetFid := if s.build == .full then t.pointHitFrameId else 0
      match resolveAllowedFrame s t targetFid with
      | .error e => .error e
      | .ok owner => .ok (.actOnFrame tid targetFid owner (s.build == .full))

  | .type_text tid =>
    -- Fix for CE-2b: check focused frame's site policy BEFORE sending keystrokes!
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      let targetFid := if s.build == .full then t.focusedFrameId else 0
      match resolveAllowedFrame s t targetFid with
      | .error e => .error e
      | .ok owner => .ok (.actOnFrame tid targetFid owner (s.build == .full))

  | .javascript tid fid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      match resolveAllowedFrame s t fid with
      | .error e => .error e
      | .ok owner =>
        match assertNoFilledSecret_repaired s t fid with
        | .error e => .error e
        | .ok () =>
          let targetFrame := findFrame t.frames fid
          let exposed := match targetFrame with
            | some f => frameFilledSecrets f
            | none => []
          .ok (.evalJs tid fid owner exposed)

  | .screenshot tid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      match captureUnredactedSecrets_repaired s t with
      | .error e => .error e
      | .ok unredacted => .ok (.takeScreenshot tid unredacted)

  | .read_console tid =>
    -- Fix for CE-3: filter subframe console entries by site/block policy!
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      if s.build != .full then
        .error "read_console needs trusted input"
      else
        let allowedFrames := t.frames.filter fun f =>
          let owner := ownerUrl t.frames f
          !hostBlocked s.settings.blockedHosts owner && siteAllowedIn s owner
        let msgs := allowedFrames.flatMap fun f =>
          f.consoleMsgs.map fun m => (f.frameId, ownerUrl t.frames f, m)
        .ok (.readConsole tid msgs)

  | .read_network tid =>
    -- Fix for CE-8: filter out any network URLs from blocked or unlisted sites!
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      let allowedUrls := t.networkLog.filter fun u =>
        !hostBlocked s.settings.blockedHosts u && siteAllowedIn s u
      .ok (.readNetwork tid allowedUrls)

  | .navigate_url tid url =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok _ =>
      if checkUrlOk s.settings.siteAccess s.settings.allowedHosts s.settings.blockedHosts (browserGrants s) url then
        .ok (.openTab url)
      else
        .error "URL rejected by checkUrl"

  | .navigate_back tid destUrl =>
    -- `navigate` with "back" or "forward" (`extension/background.js`): the history entry's address
    -- is checked like any address an agent opens (read beforehand through `historyUrl` in
    -- `extension/experiment/parent.js`; in the signed build, where it lands, and undone if refused).
    match accessibleTab s tid with
    | .error e => .error e
    | .ok _ =>
      if !hostBlocked s.settings.blockedHosts destUrl && siteAllowedIn s destUrl then
        .ok (.navigateHistory tid destUrl)
      else
        .error "History destination is blocked or not allowed"

  | .top_page tid =>
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t => .ok (.actOnFrame tid 0 t.url false)

  | .handle_dialog tid =>
    -- Fix for CE-11 (`dialogAllowed`, `extension/background.js`): a dialog belongs to the page
    -- that opened it. Its text is shown, and it may be answered, only when every page it can
    -- have come from is one agents may read. `assertNoDialog` and `click` use the same check for
    -- the dialog text they report.
    match accessibleTab s tid with
    | .error e => .error e
    | .ok t =>
      if s.build != .full then
        .error "handle_dialog needs trusted input"
      else if (dialogSources t).all (fun u => !hostBlocked s.settings.blockedHosts u && siteAllowedIn s u) then
        .ok (.readDialog tid (dialogSources t))
      else
        .error "The dialog comes from a frame of a site agents are not allowed on"

  | .wait => .ok .nothing

  | .batch _ => .error "batch cannot be nested"
  -- As in the current model: not a tool of the add-on, so it cannot ride inside `batch`.
  | .browser_open _ => .error "Unknown tool browser_open"

def runToolSingle_repaired (s : BrowserState) (conn : ConnId) (access : AgentAccess)
    (req : ToolRequest) : Except String Effect :=
  match req with
  | .status _ _ => execSingleTool_repaired s conn access req
  | _ =>
    if s.settings.paused then
      .error "The user has paused agent access"
    else
      execSingleTool_repaired s conn access req

def runBatchList_repaired (s : BrowserState) (conn : ConnId) : List ToolRequest → Except String (List Effect)
  | [] => .ok []
  | a :: rest =>
    match a with
    | .batch _ => .error "batch cannot be nested"
    | _ =>
      match runToolSingle_repaired s conn .allowed a with
      | .error e => .error e
      | .ok eff =>
        match runBatchList_repaired s conn rest with
        | .error e => .error e
        | .ok effs => .ok (eff :: effs)

/-- A request that reaches the browser: the native host's stop check (`bridge/host.mjs`), then the
add-on's agent approval and pause (`extension/background.js`). -/
def handleGated_repaired (s : BrowserState) (conn : ConnId) (client : String)
    (req : ToolRequest) : Except String Effect :=
  if agentsStopped_repaired s then
    .error "The user stopped agents in this browser profile"
  else
    match req with
    | .status ask _ =>
      let access := agentAccess_current s conn client ask
      runToolSingle_repaired s conn access req
    | .batch actions =>
      if !agentAllowedNow s conn client then
        .error "Agent not allowed"
      else if s.settings.paused then
        .error "The user has paused agent access"
      else if actions.isEmpty then
        .error "actions must be non-empty"
      else
        match runBatchList_repaired s conn actions with
        | .error e => .error e
        | .ok effs => .ok (.batchEffects effs)
    | _ =>
      if !agentAllowedNow s conn client then
        .error "Agent not allowed"
      else
        runToolSingle_repaired s conn .allowed req

/-- Repaired full request pipeline. `browser_open` is answered by `bridge/mcp.mjs` before any
browser is reached (the browser it would have to ask is the one not running), so neither the
host's stop check nor the add-on's pause and approval see it. What applies to it is the stop mark
of the profile it names: `openableProfiles` (`bridge/open.mjs`) leaves stopped profiles out.
Every other request goes through `handleGated_repaired`. -/
def handleRequest_repaired (s : BrowserState) (conn : ConnId) (client : String)
    (req : ToolRequest) : Except String Effect :=
  match req with
  | .browser_open p =>
    if s.stoppedProfiles.contains p then
      .error "The user stopped agents in this browser profile"
    else
      .ok (.hostLaunchBrowser p)
  | _ => handleGated_repaired s conn client req

/-! ## Reachable State Transition System & Inductive Invariant -/

/-- Structural browser invariant preserved across all user and browser transitions:
every tab tracked in `createdBy` belongs to `agentTabs`. -/
structure StateInv (s : BrowserState) : Prop where
  createdBy_in_agentTabs : ∀ p ∈ s.createdBy, s.agentTabs.contains p.1 = true

def initialState (build : BuildMode) : BrowserState :=
  { build := build
    settings :=
      { paused := false
        blockedHosts := []
        siteAccess := .listed
        allowedHosts := []
        askApproval := true
        approvedAgents := [] }
    browserGrantsAll := (build == .full)
    browserGrantedHosts := []
    agentTabs := []
    createdBy := []
    allowedConns := []
    deniedConns := []
    tabs := [] }

/-- Repaired popup unshare deletes from BOTH `agentTabs` AND `createdBy` (Fix for CE-9). -/
def popupUnshareTab_repaired (s : BrowserState) (tid : TabId) : BrowserState :=
  { s with
    agentTabs := s.agentTabs.filter (· != tid)
    createdBy := s.createdBy.filter (fun p => p.1 != tid) }

/-- Browser/user state transitions (all operations that evolve `BrowserState`). -/
inductive Step : BrowserState → BrowserState → Prop where
  | setPaused (s : BrowserState) (v : Bool) :
      Step s { s with settings := { s.settings with paused := v } }
  | setBlocked (s : BrowserState) (hs : List Host) :
      Step s { s with settings := { s.settings with blockedHosts := hs } }
  | allowSite (s : BrowserState) (h : Host) :
      Step s { s with
        settings := { s.settings with allowedHosts := h :: s.settings.allowedHosts }
        browserGrantedHosts := h :: s.browserGrantedHosts }
  | listedOnly (s : BrowserState) :
      Step s (popupListedOnly s)
  | grantAllSites (s : BrowserState) :
      Step s (popupGrantAllSites s)
  | shareTab (s : BrowserState) (tid : TabId) :
      Step s { s with agentTabs := tid :: s.agentTabs }
  | unshareTab (s : BrowserState) (tid : TabId) :
      Step s (popupUnshareTab_repaired s tid)
  | agentCreateTab (s : BrowserState) (conn : ConnId) (t : Tab) :
      Step s { s with
        agentTabs := t.tabId :: s.agentTabs
        createdBy := (t.tabId, conn) :: s.createdBy
        tabs := t :: s.tabs }
  | userOpenPrivateTab (s : BrowserState) (t : Tab) :
      Step s { s with tabs := t :: s.tabs }
  | closeTab (s : BrowserState) (tid : TabId) :
      Step s { s with
        agentTabs := s.agentTabs.filter (· != tid)
        createdBy := s.createdBy.filter (fun p => p.1 != tid)
        tabs := s.tabs.filter (fun t => t.tabId != tid) }
  | allowConn (s : BrowserState) (conn : ConnId) :
      Step s { s with allowedConns := conn :: s.allowedConns }
  | denyConn (s : BrowserState) (conn : ConnId) :
      Step s { s with deniedConns := conn :: s.deniedConns }
  | approveAgent (s : BrowserState) (name : String) :
      Step s { s with settings := { s.settings with approvedAgents := name :: s.settings.approvedAgents } }
  | forgetConn (s : BrowserState) (conn : ConnId) :
      Step s { s with
        allowedConns := s.allowedConns.filter (· != conn)
        deniedConns := s.deniedConns.filter (· != conn) }
  | webRequest (s : BrowserState) (tid : TabId) (u : Url) :
      Step s (onBeforeRequest_current s tid u)
  | toggleProfileStop (s : BrowserState) (stopped : List ProfileId) :
      Step s { s with stoppedProfiles := stopped }

inductive Reachable : BrowserState → Prop where
  | init (build : BuildMode) : Reachable (initialState build)
  | step {s s' : BrowserState} : Reachable s → Step s s' → Reachable s'

theorem reachable_inv {s : BrowserState} (hr : Reachable s) : StateInv s := by
  induction hr with
  | init build =>
    exact ⟨fun p hp => by cases hp⟩
  | step _ hstep ih =>
    cases hstep with
    | setPaused _ | setBlocked _ | allowSite _ | listedOnly | grantAllSites
    | userOpenPrivateTab _ | allowConn _ | denyConn _ | approveAgent _
    | forgetConn _ | toggleProfileStop _ =>
      exact ⟨ih.createdBy_in_agentTabs⟩
    | webRequest tid u =>
      unfold onBeforeRequest_current
      split
      · exact ih
      · split
        · exact ih
        · exact ⟨ih.createdBy_in_agentTabs⟩
    | shareTab tid =>
      refine ⟨fun p hp => ?_⟩
      have h := ih.createdBy_in_agentTabs p hp
      rw [List.contains_iff_mem] at h ⊢
      exact List.mem_cons_of_mem tid h
    | unshareTab tid =>
      refine ⟨fun p hp => ?_⟩
      simp only [popupUnshareTab_repaired, List.mem_filter] at hp ⊢
      have h := ih.createdBy_in_agentTabs p hp.1
      rw [List.contains_iff_mem] at h ⊢
      rw [List.mem_filter]
      exact ⟨h, hp.2⟩
    | agentCreateTab conn t =>
      refine ⟨fun p hp => ?_⟩
      rcases List.mem_cons.mp hp with rfl | hp'
      · simp
      · have h := ih.createdBy_in_agentTabs p hp'
        rw [List.contains_iff_mem] at h ⊢
        exact List.mem_cons_of_mem t.tabId h
    | closeTab tid =>
      refine ⟨fun p hp => ?_⟩
      simp only [List.mem_filter] at hp ⊢
      have h := ih.createdBy_in_agentTabs p hp.1
      rw [List.contains_iff_mem] at h ⊢
      rw [List.mem_filter]
      exact ⟨h, hp.2⟩

/-! ## Helper Lemmas for the Master Proof -/

private theorem findFrame_mem {frames : List Frame} {fid : Nat} {f : Frame}
    (h : findFrame frames fid = some f) : f ∈ frames := by
  unfold findFrame at h
  exact List.mem_of_find?_eq_some h

private theorem resolveAllowedFrame_ok {s : BrowserState} {t : Tab} {fid : Nat} {owner : Url}
    (hok : resolveAllowedFrame s t fid = .ok owner) :
    hostBlocked s.settings.blockedHosts owner = false ∧
    siteAllowedIn s owner = true ∧
    (∀ f, findFrame t.frames fid = some f → owner = ownerUrl t.frames f ∧ framePermitted s t f = true) := by
  unfold resolveAllowedFrame at hok
  dsimp only at hok
  generalize hresDef :
      (match findFrame t.frames fid with | some f => ownerUrl t.frames f | none => t.url) = resolved at hok
  split at hok
  · cases hok
  · rename_i hblk
    have hblk' : hostBlocked s.settings.blockedHosts resolved = false := by
      cases hb : hostBlocked s.settings.blockedHosts resolved with
      | false => rfl
      | true => exact absurd hb hblk
    split at hok
    · cases hok
    · rename_i hsite
      have hsite' : siteAllowedIn s resolved = true := by
        cases hs : siteAllowedIn s resolved with
        | true => rfl
        | false => simp [hs] at hsite
      cases hok
      refine ⟨hblk', hsite', ?_⟩
      intro f hfind
      have heq : owner = ownerUrl t.frames f := by rw [← hresDef, hfind]
      refine ⟨heq, ?_⟩
      unfold framePermitted
      split
      · rfl
      · rw [← heq]; simp [hblk', hsite']

private theorem findFrame_id {frames : List Frame} {fid : Nat} {f : Frame}
    (h : findFrame frames fid = some f) : (f.frameId == fid) = true := by
  unfold findFrame at h
  exact List.find?_some (p := fun (g : Frame) => g.frameId == fid) h

/-- When the check passes for a script aimed at frame `fid`, that frame (if agents may be in it)
answered the check and holds no filled secret. -/
private theorem assertNoFilledSecret_repaired_no_leak {s : BrowserState} {t : Tab} {fid : Nat} {f : Frame}
    (hok : assertNoFilledSecret_repaired s t fid = .ok ())
    (hmem : f ∈ t.frames)
    (hperm : framePermitted s t f = true)
    (hid : (f.frameId == fid) = true) :
    frameFilledSecrets f = [] := by
  unfold assertNoFilledSecret_repaired at hok
  split at hok
  · cases hok
  · split at hok
    · cases hok
    · rename_i hnoSecret
      split at hok
      · cases hok
      · split at hok
        · cases hok
        · rename_i hnoUnchecked
          have h1 := Bool.eq_false_iff.mpr hnoSecret
          have h2 := Bool.eq_false_iff.mpr hnoUnchecked
          rw [List.any_eq_false] at h1 h2
          have hf1 := h1 f hmem
          have hf2 := h2 f hmem
          have hmust : mustAnswer fid f = true := by simp [mustAnswer, hid]
          have hnoSec : frameHasFilledSecret f = false := by
            cases hout : f.scriptOutcome with
            | ok => simpa [hperm, hout] using hf1
            | timeoutOrError => exact absurd (by simp [hperm, hout, hmust]) hf2
          unfold frameHasFilledSecret at hnoSec
          rw [List.any_eq_false] at hnoSec
          unfold frameFilledSecrets
          have hfilt : f.fields.filter (fun fld => fld.isSecret && !fld.value.isEmpty) = [] := by
            rw [List.filter_eq_nil_iff]
            intro fld hfld
            exact hnoSec fld hfld
          simp [hfilt]

private theorem execSingleTool_repaired_sound
    {s : BrowserState} {conn : ConnId} {acc : AgentAccess} {req : ToolRequest} {eff : Effect}
    (hok : execSingleTool_repaired s conn acc req = .ok eff) :
    (∀ tid ∈ eff.tabsAccessed, s.agentTabs.contains tid = true) ∧
    eff.respectsSitePolicy s = true ∧
    eff.leaksSecret = false := by
  cases req with
  | status ask tabOpt =>
    cases tabOpt with
    | none =>
      simp only [execSingleTool_repaired, Except.ok.injEq] at hok
      subst hok
      exact ⟨(fun _ h => by simp [Effect.tabsAccessed] at h), by simp [Effect.respectsSitePolicy, Effect.urlsAccessed], by simp [Effect.leaksSecret]⟩
    | some tid =>
      simp only [execSingleTool_repaired] at hok
      split at hok
      · cases hok
      · split at hok
        · cases hok
        · cases hacc : accessibleTab s tid with
          | error e => simp [hacc] at hok
          | ok t =>
            simp only [hacc, Except.ok.injEq] at hok
            subst hok
            have ⟨hmem, _, hblk, hsite⟩ := accessibleTab_ok_inv hacc
            refine ⟨?_, ?_, by simp [Effect.leaksSecret]⟩
            · intro id hid
              simp only [Effect.tabsAccessed, List.mem_singleton] at hid
              subst hid; exact hmem
            · simp [Effect.respectsSitePolicy, Effect.urlsAccessed, hblk, hsite]
  | tabs_list =>
    simp only [execSingleTool_repaired, Except.ok.injEq] at hok
    subst hok
    refine ⟨?_, by simp [Effect.respectsSitePolicy, Effect.urlsAccessed], by simp [Effect.leaksSecret]⟩
    intro tid htid
    simp only [Effect.tabsAccessed, List.mem_map] at htid
    rcases htid with ⟨⟨id0, ln⟩, ⟨t, ht, heq⟩, rfl⟩
    rw [List.mem_filter] at ht
    have hEq : t.tabId = id0 := congrArg Prod.fst heq
    rw [← hEq]; exact ht.2
  | tabs_create u =>
    simp only [execSingleTool_repaired] at hok
    split at hok
    · rename_i hchk
      cases hok
      simp only [checkUrlOk, Bool.and_eq_true, Bool.not_eq_true'] at hchk
      refine ⟨(fun _ h => by simp [Effect.tabsAccessed] at h), ?_, by simp [Effect.leaksSecret]⟩
      simp [Effect.respectsSitePolicy, Effect.urlsAccessed, siteAllowedIn, hchk.1.2, hchk.2]
    · cases hok
  | tabs_close_one tid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc, Except.ok.injEq] at hok
      subst hok
      refine ⟨?_, by simp [Effect.respectsSitePolicy, Effect.urlsAccessed], by simp [Effect.leaksSecret]⟩
      intro id hid
      simp only [Effect.tabsAccessed, List.mem_singleton] at hid
      subst hid; exact (accessibleTab_ok_inv hacc).1
  | tabs_close_mine =>
    simp only [execSingleTool_repaired, Except.ok.injEq] at hok
    subst hok
    refine ⟨?_, by simp [Effect.respectsSitePolicy, Effect.urlsAccessed], by simp [Effect.leaksSecret]⟩
    intro tid htid
    simp only [Effect.tabsAccessed, List.mem_map] at htid
    rcases htid with ⟨⟨t, c⟩, hp, hfst⟩
    simp only at hfst; subst hfst
    rw [List.mem_filter] at hp
    simp only [Bool.and_eq_true] at hp
    exact hp.2.2
  | read_page tid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      split at hok
      · cases hok
      · cases hok
        have ⟨hmem, _, _, _⟩ := accessibleTab_ok_inv hacc
        refine ⟨?_, ?_, ?_⟩
        · intro id hid
          simp only [Effect.tabsAccessed, List.mem_singleton] at hid
          subst hid; exact hmem
        · simp only [Effect.respectsSitePolicy, Effect.urlsAccessed, List.all_eq_true]
          intro u hu
          rw [List.mem_map] at hu
          rcases hu with ⟨x, hx, rfl⟩
          rw [List.mem_map] at hx
          rcases hx with ⟨f, hf, rfl⟩
          rw [List.mem_filter] at hf
          simp only [Bool.and_eq_true] at hf ⊢
          exact hf.2.1
        · simp only [Effect.leaksSecret, List.any_eq_false]
          intro x _
          rw [Bool.not_eq_true, List.any_eq_false]
          intro out _
          cases out <;> simp
  | click_ref tid fid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      cases hres : resolveAllowedFrame s t fid with
      | error e => simp [hres] at hok
      | ok owner =>
        simp only [hres, Except.ok.injEq] at hok
        subst hok
        have ⟨hblk, hsite, _⟩ := resolveAllowedFrame_ok hres
        refine ⟨?_, ?_, by simp [Effect.leaksSecret]⟩
        · intro id hid
          simp only [Effect.tabsAccessed, List.mem_singleton] at hid
          subst hid; exact (accessibleTab_ok_inv hacc).1
        · simp [Effect.respectsSitePolicy, Effect.urlsAccessed, hblk, hsite]
  | click_xy tid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      split at hok
      · cases hok
      · rename_i owner hres
        cases hok
        have ⟨hblk, hsite, _⟩ := resolveAllowedFrame_ok hres
        refine ⟨?_, ?_, by simp [Effect.leaksSecret]⟩
        · intro id hid
          simp only [Effect.tabsAccessed, List.mem_singleton] at hid
          subst hid; exact (accessibleTab_ok_inv hacc).1
        · simp [Effect.respectsSitePolicy, Effect.urlsAccessed, hblk, hsite]
  | type_text tid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      split at hok
      · cases hok
      · rename_i owner hres
        cases hok
        have ⟨hblk, hsite, _⟩ := resolveAllowedFrame_ok hres
        refine ⟨?_, ?_, by simp [Effect.leaksSecret]⟩
        · intro id hid
          simp only [Effect.tabsAccessed, List.mem_singleton] at hid
          subst hid; exact (accessibleTab_ok_inv hacc).1
        · simp [Effect.respectsSitePolicy, Effect.urlsAccessed, hblk, hsite]
  | javascript tid fid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      cases hres : resolveAllowedFrame s t fid with
      | error e => simp [hres] at hok
      | ok owner =>
        simp only [hres] at hok
        cases hsec : assertNoFilledSecret_repaired s t fid with
        | error e => simp [hsec] at hok
        | ok _ =>
          simp only [hsec, Except.ok.injEq] at hok
          subst hok
          have ⟨hblk, hsite, hpermF⟩ := resolveAllowedFrame_ok hres
          refine ⟨?_, ?_, ?_⟩
          · intro id hid
            simp only [Effect.tabsAccessed, List.mem_singleton] at hid
            subst hid; exact (accessibleTab_ok_inv hacc).1
          · simp [Effect.respectsSitePolicy, Effect.urlsAccessed, hblk, hsite]
          · simp only [Effect.leaksSecret]
            cases hff : findFrame t.frames fid with
            | none => rfl
            | some f =>
              have hmem := findFrame_mem hff
              have hpf := (hpermF f hff).2
              have hnil := assertNoFilledSecret_repaired_no_leak hsec hmem hpf (findFrame_id hff)
              simp [hnil]
  | screenshot tid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      cases hcap : captureUnredactedSecrets_repaired s t with
      | error e => simp [hcap] at hok
      | ok unred =>
        simp only [hcap, Except.ok.injEq] at hok
        subst hok
        have hunredNil : unred = [] := by
          unfold captureUnredactedSecrets_repaired at hcap
          split at hcap
          · cases hcap
          · split at hcap
            · cases hcap
            · split at hcap
              · cases hcap
              · cases hcap; rfl
        subst hunredNil
        refine ⟨?_, by simp [Effect.respectsSitePolicy, Effect.urlsAccessed], by simp [Effect.leaksSecret]⟩
        intro id hid
        simp only [Effect.tabsAccessed, List.mem_singleton] at hid
        subst hid; exact (accessibleTab_ok_inv hacc).1
  | read_console tid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      split at hok
      · cases hok
      · cases hok
        refine ⟨?_, ?_, by simp [Effect.leaksSecret]⟩
        · intro id hid
          simp only [Effect.tabsAccessed, List.mem_singleton] at hid
          subst hid; exact (accessibleTab_ok_inv hacc).1
        · simp only [Effect.respectsSitePolicy, Effect.urlsAccessed, List.all_eq_true]
          intro u hu
          rw [List.mem_map] at hu
          rcases hu with ⟨entry, hentry, rfl⟩
          rw [List.mem_flatMap] at hentry
          rcases hentry with ⟨f, hf, hm⟩
          rw [List.mem_filter] at hf
          rw [List.mem_map] at hm
          rcases hm with ⟨m, _, rfl⟩
          exact hf.2
  | read_network tid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc, Except.ok.injEq] at hok
      subst hok
      refine ⟨?_, ?_, by simp [Effect.leaksSecret]⟩
      · intro id hid
        simp only [Effect.tabsAccessed, List.mem_singleton] at hid
        subst hid; exact (accessibleTab_ok_inv hacc).1
      · simp only [Effect.respectsSitePolicy, Effect.urlsAccessed, List.all_eq_true]
        intro u hu
        rw [List.mem_filter] at hu
        exact hu.2
  | navigate_url tid u =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      split at hok
      · rename_i hchk
        cases hok
        simp only [checkUrlOk, Bool.and_eq_true, Bool.not_eq_true'] at hchk
        refine ⟨(fun _ h => by simp [Effect.tabsAccessed] at h), ?_, by simp [Effect.leaksSecret]⟩
        simp [Effect.respectsSitePolicy, Effect.urlsAccessed, siteAllowedIn, hchk.1.2, hchk.2]
      · cases hok
  | navigate_back tid destUrl =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      split at hok
      · rename_i hokUrl
        cases hok
        refine ⟨?_, ?_, by simp [Effect.leaksSecret]⟩
        · intro id hid
          simp only [Effect.tabsAccessed, List.mem_singleton] at hid
          subst hid; exact (accessibleTab_ok_inv hacc).1
        · simp [Effect.respectsSitePolicy, Effect.urlsAccessed, hokUrl]
      · cases hok
  | top_page tid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc, Except.ok.injEq] at hok
      subst hok
      have ⟨hmem, _, hblk, hsite⟩ := accessibleTab_ok_inv hacc
      refine ⟨?_, ?_, by simp [Effect.leaksSecret]⟩
      · intro id hid
        simp only [Effect.tabsAccessed, List.mem_singleton] at hid
        subst hid; exact hmem
      · simp [Effect.respectsSitePolicy, Effect.urlsAccessed, hblk, hsite]
  | handle_dialog tid =>
    simp only [execSingleTool_repaired] at hok
    cases hacc : accessibleTab s tid with
    | error e => simp [hacc] at hok
    | ok t =>
      simp only [hacc] at hok
      split at hok
      · cases hok
      · split at hok
        · rename_i hall
          cases hok
          refine ⟨?_, ?_, by simp [Effect.leaksSecret]⟩
          · intro id hid
            simp only [Effect.tabsAccessed, List.mem_singleton] at hid
            subst hid; exact (accessibleTab_ok_inv hacc).1
          · simp only [Effect.respectsSitePolicy, Effect.urlsAccessed]
            exact hall
        · cases hok
  | wait =>
    simp only [execSingleTool_repaired, Except.ok.injEq] at hok
    subst hok
    exact ⟨(fun _ h => by simp [Effect.tabsAccessed] at h), by simp [Effect.respectsSitePolicy, Effect.urlsAccessed], by simp [Effect.leaksSecret]⟩
  | batch _ => simp [execSingleTool_repaired] at hok
  | browser_open _ => simp [execSingleTool_repaired] at hok

private theorem agentAccess_allowed_implies_agentAllowedNow {s : BrowserState} {conn : ConnId}
    {client : String} {ask : Bool}
    (h : agentAccess_current s conn client ask = .allowed) :
    agentAllowedNow s conn client = true := by
  unfold agentAccess_current at h
  unfold agentAllowedNow
  split at h
  · rename_i hna; simp [hna]
  · split at h
    · rename_i hall
      rw [Bool.or_assoc, hall, Bool.or_true]
    · split at h
      · cases h
      · split at h <;> cases h

private theorem runToolSingle_repaired_sound
    {s : BrowserState} {conn : ConnId} {acc : AgentAccess} {req : ToolRequest} {eff : Effect}
    (hok : runToolSingle_repaired s conn acc req = .ok eff) :
    (eff.touchesTabOrPage = true → s.settings.paused = false) ∧
    (∀ tid ∈ eff.tabsAccessed, s.agentTabs.contains tid = true) ∧
    eff.respectsSitePolicy s = true ∧
    eff.leaksSecret = false := by
  cases req with
  | status ask tabOpt =>
    simp only [runToolSingle_repaired] at hok
    have hcore := execSingleTool_repaired_sound hok
    refine ⟨?_, hcore⟩
    cases tabOpt with
    | none =>
      simp only [execSingleTool_repaired, Except.ok.injEq] at hok
      subst hok
      intro ht; simp [Effect.touchesTabOrPage] at ht
    | some tid =>
      simp only [execSingleTool_repaired] at hok
      split at hok
      · cases hok
      · rename_i hnp
        intro _
        cases hp : s.settings.paused with
        | false => rfl
        | true => exact absurd hp hnp
  | _ =>
    simp only [runToolSingle_repaired] at hok
    split at hok
    · cases hok
    · rename_i hnp
      have hnp' : s.settings.paused = false := by
        cases hp : s.settings.paused with
        | false => rfl
        | true => exact absurd hp hnp
      exact ⟨fun _ => hnp', execSingleTool_repaired_sound hok⟩

private theorem runBatchList_repaired_sound
    {s : BrowserState} {conn : ConnId} {actions : List ToolRequest} {effs : List Effect}
    (hok : runBatchList_repaired s conn actions = .ok effs) :
    ∀ eff ∈ effs,
      (∀ tid ∈ eff.tabsAccessed, s.agentTabs.contains tid = true) ∧
      eff.respectsSitePolicy s = true ∧
      eff.leaksSecret = false := by
  induction actions generalizing effs with
  | nil =>
    simp only [runBatchList_repaired, Except.ok.injEq] at hok
    subst hok
    intro eff heff; cases heff
  | cons a rest ih =>
    simp only [runBatchList_repaired] at hok
    split at hok
    · cases hok
    · cases h1 : runToolSingle_repaired s conn .allowed a with
      | error e => simp [h1] at hok
      | ok e1 =>
        simp only [h1] at hok
        cases h2 : runBatchList_repaired s conn rest with
        | error e => simp [h2] at hok
        | ok es =>
          simp only [h2, Except.ok.injEq] at hok
          subst hok
          intro eff heff
          rcases List.mem_cons.mp heff with rfl | hmem
          · exact (runToolSingle_repaired_sound h1).2
          · exact ih h2 eff hmem

/-! ## Master Unified Promise Theorem -/

/-- Everything `handleGated_repaired` lets through: the profile is not stopped, pause and agent
approval hold for anything touching a tab or page, and tabs, sites and secret fields are respected. -/
private theorem handleGated_repaired_sound
    {s : BrowserState}
    {conn : ConnId} {client : String} {req : ToolRequest} {eff : Effect}
    (hok : handleGated_repaired s conn client req = .ok eff) :
    agentsStopped_repaired s = false ∧
    (eff.touchesTabOrPage = true →
      s.settings.paused = false ∧ agentAllowedNow s conn client = true) ∧
    (∀ tid ∈ eff.tabsAccessed, s.agentTabs.contains tid = true) ∧
    eff.respectsSitePolicy s = true ∧
    eff.leaksSecret = false := by
  unfold handleGated_repaired at hok
  split at hok
  · cases hok
  · rename_i hnotStopped
    have hns' : agentsStopped_repaired s = false := by
      cases hs : agentsStopped_repaired s with
      | false => rfl
      | true => exact absurd hs hnotStopped
    refine ⟨hns', ?_⟩
    cases req with
    | status ask tabOpt =>
      dsimp only at hok
      have hcore := runToolSingle_repaired_sound hok
      refine ⟨?_, hcore.2⟩
      intro htouch
      refine ⟨hcore.1 htouch, ?_⟩
      cases tabOpt with
      | none =>
        simp only [runToolSingle_repaired, execSingleTool_repaired, Except.ok.injEq] at hok
        subst hok
        simp [Effect.touchesTabOrPage] at htouch
      | some tid =>
        simp only [runToolSingle_repaired, execSingleTool_repaired] at hok
        split at hok
        · cases hok
        · split at hok
          · cases hok
          · rename_i hall
            have hallEq : agentAccess_current s conn client ask = .allowed := by
              cases ha : agentAccess_current s conn client ask with
              | allowed => rfl
              | waiting => simp [ha] at hall
              | refused => simp [ha] at hall
              | unasked => simp [ha] at hall
            exact agentAccess_allowed_implies_agentAllowedNow hallEq
    | batch actions =>
      dsimp only at hok
      split at hok
      · cases hok
      · rename_i hall
        have hall' : agentAllowedNow s conn client = true := by
          cases ha : agentAllowedNow s conn client with
          | true => rfl
          | false => simp [ha] at hall
        split at hok
        · cases hok
        · rename_i hnp
          have hnp' : s.settings.paused = false := by
            cases hp : s.settings.paused with
            | false => rfl
            | true => exact absurd hp hnp
          split at hok
          · cases hok
          · cases hbl : runBatchList_repaired s conn actions with
            | error e => simp [hbl] at hok
            | ok effs =>
              simp only [hbl, Except.ok.injEq] at hok
              subst hok
              have hallEffs := runBatchList_repaired_sound hbl
              refine ⟨fun _ => ⟨hnp', hall'⟩, ?_, ?_, ?_⟩
              · intro tid htid
                simp only [Effect.tabsAccessed, List.mem_flatMap] at htid
                rcases htid with ⟨e, he, htid'⟩
                exact (hallEffs e he).1 tid htid'
              · simp only [Effect.respectsSitePolicy, Effect.urlsAccessed, List.all_eq_true]
                intro u hu
                rw [List.mem_flatMap] at hu
                rcases hu with ⟨e, he, hu'⟩
                have hsp := (hallEffs e he).2.1
                simp only [Effect.respectsSitePolicy, List.all_eq_true] at hsp
                exact hsp u hu'
              · simp only [Effect.leaksSecret, List.any_eq_false]
                intro e he
                simp [(hallEffs e he).2.2]
    | _ =>
      dsimp only at hok
      split at hok
      · cases hok
      · rename_i hall
        have hall' : agentAllowedNow s conn client = true := by
          cases ha : agentAllowedNow s conn client with
          | true => rfl
          | false => simp [ha] at hall
        have hcore := runToolSingle_repaired_sound hok
        exact ⟨fun ht => ⟨hcore.1 ht, hall'⟩, hcore.2⟩

/-- **The One Promise of `foxreach` (machine-checked over all reachable states of the model):**
for every reachable browser state `s`, every connection `conn` / agent `client`, and every
modelled tool call `req` (including `batch`) that succeeds with observable effect `eff`:
1. `browser_open p` succeeds only for a profile `p` the user has not stopped agents in.
2. Any other call succeeds only while the native host does not see the profile as stopped.
3. Any effect touching a tab or page requires `paused = false` and an allowed agent.
4. Every tab accessed or closed belongs to `s.agentTabs` (unshared user tabs stay untouched).
5. Every URL and frame read, acted on or navigated to obeys `blockedHosts` and `siteAllowed`,
   and so does every page an open dialog's text can have come from.
6. No filled secret field is exposed by the modelled DOM read, script or screenshot effects.

Not promised, because the code cannot enforce it: pause and agent approval do not apply to
`browser_open` (see `browser_open_not_gated_by_pause_or_approval`). -/
theorem foxreach_promise_proved
    {s : BrowserState} (_hreach : Reachable s)
    {conn : ConnId} {client : String} {req : ToolRequest} {eff : Effect}
    (hok : handleRequest_repaired s conn client req = .ok eff) :
    (∀ p, req = .browser_open p → s.stoppedProfiles.contains p = false) ∧
    ((∀ p, req ≠ .browser_open p) → agentsStopped_repaired s = false) ∧
    (eff.touchesTabOrPage = true →
      s.settings.paused = false ∧ agentAllowedNow s conn client = true) ∧
    (∀ tid ∈ eff.tabsAccessed, s.agentTabs.contains tid = true) ∧
    eff.respectsSitePolicy s = true ∧
    eff.leaksSecret = false := by
  cases req with
  | browser_open p =>
    simp only [handleRequest_repaired] at hok
    split at hok
    · cases hok
    · rename_i hns
      cases hok
      refine ⟨?_, ?_, ?_, ?_, ?_, ?_⟩
      · intro q hq
        cases hq
        exact Bool.eq_false_iff.mpr hns
      · intro h
        exact absurd rfl (h p)
      · intro ht
        simp [Effect.touchesTabOrPage] at ht
      · intro tid htid
        simp [Effect.tabsAccessed] at htid
      · simp [Effect.respectsSitePolicy, Effect.urlsAccessed]
      · simp [Effect.leaksSecret]
  | _ =>
    simp only [handleRequest_repaired] at hok
    have h := handleGated_repaired_sound hok
    exact ⟨fun p hp => (by cases hp), fun _ => h.1, h.2⟩

/-! ## Regression Verification: the counterexamples against `handleRequest_repaired` -/

theorem repaired_blocks_ce1 :
    ∀ eff, handleRequest_repaired ce1State 99 "rogue-agent" (.status false (some 1)) ≠ .ok eff := by
  intro eff h; cases h

theorem repaired_blocks_ce2a :
    ∀ eff, handleRequest_repaired ce2State 1 "claude" (.click_xy 1) ≠ .ok eff := by
  intro eff h; cases h

theorem repaired_blocks_ce2b :
    ∀ eff, handleRequest_repaired ce2State 1 "claude" (.type_text 1) ≠ .ok eff := by
  intro eff h; cases h

theorem repaired_sanitizes_ce3 :
    handleRequest_repaired ce2State 1 "claude" (.read_console 1) = .ok (.readConsole 1 []) := by
  rfl

theorem repaired_blocks_ce4 :
    ∀ eff, handleRequest_repaired ce4State 1 "claude" (.javascript 1 0) ≠ .ok eff := by
  intro eff h; cases h

theorem repaired_blocks_ce5a :
    ∀ eff, handleRequest_repaired ce5aState 1 "claude" (.screenshot 1) ≠ .ok eff := by
  intro eff h; cases h

theorem repaired_blocks_ce5b :
    ∀ eff, handleRequest_repaired ce4State 1 "claude" (.screenshot 1) ≠ .ok eff := by
  intro eff h; cases h

theorem repaired_blocks_ce7 :
    ∀ eff, handleRequest_repaired ce7State 1 "claude" .tabs_list ≠ .ok eff := by
  intro eff h; cases h

theorem repaired_sanitizes_ce8 :
    let s1 := onBeforeRequest_current ce8Before 1 { scheme := .https, host := ["private", "mail"] }
    handleRequest_repaired s1 1 "claude" (.read_network 1) = .ok (.readNetwork 1 []) := by
  rfl

theorem repaired_sanitizes_ce9 :
    let s1 := popupUnshareTab_repaired ce9BeforeUnshare 1
    handleRequest_repaired s1 7 "claude" .tabs_close_mine = .ok (.closeTabs []) := by
  rfl

theorem repaired_blocks_ce11 :
    ∀ eff, handleRequest_repaired ce11State 1 "claude" (.handle_dialog 1) ≠ .ok eff := by
  intro eff h; cases h

/-- CE-10, the part the code closes: `browser_open` refuses a profile the user stopped agents in. -/
theorem repaired_browser_open_refuses_stopped_profile :
    ce7State.stoppedProfiles.contains 0 = true ∧
    ∀ eff, handleRequest_repaired ce7State 1 "claude" (.browser_open 0) ≠ .ok eff := by
  refine ⟨rfl, ?_⟩
  intro eff h; cases h

/-- CE-10, the part that stays: `browser_open` is answered before any browser runs, so a paused
add-on and an agent the user refused do not stop it. Stated here so the promise does not claim more
than the code does. -/
theorem browser_open_not_gated_by_pause_or_approval :
    ce1State.settings.paused = true ∧
    agentAllowedNow ce1State 99 "rogue-agent" = false ∧
    handleRequest_repaired ce1State 99 "rogue-agent" (.browser_open 0) = .ok (.hostLaunchBrowser 0) := by
  exact ⟨rfl, rfl, rfl⟩

/-- `browser_open` cannot pass the stop mark by riding inside `batch`: the add-on, which runs a
batch's steps, has no such tool. -/
theorem browser_open_refused_inside_batch (s : BrowserState) (conn : ConnId) (client : String)
    (p : ProfileId) (rest : List ToolRequest) :
    ∀ eff, handleRequest_repaired s conn client (.batch (.browser_open p :: rest)) ≠ .ok eff := by
  intro eff h
  simp only [handleRequest_repaired, handleGated_repaired] at h
  split at h
  · cases h
  · split at h
    · cases h
    · split at h
      · cases h
      · split at h
        · cases h
        · rename_i hnp _hne
          have hp : s.settings.paused = false := Bool.eq_false_iff.mpr hnp
          simp [runBatchList_repaired, runToolSingle_repaired, execSingleTool_repaired, hp] at h

/-- On every reachable state (where `popupUnshareTab_repaired` and `closeTab` keep `createdBy ⊆ agentTabs`),
every tab in `createdBy` is genuinely in `agentTabs`, so `tabs_close(mine)` never hits a stale entry. -/
theorem reachable_createdBy_always_agentTab {s : BrowserState} (hreach : Reachable s)
    {tid : TabId} {conn : ConnId} (hmem : (tid, conn) ∈ s.createdBy) :
    s.agentTabs.contains tid = true :=
  (reachable_inv hreach).createdBy_in_agentTabs (tid, conn) hmem

end Foxreach
