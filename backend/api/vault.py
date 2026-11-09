"""WP-05 — encrypted configuration vault (Phase 5). No restore-to-device endpoint exists."""
from __future__ import annotations

from ..infrastructure.posture import read_only, write_posture

import difflib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_onboarding import OnboardingConfigSnapshot
from ..database.models_phases import ConfigArtifact, RestoreValidation
from ..database.session import get_db
from ..domain import vault as V
from ..domain.reporting import SECRET_KEYS
from ..infrastructure.phase_services import LocalObjectStore, vault_master_key
from ..security.deps import audit, require_any_role, require_role
from ._phase_common import iso, real_device, require_write_lock

router = APIRouter(prefix="/api/v1", tags=["wp05-vault"])
MAX_CONFIG_BYTES = 2_000_000


class CaptureInput(BaseModel):
    content: str = Field(min_length=1)
    capture_source: Literal["OPERATOR_UPLOAD"] = "OPERATOR_UPLOAD"
    trigger: Literal["MANUAL", "PRE_CHANGE", "POST_CHANGE", "DRIFT"] = "MANUAL"
    retention_days: int = Field(default=365, ge=30, le=3650)


class TargetInput(BaseModel):
    device_id: str | None = None
    vendor: str | None = None
    model: str | None = None
    firmware: str | None = None
    compatible_models: list[str] = []


def _public(a: ConfigArtifact) -> dict:
    return {"id": a.id, "created_at": iso(a.created_at), "trigger": a.trigger,
            "restore_state": a.restore_state, "retention_days": a.retention_days,
            "created_by": a.created_by, **V.redact_for_listing(a.manifest)}


def _open(a: ConfigArtifact) -> bytes:
    return V.open_sealed(LocalObjectStore().get(a.object_path), wrapped_key=a.wrapped_key,
                         manifest=a.manifest, signature=a.signature, master_key=vault_master_key())


@router.post("/vault/devices/{device_id}/artifacts", status_code=201)
def capture(device_id: str, body: CaptureInput, request: Request, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin"))):
    require_write_lock()
    d = real_device(db, device_id)
    raw = body.content.encode()
    if len(raw) > MAX_CONFIG_BYTES:
        raise HTTPException(413, "Configuration exceeds 2 MB.")
    sealed = V.seal(raw, master_key=vault_master_key(), identity={
        "device_id": d.id, "serial": d.serial_number, "vendor": d.vendor, "model": d.model,
        "firmware": d.firmware, "capture_source": body.capture_source, "fidelity": V.FIDELITY_FULL})
    latest = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == d.id)
                       .order_by(ConfigArtifact.created_at.desc()).limit(1))
    if latest is not None and latest.plaintext_sha256 == sealed.plaintext_sha256:
        return dict(_public(latest), duplicate=True)
    path = LocalObjectStore().put(sealed.ciphertext, sealed.ciphertext_sha256)
    a = ConfigArtifact(device_id=d.id, serial=d.serial_number, vendor=d.vendor, model=d.model,
                       firmware=d.firmware, fidelity=V.FIDELITY_FULL, capture_source=body.capture_source,
                       trigger=body.trigger, object_path=path, wrapped_key=sealed.wrapped_key,
                       key_id=sealed.key_id, plaintext_sha256=sealed.plaintext_sha256,
                       ciphertext_sha256=sealed.ciphertext_sha256, manifest=sealed.manifest,
                       signature=sealed.signature, size=len(raw), retention_days=body.retention_days,
                       created_by=user.username)
    db.add(a); db.flush()
    audit(db, "vault_artifact_sealed", username=user.username, target=d.id,
          detail=f"artifact={a.id} sha256={a.plaintext_sha256[:16]} key={a.key_id}; content not logged",
          request=request)
    return dict(_public(a), duplicate=False)


