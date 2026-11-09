"""Phase 9 — per-device opt-in automatic RF optimization loop.

For each ARMED DEVICE_OPT_IN enrollment: derive a bounded proposal from measured telemetry, evaluate every
guard at execution time, create a change plan (approved by the enrollment's standing policy approval, or
left PENDING for a human), and execute it through the certified change engine (backup, readback, rollback).
After the cooldown it measures the outcome; a degradation pauses the enrollment and executes a rollback plan.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models_inm import InmDevice
from ..database.models_phases import (ChangePlan, CollectionPlan, ConfigArtifact, ExecutionDecision,
                                      OptimizationEnrollment, OptimizationSitePolicy)
from ..domain import change_control as CC
from ..domain import collection as C
from ..domain import learning as L
from ..domain import optimization_policy as P
from . import change_executor as X
from .phase_services import control

UTIL_TRIGGER = 70.0


def feature_enabled() -> bool:
    return os.environ.get("INM_AUTOMATIC_OPTIMIZATION", "false").lower() in ("1", "true", "yes")


def _utc(t):
    return None if t is None else (t if t.tzinfo else t.replace(tzinfo=timezone.utc))


def enrollment_dict(e: OptimizationEnrollment | None) -> dict | None:
    if e is None:
        return None
    return {"device_id": e.device_id, "immutable_serial": e.immutable_serial, "mode": e.mode, "status": e.status,
            "expires_at_utc": _utc(e.expires_at_utc), "firmware_bound": e.firmware_bound,
            "independent_approver_id": e.independent_approver_id, "requester_id": e.requester_id,
            "allowed_operation_types": e.allowed_operation_types, "value_constraints": e.value_constraints,
            "cooldown_seconds": e.cooldown_seconds, "max_actions_per_day": e.max_actions_per_day}


def current_enrollment(db: Session, device_id: str) -> OptimizationEnrollment | None:
    return db.scalar(select(OptimizationEnrollment).where(
        OptimizationEnrollment.device_id == device_id,
        OptimizationEnrollment.status.in_(("PENDING_APPROVAL", "ARMED", "PAUSED")))
        .order_by(OptimizationEnrollment.created_at.desc()).limit(1))


def evaluate(db: Session, device: InmDevice, op: dict, *, actor: str, recommendation_id: str | None = None,
             quality_degraded: bool = False) -> tuple[dict, ExecutionDecision]:
    t = datetime.now(timezone.utc)
    e = current_enrollment(db, device.id)
    if e is not None and e.status == "ARMED" and _utc(e.expires_at_utc) <= t:
        e.status, e.status_reason = "EXPIRED", "EXPIRED"
    plan = db.scalar(select(CollectionPlan).where(CollectionPlan.device_id == device.id))
    fresh = plan is not None and C.freshness(plan.last_success_at, plan.interval_seconds, t) == "FRESH"
    last = db.scalar(select(ExecutionDecision).where(ExecutionDecision.device_id == device.id,
                                                     ExecutionDecision.kind == "OPTIMIZATION",
                                                     ExecutionDecision.decision == "ALLOW")
                     .order_by(ExecutionDecision.evaluated_at.desc()).limit(1))
    today = db.scalar(select(func.count()).select_from(ExecutionDecision).where(
        ExecutionDecision.device_id == device.id, ExecutionDecision.kind == "OPTIMIZATION",
        ExecutionDecision.decision == "ALLOW", ExecutionDecision.evaluated_at >= t - timedelta(days=1))) or 0
    backup = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == device.id)
                       .order_by(ConfigArtifact.created_at.desc()).limit(1))
    certified = X.certification_map(db, device).get(op.get("op"), "") == "CERTIFIED_WRITE"
    hardware = X.environment(device) == "HARDWARE"
    lock = (get_settings().device_write_lock or not get_settings().allow_config_changes) if hardware else False
    site_ok = True
    sp = db.get(OptimizationSitePolicy, device.site_id) if device.site_id else None
    if sp is not None and op.get("op") == "ap.radio.channel":
        allowed = sp.allowed_channels_5g if op.get("band", "5") == "5" else sp.allowed_channels_2g
        site_ok = not allowed or op.get("value") in allowed
    res = P.evaluate_action(now=t, feature_enabled=feature_enabled(), global_write_lock=lock,
                            emergency_stop=control(db).emergency_stop, enrollment=enrollment_dict(e),
                            device={"id": device.id, "serial": device.serial_number, "firmware": device.firmware,
                                    "family": device.family},
                            proposed_op=op, observation_fresh=fresh, backup_verified=backup is not None,
                            adapter_certified=certified, last_action_at=_utc(last.evaluated_at) if last else None,
                            actions_today=today, quality_degraded=quality_degraded)
    if not site_ok:
        res["decision"] = "DENY"
        res["guard_evaluation"].append({"guard": "site_policy_allows_value", "pass": False})
        res["denial_reason"] = ((res["denial_reason"] + "; ") if res["denial_reason"] else "") + "Value outside site policy."
    dec = ExecutionDecision(kind="OPTIMIZATION", enrollment_id=e.id if e else None, recommendation_id=recommendation_id,
                            device_id=device.id, global_lock_state="LOCKED" if lock else "UNLOCKED",
                            decision=res["decision"], guards=res["guard_evaluation"], denial_reason=res["denial_reason"],
                            evaluated_by=actor)
    db.add(dec)
    db.flush()
    return res, dec


def _latest_util(device: InmDevice) -> float | None:
    from .runtime import telemetry_store
    store = telemetry_store()
    if store is None:
        return None
    rows = store.query(device_id=device.id, metric_name="util_5g_percent",
                       since=datetime.now(timezone.utc) - timedelta(minutes=30), limit=10)
    vals = [r["value"] for r in rows if r.get("value") is not None]
    return sum(vals) / len(vals) if vals else None


def propose(db: Session, device: InmDevice, e: OptimizationEnrollment) -> dict | None:
    """Rule-based proposal: sustained 5 GHz utilisation above the trigger -> move to the next allowed channel
    not used by the device's last optimizer change. Bounded by enrollment and site constraints."""
    if "ap.radio.channel" not in (e.allowed_operation_types or []):
        return None
    util = _latest_util(device)
    if util is None or util < UTIL_TRIGGER:
        return None
    sp = db.get(OptimizationSitePolicy, device.site_id) if device.site_id else None
    allowed = [c for c in ((sp.allowed_channels_5g if sp else None) or [36, 40, 44, 48, 149, 153, 157, 161])
               if c not in (e.value_constraints or {}).get("excluded_channels", [])]
    if not allowed:
        return None
    last = db.scalar(select(ChangePlan).where(ChangePlan.device_id == device.id, ChangePlan.source == "AUTO_OPTIMIZER")
                     .order_by(ChangePlan.created_at.desc()).limit(1))
    prev = next((o.get("value") for o in (last.operations if last else []) if o.get("op") == "ap.radio.channel"), None)
    nxt = allowed[(allowed.index(prev) + 1) % len(allowed)] if prev in allowed else allowed[0]
    return {"op": "ap.radio.channel", "band": "5", "value": nxt, "power": 14,
            "reason": f"5 GHz utilisation {util:.0f}% ≥ {UTIL_TRIGGER:.0f}%"}


