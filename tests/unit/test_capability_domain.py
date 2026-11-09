"""Unit tests for WP-02..WP-12 pure domain modules (no services)."""
import random
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

from backend.domain import adapters as AD
from backend.domain import attribution as A
from backend.domain import change_control as CC
from backend.domain import collection as C
from backend.domain import learning as L
from backend.domain import optimization_policy as P
from backend.domain import printer_intelligence as PI
from backend.domain import replacement as RP
from backend.domain import reporting as R
from backend.domain import vault as V

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


# ---- WP-02 collection ---------------------------------------------------------------------
def test_plan_validation_rejects_bad_families_intervals_and_windows():
    ok = C.validate_plan(C.PlanSpec("access_point", ("radios", "system"), 300))
    assert ok.metric_families == ("radios", "system")
    for bad in [C.PlanSpec("access_point", ("interfaces",), 300), C.PlanSpec("switch", (), 300),
                C.PlanSpec("switch", ("system",), 10), C.PlanSpec("switch", ("system",), 300, 3, None),
                C.PlanSpec("router", ("system",), 300), C.PlanSpec("switch", ("system", "system"), 300)]:
        with pytest.raises(C.PlanError):
            C.validate_plan(bad)


def test_dedupe_key_is_per_device_family_window_and_stable():
    w = C.scheduled_window(T0, 300)
    assert C.dedupe_key("d1", "system", w) == C.dedupe_key("d1", "system", w)
    assert C.dedupe_key("d1", "system", w) != C.dedupe_key("d1", "system", w + 1)
    assert C.dedupe_key("d1", "system", w) != C.dedupe_key("d2", "system", w)


def test_backoff_is_bounded_and_failures_never_become_success():
    rng = random.Random(1)
    delays = [C.backoff_seconds(a, rng=rng) for a in range(1, 12)]
    assert all(0 < d <= 900 for d in delays)
    assert C.next_action_after_failure("UNREACHABLE", 1) == "RETRY"
    assert C.next_action_after_failure("UNREACHABLE", C.MAX_ATTEMPTS) == "DLQ"
    assert C.next_action_after_failure("AUTH_REJECTED", 1) == "DLQ"
    assert C.next_action_after_failure("bogus", 1) in ("RETRY", "DLQ")


def test_freshness_and_experience_coverage_never_zero_fill():
    assert C.freshness(None, 60, T0) == "NEVER"
    assert C.freshness(T0 - timedelta(seconds=90), 60, T0) == "FRESH"
    assert C.freshness(T0 - timedelta(seconds=300), 60, T0) == "STALE"
    assert C.freshness(T0 - timedelta(hours=2), 60, T0) == "GAP"
    k = C.experience_kpis([{"metric_name": "client_count", "value": 4, "quality": "OBSERVED"},
                           {"metric_name": "util_5g_percent", "value": None, "quality": "OBSERVED"}])
    assert k["measured"]["client_count"]["avg"] == 4
    assert "util_5g_percent" in k["unmeasured"] and "latency_ms" in k["unmeasured"]
    assert k["caveat"]


# ---- WP-03 attribution --------------------------------------------------------------------
CX_LINE = "<190>Nov  1 12:00:00 sw1 hpe-config[22]: Event|6801|LOG_INFO|AMM|-|User alice configuration change committed"


def test_normalize_cx_and_unknown_lines():
    e = A.normalize(CX_LINE, source_ip="10.1.1.2", device_serial="SG1", vendor="Aruba", received_at=T0)
    assert e.event_code == "6801" and e.user == "alice" and e.is_config_change and e.severity == "info"
    u = A.normalize("garbled text", source_ip="10.1.1.2", device_serial=None, vendor="x", received_at=T0)
    assert u.message == "garbled text" and u.event_code is None and not u.is_config_change
    with pytest.raises(ValueError):
        A.normalize("x", source_ip="1", device_serial=None, vendor="x", received_at=T0, transport="FTP")


