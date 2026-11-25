import Foxreach.CurrentModel

/-!
# Theorems Proved of Current `foxreach` (`v0.4.1`)

Every theorem in this file holds of `CurrentModel` (the faithful transcription of `v0.4.1`)
without modifying a single line of production behavior.
-/

namespace Foxreach

/-! ## 1. Blocked-Host Precedence & Tab-Line Privacy -/

/-- `accessibleTab` always rejects a tab on a blocked host, even if `siteAccess = .all`
and the host is in `allowedHosts`. -/
theorem blocked_precedence_accessibleTab (s : BrowserState) (tid : TabId) (t : Tab)
    (hfind : findTab s tid = some t)
    (hblk : hostBlocked s.settings.blockedHosts t.url = true) :
    ∃ msg, accessibleTab s tid = .error msg := by
  unfold accessibleTab
  split
  · exact ⟨_, rfl⟩
  · simp [hfind, hblk]

/-- `accessibleTab` returning `.ok t` guarantees `tid ∈ agentTabs`, the tab URL is not blocked,
and the tab URL is site-allowed. -/
theorem accessibleTab_ok_inv {s : BrowserState} {tid : TabId} {t : Tab}
    (hok : accessibleTab s tid = .ok t) :
    s.agentTabs.contains tid = true ∧
    findTab s tid = some t ∧
    hostBlocked s.settings.blockedHosts t.url = false ∧
    siteAllowedIn s t.url = true := by
  unfold accessibleTab at hok
  split at hok
  · cases hok
  · rename_i hmem
    have hmem' : s.agentTabs.contains tid = true := by
      simpa [Bool.not_eq_true'] using hmem
    cases hfind : findTab s tid with
    | none => simp [hfind] at hok
    | some tab =>
      simp only [hfind] at hok
      split at hok
      · cases hok
      · rename_i hblk
        have hblk' : hostBlocked s.settings.blockedHosts tab.url = false := by
          cases hb : hostBlocked s.settings.blockedHosts tab.url with
          | false => rfl
          | true => exact absurd hb hblk
        split at hok
        · cases hok
        · rename_i hsite
          have hsite' : siteAllowedIn s tab.url = true := by
            cases hs : siteAllowedIn s tab.url with
            | true => rfl
            | false => simp [hs] at hsite
          cases hok
          exact ⟨hmem', rfl, hblk', hsite'⟩

/-- `tabLine` never reveals a blocked tab's title, URL, or even hostname. -/
theorem blocked_precedence_tabLine (s : BrowserState) (t : Tab)
    (hblk : hostBlocked s.settings.blockedHosts t.url = true) :
    tabLine s t = .blockedPlaceholder := by
  simp [tabLine, hblk]

/-- `tabLine` never reveals an unlisted tab's title or full URL (only its host). -/
theorem unlisted_tabLine_hides_title (s : BrowserState) (t : Tab)
    (hnblk : hostBlocked s.settings.blockedHosts t.url = false)
    (hunlisted : siteListed s.settings.siteAccess s.settings.allowedHosts t.url = false) :
    tabLine s t = .unlistedHostOnly t.url.host := by
  simp [tabLine, hnblk, hunlisted]

/-- `webRequest.onBeforeRequest` never logs a blocked URL into any tab's `networkLog`. -/
theorem webRequest_cancels_blocked (s : BrowserState) (tid : TabId) (u : Url)
    (hblk : hostBlocked s.settings.blockedHosts u = true) :
    onBeforeRequest_current s tid u = s := by
  simp [onBeforeRequest_current, hblk]

/-! ## 2. Tab Isolation (for all tools except the `tabs_close(mine)` unshare bug) -/

/-- `tabs_list` never includes any unshared user tab's ID, title, or URL. -/
theorem tabs_list_hides_unshared_tabs {s : BrowserState} {conn : ConnId} {acc : AgentAccess}
    {entries : List (TabId × TabLineOutput)} {hidden : Nat}
    (hok : execSingleTool_current s conn acc .tabs_list = .ok (.listTabs entries hidden))
    {tid : TabId} {line : TabLineOutput} (hmem : (tid, line) ∈ entries) :
    s.agentTabs.contains tid = true := by
  simp only [execSingleTool_current, Except.ok.injEq, Effect.listTabs.injEq] at hok
  rcases hok with ⟨rfl, _⟩
  rw [List.mem_map] at hmem
  rcases hmem with ⟨t, ht, heq⟩
  rw [List.mem_filter] at ht
  have htid : t.tabId = tid := congrArg Prod.fst heq
  rw [← htid]
  exact ht.2

/-- `tabs_close(mine := true)` ONLY closes tabs recorded in `createdBy` for `conn`
(never user-shared tabs that were never created by an agent, and never another agent's tabs). -/
theorem tabs_close_mine_only_own_created {s : BrowserState} {conn : ConnId} {acc : AgentAccess}
    {ids : List TabId}
    (hok : execSingleTool_current s conn acc .tabs_close_mine = .ok (.closeTabs ids))
    {tid : TabId} (hmem : tid ∈ ids) :
    (tid, conn) ∈ s.createdBy := by
  simp only [execSingleTool_current, Except.ok.injEq, Effect.closeTabs.injEq] at hok
  subst hok
  rw [List.mem_map] at hmem
  rcases hmem with ⟨⟨t, c⟩, hpair, hfst⟩
  simp only at hfst
  subst hfst
  rw [List.mem_filter] at hpair
  have hc : c = conn := by simpa using hpair.2
  subst hc
  exact hpair.1

/-- Every non-batch, non-`tabs_close_mine` tool only accesses tabs in `s.agentTabs`.
(Notice why `tabs_close_mine` is excluded: see `ce9_unshared_tab_closed_by_tabs_close_mine`!) -/
theorem tab_isolation_except_close_mine {s : BrowserState} {conn : ConnId} {acc : AgentAccess}
    {req : ToolRequest} {eff : Effect}
    (hnotMine : req ≠ .tabs_close_mine)
    (hok : execSingleTool_current s conn acc req = .ok eff)
    {tid : TabId} (htid : tid ∈ eff.tabsAccessed) :
    s.agentTabs.contains tid = true := by
  cases req with
  | status ask tabOpt =>
    cases tabOpt with
    | none =>
      simp only [execSingleTool_current, Except.ok.injEq] at hok
      subst hok; simp [Effect.tabsAccessed] at htid
    | some t0 =>
      simp only [execSingleTool_current] at hok
      cases hacc : accessibleTab s t0 with
      | error e => simp [hacc] at hok
      | ok tab =>
        simp only [hacc, Except.ok.injEq] at hok
        subst hok
        simp only [Effect.tabsAccessed, List.mem_singleton] at htid
        subst htid
        exact (accessibleTab_ok_inv hacc).1
  | tabs_list =>
    simp only [execSingleTool_current, Except.ok.injEq] at hok
    subst hok
    simp only [Effect.tabsAccessed, List.mem_map] at htid
    rcases htid with ⟨⟨id0, ln⟩, ⟨t, ht, heq⟩, rfl⟩
    rw [List.mem_filter] at ht
    have hEq : t.tabId = id0 := congrArg Prod.fst heq
    rw [← hEq]
    exact ht.2
  | tabs_create u =>
    simp only [execSingleTool_current] at hok
    split at hok
    · cases hok; simp [Effect.tabsAccessed] at htid
    · cases hok
  | tabs_close_one t0 =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc, Except.ok.injEq] at hok
      subst hok
      simp only [Effect.tabsAccessed, List.mem_singleton] at htid
      subst htid
      exact (accessibleTab_ok_inv hacc).1
  | tabs_close_mine => exact absurd rfl hnotMine
  | read_page t0 =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc] at hok
      split at hok
      · cases hok
      · cases hok
        simp only [Effect.tabsAccessed, List.mem_singleton] at htid
        subst htid
        exact (accessibleTab_ok_inv hacc).1
  | click_ref t0 fid =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc] at hok
      cases hfr : assertFrameAllowed s tab fid with
      | error e => simp [hfr] at hok
      | ok _ =>
        simp only [hfr, Except.ok.injEq] at hok
        subst hok
        simp only [Effect.tabsAccessed, List.mem_singleton] at htid
        subst htid
        exact (accessibleTab_ok_inv hacc).1
  | click_xy t0 =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc] at hok
      split at hok <;> (
        cases hok
        simp only [Effect.tabsAccessed, List.mem_singleton] at htid
        subst htid
        exact (accessibleTab_ok_inv hacc).1)
  | type_text t0 =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc] at hok
      split at hok <;> (
        cases hok
        simp only [Effect.tabsAccessed, List.mem_singleton] at htid
        subst htid
        exact (accessibleTab_ok_inv hacc).1)
  | javascript t0 fid =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc] at hok
      cases hfr : assertFrameAllowed s tab fid with
      | error e => simp [hfr] at hok
      | ok _ =>
        simp only [hfr] at hok
        cases hsec : assertNoFilledSecret_current s tab with
        | error e => simp [hsec] at hok
        | ok _ =>
          simp only [hsec, Except.ok.injEq] at hok
          subst hok
          simp only [Effect.tabsAccessed, List.mem_singleton] at htid
          subst htid
          exact (accessibleTab_ok_inv hacc).1
  | screenshot t0 =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc] at hok
      cases hcap : captureUnredactedSecrets_current s tab with
      | error e => simp [hcap] at hok
      | ok unred =>
        simp only [hcap, Except.ok.injEq] at hok
        subst hok
        simp only [Effect.tabsAccessed, List.mem_singleton] at htid
        subst htid
        exact (accessibleTab_ok_inv hacc).1
  | read_console t0 =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc] at hok
      split at hok
      · cases hok
      · cases hok
        simp only [Effect.tabsAccessed, List.mem_singleton] at htid
        subst htid
        exact (accessibleTab_ok_inv hacc).1
  | read_network t0 =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc, Except.ok.injEq] at hok
      subst hok
      simp only [Effect.tabsAccessed, List.mem_singleton] at htid
      subst htid
      exact (accessibleTab_ok_inv hacc).1
  | navigate_url t0 u =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc] at hok
      split at hok
      · cases hok; simp [Effect.tabsAccessed] at htid
      · cases hok
  | navigate_back t0 u =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc, Except.ok.injEq] at hok
      subst hok
      simp only [Effect.tabsAccessed, List.mem_singleton] at htid
      subst htid
      exact (accessibleTab_ok_inv hacc).1
  | top_page t0 =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc, Except.ok.injEq] at hok
      subst hok
      simp only [Effect.tabsAccessed, List.mem_singleton] at htid
      subst htid
      exact (accessibleTab_ok_inv hacc).1
  | handle_dialog t0 =>
    simp only [execSingleTool_current] at hok
    cases hacc : accessibleTab s t0 with
    | error e => simp [hacc] at hok
    | ok tab =>
      simp only [hacc] at hok
      split at hok
      · cases hok
      · cases hok
        simp only [Effect.tabsAccessed, List.mem_singleton] at htid
        subst htid
        exact (accessibleTab_ok_inv hacc).1
  | wait =>
    simp only [execSingleTool_current, Except.ok.injEq] at hok
    subst hok
    simp [Effect.tabsAccessed] at htid
  | batch _ => simp [execSingleTool_current] at hok
  | browser_open _ => simp [execSingleTool_current] at hok

