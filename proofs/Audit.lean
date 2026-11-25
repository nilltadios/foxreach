import Foxreach

-- Axiom audit: every theorem below must use ONLY Lean's standard axioms (no `sorryAx`, no custom axioms).
-- Every theorem other files can name is listed here; test/proofs.py fails when one is missing.
#print axioms Foxreach.labelSuffix_self
#print axioms Foxreach.labelSuffix_append
#print axioms Foxreach.labelSuffix_iff_exists_prefix
#print axioms Foxreach.inList_mem
#print axioms Foxreach.inList_subdomain
#print axioms Foxreach.no_suffix_label_collision
#print axioms Foxreach.normalize_trailing_dot_and_case
#print axioms Foxreach.checkUrl_blocked_precedence
#print axioms Foxreach.checkUrl_restricts_scheme
#print axioms Foxreach.ownerUrl_of_web
#print axioms Foxreach.ownerUrl_blank_inherits_web_parent
#print axioms Foxreach.stateOf_never_leaks_secret
#print axioms Foxreach.assertPrivate_guarantees_private_socket_dir
#print axioms Foxreach.windows_pipe_no_request_before_proof
#print axioms Foxreach.readable_default_safety
#print axioms Foxreach.savePath_safety

#print axioms Foxreach.blocked_precedence_accessibleTab
#print axioms Foxreach.accessibleTab_ok_inv
#print axioms Foxreach.blocked_precedence_tabLine
#print axioms Foxreach.unlisted_tabLine_hides_title
#print axioms Foxreach.tabs_list_hides_unshared_tabs
#print axioms Foxreach.tabs_close_mine_only_own_created
#print axioms Foxreach.tab_isolation_except_close_mine
#print axioms Foxreach.non_status_respects_pause
#print axioms Foxreach.non_status_respects_agent_approval
#print axioms Foxreach.read_page_respects_subframe_policy
#print axioms Foxreach.click_ref_respects_subframe_policy
#print axioms Foxreach.javascript_blocks_when_secret_observed
#print axioms Foxreach.webRequest_cancels_blocked

#print axioms Foxreach.ce1_status_tabId_bypasses_approval_and_pause
#print axioms Foxreach.ce2a_coord_click_bypasses_subframe_policy
#print axioms Foxreach.ce2b_type_text_bypasses_subframe_policy
#print axioms Foxreach.ce3_read_console_bypasses_subframe_policy
#print axioms Foxreach.ce4_javascript_secret_guard_fail_open
#print axioms Foxreach.ce5a_secretBoxes_catch_fail_open
#print axioms Foxreach.ce5b_secretBoxes_top_frame_timeout_fail_open
#print axioms Foxreach.ce6_signed_build_listed_and_screenshot_unsat
#print axioms Foxreach.ce7_macos_agentsStopped_fail_open
#print axioms Foxreach.ce8_read_network_leaks_unlisted_visit
#print axioms Foxreach.ce9_unshared_tab_closed_by_tabs_close_mine
#print axioms Foxreach.ce10_browser_open_bypasses_approval_and_pause
#print axioms Foxreach.ce11_handle_dialog_reads_other_sites_dialog
#print axioms Foxreach.current_v041_violates_unified_promise

#print axioms Foxreach.reachable_inv
#print axioms Foxreach.foxreach_promise_proved
#print axioms Foxreach.repaired_blocks_ce1
#print axioms Foxreach.repaired_blocks_ce2a
#print axioms Foxreach.repaired_blocks_ce2b
#print axioms Foxreach.repaired_sanitizes_ce3
#print axioms Foxreach.repaired_blocks_ce4
#print axioms Foxreach.repaired_blocks_ce5a
#print axioms Foxreach.repaired_blocks_ce5b
#print axioms Foxreach.repaired_blocks_ce7
#print axioms Foxreach.repaired_sanitizes_ce8
#print axioms Foxreach.repaired_sanitizes_ce9
#print axioms Foxreach.repaired_blocks_ce11
#print axioms Foxreach.repaired_browser_open_refuses_stopped_profile
#print axioms Foxreach.browser_open_not_gated_by_pause_or_approval
#print axioms Foxreach.browser_open_refused_inside_batch
#print axioms Foxreach.reachable_createdBy_always_agentTab
#print axioms Foxreach.no_request_only_for_the_browser_lists
#print axioms Foxreach.a_step_is_never_a_batch
#print axioms Foxreach.every_tool_keeps_the_promise
