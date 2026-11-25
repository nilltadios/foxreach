import Foxreach.CurrentModel

/-!
# Machine-Checked Counterexamples Against Current `foxreach` (`v0.4.1`)

Each theorem below constructs a concrete state/request witness and proves (by Lean's kernel,
with zero `sorry`) that the current implementation in `v0.4.1` violates one clause of the
core promise:

> "Let any local MCP agent work inside your real, logged-in Firefox tabs — strictly bounded
> to the tabs, sites, and fields you approve, without exposing the rest of your browser."
-/

namespace Foxreach

private def exampleHost : Host := ["example", "com"]
private def bankHost : Host := ["bank", "example"]
private def unlistedMailHost : Host := ["private", "mail"]

private def exampleUrl : Url := { scheme := .https, host := exampleHost }
private def bankUrl : Url := { scheme := .https, host := bankHost }
private def unlistedMailUrl : Url := { scheme := .https, host := unlistedMailHost }

private def secretPasswordField : FormField :=
  { fieldId := "pw", isSecret := true, value := "hunter2" }

/-! ## CE-1: `status({ tabId })` bypasses BOTH agent approval AND pause
(`extension/background.js:785-795, 1292, 1312`) -/

def ce1State : BrowserState :=
  { build := .full
    settings :=
      { paused := true
        askApproval := true
        siteAccess := .listed
        allowedHosts := [exampleHost]
        blockedHosts := [bankHost] }
    browserGrantsAll := true
    agentTabs := [1]
    deniedConns := [99]  -- Explicitly REFUSED agent!
    tabs := [{ tabId := 1, url := exampleUrl, title := "Example" }] }

/-- A REFUSED agent calling `status(ask := false, tabId := some 1)` while PAUSED
still passes through `handleRequest_current`, probes tab 1, and injects `content.js`. -/
theorem ce1_status_tabId_bypasses_approval_and_pause :
    ce1State.settings.paused = true ∧
    agentAllowedNow ce1State 99 "rogue-agent" = false ∧
    agentAccess_current ce1State 99 "rogue-agent" false = .refused ∧
    ∃ eff,
      handleRequest_current ce1State 99 "rogue-agent" (.status false (some 1)) = .ok eff ∧
      eff.touchesTabOrPage = true := by
  exact ⟨rfl, rfl, rfl, .statusWithTabProbe 1 exampleUrl, rfl, by simp [Effect.touchesTabOrPage]⟩

/-! ## CE-2a & CE-2b: Coordinate & keyboard trusted input bypasses subframe policy in `.full` build
(`extension/background.js:952-1050` + `extension/experiment/parent.js:68-89, 186-224`) -/

def ce2State : BrowserState :=
  { build := .full
    settings :=
      { paused := false
        askApproval := true
        siteAccess := .listed
        allowedHosts := [exampleHost]
        blockedHosts := [bankHost] }
    browserGrantsAll := true
    agentTabs := [1]
    allowedConns := [1]
    tabs :=
      [{ tabId := 1
         url := exampleUrl
         title := "Allowed wrapper"
         frames :=
           [ { frameId := 0, parentFrameId := none, url := exampleUrl }
           , { frameId := 2, parentFrameId := some 0, url := bankUrl
               consoleMsgs := ["session_secret=abc123"] } ]
         pointHitFrameId := 2
         focusedFrameId := 2 }] }

/-- `click_xy` in the full build descends into a BLOCKED & UNLISTED subframe (`bank.example`)
and delivers real trusted input into it. -/
theorem ce2a_coord_click_bypasses_subframe_policy :
    hostBlocked ce2State.settings.blockedHosts bankUrl = true ∧
    ∃ eff,
      handleRequest_current ce2State 1 "claude" (.click_xy 1) = .ok eff ∧
      eff.respectsSitePolicy ce2State = false := by
  exact ⟨rfl, .actOnFrame 1 2 bankUrl true, rfl, by simp [Effect.respectsSitePolicy, Effect.urlsAccessed]; decide⟩

