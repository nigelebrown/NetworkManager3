"""Phase 9 — per-device opt-in automatic RF optimization loop.

For each ARMED DEVICE_OPT_IN enrollment: derive a bounded proposal from measured telemetry, evaluate every
guard at execution time, create a change plan (approved by the enrollment's standing policy approval, or
left PENDING for a human), and execute it through the certified change engine (backup, readback, rollback).
After the cooldown it measures the outcome; a degradation pauses the enrollment and executes a rollback plan.
"""
from __future__ import annotations

import os
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models import AppSetting
from ..database.models_inm import InmDevice
from ..database.models_phases import (ChangePlan, ChangeExecution, CollectionPlan, ConfigArtifact, ExecutionDecision,
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
    successful = select(ChangeExecution).join(ChangePlan,ChangePlan.id==ChangeExecution.plan_id).where(
        ChangeExecution.device_id==device.id,ChangePlan.source=="AUTO_OPTIMIZER",
        ChangeExecution.outcome=="APPLIED_VERIFIED")
    last = db.scalar(successful.order_by(ChangeExecution.finished_at.desc()).limit(1))
    today = db.scalar(select(func.count()).select_from(successful.where(
        ChangeExecution.finished_at>=t-timedelta(days=1)).subquery())) or 0
    backup = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == device.id)
                       .order_by(ConfigArtifact.created_at.desc()).limit(1))
    certified = X.certification_map(db, device).get(op.get("op"), "") == "CERTIFIED_WRITE"
    hardware = X.environment(device) == "HARDWARE"
    lock = (get_settings().device_write_lock or not get_settings().allow_config_changes) if hardware else False
    site_ok = True
    from .arm_measurements import snapshot
    from ..aruba.arm import assessments
    measurement = snapshot(db,device.id)
    if device.source_type=="REAL":
        fresh = measurement.get("state")=="MEASURED" and not any(f["id"]=="arm_missing" for f in assessments(measurement))
    verified_backup = False
    if backup is not None:
        try:
            X._artifact_bytes(db,backup.id,device)
            verified_backup = True
        except Exception:
            pass
    sp = db.get(OptimizationSitePolicy, device.site_id) if device.site_id else None
    if sp is not None and op.get("op") == "ap.radio.channel" and not op.get("arm_managed"):
        allowed = sp.allowed_channels_5g if op.get("band", "5") == "5" else sp.allowed_channels_2g
        site_ok = not allowed or op.get("value") in allowed
    res = P.evaluate_action(now=t, feature_enabled=feature_enabled(), global_write_lock=lock,
                            emergency_stop=control(db).emergency_stop, enrollment=enrollment_dict(e),
                            device={"id": device.id, "serial": device.serial_number, "firmware": device.firmware,
                                    "family": device.family},
                            proposed_op=op, observation_fresh=fresh, backup_verified=verified_backup,
                            adapter_certified=certified, last_action_at=_utc(last.finished_at) if last else None,
                            actions_today=today, quality_degraded=quality_degraded)
    if not site_ok:
        res["decision"] = "DENY"
        res["guard_evaluation"].append({"guard": "site_policy_allows_value", "pass": False})
        res["denial_reason"] = ((res["denial_reason"] + "; ") if res["denial_reason"] else "") + "Value outside site policy."
    windows = []
    if sp and sp.window_start_hour is not None and sp.window_end_hour is not None:
        windows.append((sp.window_start_hour,sp.window_end_hour))
    if e and e.permitted_windows:
        windows.append((e.permitted_windows.get("start_hour"),e.permitted_windows.get("end_hour")))
    for lo,hi in windows:
        allowed = isinstance(lo,int) and isinstance(hi,int) and (lo<=t.hour<hi if lo<hi else t.hour>=lo or t.hour<hi)
        if not allowed:
            res["decision"]="DENY"
            res["guard_evaluation"].append({"guard":"maintenance_window_utc","pass":False})
            res["denial_reason"]=(res["denial_reason"] or "")+"; Outside approved UTC maintenance window."
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
    """Native ARM is the RF optimizer. Never rotate channels from cluster counts or a utilization sample."""
    from ..aruba.arm import assessments
    from .arm_measurements import snapshot
    measurement=snapshot(db,device.id)
    if "ap.radio.channel" not in (e.allowed_operation_types or []):
        return None
    options=e.value_constraints or {}
    for finding in assessments(measurement):
        p=finding.get("proposal")
        if not p:
            continue
        native=bool(p.get("arm_managed"))
        if not native and not options.get("allow_manual_channel_override",False):
            continue
        if native and options.get("prefer_native_arm",True) is False:
            continue
        op={"op":"ap.radio.channel","band":"2.4" if p["band"].startswith("2") else "5",
            "value":p["to"],"power":p.get("power"),"reason":finding["title"]}
        if native: op["arm_managed"]=True
        if op["value"] in options.get("excluded_channels",[]) and not native:
            continue
        sp=db.get(OptimizationSitePolicy,device.site_id) if device.site_id else None
        if sp and not native:
            allowed=sp.allowed_channels_5g if op["band"]=="5" else sp.allowed_channels_2g
            if allowed and op["value"] not in allowed:
                continue
            if op["power"] is None or not sp.min_tx_dbm<=op["power"]<=sp.max_tx_dbm:
                continue
        return op
    return None