def test_attribution_levels_verified_correlated_unknown():
    s = A.AuthSession("s1", "SG1", "alice", T0 - timedelta(minutes=5), T0 + timedelta(minutes=5), "TACACS+")
    assert A.attribute(device_serial="SG1", change_at=T0, device_event_user="alice", sessions=[s])["level"] == "VERIFIED"
    # Forged/unrelated session (different device) cannot earn VERIFIED
    other = A.AuthSession("s2", "OTHER", "alice", T0 - timedelta(minutes=5), None, "RADIUS")
    assert A.attribute(device_serial="SG1", change_at=T0, device_event_user="alice", sessions=[other])["level"] == "UNKNOWN"
    # Two candidates -> CORRELATED, no actor named
    s3 = A.AuthSession("s3", "SG1", "bob", T0 - timedelta(minutes=1), None, "TACACS+")
    r = A.attribute(device_serial="SG1", change_at=T0, device_event_user="alice", sessions=[s, s3])
    assert r["level"] == "CORRELATED" and r["actor"] is None and r["candidates"] == ["alice", "bob"]
    # Device event doesn't name the user -> CORRELATED
    assert A.attribute(device_serial="SG1", change_at=T0, device_event_user=None, sessions=[s])["level"] == "CORRELATED"
    assert A.attribute(device_serial="SG1", change_at=T0, device_event_user=None, sessions=[])["actor"] is None
    assert A.coverage({"syslog"})["missing"] == ["aaa", "app_audit"]


# ---- WP-05 vault ---------------------------------------------------------------------------
IDENT = {"device_id": "d1", "serial": "SG1", "vendor": "Aruba", "model": "CX 6200F", "firmware": "10.14.1000",
         "capture_source": "OPERATOR_UPLOAD", "fidelity": V.FIDELITY_FULL}


def test_vault_roundtrip_and_tamper_detection():
    key = Fernet.generate_key()
    s = V.seal(b"hostname sw1\nvlan 100\n", master_key=key, identity=IDENT)
    assert b"vlan" not in s.ciphertext
    assert V.open_sealed(s.ciphertext, wrapped_key=s.wrapped_key, manifest=s.manifest,
                         signature=s.signature, master_key=key) == b"hostname sw1\nvlan 100\n"
    tampered = bytearray(s.ciphertext); tampered[10] ^= 1
    with pytest.raises(V.VaultIntegrityError):
        V.open_sealed(bytes(tampered), wrapped_key=s.wrapped_key, manifest=s.manifest, signature=s.signature, master_key=key)
    with pytest.raises(V.VaultIntegrityError):
        V.open_sealed(s.ciphertext, wrapped_key=s.wrapped_key, manifest=dict(s.manifest, model="X"),
                      signature=s.signature, master_key=key)
    with pytest.raises(V.VaultIntegrityError):
        V.open_sealed(s.ciphertext, wrapped_key=s.wrapped_key, manifest=s.manifest, signature=s.signature,
                      master_key=Fernet.generate_key())
    assert "wrapped_key" not in V.redact_for_listing(s.manifest)


def test_restore_compatibility_rejects_wrong_model_firmware_and_redacted():
    t = {"vendor": "Aruba", "model": "CX 6200F", "firmware": "10.14.1020"}
    assert V.restore_compatibility(artifact=IDENT, target=t)["compatible"]
    assert not V.restore_compatibility(artifact=IDENT, target=dict(t, model="CX 6300"))["compatible"]
    assert not V.restore_compatibility(artifact=IDENT, target=dict(t, firmware="10.13.1000"))["compatible"]
    assert not V.restore_compatibility(artifact=dict(IDENT, fidelity=V.FIDELITY_REDACTED), target=t)["compatible"]
    assert V.restore_compatibility(artifact=IDENT, target=dict(t, model="CX 6200M", compatible_models=["CX 6200F"]))["compatible"]


