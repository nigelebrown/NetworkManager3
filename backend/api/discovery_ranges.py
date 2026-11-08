"""Administrator-only, zero-traffic discovery planning API."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..database.models import User
from ..security.deps import require_role
from ..discovery.ranges import RangeValidationError, plan_range, preview_batches

router = APIRouter(prefix="/api/v1/discovery/ranges", tags=["discovery-planning"])


class RangeInput(BaseModel):
    cidr: str = Field(min_length=1, max_length=80, examples=["128.0.0.0/9"])


@router.post("/plan")
def plan_range_endpoint(body: RangeInput, user: User = Depends(require_role("admin"))):
    try:
        return plan_range(body.cidr)
    except RangeValidationError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/batches")
def preview_batches_endpoint(
    cidr: str = Query(..., min_length=1, max_length=80),
    offset: int = Query(0, ge=0),
    limit: int = Query(16, ge=1, le=64),
    user: User = Depends(require_role("admin")),
):
    try:
        return preview_batches(cidr, offset=offset, limit=limit)
    except RangeValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
