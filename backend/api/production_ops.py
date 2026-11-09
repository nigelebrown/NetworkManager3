"""Production read/write operations: hardware certification, SSH host-key pinning, capture-from-device,
vault schedules, restore plans and replacement restore plans."""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_phases import (ChangePlan, ConfigArtifact, DeviceAccess, DeviceCertification,
                                      ReplacementPlan, VaultSchedule)
from ..database.session import get_db
from ..domain import change_control as CC
from ..infrastructure import change_executor as X
from ..infrastructure.writers import WriterError
from ..security.deps import audit, require_any_role, require_role
from ._phase_common import iso, now, real_device

router = APIRouter(prefix="/api/v1", tags=["production-read-write"])


# ---- certification --------------------------------------------------------------------------
class CertInput(BaseModel):
    device_id: str
    lab_device_acknowledged: bool = False
    test_port: str | None = None
    test_vlan: int = Field(default=4090, ge=2, le=4094)
    lag_ports: list[str] | None = None
    test_channel: int = 44
    test_ssid: str | None = None


def _cert_public(c: DeviceCertification) -> dict:
    return {"id": c.id, "adapter_key": c.adapter_key, "model_family": c.model_family, "firmware_train": c.firmware_train,
            "operation": c.operation, "status": c.status, "environment": c.environment, "run_id": c.run_id,
            "device_id": c.device_id, "evidence": c.evidence, "tested_by": c.tested_by, "tested_at": iso(c.tested_at),
            "approved_by": c.approved_by, "approved_at": iso(c.approved_at)}


