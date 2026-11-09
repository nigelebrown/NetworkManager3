"""Live write posture and certification status (E2E-12/13).

Read-only operations (inventory, probes, discovery, baseline capture, monitoring) never depend on the write
posture: enabling authorized writes must not disable them. Responses report the REAL current posture instead
of a hardcoded "LOCKED", and certification is read from DeviceCertification instead of a constant.
"""
from __future__ import annotations

from ..config import get_settings


def write_posture() -> dict:
    s = get_settings()
    locked = bool(s.device_write_lock) or not bool(s.allow_config_changes)
    return {"hardware_writes": "LOCKED" if locked else "ENABLED",
            "global_write_lock": bool(s.device_write_lock), "config_changes_allowed": bool(s.allow_config_changes)}


def read_only(**extra) -> dict:
    """Fields every read-only response carries: what this call did, and the live platform write posture."""
    return {"operation": "READ_ONLY", "device_writes": write_posture()["hardware_writes"], **extra}


def certification_summary(db, device) -> dict:
    """Per-device certification for its adapter/model family/firmware train/environment. hardware_certified is
    true only when at least one operation is CERTIFIED_WRITE in the HARDWARE environment."""
    from ..infrastructure.change_executor import certification_map, environment
    env = environment(device)
    cmap = certification_map(db, device)
    certified = sorted(op for op, st in cmap.items() if st == "CERTIFIED_WRITE")
    return {"environment": env, "certified_operations": certified,
            "hardware_certified": env == "HARDWARE" and bool(certified)}