# ---- WP-06 change control ------------------------------------------------------------------
def test_rf_policy_rejects_80mhz_excluded_dfs_and_invalid():
    with pytest.raises(CC.PolicyViolation):
        CC.validate_operation({"op": "ap.radio.width", "value": 80}, family="access_point")
    with pytest.raises(CC.PolicyViolation):
        CC.validate_operation({"op": "ap.radio.channel", "band": "5", "value": 149}, family="access_point",
                              excluded_channels={149})
    with pytest.raises(CC.PolicyViolation):
        CC.validate_operation({"op": "ap.radio.channel", "band": "5", "value": 100}, family="access_point")
    with pytest.raises(CC.PolicyViolation):
        CC.validate_operation({"op": "ap.radio.channel", "band": "2.4", "value": 14}, family="access_point")
    assert CC.validate_operation({"op": "ap.radio.width", "value": 40}, family="access_point")
    assert CC.validate_operation({"op": "ap.radio.channel", "band": "5", "value": 100}, family="access_point", allow_dfs=True)


def test_switch_policy_protects_uplinks_and_family_mismatch():
    with pytest.raises(CC.PolicyViolation):
        CC.validate_operation({"op": "switch.port.admin_state", "port": "1/1/49", "value": "down"},
                              family="switch", uplink_ports={"1/1/49"})
    with pytest.raises(CC.PolicyViolation):
        CC.validate_operation({"op": "ap.radio.width", "value": 20}, family="switch")
    with pytest.raises(CC.PolicyViolation):
        CC.validate_operation({"op": "switch.port.trunk_vlans", "port": "1/1/1", "value": [10, 10]}, family="switch")
    report = CC.capability_check([{"op": "switch.lldp"}], {})
    assert report == [{"op": "switch.lldp", "capability": "UNSUPPORTED"}]


def test_two_person_rule_approval_expiry_and_gate_denies_while_locked():
    with pytest.raises(CC.PolicyViolation):
        CC.check_approval(requester="ann", approver="ANN", approver_role="approver")
    with pytest.raises(CC.PolicyViolation):
        CC.check_approval(requester="ann", approver="bob", approver_role="viewer")
    ok, _ = CC.approval_valid(approved_at=T0, now=T0 + timedelta(hours=1), approved_config_hash="h",
                              current_config_hash="h", approved_firmware="f", current_firmware="f")
    assert ok
    assert CC.approval_valid(approved_at=T0, now=T0 + timedelta(days=2), approved_config_hash="h",
                             current_config_hash="h", approved_firmware="f", current_firmware="f")[1] == "Approval expired."
    assert "drift" in CC.approval_valid(approved_at=T0, now=T0, approved_config_hash="h", current_config_hash="x",
                                        approved_firmware="f", current_firmware="f")[1]
    everything = dict(emergency_stop=False, plan_state="APPROVED", approval_ok=True,
                      capabilities=[{"op": "x", "capability": "CERTIFIED_WRITE"}], pre_backup_verified=True,
                      device_lease_held=True, executor_authenticated=True)
    assert CC.execution_gate(write_lock=True, **everything)["decision"] == "DENY"
    assert CC.execution_gate(write_lock=False, **everything)["decision"] == "ALLOW"
    assert CC.execution_gate(write_lock=False, **dict(everything, pre_backup_verified=False))["decision"] == "DENY"


# ---- WP-07 replacement ---------------------------------------------------------------------
def test_replacement_plan_blocks_incompatible_and_requires_lab():
    old = {"id": "d1", "serial": "SG1", "family": "switch", "vendor": "Aruba", "model": "CX 6200F", "firmware": "10.14.1"}
    new = {"id": "d2", "serial": "SG2", "family": "switch", "vendor": "Aruba", "model": "CX 6200F", "firmware": "10.14.2"}
    p = RP.build_plan(old_device=old, artifact=IDENT, new_device=new, identity_map={"mgmt_ip": {"old": "a", "new": "b"}})
    assert p["state"] == "LAB_VALIDATION_REQUIRED" and p["identity_map"]["serial"]["new"] == "SG2"
    assert p["optimization_on_successor"] == "RECOMMEND_ONLY"
    bad = RP.build_plan(old_device=old, artifact=IDENT, new_device=dict(new, model="CX 6300"), identity_map={})
    assert bad["state"] == "BLOCKED" and bad["blockers"]
    assert RP.build_plan(old_device=old, artifact=None, new_device=new, identity_map={})["state"] == "BLOCKED"


