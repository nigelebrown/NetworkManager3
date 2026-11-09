"""Reconcile docs/Unified_Acceptance_Register_v4.csv for 1.2.0-rc1 and emit the phase test matrix.

Status rules (owner directive 2026-11-09):
  PASS     - software-only control proven by a named automated test in the 1.2.0-rc1 regression run.
  PARTIAL  - behaviour proven only against the protocol emulator (EMULATED) or with mocked device I/O;
             the real-hardware half is still BLOCKED. Never counted as hardware evidence.
  BLOCKED  - needs AP-505 / AP-535 / CX 6100 / CX 6200 hardware, a clean CentOS Stream 9 VM, Docker registry
             access, multi-VM topology or an external party. Not available in the build environment.
  NOT_RUN  - could be run in software but no test exists yet (listed as test debt in the gap register).
Rows already PASS are left untouched.
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "docs" / "Unified_Acceptance_Register_v4.csv"
LOG = "evidence/tests/regression-1.2.0-rc1.log"
E2E = "evidence/e2e-live/"
DATE = "2026-11-09"
HW = "requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available)"
VM = "requires clean CentOS Stream 9 VM(s) with Docker registry access (not available)"

# test_id -> (status, actual result, evidence)
D: dict[str, tuple[str, str, str]] = {
    # P1
    "P1-01": ("BLOCKED", VM, ""), "P1-10": ("BLOCKED", "requires Docker volumes on a disposable host", ""),
    # P2 discovery / onboarding
    "P2-01": ("BLOCKED", HW, ""), "P2-02": ("BLOCKED", HW, ""),
    "P2-03": ("PASS", "Out-of-scope and oversized targets refused before any probe", "test_discovery_refuses_unauthorized_or_oversized_target_before_probe"),
    "P2-04": ("PARTIAL", "Bad credentials -> AUTH_REJECTED, secret never logged (mocked device); hardware BLOCKED", "test_device_rejection_and_bad_login; test_read_only_profile_test_failure_keeps_credentials_secret"),
    "P2-05": ("PARTIAL", "Repeat import upserts identity without duplicates (software); hardware re-discovery BLOCKED", "test_repeat_import_updates_existing_identity_without_duplicates"),
    "P2-06": ("BLOCKED", HW + " (Instant cluster)", ""),
    "P2-07": ("PARTIAL", "Capture-from-device + site assignment proven on EMULATED devices; hardware BLOCKED", "test_vault_capture_schedule_and_replacement_restore; test_move_ap_between_sites"),
    "P2-08": ("PASS", "Write routes refused and audited while locked/uncertified; no device contact", "test_device_write_routes_refused_and_audited; test_uncertified_and_locked_hardware_paths_never_touch_devices"),
    "P2-09": ("NOT_RUN", "No worker-kill test for in-flight /24 discovery yet", ""),
    # P3 monitoring
    "P3-01": ("BLOCKED", HW + " (syslog source)", ""),
    "P3-02": ("PARTIAL", "Collection pipeline proven on real CX code path with emulated socket; hardware BLOCKED", "test_full_pipeline_and_alert_lifecycle; test_collection_plan_success_failure_dedupe_and_experience"),
    "P3-03": ("PARTIAL", "Unreachable/auth failure -> DLQ + alert + site marked (emulated)", "test_live_poll_failure_raises_alert_and_marks_site; test_unreachable_threshold_override_and_maintenance_suppression"),
    "P3-04": ("PASS", "Exactly one logical result on redelivery", "test_ingest_exactly_one_logical_result_on_redelivery; test_dedupe_key_is_per_device_family_window_and_stable"),
    "P3-05": ("PARTIAL", "Missing samples never zero-filled; source drift not yet exercised", "test_freshness_and_experience_coverage_never_zero_fill; test_telemetry_absent_values_are_not_zero"),
    "P3-06": ("BLOCKED", HW + " (an hour of real metrics)", ""),
    "P3-07": ("NOT_RUN", "Retention/aggregation pilot settings not exercised", ""),
    "P3-08": ("NOT_RUN", "No bounded saturation test", ""),
    "P3-09": ("PASS", "80 MHz detected by compliance and refused by policy; 20/40 inheritance", "test_rf_policy_rejects_80mhz_excluded_dfs_and_invalid; test_ap_20_40_widths_inherit_by_ssid_identity_without_secret_fields"),
    # P4 accountability
    "P4-01": ("BLOCKED", HW, ""), "P4-02": ("BLOCKED", HW + " + AAA server", ""), "P4-03": ("BLOCKED", HW, ""),
    "P4-04": ("PASS", "Hash chain detects modification and deletion", "test_audit_hash_chain_detects_modification_and_deletion"),
    "P4-05": ("PASS", "Site-scoped users see only granted sites", "test_site_scoped_user_sees_only_granted_site; test_site_scoped_operator_cannot_read_manual_global_inventory"),
    "P4-06": ("PARTIAL", "Platform executions attributed APPLICATION (emulated device)", "test_switch_certify_apply_readback_and_rollback_paths"),
    "P4-07": ("PASS", "Audit export CSV with hashes; Who-changed-what filter page renders", "test_audit_export_is_csv_with_hashes; " + E2E),
    "P4-08": ("BLOCKED", "requires syslog + AAA infrastructure outage drill", ""),
    "P4-09": ("NOT_RUN", "Emergency privileged-access audit not implemented as a distinct workflow", ""),
    # P5 vault
    "P5-01": ("PARTIAL", "AP and CX full-config capture proven on EMULATED devices; hardware BLOCKED", "test_vault_capture_schedule_and_replacement_restore; test_instant_certify_and_apply_rf_and_ssid"),
    "P5-02": ("PARTIAL", "New version only on change; external modification -> drift (emulated)", "test_vault_compliance_change_gate_and_integration"),
    "P5-03": ("PASS", "Viewer denied and denial audited", "test_viewer_denied_privileged_actions_and_denial_audited; test_rbac"),
    "P5-04": ("PASS", "Tampered envelope rejected", "test_vault_roundtrip_and_tamper_detection; test_tampered_envelope_is_rejected"),
    "P5-05": ("NOT_RUN", "Snapshot retrieval failure path not separately tested", ""),
    "P5-06": ("BLOCKED", "requires isolated second instance", ""),
    "P5-07": ("PASS", "Restore compatibility rejects wrong model/firmware/redacted", "test_restore_compatibility_rejects_wrong_model_firmware_and_redacted"),
    "P5-08": ("PASS", "Diff shown; raw stored encrypted", "test_vault_compliance_change_gate_and_integration; test_cert_plan_is_display_safe_and_encrypted_at_rest"),
    # P6 change control
    "P6-01": ("PASS", "Plan submitted PENDING_APPROVAL; UI flow in browser E2E", "test_change_plan_two_person_concurrency_and_gate; " + E2E + "07_change_submitted.png"),
    "P6-02": ("PASS", "Self-approval refused (new engine and legacy SIMS path)", "test_two_person_rule_approval_expiry_and_gate_denies_while_locked; test_legacy_apply_requires_independent_approval"),
    "P6-03": ("PARTIAL", "APPLIED_VERIFIED on EMULATED CX via browser; hardware BLOCKED", "test_switch_certify_apply_readback_and_rollback_paths; " + E2E + "08d_emulated_apply_verified.png"),
    "P6-04": ("PARTIAL", "Gate requires verified pre-change backup; failure injection on emulator", "test_switch_certify_apply_readback_and_rollback_paths; test_every_guard_must_pass"),
    "P6-05": ("PASS", "80 MHz refused before any adapter call", "test_rf_policy_rejects_80mhz_excluded_dfs_and_invalid; test_enrollment_validation_rejects_all_settings_80mhz_spoof_and_switches"),
    "P6-06": ("NOT_RUN", "Out-of-band modification between approval and apply not exercised", ""),
    "P6-07": ("PARTIAL", "Mid-write failure -> automatic rollback (emulated); worker restart mid-write not run", "test_switch_certify_apply_readback_and_rollback_paths"),
    "P6-08": ("PARTIAL", "Rollback proven by full-config comparison (emulated)", "test_switch_certify_apply_readback_and_rollback_paths; test_modify_existing_ssid_vlan_and_rollback_restores_previous"),
    "P6-09": ("PASS", "Auditor reads but cannot operate", "test_auditor_reads_audit_but_cannot_operate"),
    "P6-10": ("NOT_RUN", "Multi-device/cluster plans not supported (one device per plan by design)", ""),
    # P7 replacement
    "P7-01": ("PARTIAL", "Replacement + restore plan on EMULATED devices", "test_vault_capture_schedule_and_replacement_restore"),
    "P7-02": ("PASS", "Incompatible model/firmware blocked", "test_replacement_plan_blocks_incompatible_and_requires_lab"),
    "P7-03": ("BLOCKED", HW, ""),
    "P7-04": ("PASS", "Restore is a change plan -> independent approval required", "test_two_person_rule_approval_expiry_and_gate_denies_while_locked; test_vault_capture_schedule_and_replacement_restore"),
    "P7-05": ("PARTIAL", "config.restore certified + executed on emulator; hardware BLOCKED", "test_switch_certify_apply_readback_and_rollback_paths"),
    "P7-06": ("BLOCKED", HW, ""),
    "P7-07": ("PARTIAL", "Post-restore backup with provenance (emulated)", "test_vault_capture_schedule_and_replacement_restore"),
    "P7-08": ("NOT_RUN", "Manual export path for unsupported restore not tested", ""),
    # P8 learning
    "P8-01": ("PASS", "Rule analysis over synthetic demo dataset", "test_maintenance_service_history_and_rule_analysis"),
    "P8-02": ("BLOCKED", "requires real telemetry history", ""),
    "P8-03": ("PARTIAL", "Temporal split without leakage; training refused without labels", "test_temporal_split_has_no_leakage_and_training_needs_labels"),
    "P8-04": ("PARTIAL", "Comparison refused without three valid cycles", "test_model_comparison_requires_three_valid_cycles"),
    "P8-05": ("PASS", "Rejection recorded as feedback", "test_learning_feedback_outcome_and_training_refusal"),
    "P8-06": ("PASS", "Insufficient evidence disclosed; stale data not scored", "test_insufficient_evidence_disclosed_on_recommendation; test_nothing_measured_means_no_score_not_zero"),
    "P8-07": ("PASS", "AI identity cannot reach device writes or secrets", "test_ai_allows_only_dedicated_service_token; test_ai_service_refuses_platform_secrets"),
    "P8-08": ("PARTIAL", "Hourly analysis scheduled in worker/standalone; on-demand run tested", "test_maintenance_service_history_and_rule_analysis"),
    # P9 optimization
    "P9-01": ("PASS", "Congestion evaluated; feature-off denies; on -> exactly one change", "test_per_device_automatic_optimization_end_to_end"),
    "P9-02": ("PASS", "Only 20/40 MHz proposals; 80 MHz constraints refused", "test_enrollment_validation_rejects_all_settings_80mhz_spoof_and_switches; test_per_device_automatic_optimization_end_to_end"),
    "P9-03": ("PARTIAL", "Approved DEVICE_OPT_IN change APPLIED_VERIFIED on EMULATED AP; hardware BLOCKED", "test_per_device_automatic_optimization_end_to_end"),
    "P9-04": ("PARTIAL", "Degradation -> rollback (optimizer outcome check, shadow sim)", "test_shadow_simulation_cooldown_and_stop_without_writes"),
    "P9-05": ("PASS", "Cooldown prevents oscillation", "test_shadow_simulation_cooldown_and_stop_without_writes; test_per_device_automatic_optimization_end_to_end"),
    "P9-06": ("PASS", "Uncertified/feature-off -> DENY", "test_feature_flag_off_denies_even_a_fully_armed_device; test_every_guard_must_pass"),
    "P9-07": ("PASS", "Missing evidence disclosed and blocks action", "test_insufficient_evidence_disclosed_on_recommendation"),
    # P10 vendors
    "P10-01": ("BLOCKED", "requires Cisco switch hardware; adapter is placeholder", ""),
    "P10-02": ("BLOCKED", "requires Cisco syslog/AAA source", ""),
    "P10-03": ("BLOCKED", "requires UniFi controller", ""),
    "P10-04": ("PASS", "Unsupported vendor actions return UNSUPPORTED", "test_adapter_registry_unsupported_and_writes_central"),
    "P10-05": ("PASS", "Full Aruba regression 397/397", LOG),
    "P10-06": ("PARTIAL", "Compatibility gates are vendor-keyed; no non-Aruba restore exists", "test_restore_compatibility_rejects_wrong_model_firmware_and_redacted"),
    # P11 production topology
    **{f"P11-0{i}": ("BLOCKED", "requires multi-VM production-like environment", "") for i in range(1, 10)},
    "P11-05": ("BLOCKED", "requires independent security/penetration review", ""),
    # Aruba Instant API
    "AAPI-01": ("PARTIAL", "REST-disabled distinguished from bad credentials (mocked)", "test_login_distinguishes_rest_disabled_from_bad_credentials"),
    "AAPI-02": ("BLOCKED", HW + " (Instant cluster)", ""),
    "AAPI-03": ("PASS", "Expired SID retried once", "test_expired_session_is_retried_once"),
    "AAPI-04": ("PARTIAL", "Application-level failure marked failed (mocked)", "test_device_rejection_marks_failed"),
    "AAPI-05": ("PASS", "Only allowlisted show commands", "test_non_readonly_or_unsafe_commands_are_blocked; test_runs_only_the_fixed_commands"),
    "AAPI-06": ("PARTIAL", "Parsers tested on reconstructed fixtures; real 8.10 captures BLOCKED", "test_text_table_ap_parsing; test_header_underline_is_not_an_ap_and_master_ip_is_cleaned"),
    # Aruba CX API
    "CXAPI-01": ("PARTIAL", "Single /rest/v10.14 prefix (defect fixed); real switch BLOCKED", "test_cx_probe_uses_single_rest_prefix"),
    "CXAPI-02": ("PARTIAL", "Interfaces/VLANs read via real code path + emulator", "test_full_pipeline_and_alert_lifecycle"),
    "CXAPI-03": ("PASS", "Writes refused under lock / without certification", "test_aruba_client_refuses_writes_under_lock; test_uncertified_and_locked_hardware_paths_never_touch_devices"),
    "CXAPI-04": ("BLOCKED", HW, ""), "CXAPI-05": ("BLOCKED", HW + " + AAA", ""),
    "CXAPI-06": ("PARTIAL", "/fullconfigs checkpoint/backup/restore on emulator", "test_switch_certify_apply_readback_and_rollback_paths"),
    "CXAPI-07": ("NOT_RUN", "Metadata-only change distinction not tested", ""),
    "CXAPI-08": ("PARTIAL", "Template deploy plan converges (emulated)", "test_template_inheritance_deploys_and_reconciles"),
    "CXAPI-09": ("PASS", "Incompatible restore rejected", "test_restore_compatibility_rejects_wrong_model_firmware_and_redacted"),
    "CXAPI-10": ("PARTIAL", "Approved restore with backup/readback/compare on emulator; hardware BLOCKED", "test_switch_certify_apply_readback_and_rollback_paths"),
    "SEC-03": ("PASS", "Audit tamper detected", "test_audit_hash_chain_detects_modification_and_deletion"),
    "SEC-04": ("BLOCKED", "requires distributed telemetry DB deployment", ""),
    # deployment
    "P1-DEP-01": ("BLOCKED", VM, ""), "P1-DEP-13": ("BLOCKED", VM, ""), "P2-DEP-01": ("BLOCKED", VM, ""),
    "P4-DEP-01": ("BLOCKED", VM, ""), "P5-DEP-01": ("NOT_RUN", "Vault upgrade with versioned records not exercised", ""),
    "P6-DEP-01": ("NOT_RUN", "Upgrade during pending approved change not exercised", ""),
    "P8-DEP-01": ("NOT_RUN", "One-click inference model rollout not implemented", ""),
    **{f"P11-DEP-{i:02d}": ("BLOCKED", VM, "") for i in range(1, 11)},
    "P11-DEP-02": ("PARTIAL", "Unknown major schema refused; cross-VM upgrade BLOCKED", "test_unknown_major_schema_version_rejected; test_schema_gate_refuses_release_without_declaration"),
    "P11-DEP-03": ("PARTIAL", "Expand-only migrations 0004->0009 preserve data; contract step not run", "test_expand_only_management_0004_to_head_preserves_existing_site"),
    "P11-DEP-06": ("PARTIAL", "Malformed/incorrect release refused; image signing not implemented", "test_incorrect_release_refuses_to_proceed; test_malformed_release_json_refuses_to_proceed"),
    # integration
    "INTEG-P1-09": ("BLOCKED", VM, ""),
    "INTEG-P2-01": ("BLOCKED", HW, ""), "INTEG-P2-02": ("BLOCKED", "requires real Instant 8.10 captures", ""),
    "INTEG-P2-03": ("BLOCKED", HW, ""), "INTEG-P2-04": ("BLOCKED", HW, ""),
    "INTEG-P2-05": ("PARTIAL", "Site reassignment (software)", "test_move_ap_between_sites"),
    "INTEG-P2-06": ("PASS", "Unapproved CIDR and disabled profile refused", "test_discovery_refuses_unauthorized_or_oversized_target_before_probe; test_disabled_profile_is_not_available_to_discover"),
    "INTEG-P3-01": ("NOT_RUN", "No frozen historical fixture recompute", ""),
    "INTEG-P3-02": ("NOT_RUN", "Sample-history migration not exercised", ""),
    "INTEG-P3-03": ("BLOCKED", HW + " + Alloy", ""),
    "INTEG-P3-04": ("PASS", "Offline AP alert lifecycle", "test_offline_ap_alert_ack_resolve_flow; test_ap_missing_from_poll_goes_down"),
    "INTEG-P3-05": ("PARTIAL", "Stale counters never scored; active probe not implemented", "test_freshness_and_experience_coverage_never_zero_fill"),
    "INTEG-P4-01": ("BLOCKED", HW, ""),
    "INTEG-P4-02": ("PARTIAL", "Attribution levels incl. UNKNOWN on missing AAA", "test_attribution_levels_verified_correlated_unknown"),
    "INTEG-P4-03": ("PASS", "Cross-site request refused", "test_site_scoped_user_sees_only_granted_site"),
    "INTEG-P4-04": ("PASS", "Self-approval refused", "test_two_person_rule_approval_expiry_and_gate_denies_while_locked"),
    "INTEG-P4-05": ("PASS", "Tamper detected", "test_audit_hash_chain_detects_modification_and_deletion"),
    "INTEG-P5-01": ("PARTIAL", "Instant config encrypted in vault (emulated)", "test_vault_capture_schedule_and_replacement_restore"),
    "INTEG-P5-02": ("PASS", "Legacy redacted snapshots imported as never-restorable", "test_legacy_backups_never_restorable; test_migration_marks_preexisting_backups_not_restorable"),
    "INTEG-P5-03": ("PASS", "Hash mismatch blocks restore", "test_vault_roundtrip_and_tamper_detection"),
    "INTEG-P5-04": ("PARTIAL", "Scheduled + pre-change + post-change backups (emulated)", "test_vault_capture_schedule_and_replacement_restore"),
    "INTEG-P6-01": ("PARTIAL", "SIMS SSID builders behind execution engine (emulated)", "test_instant_certify_and_apply_rf_and_ssid"),
    "INTEG-P6-02": ("PASS", "80 MHz and invalid channels refused", "test_rf_policy_rejects_80mhz_excluded_dfs_and_invalid"),
    "INTEG-P6-03": ("PASS", "One lease winner per slot; CAS APPROVED->EXECUTING", "test_lease_one_winner_per_slot; test_change_plan_two_person_concurrency_and_gate"),
    "INTEG-P6-04": ("PARTIAL", "VLAN change with snapshot/readback on emulator + browser", "test_switch_certify_apply_readback_and_rollback_paths; " + E2E),
    "INTEG-P6-05": ("PASS", "Uncertified hardware refused", "test_write_capability_requires_certification"),
    "INTEG-P7-01": ("PASS", "Different model/firmware blocked", "test_replacement_plan_blocks_incompatible_and_requires_lab"),
    "INTEG-P7-02": ("PARTIAL", "Recovery to certified emulated device", "test_vault_capture_schedule_and_replacement_restore"),
    "INTEG-P8-01": ("PARTIAL", "Temporal separation enforced; no real dataset", "test_temporal_split_has_no_leakage_and_training_needs_labels"),
    "INTEG-P8-02": ("PASS", "Rule-based recommendations without ML", "test_maintenance_service_history_and_rule_analysis"),
    "INTEG-P8-03": ("PASS", "Ollama unavailable -> friendly error", "test_unreachable_ollama_gives_friendly_error_and_releases_lock"),
    "INTEG-P8-04": ("PARTIAL", "Comparison gated on valid cycles", "test_model_comparison_requires_three_valid_cycles"),
    "INTEG-P9-01": ("PASS", "AI worker cannot write to devices", "test_ai_allows_only_dedicated_service_token"),
    "INTEG-P9-02": ("PARTIAL", "Degradation stops/rolls back (shadow + emulated)", "test_shadow_simulation_cooldown_and_stop_without_writes"),
    "INTEG-P10-01": ("PASS", "Placeholder adapters report UNSUPPORTED", "test_adapter_registry_unsupported_and_writes_central"),
    "INTEG-P10-02": ("PASS", "Domain tests unchanged; registry-based", "test_domain_imports_no_framework_orm_bus_or_vendor; test_adapter_registry_unsupported_and_writes_central"),
    "INTEG-P11-01": ("BLOCKED", VM, ""), "INTEG-P11-02": ("BLOCKED", VM, ""), "INTEG-P11-03": ("BLOCKED", VM, ""),
    "INTEG-P11-04": ("NOT_RUN", "Post-migration scoped report not exercised", ""),
    "GAP-T01": ("BLOCKED", HW, ""), "GAP-T13": ("BLOCKED", VM, ""),
    "GAP-T15": ("NOT_RUN", "Isolated AI code reviewer (GAP-17) not implemented in 1.2.0-rc1", ""),
}


def main() -> None:
    rows = list(csv.DictReader(REG.open()))
    fields = list(rows[0].keys())
    for k in ("actual_result", "defect"):
        if k not in fields:
            fields.append(k)
    changed = 0
    for r in rows:
        r.setdefault("actual_result", "")
        r.setdefault("defect", "")
        if r["status"] == "PASS":
            continue
        if r["test_id"] in D:
            st, actual, ev = D[r["test_id"]]
            r["status"], r["actual_result"], r["date"] = st, actual, DATE
            if ev:
                r["artifact_link"] = (ev if ev.startswith("evidence/") else f"{LOG} :: {ev}")
            r["owner"] = "Claude (build agent) — independent re-run required"
            changed += 1
    with REG.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    from collections import Counter, defaultdict
    by = defaultdict(list)
    for r in rows:
        by[r["phase"]].append(r)
    out = ["# 1.2.0-rc1 phase test matrix", "",
           "Generated by `scripts/reconcile_register_1_2_0.py` from `docs/Unified_Acceptance_Register_v4.csv`.",
           "PARTIAL = proven on the protocol emulator or mocked device I/O only; the hardware half is BLOCKED. "
           "No row is PASS on emulator evidence alone.", "",
           "| Status | Count |", "|---|---|"]
    tot = Counter(r["status"] for r in rows)
    out += [f"| {k} | {v} |" for k, v in sorted(tot.items())] + [f"| **Total** | **{len(rows)}** |", ""]
    order = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11", "CROSS", "PRINTER"]
    for ph in order + sorted(set(by) - set(order)):
        if ph not in by:
            continue
        c = Counter(r["status"] for r in by[ph])
        out += [f"## {ph} — " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())), "",
                "| Test ID | Procedure | Expected | Actual | Status | Evidence | Defect |", "|---|---|---|---|---|---|---|"]
        for r in by[ph]:
            cell = lambda s: (s or "").replace("|", "/").replace("\n", " ")
            out.append(f"| {r['test_id']} | {cell(r['procedure'])} | {cell(r['expected_result'])} | "
                       f"{cell(r.get('actual_result') or r['evidence'])} | **{r['status']}** | {cell(r['artifact_link'])} | {cell(r.get('defect'))} |")
        out.append("")
    (ROOT / "docs" / "RC_1.2.0_TEST_MATRIX.md").write_text("\n".join(out))
    print(f"updated {changed} rows; totals {dict(tot)}")


if __name__ == "__main__":
    sys.exit(main())