/-! ## 3. Pause Gate & Agent Approval Gate (for non-`status`, non-`browser_open` tools) -/

def isStatusOrBrowserOpen : ToolRequest → Bool
  | .status _ _ => true
  | .browser_open _ => true
  | _ => false

/-- For every tool other than `status` and `browser_open`, pausing blocks execution (including `batch`). -/
theorem non_status_respects_pause {s : BrowserState} {conn : ConnId} {client : String}
    {req : ToolRequest}
    (hnot : isStatusOrBrowserOpen req = false)
    (hpaused : s.settings.paused = true) :
    ∃ msg, handleRequest_current s conn client req = .error msg := by
  cases req with
  | status _ _ => simp [isStatusOrBrowserOpen] at hnot
  | browser_open _ => simp [isStatusOrBrowserOpen] at hnot
  | batch actions =>
    simp only [handleRequest_current, hpaused, ↓reduceIte]
    split
    · exact ⟨_, rfl⟩
    · split <;> exact ⟨_, rfl⟩
  | _ =>
    simp only [handleRequest_current, runToolSingle_current, hpaused, ↓reduceIte]
    split
    · exact ⟨_, rfl⟩
    · split <;> exact ⟨_, rfl⟩

/-- For every tool other than `status` and `browser_open`, an unapproved/refused agent is rejected. -/
theorem non_status_respects_agent_approval {s : BrowserState} {conn : ConnId} {client : String}
    {req : ToolRequest}
    (hnot : isStatusOrBrowserOpen req = false)
    (hunapproved : agentAllowedNow s conn client = false) :
    ∃ msg, handleRequest_current s conn client req = .error msg := by
  cases req with
  | status _ _ => simp [isStatusOrBrowserOpen] at hnot
  | browser_open _ => simp [isStatusOrBrowserOpen] at hnot
  | batch actions =>
    simp only [handleRequest_current, hunapproved, Bool.not_false, ↓reduceIte]
    split <;> exact ⟨_, rfl⟩
  | _ =>
    simp only [handleRequest_current, hunapproved, Bool.not_false, ↓reduceIte]
    split <;> exact ⟨_, rfl⟩