# ---- WP-08 learning ------------------------------------------------------------------------
def _obs(vals, start):
    return [{"metric_name": "util_5g_percent", "value": v, "quality": "OBSERVED",
             "observed_at": start + timedelta(minutes=i)} for i, v in enumerate(vals)]


def test_outcome_requires_evidence_and_reports_direction():
    assert L.evaluate_outcome(metric="util_5g_percent", higher_is_better=False, before=_obs([90], T0),
                              after=_obs([50, 50, 50], T0), action_applied=True)["status"] == "INSUFFICIENT_EVIDENCE"
    r = L.evaluate_outcome(metric="util_5g_percent", higher_is_better=False, before=_obs([90, 91, 89], T0),
                           after=_obs([60, 61, 59], T0), action_applied=True)
    assert r["status"] == "IMPROVED" and r["success"] is True
    r2 = L.evaluate_outcome(metric="util_5g_percent", higher_is_better=False, before=_obs([90, 91, 89], T0),
                            after=_obs([60, 61, 59], T0), action_applied=False)
    assert r2["success"] is None and "NO_ACTION_APPLIED" in r2["confounders"]


def test_temporal_split_has_no_leakage_and_training_needs_labels():
    rows = [{"id": i, "t": T0 + timedelta(hours=i), "rule_id": "R1" if i % 2 else "R2", "label": i % 3 != 0}
            for i in range(40)]
    parts = L.temporal_split(rows)
    assert max(r["t"] for r in parts["train"]) <= min(r["t"] for r in parts["holdout"])
    with pytest.raises(ValueError):
        L.check_no_leakage({"train": [rows[10]], "validation": [rows[5]], "holdout": []})
    assert L.train_rule_success_model(rows[:5])["status"] == "NOT_TRAINED"
    m = L.train_rule_success_model(rows)
    assert m["status"] == "CANDIDATE" and m["kind"] == "TRAINED_ML" and m["metrics"]["holdout"]["n"] > 0
    with pytest.raises(ValueError):
        L.check_promotion(trainer="a", approver="A", metrics=m["metrics"])


def test_insufficient_evidence_disclosed_on_recommendation():
    assert L.recommendation_quality({"evidence": {"samples": [{"value": 1}]}})["evidence_status"] == "INSUFFICIENT_EVIDENCE"
    assert L.recommendation_quality({"evidence": {}})["kind"] == "RULE_BASED"


# ---- WP-09 optimization (GAP-T17..T24 policy layer) ---------------------------------------
DEV = {"id": "ap1", "serial": "CN1", "family": "access_point", "firmware": "8.10.0.9"}


def _req(**kw):
    base = {"device_id": "ap1", "serial": "CN1", "mode": "SHADOW",
            "allowed_operation_types": ["ap.radio.channel"], "value_constraints": {"allowed_widths_mhz": [20, 40]},
            "cooldown_seconds": 3600, "max_actions_per_day": 2, "expires_at_utc": T0 + timedelta(days=30)}
    base.update(kw)
    return base


def test_default_is_recommend_only_never_inherited():
    s = P.default_state("new-ap")
    assert s["mode"] == "RECOMMEND_ONLY" and s["status"] == "OFF" and s["inherited"] is False


def test_enrollment_validation_rejects_all_settings_80mhz_spoof_and_switches():
    assert P.validate_enrollment_request(_req(), device=DEV, now=T0)
    for bad in [_req(allowed_operation_types=["*"]), _req(value_constraints={"allowed_widths_mhz": [80]}),
                _req(serial="SPOOF"), _req(device_id="other"), _req(expires_at_utc=T0 + timedelta(days=400)),
                _req(cooldown_seconds=10), _req(allowed_operation_types=["switch.port.poe"]), _req(mode="AUTO"),
                ]:
        with pytest.raises(CC.PolicyViolation):
            P.validate_enrollment_request(bad, device=DEV, now=T0)
    with pytest.raises(CC.PolicyViolation):
        P.validate_enrollment_request(_req(), device=dict(DEV, family="switch"), now=T0)
    with pytest.raises(CC.PolicyViolation):
        P.check_independent_approval("ann", "ann")