/-- `type_text` in the full build follows focus into a BLOCKED & UNLISTED subframe (`bank.example`)
and sends real trusted keystrokes into it. -/
theorem ce2b_type_text_bypasses_subframe_policy :
    hostBlocked ce2State.settings.blockedHosts bankUrl = true ∧
    ∃ eff,
      handleRequest_current ce2State 1 "claude" (.type_text 1) = .ok eff ∧
      eff.respectsSitePolicy ce2State = false := by
  exact ⟨rfl, .actOnFrame 1 2 bankUrl true, rfl, by simp [Effect.respectsSitePolicy, Effect.urlsAccessed]; decide⟩

/-! ## CE-3: `read_console` reads console messages from blocked/unlisted subframes
(`extension/background.js:1186-1190` + `extension/experiment/parent.js:229-235`) -/

theorem ce3_read_console_bypasses_subframe_policy :
    hostBlocked ce2State.settings.blockedHosts bankUrl = true ∧
    ∃ eff,
      handleRequest_current ce2State 1 "claude" (.read_console 1) = .ok eff ∧
      eff = .readConsole 1 [(2, bankUrl, "session_secret=abc123")] ∧
      eff.respectsSitePolicy ce2State = false := by
  exact ⟨rfl, .readConsole 1 [(2, bankUrl, "session_secret=abc123")], rfl, rfl,
    by simp [Effect.respectsSitePolicy, Effect.urlsAccessed]; decide⟩

/-! ## CE-4: `assertNoFilledSecret` is fail-open on frame timeout/error
(`extension/background.js:367-376, 418-426`) -/

def ce4State : BrowserState :=
  { build := .full
    settings :=
      { paused := false
        askApproval := true
        siteAccess := .listed
        allowedHosts := [exampleHost] }
    browserGrantsAll := true
    agentTabs := [1]
    allowedConns := [1]
    tabs :=
      [{ tabId := 1
         url := exampleUrl
         title := "Login"
         frames :=
           [{ frameId := 0
              parentFrameId := none
              url := exampleUrl
              fields := [secretPasswordField]
              scriptOutcome := .timeoutOrError }] }] }

/-- When `filledSecret` times out or errors in a frame holding a password,
`assertNoFilledSecret_current` misses it and allows `javascript` to execute and read `"hunter2"`. -/
theorem ce4_javascript_secret_guard_fail_open :
    ∃ eff,
      handleRequest_current ce4State 1 "claude" (.javascript 1 0) = .ok eff ∧
      eff = .evalJs 1 0 exampleUrl ["hunter2"] ∧
      eff.leaksSecret = true := by
  exact ⟨.evalJs 1 0 exampleUrl ["hunter2"], rfl, rfl, by simp [Effect.leaksSecret]⟩

/-! ## CE-5a & CE-5b: `secretBoxes` is fail-open on `eachFrame` rejection AND top-frame timeout
(`extension/background.js:708, 714`) -/

def ce5aState : BrowserState :=
  { ce4State with
    tabs :=
      [{ tabId := 1
         url := exampleUrl
         title := "Login"
         eachFrameRejects := true
         frames :=
           [{ frameId := 0
              parentFrameId := none
              url := exampleUrl
              fields := [secretPasswordField]
              scriptOutcome := .ok }] }] }

/-- Line 708 `.catch(() => [])`: if `eachFrame` rejects (e.g. `ensureInjected` fails),
`secretBoxes` returns `[]` and `capture` returns an unredacted screenshot containing the password. -/
theorem ce5a_secretBoxes_catch_fail_open :
    ∃ eff,
      handleRequest_current ce5aState 1 "claude" (.screenshot 1) = .ok eff ∧
      eff.leaksSecret = true := by
  exact ⟨.takeScreenshot 1 [(0, exampleUrl)], rfl, by simp [Effect.leaksSecret]⟩

/-- Line 714 `r.frame.frameId && !r.value`: because `frameId = 0` is falsy in JS,
a timeout/error in the top frame skips `unchecked` and returns an unredacted screenshot. -/
theorem ce5b_secretBoxes_top_frame_timeout_fail_open :
    ∃ eff,
      handleRequest_current ce4State 1 "claude" (.screenshot 1) = .ok eff ∧
      eff.leaksSecret = true := by
  exact ⟨.takeScreenshot 1 [(0, exampleUrl)], rfl, by simp [Effect.leaksSecret]⟩