@router.get("/devices/{device_id}/config-versions")
def config_versions(device_id: str, db: Session = Depends(get_db),
                    user: User = Depends(require_any_role("admin", "auditor", "operator"))):
    d = real_device(db, device_id)
    full = db.scalars(select(ConfigArtifact).where(ConfigArtifact.device_id == d.id)
                      .order_by(ConfigArtifact.created_at.desc()))
    redacted = db.scalars(select(OnboardingConfigSnapshot).where(OnboardingConfigSnapshot.device_id == d.id)
                          .order_by(OnboardingConfigSnapshot.captured_at.desc()))
    return {"device_id": d.id,
            "full_fidelity": [_public(a) for a in full],
            "redacted_baselines": [{"id": s.id, "captured_at": iso(s.captured_at), "source": s.source,
                                    "completeness": s.completeness, "fidelity": V.FIDELITY_REDACTED,
                                    "restorable": False} for s in redacted],
            "note": "Redacted baselines are not restorable and are never equivalent to vault artifacts."}


def _artifact(db: Session, artifact_id: str) -> ConfigArtifact:
    a = db.get(ConfigArtifact, artifact_id)
    if a is None:
        raise HTTPException(404, "Artifact not found.")
    return a


@router.post("/vault/artifacts/{artifact_id}/verify")
def verify(artifact_id: str, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_role("admin"))):
    a = _artifact(db, artifact_id)
    try:
        _open(a)
        ok, reason = True, None
    except (V.VaultIntegrityError, OSError, ValueError) as exc:
        ok, reason = False, str(exc)
        a.restore_state = "INTEGRITY_FAILED"
    audit(db, "vault_artifact_verified", username=user.username, target=a.id,
          detail=f"integrity={'PASS' if ok else 'FAIL'} {reason or ''}", request=request, success=ok)
    return {"artifact_id": a.id, "integrity": "PASS" if ok else "FAIL", "reason": reason}


@router.post("/vault/artifacts/{artifact_id}/restore-validation", status_code=201)
def restore_validation(artifact_id: str, body: TargetInput, request: Request, db: Session = Depends(get_db),
                       user: User = Depends(require_role("admin"))):
    """Integrity + compatibility pre-check against a lab/replacement target. Does NOT restore."""
    require_write_lock()
    a = _artifact(db, artifact_id)
    if body.device_id:
        t = real_device(db, body.device_id)
        target = {"vendor": t.vendor, "model": t.model, "firmware": t.firmware,
                  "compatible_models": body.compatible_models, "device_id": t.id}
    else:
        target = body.model_dump()
    reasons = []
    try:
        _open(a)
    except (V.VaultIntegrityError, OSError, ValueError) as exc:
        reasons.append(f"Integrity: {exc}")
    reasons += V.restore_compatibility(artifact=a.manifest, target=target)["reasons"]
    result = "PASS" if not reasons else "FAIL"
    v = RestoreValidation(artifact_id=a.id, target=target, result=result, reasons=reasons,
                          validated_by=user.username)
    db.add(v)
    if result == "PASS" and a.restore_state == "UNVERIFIED":
        a.restore_state = "PRECHECK_PASSED"   # RESTORE_VERIFIED requires lab readback evidence.
    audit(db, "vault_restore_precheck", username=user.username, target=a.id,
          detail=f"{result}: {'; '.join(reasons) or 'compatible'}; NO device write", request=request,
          success=result == "PASS")
    return {"artifact_id": a.id, "result": result, "reasons": reasons, "restore_state": a.restore_state,
            **read_only()}


def _redact_lines(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        out.append(line.split(" ", 2)[0] + " ***REDACTED***" if SECRET_KEYS.search(line) else line)
    return out


@router.get("/vault/artifacts/{artifact_id}/diff/{other_id}")
def diff(artifact_id: str, other_id: str, request: Request, db: Session = Depends(get_db),
         user: User = Depends(require_role("admin"))):
    a, b = _artifact(db, artifact_id), _artifact(db, other_id)
    if a.device_id != b.device_id:
        raise HTTPException(422, "Compare versions of the same device.")
    try:
        left, right = _open(a).decode(errors="replace"), _open(b).decode(errors="replace")
    except V.VaultIntegrityError as exc:
        raise HTTPException(409, f"Integrity failure: {exc}") from None
    lines = list(difflib.unified_diff(_redact_lines(left), _redact_lines(right),
                                      fromfile=a.id, tofile=b.id, lineterm="", n=2))
    audit(db, "vault_diff_viewed", username=user.username, target=a.device_id,
          detail=f"{a.id}..{b.id} (secret lines redacted)", request=request)
    return {"from": a.id, "to": b.id, "diff": lines[:2000], "secret_lines": "REDACTED"}
