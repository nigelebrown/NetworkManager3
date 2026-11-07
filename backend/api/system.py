from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import get_settings
from ..database.models import User
from ..security.deps import require_role

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/status")
def status(_: User = Depends(require_role("viewer"))):
    s = get_settings()
    return {
        "mode": s.mode,
        "simulated_data": s.mode == "demo",
        "config_changes_enabled": s.device_writes_permitted,
        "device_write_lock": s.device_write_lock,
        "inm_profile": s.inm_profile,
        "role": s.role,
        "config_changes_available": True,
        "tls_verification": s.aruba_verify_tls,
        "poll_interval_seconds": s.poll_interval_seconds,
        "phase": 2,
    }