@router.post("/certification/run", status_code=201)
def certify(body: CertInput, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin"))):
    d = real_device(db, body.device_id)
    if not body.lab_device_acknowledged:
        raise HTTPException(422, "Certification writes test changes to the device. Confirm it is an authorised lab device.")
    if d.family == "switch" and not body.test_port:
        raise HTTPException(422, "Choose an unused test port for switch certification.")
    try:
        res = X.run_certification(db, d, actor=user.username, test=body.model_dump())
    except WriterError as exc:
        audit(db, "certification_refused", username=user.username, target=d.id, detail=str(exc), request=request,
              success=False)
        db.commit()
        raise HTTPException(409, f"{exc.category}: {exc}") from None
    passed = sum(r["passed"] for r in res["results"])
    audit(db, "certification_run", username=user.username, target=d.id,
          detail=f"run={res['run_id']} env={res['environment']} {res['model_family']} fw {res['firmware_train']} "
                 f"passed={passed}/{len(res['results'])}; awaiting independent approval", request=request)
    return res


@router.get("/certification")
def certifications(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return [_cert_public(c) for c in db.scalars(select(DeviceCertification).order_by(
        DeviceCertification.model_family, DeviceCertification.firmware_train, DeviceCertification.operation))]


@router.post("/certification/{cert_id}/{action}")
def decide_cert(cert_id: str, action: str, request: Request, db: Session = Depends(get_db),
                user: User = Depends(require_any_role("approver", "admin"))):
    c = db.get(DeviceCertification, cert_id)
    if c is None:
        raise HTTPException(404, "Certification not found.")
    if action == "approve":
        if c.status != "TESTED_PENDING_APPROVAL":
            raise HTTPException(409, f"Certification is {c.status}.")
        if c.tested_by.casefold() == user.username.casefold():
            raise HTTPException(403, "Certification needs an approver independent of the tester.")
        c.status, c.approved_by, c.approved_at = "CERTIFIED_WRITE", user.username, now()
    elif action == "revoke":
        c.status, c.approved_by, c.approved_at = "REVOKED", user.username, now()
    else:
        raise HTTPException(404, "Unknown action.")
    audit(db, f"certification_{action}", username=user.username, target=c.id,
          detail=f"{c.model_family} fw {c.firmware_train} {c.operation} env={c.environment} -> {c.status}", request=request)
    return _cert_public(c)


@router.get("/devices/{device_id}/certification")
def device_cert(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    d = real_device(db, device_id)
    return {"device_id": d.id, "environment": X.environment(d), "model_family": X.model_family(d.model),
            "firmware_train": X.firmware_train(d.firmware), "operations": X.certification_map(db, d)}


# ---- SSH host key pinning -------------------------------------------------------------------------
class PinInput(BaseModel):
    fingerprint: str = Field(min_length=10, max_length=80, pattern=r"^SHA256:[A-Za-z0-9+/=]+$")
    vc_host: str | None = None


@router.post("/devices/{device_id}/ssh-key/scan")
def scan_key(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    d = real_device(db, device_id)
    if d.source_type == "DEMO":
        return {"fingerprint": "SHA256:emulated", "environment": "EMULATED"}
    import ipaddress

    from ..api.quick_devices import _permit_probe
    from ..aruba.errors import ArubaError
    from ..aruba.ssh import scan_host_key
    if not d.mgmt_addresses:
        raise HTTPException(409, "No management address recorded.")
    _permit_probe(ipaddress.IPv4Address(str(d.mgmt_addresses[0])))
    try:
        return scan_host_key(str(d.mgmt_addresses[0])) | {"environment": "HARDWARE"}
    except ArubaError as exc:
        raise HTTPException(502, str(exc)[:200]) from None


@router.post("/devices/{device_id}/ssh-key/pin")
def pin_key(device_id: str, body: PinInput, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin"))):
    d = real_device(db, device_id)
    a = db.get(DeviceAccess, d.id) or DeviceAccess(device_id=d.id, ssh_fingerprint=body.fingerprint, pinned_by=user.username)
    a.ssh_fingerprint, a.vc_host, a.pinned_by, a.pinned_at = body.fingerprint, body.vc_host, user.username, now()
    db.add(a)
    audit(db, "ssh_host_key_pinned", username=user.username, target=d.id, detail=body.fingerprint, request=request)
    return {"device_id": d.id, "fingerprint": a.ssh_fingerprint, "vc_host": a.vc_host}


# ---- capture from device / schedules --------------------------------------------------------------
@router.post("/vault/devices/{device_id}/capture-from-device", status_code=201)
def capture_from_device(device_id: str, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require_role("admin"))):
    """Read the FULL running configuration from the device (read-only) and seal it in the vault."""
    d = real_device(db, device_id)
    try:
        w, env = X.writer_for(db, d)
        w.open()
        try:
            content = w.backup()
        finally:
            w.close()
        a = X.seal_artifact(db, d, content, trigger="MANUAL", actor=user.username,
                            source="EMULATOR" if env == "EMULATED" else "DEVICE_READ")
    except WriterError as exc:
        audit(db, "vault_capture_failed", username=user.username, target=d.id, detail=f"{exc.category}: {exc}",
              request=request, success=False)
        db.commit()
        raise HTTPException(502 if exc.category in ("UNREACHABLE", "CONNECTION_LOST") else 409,
                            f"{exc.category}: {exc}") from None
    audit(db, "vault_capture_from_device", username=user.username, target=d.id,
          detail=f"artifact={a.id} sha256={a.plaintext_sha256[:16]} env={env}", request=request)
    return {"artifact_id": a.id, "sha256": a.plaintext_sha256, "size": a.size, "environment": env}


class ScheduleInput(BaseModel):
    interval_hours: int = Field(default=24, ge=1, le=24 * 30)


@router.post("/vault/devices/{device_id}/schedule")
def schedule(device_id: str, body: ScheduleInput, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_role("admin"))):
    d = real_device(db, device_id)
    s = db.get(VaultSchedule, d.id) or VaultSchedule(device_id=d.id, created_by=user.username)
    s.interval_hours, s.next_due_at = body.interval_hours, now()
    db.add(s)
    audit(db, "vault_schedule_set", username=user.username, target=d.id, detail=f"every {body.interval_hours}h",
          request=request)
    return {"device_id": d.id, "interval_hours": s.interval_hours, "next_due_at": iso(s.next_due_at)}


@router.get("/vault/schedules")
def schedules(db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    return [{"device_id": s.device_id, "interval_hours": s.interval_hours, "next_due_at": iso(s.next_due_at),
             "last_run_at": iso(s.last_run_at), "last_result": s.last_result} for s in db.scalars(select(VaultSchedule))]


def _reconcile(db: Session, d, text: str) -> None:
    """C08 continuous reconciliation: template drift and critical best-practice failures raise alerts."""
    from ..database.models_templates import InmConfigTemplate
    from ..domain import best_practices as BP
    from ..domain.template_inheritance import preview
    from ..infrastructure.alert_engine import apply as apply_alerts
    tpls = list(db.scalars(select(InmConfigTemplate).where(InmConfigTemplate.enabled.is_(True),
                                                          InmConfigTemplate.family == d.family)))
    pv = preview(d, tpls)
    drift = BP.intended_vs_installed(d.family, pv["effective"] if pv["templates"] else None, text)
    comp = BP.evaluate(d.family, text)
    apply_alerts(db, d, {"template_drift_count": float(len(drift["differences"])),
                         "critical_rule_failures": float(sum(1 for r in comp["results"]
                                                             if r["status"] == "FAIL" and r["severity"] == "critical"))})


def run_due_captures(db: Session, *, actor: str = "vault-scheduler") -> dict:
    """Worker/scheduler entry: capture every due device configuration (read-only)."""
    from ..database.models_inm import InmDevice
    t, done, failed = now(), 0, 0
    for s in db.scalars(select(VaultSchedule)):
        due = s.next_due_at if s.next_due_at is None or s.next_due_at.tzinfo else s.next_due_at.replace(tzinfo=t.tzinfo)
        if due is not None and due > t:
            continue
        d = db.get(InmDevice, s.device_id)
        s.last_run_at, s.next_due_at = t, t + timedelta(hours=s.interval_hours)
        if d is None:
            continue
        try:
            w, env = X.writer_for(db, d)
            w.open()
            try:
                content = w.backup()
            finally:
                w.close()
            latest = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == d.id)
                               .order_by(ConfigArtifact.created_at.desc()).limit(1))
            import hashlib
            if latest is not None and latest.plaintext_sha256 == hashlib.sha256(content).hexdigest():
                s.last_result = "UNCHANGED"
            else:
                X.seal_artifact(db, d, content, trigger="SCHEDULE", actor=actor,
                                source="EMULATOR" if env == "EMULATED" else "DEVICE_READ")
                s.last_result = "CAPTURED" if latest is None else "CAPTURED_DRIFT_DETECTED"
            _reconcile(db, d, content.decode(errors="replace"))
            done += 1
        except WriterError as exc:
            s.last_result = f"FAILED {exc.category}"[:120]
            failed += 1
    db.flush()
    return {"captured": done, "failed": failed}


# ---- restore plans ---------------------------------------------------------------------------------
class RestorePlanInput(BaseModel):
    target_device_id: str | None = None
    title: str | None = None


def _restore_plan(db: Session, artifact: ConfigArtifact, target, user: User, title: str) -> ChangePlan:
    from ..domain import vault as V
    compat = V.restore_compatibility(artifact=artifact.manifest, target={
        "vendor": target.vendor, "model": target.model, "firmware": target.firmware,
        "compatible_models": [artifact.model or ""] if X.model_family(artifact.model) == X.model_family(target.model) else []})
    if not compat["compatible"]:
        raise HTTPException(422, "Incompatible restore: " + "; ".join(compat["reasons"]))
    ops = [{"op": "config.restore", "artifact_id": artifact.id}]
    latest = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == target.id)
                       .order_by(ConfigArtifact.created_at.desc()).limit(1))
    p = ChangePlan(device_id=target.id, title=title, operations=ops,
                   capability_report=[{"op": "config.restore", "capability": X.certification_map(db, target).get(
                       "config.restore", "UNCERTIFIED")}],
                   plan_hash=CC.plan_hash(target.id, ops, latest.plaintext_sha256 if latest else None),
                   expected_config_hash=latest.plaintext_sha256 if latest else None, firmware_at_request=target.firmware,
                   rollback_plan={"strategy": "DEVICE_CHECKPOINT"}, state="PENDING_APPROVAL", requester=user.username)
    db.add(p)
    db.flush()
    return p