def run_optimizer(db: Session, *, actor: str = "optimizer", device_id: str | None = None) -> dict:
    out={"evaluated":0,"executed":0,"pending_approval":0,"denied":0,"rolled_back_degraded":0,
         "no_adjustment_needed":0,"insufficient_evidence":0,"manual_review_required":0,
         "waiting_existing_plan":0,"shadow_evaluated":0,"failed":0,
         "armed_enrollments":0,"selected_for_review":0,"skipped_not_armed":0,
         "skipped_expired":0,"skipped_missing_device":0,"skipped_paused":0,"execution_failed":0}
    holder="optimizer-"+uuid.uuid4().hex
    if not X._lease(db,"optimizer-scheduler",holder,ttl=900):
        return {**out,"busy":1}
    try:
        from .ap_autodiscovery import _get,_put
        if device_id is not None:
            # Explicit target never advances the fleet cursor and never runs another AP.
            current = current_enrollment(db, device_id)
            if current is None or current.status != "ARMED":
                out["skipped_not_armed"] = 1
                return {**out, "enrollment_status": current.status if current else "NOT_ENROLLED"}
            enrollments = [current]
            out["armed_enrollments"] = 1
        else:
            enrollments=list(db.scalars(select(OptimizationEnrollment).where(
                OptimizationEnrollment.status=="ARMED").order_by(OptimizationEnrollment.created_at, OptimizationEnrollment.id).limit(2000)))
            out["armed_enrollments"] = len(enrollments)
            cursor=int((_get(db,"optimizer:cursor") or {}).get("offset",0))
            if enrollments:
                cursor=cursor%len(enrollments)
                ordered=enrollments[cursor:]+enrollments[:cursor]
                enrollments=ordered[:50]
                _put(db,"optimizer:cursor",{"offset":cursor+len(enrollments)},actor)
                db.commit()
        out["selected_for_review"] = len(enrollments)
        if not enrollments:
            return {**out, "reason": "No ARMED enrollments were eligible for this run."}
        for e in enrollments:
            did=e.device_id
            try:
                X._renew_lease(db,"optimizer-scheduler",holder,ttl=900)
                db.refresh(e)
                if e.status != "ARMED":
                    out["skipped_not_armed"] += 1
                    continue
                from .optimization_workflow import active as workflow_active
                if workflow_active(db, did):
                    out["waiting_existing_plan"] += 1
                    continue
                device=db.get(InmDevice,did)
                if device is None:
                    out["skipped_missing_device"] += 1
                    continue
                if _utc(e.expires_at_utc)<=datetime.now(timezone.utc):
                    e.status,e.status_reason="EXPIRED","EXPIRED"
                    out["skipped_expired"] += 1
                    db.commit()
                    continue
                out["evaluated"] += 1
                op=propose(db,device,e)
                if e.mode=="SHADOW":
                    from .ap_autodiscovery import _put
                    _put(db,"optimizer-analysis:"+device.id,{"at":datetime.now(timezone.utc).isoformat(),
                        "mode":"SHADOW","proposed_operation":op,"device_writes":0,
                        "outcome":"REVIEW_ADJUSTMENT" if op else "KEEP_ARM_OR_INSUFFICIENT_EVIDENCE"},actor)
                    out["shadow_evaluated"]+=1
                    continue
                out["rolled_back_degraded"]+=_check_outcome(db,device,e,actor)
                if out["rolled_back_degraded"] or e.status_reason=="QUALITY_DEGRADED_ROLLBACK_FAILED":
                    db.commit()
                    break
                if e.status!="ARMED":
                    out["skipped_paused"] += 1
                    continue
                existing=db.scalar(select(ChangePlan).where(ChangePlan.device_id==device.id,
                    ChangePlan.source.in_(("AUTO_OPTIMIZER","ARM_DIAGNOSIS")),
                    ChangePlan.state.in_(("DRAFT","PENDING_APPROVAL","APPROVED","EXECUTING"))).limit(1))
                if existing:
                    out["waiting_existing_plan"]+=1; continue
                if not op:
                    from ..aruba.arm import assessments
                    from .arm_measurements import snapshot
                    findings=assessments(snapshot(db,device.id))
                    if any(f["id"]=="arm_missing" for f in findings):
                        out["insufficient_evidence"]+=1
                    elif any(f["severity"] in ("high","medium") for f in findings):
                        out["manual_review_required"]+=1
                    else:
                        out["no_adjustment_needed"]+=1
                    continue
                clean={k:v for k,v in op.items() if k!="reason"}
                res,dec=evaluate(db,device,clean,actor=actor)
                if res["decision"]!="ALLOW":
                    out["denied"]+=1; continue
                operations=[CC.validate_operation(clean,family=device.family,
                    excluded_channels=set((e.value_constraints or {}).get("excluded_channels",[])))]
                latest=db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id==device.id)
                    .order_by(ConfigArtifact.created_at.desc()).limit(1))
                plan=ChangePlan(device_id=device.id,title="ARM optimization: "+op["reason"],operations=operations,
                    capability_report=[{"op":clean["op"],"capability":"CERTIFIED_WRITE"}],
                    expected_config_hash=latest.plaintext_sha256 if latest else None,
                    plan_hash=CC.plan_hash(device.id,operations,latest.plaintext_sha256 if latest else None),
                    firmware_at_request=device.firmware,rollback_plan={"strategy":"EXACT_EXECUTION_JOURNAL"},
                    source="AUTO_OPTIMIZER",requester="optimizer:"+e.requester_id,state="PENDING_APPROVAL")
                if e.standing_approval:
                    plan.state,plan.approver="APPROVED",e.independent_approver_id
                    plan.approved_at,plan.decision_note=datetime.now(timezone.utc),"Standing per-device policy approval"
                db.add(plan); db.flush()
                e.last_action_id=plan.id
                if plan.state=="APPROVED":
                    r=X.execute_plan(db,plan,actor=actor)
                    out["executed"]+=int(r.get("outcome")=="APPLIED_VERIFIED")
                    if r.get("outcome")!="APPLIED_VERIFIED":
                        out["execution_failed"] += 1
                        e.status="PAUSED"
                        e.status_reason="OPTIMIZER_EXECUTION_"+str(r.get("outcome") or r.get("decision") or "FAILED")
                    db.commit()
                    break
                else:
                    out["pending_approval"]+=1
                db.commit()
            except Exception as exc:
                db.rollback()
                from ..security.deps import audit
                audit(db,"optimizer_device_failed",username=actor,target=did,
                      detail=type(exc).__name__,success=False)
                db.commit()
                out["failed"]+=1
        return out
    finally:
        X._release(db,"optimizer-scheduler",holder)
        db.commit()