def run_optimizer(db: Session, *, actor: str = "optimizer") -> dict:
    out = {"evaluated": 0, "executed": 0, "pending_approval": 0, "denied": 0, "rolled_back_degraded": 0}
    for e in list(db.scalars(select(OptimizationEnrollment).where(OptimizationEnrollment.status == "ARMED",
                                                                   OptimizationEnrollment.mode == "DEVICE_OPT_IN"))):
        device = db.get(InmDevice, e.device_id)
        if device is None:
            continue
        out["rolled_back_degraded"] += _check_outcome(db, device, e, actor)
        if e.status != "ARMED":
            continue
        op = propose(db, device, e)
        if op is None:
            continue
        out["evaluated"] += 1
        clean = {k: v for k, v in op.items() if k != "reason"}
        res, dec = evaluate(db, device, clean, actor=actor)
        if res["decision"] != "ALLOW":
            out["denied"] += 1
            continue
        ops = [CC.validate_operation(clean, family=device.family,
                                     excluded_channels=set((e.value_constraints or {}).get("excluded_channels", [])))]
        latest = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == device.id)
                           .order_by(ConfigArtifact.created_at.desc()).limit(1))
        plan = ChangePlan(device_id=device.id, title=f"Auto-optimization: {op['reason']} → channel {op['value']}",
                          operations=ops, capability_report=[{"op": clean["op"], "capability": "CERTIFIED_WRITE"}],
                          plan_hash=CC.plan_hash(device.id, ops, latest.plaintext_sha256 if latest else None),
                          expected_config_hash=latest.plaintext_sha256 if latest else None,
                          firmware_at_request=device.firmware, rollback_plan={"strategy": "DEVICE_CHECKPOINT"},
                          source="AUTO_OPTIMIZER", requester=f"optimizer:{e.requester_id}",
                          state="PENDING_APPROVAL")
        try:   # record the current channel so a degradation rollback can restore it
            w, _env = X.writer_for(db, device)
            w.open()
            try:
                _ok, seen = w.readback({"op": "ap.radio.channel", "band": "5", "value": -1})
            finally:
                w.close()
            if str(seen.get("channel") or "").isdigit():
                plan.rollback_plan = dict(plan.rollback_plan, previous_channel=int(seen["channel"]))
        except Exception:
            pass
        if e.standing_approval:
            plan.state, plan.approver = "APPROVED", e.independent_approver_id
            plan.approved_at, plan.decision_note = datetime.now(timezone.utc), "Standing per-device policy approval"
        db.add(plan)
        db.flush()
        e.last_action_id = plan.id
        if plan.state == "APPROVED":
            r = X.execute_plan(db, plan, actor=actor)
            out["executed"] += int(r.get("outcome") == "APPLIED_VERIFIED")
        else:
            out["pending_approval"] += 1
    db.flush()
    return out