/-! ## 4. Ref-Based Frame Isolation & `read_page` Secret-Field Non-Disclosure -/

/-- `read_page` skips every subframe whose `ownerUrl` is blocked or not site-allowed. -/
theorem read_page_respects_subframe_policy {s : BrowserState} {conn : ConnId} {acc : AgentAccess}
    {tid : TabId} {fs : List (Nat × Url × List FieldOutput)}
    (hok : execSingleTool_current s conn acc (.read_page tid) = .ok (.readFrames tid fs))
    {fid : Nat} {owner : Url} {outs : List FieldOutput}
    (hmem : (fid, owner, outs) ∈ fs) (hsub : fid ≠ 0) :
    hostBlocked s.settings.blockedHosts owner = false ∧ siteAllowedIn s owner = true := by
  simp only [execSingleTool_current] at hok
  cases hacc : accessibleTab s tid with
  | error e => simp [hacc] at hok
  | ok t =>
    simp only [hacc] at hok
    split at hok
    · cases hok
    · simp only [Except.ok.injEq, Effect.readFrames.injEq] at hok
      rcases hok with ⟨_, rfl⟩
      rw [List.mem_map] at hmem
      rcases hmem with ⟨f, hf, heq⟩
      rw [List.mem_filter] at hf
      have hfid : f.frameId = fid := congrArg Prod.fst heq
      have hown : ownerUrl t.frames f = owner := congrArg (fun x => x.2.1) heq
      have hperm := hf.2
      simp only [Bool.and_eq_true] at hperm
      have hfp := hperm.1
      have hne : (f.frameId == 0) = false := by
        rw [hfid]
        exact beq_false_of_ne hsub
      simp only [framePermitted, hne, Bool.false_eq_true, ↓reduceIte,
        Bool.and_eq_true, Bool.not_eq_true'] at hfp
      rw [← hown]
      exact hfp

/-- `click_ref` enforces `assertFrameAllowed`: a subframe whose `ownerUrl` is blocked or unlisted
cannot be clicked via ref. -/
theorem click_ref_respects_subframe_policy {s : BrowserState} {conn : ConnId} {acc : AgentAccess}
    {tid : TabId} {fid : Nat} {owner : Url} {tr : Bool} {t : Tab} {f : Frame}
    (hacc : accessibleTab s tid = .ok t)
    (hfind : findFrame t.frames fid = some f)
    (hsub : fid ≠ 0)
    (hok : execSingleTool_current s conn acc (.click_ref tid fid) = .ok (.actOnFrame tid fid owner tr)) :
    hostBlocked s.settings.blockedHosts (ownerUrl t.frames f) = false ∧
    siteAllowedIn s (ownerUrl t.frames f) = true := by
  simp only [execSingleTool_current, hacc] at hok
  have hne : (fid == 0) = false := beq_false_of_ne hsub
  cases hfr : assertFrameAllowed s t fid with
  | error e => simp [hfr] at hok
  | ok _ =>
    simp only [assertFrameAllowed, hne, Bool.false_eq_true, ↓reduceIte, hfind] at hfr
    split at hfr
    · cases hfr
    · rename_i hblk
      have hblk' : hostBlocked s.settings.blockedHosts (ownerUrl t.frames f) = false := by
        cases hb : hostBlocked s.settings.blockedHosts (ownerUrl t.frames f) with
        | false => rfl
        | true => exact absurd hb hblk
      split at hfr
      · cases hfr
      · rename_i hsite
        have hsite' : siteAllowedIn s (ownerUrl t.frames f) = true := by
          cases hs : siteAllowedIn s (ownerUrl t.frames f) with
          | true => rfl
          | false => simp [hs] at hsite
        exact ⟨hblk', hsite'⟩

/-- When a permitted frame responds `.ok` and contains a filled secret field,
`assertNoFilledSecret_current` blocks `javascript`. -/
theorem javascript_blocks_when_secret_observed {s : BrowserState} {t : Tab} {f : Frame}
    (hmem : f ∈ t.frames)
    (hperm : framePermitted s t f = true)
    (hfok : f.scriptOutcome = .ok)
    (hsec : frameHasFilledSecret f = true) :
    ∃ msg, assertNoFilledSecret_current s t = .error msg := by
  unfold assertNoFilledSecret_current
  split
  · exact ⟨_, rfl⟩
  · have hany : (t.frames.any fun x =>
        framePermitted s t x && (x.scriptOutcome == .ok) && frameHasFilledSecret x) = true := by
      rw [List.any_eq_true]
      exact ⟨f, hmem, by simp [hperm, hfok, hsec]⟩
    simp [hany]

end Foxreach