def _check_outcome(db: Session, device: InmDevice, e: OptimizationEnrollment, actor: str) -> int:
    """Use measured experience and the exact execution's journal; never guess a rollback channel."""
    from .runtime import telemetry_store
    if not e.last_action_id:
        return 0
    plan=db.get(ChangePlan,e.last_action_id)
    if plan is None or plan.state!="APPLIED_VERIFIED" or (plan.rollback_plan or {}).get("outcome_checked"):
        return 0
    execution=db.scalar(select(ChangeExecution).where(ChangeExecution.plan_id==plan.id,
        ChangeExecution.outcome=="APPLIED_VERIFIED").order_by(ChangeExecution.finished_at.desc()).limit(1))
    if execution is None or execution.finished_at is None:
        return 0
    t=datetime.now(timezone.utc)
    applied=_utc(execution.finished_at)
    if t-applied<timedelta(seconds=max(900,min(e.cooldown_seconds,3600))):
        return 0
    store=telemetry_store()
    if store is None:
        return 0
    rows=store.query(device_id=device.id,metric_name="experience_score",
        since=applied-timedelta(hours=1),limit=1000)
    def window(lo,hi,series=None):
        out=[]
        for r in (rows if series is None else series):
            ts=r.get("observed_at")
            if isinstance(ts,str): ts=datetime.fromisoformat(ts.replace("Z","+00:00"))
            if ts and lo<=_utc(ts)<hi:
                out.append(r)
        return out
    before,after=window(applied-timedelta(hours=1),applied),window(applied,t)
    result=L.evaluate_outcome(metric="experience_score",higher_is_better=True,
        before=before,after=after,action_applied=True)
    # Outcome observations must cover time, not just a burst of duplicate samples.
    def span(rows):
        times=[_utc(datetime.fromisoformat(r["observed_at"].replace("Z","+00:00"))) if isinstance(r["observed_at"],str)
               else _utc(r["observed_at"]) for r in rows]
        return (max(times)-min(times)).total_seconds() if len(times)>=2 else 0
    if result["status"]=="INSUFFICIENT_EVIDENCE" or span(before)<900 or span(after)<900:
        plan.rollback_plan={**(plan.rollback_plan or {}),"outcome":{**result,"status":"INSUFFICIENT_EVIDENCE"}}
        return 0
    counts=store.query(device_id=device.id,metric_name="client_count",since=applied-timedelta(hours=1),limit=1000)
    cb=[r["value"] for r in window(applied-timedelta(hours=1),applied,counts) if r.get("value") is not None]
    ca=[r["value"] for r in window(applied,t,counts) if r.get("value") is not None]
    if len(cb)<5 or len(ca)<5 or abs(sum(cb)/len(cb)-sum(ca)/len(ca))>max(2,0.2*sum(cb)/len(cb)):
        plan.rollback_plan={**(plan.rollback_plan or {}),"outcome":{**result,"status":"INSUFFICIENT_EVIDENCE","confounders":["CLIENT_POPULATION_UNMATCHED"]}}
        return 0
    degraded=result["status"]=="DEGRADED" and (result.get("quality_delta") or 0)<=-10
    plan.rollback_plan={**(plan.rollback_plan or {}),"outcome_checked":True,"outcome":result}
    if not degraded:
        return 0
    e.status,e.status_reason="PAUSED","QUALITY_DEGRADED"
    from ..database.models_remediation import ChangeRecoveryJournal
    from ..security.crypto import decrypt_secret
    journal=db.scalar(select(ChangeRecoveryJournal).where(ChangeRecoveryJournal.execution_id==execution.id))
    if journal is None or not execution.post_artifact_id:
        e.status_reason="QUALITY_DEGRADED_ROLLBACK_REQUIRES_REVIEW"; return 0
    payload=json.loads(decrypt_secret(journal.payload_enc))
    prior=payload.get("writer_checkpoint",{}).get("radio_overrides")
    operations=[]
    for original in plan.operations:
        band=original.get("band","5")
        previous=prior.get(band) if isinstance(prior,dict) else None
        if previous is None:
            e.status_reason="QUALITY_DEGRADED_ROLLBACK_REQUIRES_REVIEW"; return 0
        value,power=float(previous["channel"]),float(previous["power"])
        inverse={"op":"ap.radio.channel","band":band,"value":int(value),"power":power}
        if value==0 and power==0: inverse["arm_managed"]=True
        try: operations.append(CC.validate_operation(inverse,family=device.family))
        except CC.PolicyViolation:
            e.status_reason="QUALITY_DEGRADED_ROLLBACK_REQUIRES_REVIEW"; return 0
    post=db.get(ConfigArtifact,execution.post_artifact_id)
    if not post:
        return 0
    rb=ChangePlan(device_id=device.id,title="ARM rollback after measured client degradation",operations=operations,
        capability_report=[{"op":o["op"],"capability":"CERTIFIED_WRITE"} for o in operations],
        plan_hash=CC.plan_hash(device.id,operations,post.plaintext_sha256),
        expected_config_hash=post.plaintext_sha256,firmware_at_request=device.firmware,
        rollback_plan={"strategy":"EXACT_EXECUTION_JOURNAL","original_execution":execution.id},
        source="AUTO_OPTIMIZER_ROLLBACK",requester="optimizer:"+e.requester_id,state="APPROVED",
        approver=e.independent_approver_id,approved_at=t,decision_note="Per-device degradation rollback policy")
    db.add(rb); db.flush()
    outcome=X.execute_plan(db,rb,actor=actor)
    verified=outcome.get("outcome")=="APPLIED_VERIFIED"
    if not verified: e.status_reason="QUALITY_DEGRADED_ROLLBACK_FAILED"
    return int(verified)