def _check_outcome(db: Session, device: InmDevice, e: OptimizationEnrollment, actor: str) -> int:
    """After the cooldown, compare utilisation before/after the last automatic change. Degradation ->
    pause the enrollment (QUALITY_DEGRADED) and execute a rollback plan via the same engine."""
    from .runtime import telemetry_store
    if not e.last_action_id:
        return 0
    plan = db.get(ChangePlan, e.last_action_id)
    if plan is None or plan.state != "APPLIED_VERIFIED" or (plan.rollback_plan or {}).get("outcome_checked"):
        return 0
    t = datetime.now(timezone.utc)
    applied = _utc(plan.approved_at) or _utc(plan.created_at)
    if t - applied < timedelta(seconds=min(e.cooldown_seconds, 3600)):
        return 0
    store = telemetry_store()
    if store is None:
        return 0
    rows = store.query(device_id=device.id, metric_name="util_5g_percent", since=applied - timedelta(hours=1), limit=500)

    def w(lo, hi):
        res = []
        for r in rows:
            ts = r["observed_at"]
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00")) if isinstance(ts, str) else ts
            if lo <= _utc(ts) < hi:
                res.append(r)
        return res
    result = L.evaluate_outcome(metric="util_5g_percent", higher_is_better=False,
                                before=w(applied - timedelta(hours=1), applied), after=w(applied, t), action_applied=True)
    plan.rollback_plan = dict(plan.rollback_plan or {}, outcome_checked=True, outcome=result)
    if result["status"] != "DEGRADED":
        return 0
    e.status, e.status_reason = "PAUSED", "QUALITY_DEGRADED"
    pre = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == device.id, ConfigArtifact.trigger == "PRE_CHANGE")
                    .order_by(ConfigArtifact.created_at.desc()).limit(1))
    if pre is None:
        return 0
    prev = (plan.operations[0] or {}) if plan.operations else {}
    rb_ops = [{"op": "ap.radio.channel", "band": prev.get("band", "5"),
               "value": (plan.rollback_plan or {}).get("previous_channel", 36), "power": prev.get("power", 14)}]
    rb = ChangePlan(device_id=device.id, title="Auto-rollback after measured degradation", operations=rb_ops,
                    capability_report=[{"op": "ap.radio.channel", "capability": "CERTIFIED_WRITE"}],
                    plan_hash=CC.plan_hash(device.id, rb_ops, None),
                    expected_config_hash=(db.scalar(select(ConfigArtifact.plaintext_sha256).where(
                        ConfigArtifact.device_id == device.id).order_by(ConfigArtifact.created_at.desc()).limit(1))),
                    firmware_at_request=device.firmware, rollback_plan={"strategy": "DEVICE_CHECKPOINT"},
                    source="AUTO_OPTIMIZER", requester=f"optimizer:{e.requester_id}", state="APPROVED",
                    approver=e.independent_approver_id, approved_at=t, decision_note="Automatic rollback (degradation)")
    db.add(rb)
    db.flush()
    X.execute_plan(db, rb, actor=actor)
    return 1