/-! ## CE-6: Signed build cannot simultaneously enforce `siteAccess = .listed` and take screenshots
(`extension/background.js:750-752, 1579-1582`) -/

/-- In the signed AMO build, selecting "Only these sites" (`popupListedOnly`) revokes `<all_urls>`,
making `screenshot` provably impossible on EVERY tab. -/
theorem ce6_signed_build_listed_and_screenshot_unsat (s : BrowserState)
    (hsigned : s.build = .signed) (conn : ConnId) (acc : AgentAccess) (tid : TabId) :
    let s' := popupListedOnly s
    s'.settings.siteAccess = .listed ∧
    ∀ eff, execSingleTool_current s' conn acc (.screenshot tid) ≠ .ok eff := by
  refine ⟨rfl, ?_⟩
  intro eff hok
  simp only [execSingleTool_current] at hok
  cases hacc : accessibleTab (popupListedOnly s) tid with
  | error e => simp [hacc] at hok
  | ok t =>
    simp only [hacc] at hok
    have hcap : captureUnredactedSecrets_current (popupListedOnly s) t = .error "captureTab requires Every site" := by
      simp [captureUnredactedSecrets_current, popupListedOnly, hsigned]
    rw [hcap] at hok
    cases hok

/-! ## CE-7: `agentsStopped()` fails open on macOS (no `/proc`, empty `candidates`)
(`bridge/host.mjs:33-50, 93-97`) -/

def ce7State : BrowserState :=
  { build := .full
    settings := { paused := false, askApproval := false, siteAccess := .all }
    browserGrantsAll := true
    agentTabs := [1]
    tabs := [{ tabId := 1, url := exampleUrl, title := "Example" }]
    hostPlatform := .macNoProc
    hostProfile := none
    hostCandidates := []
    stoppedProfiles := [0] }  -- User ran `foxreach addon stop` on profile 0!

/-- On macOS (`hostProfile = none`, `hostCandidates = []`), `agentsStopped_current` returns `false`
even when the user's profile is stopped, allowing tools to execute. -/
theorem ce7_macos_agentsStopped_fail_open :
    ce7State.stoppedProfiles.contains 0 = true ∧
    agentsStopped_current ce7State = false ∧
    ∃ eff, handleRequest_current ce7State 1 "claude" (.tabs_list) = .ok eff := by
  exact ⟨rfl, rfl, .listTabs [(1, .fullTitleAndUrl "Example" exampleUrl)] 0, rfl⟩

/-! ## CE-8: `read_network` exposes URLs logged while an agent tab visited an unlisted site
(`extension/background.js:1208-1215, 1631-1645`) -/

def ce8Before : BrowserState :=
  { build := .full
    settings :=
      { paused := false
        askApproval := true
        siteAccess := .listed
        allowedHosts := [exampleHost] }
    browserGrantsAll := true
    agentTabs := [1]
    allowedConns := [1]
    tabs := [{ tabId := 1, url := exampleUrl, title := "Example", networkLog := [] }] }

/-- `webRequest.onBeforeRequest` records requests to unlisted `private.mail` on an agent tab,
and `read_network` returns that unlisted URL unfiltered once the tab is on an allowed site. -/
theorem ce8_read_network_leaks_unlisted_visit :
    let s1 := onBeforeRequest_current ce8Before 1 unlistedMailUrl
    siteAllowedIn s1 unlistedMailUrl = false ∧
    ∃ eff,
      handleRequest_current s1 1 "claude" (.read_network 1) = .ok eff ∧
      eff = .readNetwork 1 [unlistedMailUrl] ∧
      eff.respectsSitePolicy s1 = false := by
  exact ⟨rfl, .readNetwork 1 [unlistedMailUrl], rfl, rfl,
    by simp [Effect.respectsSitePolicy, Effect.urlsAccessed]; decide⟩

/-! ## CE-9: Unsharing an agent-opened tab leaves `createdBy` intact, so `tabs_close(mine)` closes it!
(`extension/background.js:849, 1559`) -/

