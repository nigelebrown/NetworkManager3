"""Shared guards for the phase 5–11 routers."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..database.models_inm import InmDevice


def require_write_lock() -> None:
    """These modules only read, plan, record and evaluate — they contain no device-write path — so they
    keep working when an operator enables the (manual, SIMS-proven) config-change path in production.
    Device writes are governed solely by Settings.device_writes_permitted and the change execution gate."""
    return None


def real_device(db: Session, device_id: str) -> InmDevice:
    """A managed device: REAL, or DEMO (loaded demo dataset; always labelled, never contacted)."""
    d = db.get(InmDevice, device_id)
    if d is None or d.source_type not in ("REAL", "DEMO"):
        raise HTTPException(404, "Registered device not found.")
    return d


def device_public(d: InmDevice) -> dict:
    return {"id": d.id, "hostname": d.hostname, "serial": d.serial_number, "vendor": d.vendor,
            "family": d.family, "model": d.model, "firmware": d.firmware, "site_id": d.site_id,
            "source_type": d.source_type}


def utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    dt = utc(dt)
    return dt.isoformat() if dt else None


def now() -> datetime:
    return datetime.now(timezone.utc)
