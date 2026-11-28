"""Durable per-AP optimization workflow. No write path outside the existing certified executor."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from ..database.models import AppSetting, User
from ..database.models_inm import InmDevice
from ..database.models_phases import ChangePlan, OptimizationEnrollment, ConfigArtifact
from ..domain import change_control as CC
from ..aruba.arm import assessments
from ..security.deps import audit
from . import ap_autodiscovery as AD, arm_measurements as ARM, optimizer as O, change_executor as X
from .phase_services import control
from .writers import WriterError

PREFIX = "opt-workflow:"
ACTIVE = {"QUEUED", "WAIT_ARM", "WAIT_ENROLLMENT", "WAIT_CHANGE", "MONITORING"}
ACTOR = "optimizer-workflow"


def now():
    return datetime.now(timezone.utc)


def state(db, device_id):
    return AD._get(db, PREFIX + device_id)


def active(db, device_id):
    return (state(db, device_id) or {}).get("status") in ACTIVE


def save(db, job, status, detail, **fields):
    job = {**job, **fields, "status": status, "detail": detail, "updated_at": now().isoformat()}
    if status == "BLOCKED" and job.get("plan_id"):
        # Retire only our unexecuted plan. A running execution is recovered by the certified engine.
        changed = db.execute(update(ChangePlan).where(ChangePlan.id == job["plan_id"],
            ChangePlan.device_id == job["device_id"], ChangePlan.state.in_(("PENDING_APPROVAL", "APPROVED")))
            .values(state="REJECTED", decision_note="Workflow stopped: " + str(detail)[:500], revision=ChangePlan.revision+1))
        if changed.rowcount:
            audit(db, "optimization_workflow_plan_retired", username=ACTOR, target=job["device_id"], detail="Stopped unexecuted workflow plan; no device writes.")
    if status == "WAIT_ENROLLMENT" or (status == "WAIT_CHANGE" and job.get("plan_id") and db.get(ChangePlan, job["plan_id"]).state == "PENDING_APPROVAL"):
        from .notifications import enqueue_optimization_approval
        job["approval_notification"] = enqueue_optimization_approval(db, job)
    AD._put(db, PREFIX + job["device_id"], job, ACTOR)
    db.commit()
    return job


def begin(db, device, user, enrollment=None, *, analyze_only=False):
    """Caller holds the workflow lease and explicitly requested this exact device."""
    old = state(db, device.id)
    if old and old["status"] in ACTIVE:
        return old
    job = {"id": str(uuid.uuid4()), "device_id": device.id, "requester": user.username,
           "serial": device.serial_number, "firmware": device.firmware, "site_id": device.site_id,
           "enrollment_id": enrollment.id if enrollment else None, "analyze_only": analyze_only, "created_at": now().isoformat(),
           "backup_artifact_id": None, "plan_id": None, "findings": [], "device_writes": 0}
    audit(db, "optimization_workflow_requested", username=user.username, target=device.id,
          detail="Explicit single-device request; preparation is read-only.")
    return save(db, job, "QUEUED", "Waiting for read-only ARM collection." if analyze_only else "Waiting for worker to verify device and capture configuration.")


def _principal(db, name, roles):
    u = db.scalar(select(User).where(User.username == name))
    return bool(u and u.is_active and not u.must_change_password and u.role in roles)


def _backup(db, device):
    holder = uuid.uuid4().hex
    if not X._lease(db, device.id, holder):
        raise WriterError("DEVICE_BUSY", "Device configuration operation already running.")
    try:
        w, env = X.reader_for(db, device)
        try:
            w.open()
            if env == "HARDWARE":
                X.verify_hardware_identity(device, w)
            content = w.backup()
            if not content:
                raise WriterError("BACKUP_UNVERIFIED", "Empty configuration.")
        finally:
            w.close()
        X._renew_lease(db, device.id, holder)
        a = X.seal_artifact(db, device, content, trigger="MANUAL", actor=ACTOR,
                            source="DEVICE_READ" if env == "HARDWARE" else "EMULATOR")
        return a.id
    except Exception:
        db.rollback()
        raise
    finally:
        X._release(db, device.id, holder)
        db.commit()


def _fresh_measurement(db, job, device):
    m = ARM.snapshot(db, device.id)
    if m.get("collection_state") == "FAILED":
        return None, "ARM collection failed: " + ", ".join(sorted((m.get("errors") or {}).keys()))
    if m.get("collection_state") != "SUCCEEDED" or not m.get("observed_at"):
        return None, None
    try:
        observed = datetime.fromisoformat(m["observed_at"].replace("Z", "+00:00"))
        requested = datetime.fromisoformat(job["arm_requested_at"])
        if observed < requested:
            return None, None
    except (KeyError, ValueError, TypeError):
        return None, "ARM measurement timestamp is invalid."
    if m.get("freshness") != "FRESH" or not (m.get("identity") or {}).get("verified"):
        return None, "Fresh target-verified ARM measurements are required."
    return m, None


def _queue_arm(db, job, device, status):
    requested = now().isoformat()
    ARM.request(db, device.id, ACTOR)
    return save(db, job, status, "Collecting fresh read-only ARM measurements.", arm_requested_at=requested)


def _plan(db, job, device, enrollment, op):
    # Serialize against other plan creation paths and never adopt an unrelated plan.
    db.scalar(select(InmDevice).where(InmDevice.id == device.id).with_for_update())
    existing = db.scalar(select(ChangePlan).where(ChangePlan.device_id == device.id,
        ChangePlan.state.in_(("DRAFT", "PENDING_APPROVAL", "APPROVED", "EXECUTING"))).limit(1))
    if existing:
        return save(db, job, "BLOCKED", "Another configuration plan is already pending.", conflicting_plan_id=existing.id)
    ops = [CC.validate_operation(op, family=device.family, excluded_channels=set((enrollment.value_constraints or {}).get("excluded_channels", [])))]
    X.validate_site_rf_policy(db, device, ops)
    artifact = db.get(ConfigArtifact, job["backup_artifact_id"])
    X._artifact_bytes(db, artifact.id, device)
    latest = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == device.id)
                       .order_by(ConfigArtifact.created_at.desc()).limit(1))
    if not latest or latest.plaintext_sha256 != artifact.plaintext_sha256:
        return save(db, job, "BLOCKED", "Configuration evidence changed; start a fresh optimization request.")
    p = ChangePlan(device_id=device.id, title="One-click ARM optimization", operations=ops,
                   capability_report=[{"op": op["op"], "capability": X.certification_map(db, device).get(op["op"], "UNCERTIFIED")}],
                   expected_config_hash=artifact.plaintext_sha256,
                   plan_hash=CC.plan_hash(device.id, ops, artifact.plaintext_sha256),
                   firmware_at_request=device.firmware, rollback_plan={"strategy": "EXACT_EXECUTION_JOURNAL", "workflow_id": job["id"]},
                   source="AUTO_OPTIMIZER", requester=job["requester"], state="PENDING_APPROVAL")
    # A standing policy is never created or broadened by the worker.
    if enrollment.standing_approval and enrollment.independent_approver_id.casefold() != job["requester"].casefold():
        p.state, p.approver, p.approved_at = "APPROVED", enrollment.independent_approver_id, now()
        p.decision_note = "Existing independent standing per-device policy"
    db.add(p)
    db.flush()
    enrollment.last_action_id = p.id
    return save(db, job, "WAIT_CHANGE", "Waiting for independent change approval." if p.state == "PENDING_APPROVAL" else "Standing approval verified; rechecking execution guards.",
                plan_id=p.id, plan_hash=p.plan_hash, proposed_operation=op)


def advance(db, job):
    device = db.get(InmDevice, job["device_id"])
    if not device or device.source_type != "REAL" or device.family != "access_point" or device.lifecycle == "merged":
        return save(db, job, "BLOCKED", "Managed physical AP is no longer available.")
    if (device.serial_number, device.firmware, device.site_id) != (job["serial"], job["firmware"], job["site_id"]):
        return save(db, job, "BLOCKED", "AP identity, firmware or site changed.")
    if not _principal(db, job["requester"], {"admin"}):
        return save(db, job, "BLOCKED", "Requesting administrator is no longer authorized.")
    e = db.get(OptimizationEnrollment, job["enrollment_id"]) if job.get("enrollment_id") else None
    if job["status"] != "MONITORING" and now() - datetime.fromisoformat(job["created_at"]) > timedelta(days=1):
        return save(db, job, "BLOCKED", "Optimization request expired; start a new request.")
    if job["status"] == "QUEUED":
        artifact_id = None if job.get("analyze_only") else _backup(db, device)
        return _queue_arm(db, {**job, "backup_artifact_id": artifact_id}, device, "WAIT_ARM")
    if job["status"] == "WAIT_ENROLLMENT":
        if not e or e.status not in {"PENDING_APPROVAL", "ARMED"}:
            return save(db, job, "BLOCKED", "Enrollment revoked, paused, expired or replaced.")
        if O._utc(e.expires_at_utc) <= now():
            return save(db, job, "BLOCKED", "Enrollment expired.")
        if e.status == "PENDING_APPROVAL":
            return job
        # Approval can arrive hours later. Always collect a fresh measurement before planning.
        return _queue_arm(db, job, device, "WAIT_ARM")
    if job["status"] == "WAIT_ARM":
        m, error = _fresh_measurement(db, job, device)
        if error:
            return save(db, job, "BLOCKED", error)
        if not m:
            if now() - datetime.fromisoformat(job["arm_requested_at"]) > timedelta(minutes=15):
                return save(db, job, "BLOCKED", "ARM worker did not return fresh measurements within 15 minutes.")
            return job
        findings = assessments(m)
        job = {**job, "findings": findings, "arm_observed_at": m["observed_at"]}
        if any(f["id"] == "arm_missing" for f in findings):
            return save(db, job, "BLOCKED", "Insufficient ARM evidence.")
        if not any(f.get("proposal") for f in findings):
            return save(db, job, "MANUAL_REVIEW" if any(f["severity"] in ("high", "medium") for f in findings) else "NO_CHANGE",
                        "No safe automatic adjustment supported by the measurements.")
        if not e:
            return save(db, job, "ANALYSIS_ONLY", "Recommendations available; read-only analysis complete." if job.get("analyze_only") else "Recommendations available; automatic optimization is not enrolled or enabled.")
        if e.mode != "DEVICE_OPT_IN" or e.status not in {"PENDING_APPROVAL", "ARMED"}:
            return save(db, job, "BLOCKED", "Enrollment is not an active DEVICE_OPT_IN request.")
        if e.status == "PENDING_APPROVAL":
            return save(db, job, "WAIT_ENROLLMENT", "Backup and ARM analysis complete. Independent enrollment approval required.")
        if not _principal(db, e.independent_approver_id, {"admin", "approver"}):
            return save(db, job, "BLOCKED", "Enrollment approver is no longer authorized.")
        candidate = O.propose(db, device, e)
        if not candidate:
            return save(db, job, "MANUAL_REVIEW", "Recommendations are outside the authorized automatic policy.")
        op = {k: v for k, v in candidate.items() if k != "reason"}
        result, _ = O.evaluate(db, device, op, actor=ACTOR)
        if result["decision"] != "ALLOW":
            return save(db, job, "BLOCKED", result["denial_reason"], guards=result["guard_evaluation"])
        return _plan(db, job, device, e, op)
    p = db.get(ChangePlan, job.get("plan_id"))
    if not p or p.device_id != device.id or p.plan_hash != job.get("plan_hash"):
        return save(db, job, "BLOCKED", "The workflow's exact change plan is missing or changed.")
    if job["status"] == "WAIT_CHANGE":
        if p.state == "PENDING_APPROVAL":
            return job
        if p.state == "EXECUTING":
            return job
        if p.state == "APPLIED_VERIFIED":
            return save(db, job, "MONITORING", "Configuration applied and readback verified.", device_writes=None)
        if p.state != "APPROVED":
            return save(db, job, "BLOCKED", "Change plan is " + p.state)
        if not e or e.status != "ARMED" or e.last_action_id != p.id:
            return save(db, job, "BLOCKED", "Enrollment revoked, paused or replaced.")
        if not _principal(db, p.approver, {"admin", "approver"}) or p.approver.casefold() == p.requester.casefold():
            return save(db, job, "BLOCKED", "Independent approval no longer valid.")
        # A new post-approval measurement is mandatory, even if the cached one is fresh.
        if not job.get("approval_refresh_requested"):
            return _queue_arm(db, {**job, "approval_refresh_requested": True}, device, "WAIT_CHANGE")
        m, error = _fresh_measurement(db, job, device)
        if not error and not m and now() - datetime.fromisoformat(job["arm_requested_at"]) < timedelta(minutes=15):
            return job
        if error or not m:
            return save(db, job, "BLOCKED", error or "Fresh ARM measurements unavailable.")
        candidate = O.propose(db, device, e)
        if not candidate or [{k:v for k,v in candidate.items() if k != "reason"}] != p.operations:
            return save(db, job, "BLOCKED", "Live ARM evidence changed; a new reviewed plan is required.")
        result, _ = O.evaluate(db, device, p.operations[0], actor=ACTOR)
        if result["decision"] != "ALLOW":
            return save(db, job, "BLOCKED", result["denial_reason"], guards=result["guard_evaluation"])
        result = X.execute_plan(db, p, actor=ACTOR)
        return save(db, job, "MONITORING" if result.get("outcome") == "APPLIED_VERIFIED" else "BLOCKED",
                    "Configuration applied and readback verified; monitoring client experience." if result.get("outcome") == "APPLIED_VERIFIED" else (result.get("denial_reason") or result.get("outcome") or "Execution failed."),
                    execution_outcome=result.get("outcome"), device_writes=result.get("device_writes"))
    if job["status"] == "MONITORING":
        if not e or e.last_action_id != p.id or e.status != "ARMED" or O._utc(e.expires_at_utc) <= now() or not _principal(db, e.independent_approver_id, {"admin","approver"}) or control(db).emergency_stop or not O.feature_enabled():
            return save(db, job, "MANUAL_REVIEW", "Automatic monitoring/rollback authorization no longer active.")
        rolled_back = O._check_outcome(db, device, e, ACTOR)
        outcome = (p.rollback_plan or {}).get("outcome")
        if rolled_back:
            return save(db, job, "ROLLED_BACK", "Degradation detected; restoration verified.", outcome=outcome)
        if e.status == "PAUSED":
            return save(db, job, "MANUAL_REVIEW", e.status_reason or "Optimization paused.", outcome=outcome)
        if (p.rollback_plan or {}).get("outcome_checked"):
            return save(db, job, "COMPLETE", "Post-change experience evaluated.", outcome=outcome)
        if now() - datetime.fromisoformat(job["created_at"]) > timedelta(days=2):
            return save(db, job, "MANUAL_REVIEW", "Insufficient post-change experience evidence.", outcome=outcome)
    return job


def tick(session_factory, limit=4):
    with session_factory() as db:
        keys = [r.key for r in db.scalars(select(AppSetting).where(AppSetting.key.startswith(PREFIX)).order_by(AppSetting.updated_at))
                if r.value.get("status") in ACTIVE][:limit]
    processed = 0
    for key in keys:
        with session_factory() as db:
            lock = key
            holder = uuid.uuid4().hex
            if not X._lease(db, lock, holder, ttl=900):
                continue
            try:
                job = AD._get(db, key)
                if job and job["status"] in ACTIVE:
                    result = advance(db, job)
                    if result is job:
                        # Rotate waiting records so one approval does not starve other APs.
                        db.execute(update(AppSetting).where(AppSetting.key == key).values(updated_at=now()))
                        db.commit()
                    processed += 1
            except Exception as exc:
                db.rollback()
                job = AD._get(db, key)
                if job and job["status"] in ACTIVE:
                    # Never replay a potentially interrupted write; the certified executor owns recovery.
                    save(db, job, "BLOCKED", "Workflow interrupted: " + (exc.category if isinstance(exc, WriterError) else type(exc).__name__))
            finally:
                X._release(db, lock, holder)
                db.commit()
    return {"processed": processed}