def _armed():
    return {"device_id": "ap1", "immutable_serial": "CN1", "mode": "DEVICE_OPT_IN", "status": "ARMED",
            "expires_at_utc": T0 + timedelta(days=5), "firmware_bound": "8.10.0.9",
            "independent_approver_id": "bob", "requester_id": "ann",
            "allowed_operation_types": ["ap.radio.channel", "ap.radio.width"],
            "value_constraints": {"allowed_widths_mhz": [20], "excluded_channels": [149]},
            "cooldown_seconds": 3600, "max_actions_per_day": 2}


def _eval(**kw):
    args = dict(now=T0, feature_enabled=True, global_write_lock=False, emergency_stop=False, enrollment=_armed(), device=DEV,
                proposed_op={"op": "ap.radio.channel", "band": "5", "value": 36}, observation_fresh=True,
                backup_verified=True, adapter_certified=True, last_action_at=None, actions_today=0,
                quality_degraded=False)
    args.update(kw)
    return P.evaluate_action(**args)


def test_feature_flag_off_denies_even_a_fully_armed_device():
    r = _eval(feature_enabled=False)
    assert r["decision"] == "DENY" and "feature is off" in r["denial_reason"]
    assert P.validate_enrollment_request(_req(mode="DEVICE_OPT_IN"), device=DEV, now=T0)["mode"] == "DEVICE_OPT_IN"


def test_every_guard_must_pass():
    assert _eval()["decision"] == "ALLOW"
    locked = _eval(global_write_lock=True)
    assert locked["decision"] == "DENY" and locked["display"] == "Configured for future automation — currently locked"
    for kw in [dict(emergency_stop=True), dict(enrollment=None), dict(backup_verified=False),
               dict(observation_fresh=False), dict(adapter_certified=False), dict(quality_degraded=True),
               dict(proposed_op={"op": "ap.radio.width", "value": 80}),
               dict(proposed_op={"op": "ap.radio.width", "value": 40}),
               dict(proposed_op={"op": "ap.radio.channel", "band": "5", "value": 149}),
               dict(proposed_op={"op": "ap.radio.tx_power", "min_dbm": 5, "max_dbm": 10}),
               dict(device=dict(DEV, firmware="8.11")), dict(device=dict(DEV, serial="SPOOF")),
               dict(enrollment=dict(_armed(), expires_at_utc=T0 - timedelta(seconds=1))),
               dict(enrollment=dict(_armed(), independent_approver_id="ann")),
               dict(last_action_at=T0 - timedelta(minutes=5)), dict(actions_today=2)]:
        assert _eval(**kw)["decision"] == "DENY", kw


def test_shadow_simulation_cooldown_and_stop_without_writes():
    props = [{"at": T0 + timedelta(minutes=10 * i), "op": "ap.radio.channel"} for i in range(8)]
    r = P.shadow_simulation(proposals=props, cooldown_seconds=1800, degrade_after=3)
    results = [x["result"] for x in r["timeline"]]
    assert r["device_writes"] == 0 and r["stopped"]
    assert "SUPPRESSED_COOLDOWN" in results and "STOP_AND_PROPOSE_ROLLBACK" in results
    assert results[-1] == "STOPPED"


# ---- WP-10 adapters ------------------------------------------------------------------------
def test_adapter_registry_unsupported_and_writes_central():
    cisco = AD.resolve("Cisco", "switch", "Catalyst 2960-X")
    assert cisco.key == "cisco_ios_xe_snmpv3"
    assert AD.operation_result(cisco, "backup_full_config")["status"] == "UNSUPPORTED"
    assert AD.operation_result(cisco, "apply_change")["status"] == "DENIED_BY_CENTRAL_POLICY"
    cx = AD.resolve("Aruba", "switch", "CX 6200F 24G")
    assert cx.key == "aruba_cx_10_14" and AD.operation_result(cx, "collect")["status"] == "AVAILABLE_READ_ONLY"
    assert AD.operation_result(cx, "apply_change")["status"] == "DENIED_BY_CENTRAL_POLICY"
    assert AD.resolve("Unknown", "other", None).key == "generic_snmpv3"
    assert AD.operation_result(cx, "format_flash")["status"] == "UNSUPPORTED"