def ce9BeforeUnshare : BrowserState :=
  { build := .full
    settings := { paused := false, askApproval := true, siteAccess := .listed, allowedHosts := [exampleHost] }
    browserGrantsAll := true
    agentTabs := [1]
    createdBy := [(1, 7)]
    allowedConns := [7]
    tabs := [{ tabId := 1, url := exampleUrl, title := "Now user's private tab" }] }

/-- After the user unshares tab 1 in the popup (`popupUnshareTab_current`), `1 ∉ agentTabs`,
yet connection 7 calling `tabs_close(mine := true)` STILL closes tab 1! -/
theorem ce9_unshared_tab_closed_by_tabs_close_mine :
    let s1 := popupUnshareTab_current ce9BeforeUnshare 1
    s1.agentTabs.contains 1 = false ∧
    ∃ eff,
      handleRequest_current s1 7 "claude" .tabs_close_mine = .ok eff ∧
      eff = .closeTabs [1] ∧
      1 ∈ eff.tabsAccessed := by
  exact ⟨rfl, .closeTabs [1], rfl, rfl, by simp [Effect.tabsAccessed]⟩

/-! ## CE-10: `browser_open` bypasses browser-side approval, pause, and host stop checks
(`bridge/mcp.mjs:507-529`) -/

theorem ce10_browser_open_bypasses_approval_and_pause :
    ce1State.settings.paused = true ∧
    agentAllowedNow ce1State 99 "rogue-agent" = false ∧
    handleRequest_current ce1State 99 "rogue-agent" (.browser_open 0) = .ok (.hostLaunchBrowser 0) := by
  exact ⟨rfl, rfl, rfl⟩

/-! ## CE-11: `handle_dialog` reads and answers a dialog opened by a frame of a site not allowed
(`extension/background.js`, `TOOLS.handle_dialog` and `assertNoDialog`; `dialogState` in
`extension/experiment/parent.js`) -/

def ce11State : BrowserState :=
  { ce2State with
    tabs :=
      [{ tabId := 1
         url := exampleUrl
         title := "Allowed wrapper"
         frames :=
           [ { frameId := 0, parentFrameId := none, url := exampleUrl }
           , { frameId := 2, parentFrameId := some 0, url := unlistedMailUrl } ]
         dialog := some (.page unlistedMailUrl) }] }

/-- A frame of `private.mail`, a site not on the list, opens a dialog over an allowed tab:
`handle_dialog` hands its text to the agent. -/
theorem ce11_handle_dialog_reads_other_sites_dialog :
    siteAllowedIn ce11State unlistedMailUrl = false ∧
    ∃ eff,
      handleRequest_current ce11State 1 "claude" (.handle_dialog 1) = .ok eff ∧
      eff = .readDialog 1 [unlistedMailUrl] ∧
      eff.respectsSitePolicy ce11State = false := by
  exact ⟨rfl, .readDialog 1 [unlistedMailUrl], rfl, rfl,
    by simp [Effect.respectsSitePolicy, Effect.urlsAccessed]; decide⟩

/-! ## Master Disproof: The Full Core Promise Does NOT Hold of Current `v0.4.1` -/

/-- The combined promise (every succeeded request respects pause, agent approval, tab isolation,
site/subframe policy, and secret-field non-disclosure) is FALSE for `handleRequest_current`. -/
theorem current_v041_violates_unified_promise :
    ¬ (∀ (s : BrowserState) (conn : ConnId) (client : String) (req : ToolRequest) (eff : Effect),
        handleRequest_current s conn client req = .ok eff →
        (eff.touchesTabOrPage = true →
          s.settings.paused = false ∧ agentAllowedNow s conn client = true) ∧
        (∀ tid ∈ eff.tabsAccessed, s.agentTabs.contains tid = true) ∧
        eff.respectsSitePolicy s = true ∧
        eff.leaksSecret = false) := by
  intro hall
  rcases ce1_status_tabId_bypasses_approval_and_pause with ⟨hpaused, _, _, eff, hok, htouch⟩
  have h := (hall ce1State 99 "rogue-agent" (.status false (some 1)) eff hok).1 htouch
  rw [hpaused] at h
  cases h.1

end Foxreach