@router.post("/vault/artifacts/{artifact_id}/restore-plan", status_code=201)
def restore_plan(artifact_id: str, body: RestorePlanInput, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("operator"))):
    a = db.get(ConfigArtifact, artifact_id)
    if a is None:
        raise HTTPException(404, "Artifact not found.")
    target = real_device(db, body.target_device_id or a.device_id)
    p = _restore_plan(db, a, target, user, body.title or f"Restore configuration {a.plaintext_sha256[:10]}")
    audit(db, "restore_plan_created", username=user.username, target=target.id, detail=f"plan={p.id} artifact={a.id}",
          request=request)
    return {"plan_id": p.id, "state": p.state}


@router.post("/replacements/{replacement_id}/restore-plan", status_code=201)
def replacement_restore(replacement_id: str, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(require_role("admin"))):
    r = db.get(ReplacementPlan, replacement_id)
    if r is None:
        raise HTTPException(404, "Replacement plan not found.")
    if r.state != "LAB_VALIDATION_REQUIRED" or not r.artifact_id:
        raise HTTPException(409, "Replacement is blocked; resolve the blockers first.")
    a = db.get(ConfigArtifact, r.artifact_id)
    target = real_device(db, r.new_device_id)
    p = _restore_plan(db, a, target, user, f"Replacement restore onto {target.hostname or target.serial_number}")
    r.state = "RESTORE_PLANNED"
    audit(db, "replacement_restore_planned", username=user.username, target=target.id,
          detail=f"replacement={r.id} plan={p.id}", request=request)
    return {"plan_id": p.id, "replacement_state": r.state}