# ---- WP-11 reporting -----------------------------------------------------------------------
def test_executive_summary_no_zero_fill_and_signed_export():
    s = R.executive_summary([], {}, [], [], [])
    assert s["poll_coverage_percent"] is None and s["poll_coverage_reason"]
    key = b"k" * 32
    ex = R.build_export(rows=[{"a": 1, "password": "hunter2"}], columns=["a", "password"],
                        meta={"export_id": "e"}, signing_key=key)
    assert "hunter2" not in ex["body"] and R.verify_export(ex, key)
    assert not R.verify_export(dict(ex, body=ex["body"] + "x"), key)
    assert not R.verify_export(ex, b"other" * 8)
    assert R.mask_user("alice", False) == "a***" and R.mask_user("alice", True) == "alice"


# ---- WP-12 printers ------------------------------------------------------------------------
def test_counter_rollover_vs_reset():
    assert PI.classify_counter(100, 150) == {"kind": "NORMAL", "delta": 50}
    assert PI.classify_counter(2**32 - 10, 5)["kind"] == "ROLLOVER"
    assert PI.classify_counter(2**32 - 10, 5)["delta"] == 15
    assert PI.classify_counter(50000, 10) == {"kind": "RESET_OR_REPLACEMENT", "delta": None}
    assert PI.classify_counter(None, 10)["kind"] == "INSUFFICIENT_DATA"


def test_supply_forecast_needs_evidence_and_ignores_estimates():
    pts = [{"observed_at": T0 + timedelta(days=i), "percent_remaining": 80 - 5 * i, "source": "SNMP"} for i in range(5)]
    f = PI.supply_forecast(pts, now=T0 + timedelta(days=4))
    assert f["status"] == "FORECAST" and f["kind"] == "STATISTICAL" and 11 < f["days_to_empty"] < 13
    assert PI.supply_forecast(pts[:2], now=T0)["status"] == "INSUFFICIENT_EVIDENCE"
    est = [dict(p, source="ESTIMATE") for p in pts]
    assert PI.supply_forecast(est, now=T0)["status"] == "INSUFFICIENT_EVIDENCE"
    refill = pts + [{"observed_at": T0 + timedelta(days=5), "percent_remaining": 100, "source": "SNMP"}]
    assert PI.supply_forecast(refill, now=T0 + timedelta(days=5))["status"] == "INSUFFICIENT_EVIDENCE"


def test_noc_health_down_device_is_poor_and_no_data_is_none():
    from backend.domain import noc as N
    assert N.health("access_point", {"cpu_percent": {"value": 10}}, "FRESH", "down")["band"] == "POOR"
    assert N.health("access_point", {}, "NEVER")["score"] is None
    h = N.health("access_point", {"util_5g_percent": {"value": 85}, "retry_percent": {"value": 16}}, "FRESH", "up")
    assert h["score"] == 55 and h["band"] == "POOR"


def test_best_practice_unknown_without_config_and_scores_good_vs_bad():
    from backend.domain import best_practices as BP
    from backend.infrastructure.demo_dataset import cx_config, iap_config
    assert BP.evaluate("switch", None)["grade"] == "UNKNOWN"
    assert BP.evaluate("switch", cx_config("hq-core-01", True))["grade"] == "A"
    bad = BP.evaluate("switch", cx_config("switch", False))
    assert bad["critical_failures"] and bad["score"] < 60
    assert BP.evaluate("access_point", iap_config("ap1", True))["grade"] == "A"
    ap_bad = {r["id"]: r["status"] for r in BP.evaluate("access_point", iap_config("ap1", False))["results"]}
    assert ap_bad["AP-RF-01"] == "FAIL" and ap_bad["AP-SEC-01"] == "FAIL"
    diff = BP.intended_vs_installed("switch", {"vlans": [100, 999], "interfaces": [{"name": "1/1/2", "access_vlan": 200}]},
                                    cx_config("x", False))
    fields = {d["field"] for d in diff["differences"]}
    assert diff["status"] == "DRIFT" and "vlan:999" in fields and "interface:1/1/2:access_vlan" in fields
