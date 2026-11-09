"""WP-07 — compatible device replacement planning (Phase 7). Planning only; no restore."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_phases import ConfigArtifact, ReplacementPlan
from ..database.session import get_db
from ..domain.replacement import build_plan
from ..infrastructure.phase_services import pause_enrollments
from ..security.deps import audit, require_role
from ._phase_common import device_public, iso, real_device, require_write_lock

router = APIRouter(prefix="/api/v1/replacements", tags=["wp07-replacement"])


class ReplacementInput(BaseModel):
    old_device_id: str
    new_device_id: str
    artifact_id: str | None = None
    identity_map: dict = {}
    compatible_models: list[str] = []


def _public(r: ReplacementPlan) -> dict:
    return {"id": r.id, "old_device_id": r.old_device_id, "new_device_id": r.new_device_id,
            "artifact_id": r.artifact_id, "state": r.state, "blockers": r.blockers,
            "identity_map": r.identity_map, "plan": r.plan, "requester": r.requester,
            "created_at": iso(r.created_at)}


@router.post("/plan", status_code=201)
def plan(body: ReplacementInput, request: Request, db: Session = Depends(get_db),
         user: User = Depends(require_role("admin"))):
    require_write_lock()
    old, new = real_device(db, body.old_device_id), real_device(db, body.new_device_id)
    if old.id == new.id:
        raise HTTPException(422, "Choose a different replacement device.")
    if body.artifact_id:
        art = db.get(ConfigArtifact, body.artifact_id)
    else:
        art = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == old.id)
                        .order_by(ConfigArtifact.created_at.desc()).limit(1))
    new_info = dict(device_public(new), compatible_models=body.compatible_models)
    result = build_plan(old_device=device_public(old), artifact=art.manifest if art else None,
                        new_device=new_info, identity_map=body.identity_map)
    r = ReplacementPlan(old_device_id=old.id, new_device_id=new.id, artifact_id=art.id if art else None,
                        state=result["state"], blockers=result["blockers"],
                        identity_map=result["identity_map"], plan=result, requester=user.username)
    db.add(r); db.flush()
    # The failed device's automation authority ends; the successor starts at AUTO OFF (no inheritance).
    revoked = pause_enrollments(db, old.id, "DEVICE_REPLACED", actor=user.username)
    audit(db, "replacement_plan_created", username=user.username, target=old.id,
          detail=f"plan={r.id} new={new.id} state={r.state} blockers={len(r.blockers)} "
                 f"old_enrollments_revoked={revoked}; NO restore", request=request)
    return _public(r)


@router.get("")
def list_plans(db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    return [_public(r) for r in db.scalars(select(ReplacementPlan).order_by(ReplacementPlan.created_at.desc()).limit(200))]