# ---- switch faceplate, management authority ---------------------------------------------------
@router.get("/devices/{device_id}/ports")
def ports(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..database.models_phases import DevicePortSnapshot
    d = real_device(db, device_id)
    s = db.get(DevicePortSnapshot, d.id)
    if s is None:
        return {"device_id": d.id, "collected_at": None, "ports": [], "hardware": {},
                "note": "No port data yet. Create a collection plan with the 'interfaces' metric family."}
    return {"device_id": d.id, "collected_at": iso(s.collected_at), "environment": s.environment,
            "ports": s.ports, "hardware": s.hardware}


@router.get("/devices/{device_id}/authority")
def authority(device_id: str, request: Request, db: Session = Depends(get_db),
              user: User = Depends(require_role("operator"))):
    d = real_device(db, device_id)
    try:
        w, env = X.writer_for(db, d)
        w.open()
        try:
            res = w.management_authority()
        finally:
            w.close()
    except WriterError as exc:
        raise HTTPException(409 if exc.category in ("PRECONDITION", "TARGET_NOT_ALLOWED") else 502,
                            f"{exc.category}: {exc}") from None
    audit(db, "management_authority_checked", username=user.username, target=d.id,
          detail=f"{res['authority']} env={env}", request=request)
    return {"device_id": d.id, "environment": env, **{k: v for k, v in res.items() if k != "raw"}}


# ---- INM alerts, thresholds, maintenance windows ---------------------------------------------------
def _alert_public(a) -> dict:
    return {"id": a.id, "device_id": a.device_id, "site_id": a.site_id, "rule_id": a.rule_id, "severity": a.severity,
            "message": a.message, "value": a.value, "threshold": a.threshold, "state": a.state, "suppressed": a.suppressed,
            "opened_at": iso(a.opened_at), "last_seen_at": iso(a.last_seen_at), "resolved_at": iso(a.resolved_at),
            "acked_by": a.acked_by, "source_type": a.source_type, "entity": a.entity,
            "correlation_id": a.correlation_id, "event_id": a.event_id}


@router.get("/alerts")
def alerts(state: str = "OPEN", db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..database.models_phases import InmAlert
    q = select(InmAlert).order_by(InmAlert.opened_at.desc()).limit(500)
    if state != "ALL":
        q = q.where(InmAlert.state.in_(("OPEN", "ACKNOWLEDGED") if state == "OPEN" else (state,)))
    return [_alert_public(a) for a in db.scalars(q)]


@router.post("/alerts/{alert_id}/ack")
def ack(alert_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    from ..database.models_phases import InmAlert
    a = db.get(InmAlert, alert_id)
    if a is None:
        raise HTTPException(404, "Alert not found.")
    a.state, a.acked_by = "ACKNOWLEDGED", user.username
    audit(db, "inm_alert_acknowledged", username=user.username, target=a.id, request=request)
    return _alert_public(a)


@router.get("/alert-rules")
def alert_rules(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..database.models_phases import AlertPolicy
    from ..domain.alert_rules import CATALOGUE
    pol = [{"scope": p.scope, "rule_id": p.rule_id, "threshold": p.threshold, "severity": p.severity,
            "enabled": p.enabled} for p in db.scalars(select(AlertPolicy))]
    return {"catalogue": [r.__dict__ for r in CATALOGUE], "policies": pol}


class PolicyInput(BaseModel):
    scope: str = Field(default="global", max_length=36)
    threshold: float
    severity: str = Field(pattern="^(critical|major|minor|info)$")
    enabled: bool = True


@router.put("/alert-rules/{rule_id}")
def put_rule(rule_id: str, body: PolicyInput, request: Request, db: Session = Depends(get_db),
             user: User = Depends(require_role("admin"))):
    from ..database.models_inm import InmSite
    from ..database.models_phases import AlertPolicy
    from ..domain.alert_rules import CATALOGUE
    if rule_id not in {r.id for r in CATALOGUE}:
        raise HTTPException(404, "Unknown rule.")
    if body.scope != "global" and db.get(InmSite, body.scope) is None:
        raise HTTPException(422, "Scope must be 'global' or a site id.")
    p = db.scalar(select(AlertPolicy).where(AlertPolicy.scope == body.scope, AlertPolicy.rule_id == rule_id)) \
        or AlertPolicy(scope=body.scope, rule_id=rule_id, threshold=body.threshold, severity=body.severity,
                       updated_by=user.username)
    p.threshold, p.severity, p.enabled, p.updated_by = body.threshold, body.severity, body.enabled, user.username
    db.add(p)
    audit(db, "alert_policy_set", username=user.username, target=f"{body.scope}:{rule_id}",
          detail=f"threshold={body.threshold} severity={body.severity} enabled={body.enabled}", request=request)
    return {"scope": body.scope, "rule_id": rule_id, "threshold": p.threshold, "severity": p.severity, "enabled": p.enabled}


class WindowInput(BaseModel):
    site_id: str | None = None
    device_id: str | None = None
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(min_length=3, max_length=200)


@router.get("/maintenance-windows")
def windows(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..database.models_phases import MaintenanceWindow
    return [{"id": w.id, "site_id": w.site_id, "device_id": w.device_id, "starts_at": iso(w.starts_at),
             "ends_at": iso(w.ends_at), "reason": w.reason, "created_by": w.created_by}
            for w in db.scalars(select(MaintenanceWindow).order_by(MaintenanceWindow.starts_at.desc()).limit(200))]


@router.post("/maintenance-windows", status_code=201)
def add_window(body: WindowInput, request: Request, db: Session = Depends(get_db),
               user: User = Depends(require_role("admin"))):
    from ..database.models_phases import MaintenanceWindow
    from ._phase_common import utc
    if utc(body.ends_at) <= utc(body.starts_at):
        raise HTTPException(422, "Window must end after it starts.")
    w = MaintenanceWindow(site_id=body.site_id, device_id=body.device_id, starts_at=utc(body.starts_at),
                          ends_at=utc(body.ends_at), reason=body.reason, created_by=user.username)
    db.add(w)
    db.flush()
    audit(db, "maintenance_window_created", username=user.username, target=w.site_id or w.device_id or "all",
          detail=f"{iso(w.starts_at)}..{iso(w.ends_at)} {body.reason}", request=request)
    return {"id": w.id}


# ---- E2E-14 durable change recovery ------------------------------------------------------------
@router.get("/recovery/journal")
def recovery_journal(state: str | None = None, db: Session = Depends(get_db),
                     user: User = Depends(require_any_role("admin", "auditor", "approver", "operator"))):
    from ..database.models_remediation import ChangeRecoveryJournal
    from ..infrastructure import recovery as RC
    RC.sweep(db)
    q = select(ChangeRecoveryJournal).order_by(ChangeRecoveryJournal.created_at.desc()).limit(200)
    if state:
        q = q.where(ChangeRecoveryJournal.state == state)
    return [RC.public(j) for j in db.scalars(q)]


@router.post("/recovery/journal/{journal_id}/recover")
def recovery_run(journal_id: str, request: Request, db: Session = Depends(get_db),
                 user: User = Depends(require_role("admin"))):
    from ..database.models_remediation import ChangeRecoveryJournal
    from ..infrastructure import recovery as RC
    j = db.get(ChangeRecoveryJournal, journal_id)
    if j is None:
        raise HTTPException(404, "Journal entry not found.")
    if X.control(db).emergency_stop:
        raise HTTPException(409, "Emergency stop is active.")
    try:
        out = RC.recover(db, j, actor=user.username)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    audit(db, "change_recovery_" + ("succeeded" if out["recovered"] else "failed"), username=user.username,
          target=j.device_id, detail=f"journal={j.id} plan={j.plan_id} steps={out['steps']}", request=request,
          success=out["recovered"])
    return out


class ResolveInput(BaseModel):
    note: str = Field(min_length=10, max_length=400)


@router.post("/recovery/journal/{journal_id}/resolve")
def recovery_resolve(journal_id: str, body: ResolveInput, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    """Close an entry after a guided/manual recovery, with the operator's evidence note."""
    from ..database.models_remediation import ChangeRecoveryJournal
    from ..infrastructure import recovery as RC
    j = db.get(ChangeRecoveryJournal, journal_id)
    if j is None:
        raise HTTPException(404, "Journal entry not found.")
    if j.state not in ("RECOVERY_REQUIRED", "OPEN"):
        raise HTTPException(409, f"Journal is {j.state}.")
    j.state, j.resolved_by, j.notes, j.updated_at = "RESOLVED_MANUALLY", user.username, body.note, now()
    audit(db, "change_recovery_resolved_manually", username=user.username, target=j.device_id,
          detail=f"journal={j.id}: {body.note}", request=request)
    return RC.public(j)


@router.get("/devices/{device_id}/guided-restore")
def guided_restore(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..infrastructure import recovery as RC
    return RC.guided_restore(real_device(db, device_id))


@router.get("/outbox/status")
def outbox_status(db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor"))):
    from ..infrastructure import outbox
    return outbox.stats(db)
