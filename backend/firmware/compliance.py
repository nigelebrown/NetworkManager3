"""Firmware compliance: compare what the cluster reports with the version an admin decided it should run.

* The version comes from `show version` on the Virtual Controller. Instant APs of one cluster normally run
  the same image, and SIMS has no verified per-AP version source, so the result is per SITE (cluster).
* SIMS only reports compliance and raises an alert. It never upgrades anything by itself.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AccessPoint, AppSetting, Site, utcnow

KEY = "firmware_policy"
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)\.(\d+)")
Status = Literal["compliant", "outdated", "newer", "unknown"]


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """'8.10.0.21-8.10.0.21' -> (8, 10, 0, 21). None when no a.b.c.d version is present."""
    m = _VERSION.search(text or "")
    return tuple(int(g) for g in m.groups()) if m else None


def fmt(v: tuple[int, ...] | None) -> str | None:
    return ".".join(map(str, v)) if v else None


class FirmwarePolicy(BaseModel):
    """`target` applies to every site unless `sites` has an entry for that site id."""
    target: str | None = None
    mode: Literal["minimum", "exact"] = "minimum"  # minimum: running >= target is fine; exact: must equal target
    sites: dict[str, str] = Field(default_factory=dict)

    @field_validator("target")
    @classmethod
    def _t(cls, v):
        if v in (None, ""):
            return None
        if parse_version(v) is None or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", v.strip()):
            raise ValueError("Version must look like 8.10.0.21")
        return v.strip()

    @field_validator("sites")
    @classmethod
    def _s(cls, v):
        for sid, ver in v.items():
            if not sid.isdigit() or not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", ver.strip()):
                raise ValueError("Per-site overrides need a site id and a version like 8.10.0.21")
        return {k: x.strip() for k, x in v.items()}


def load_policy(db: Session) -> FirmwarePolicy:
    row = db.get(AppSetting, KEY)
    if row is None:
        return FirmwarePolicy()
    try:
        return FirmwarePolicy.model_validate(row.value)
    except Exception:  # a bad stored value must never stop polling
        return FirmwarePolicy()


def save_policy(db: Session, policy: FirmwarePolicy, username: str) -> None:
    row = db.get(AppSetting, KEY)
    if row is None:
        db.add(AppSetting(key=KEY, value=policy.model_dump(), updated_by=username))
    else:
        row.value, row.updated_by, row.updated_at = policy.model_dump(), username, utcnow()


def target_for(policy: FirmwarePolicy, site_id: int) -> str | None:
    return policy.sites.get(str(site_id)) or policy.target


def evaluate(running: str | None, target: str | None, mode: str = "minimum") -> Status:
    r, t = parse_version(running), parse_version(target)
    if r is None or t is None:
        return "unknown"
    if r == t:
        return "compliant"
    if r > t:
        return "compliant" if mode == "minimum" else "newer"
    return "outdated"


def site_version(db: Session, site_id: int) -> str | None:
    """The version most APs of the site report (they share the VC's `show version`)."""
    versions = [v for v in db.scalars(select(AccessPoint.firmware).where(
        AccessPoint.site_id == site_id, AccessPoint.status == "up")) if v]
    return Counter(versions).most_common(1)[0][0] if versions else None


def site_report(db: Session, site: Site, policy: FirmwarePolicy | None = None) -> dict:
    policy = policy or load_policy(db)
    running, target = site_version(db, site.id), target_for(policy, site.id)
    ups = db.scalars(select(AccessPoint).where(AccessPoint.site_id == site.id)).all()
    return {
        "site_id": site.id, "site_name": site.name, "simulated": site.simulated,
        "running": running, "running_parsed": fmt(parse_version(running)), "target": target,
        "mode": policy.mode, "status": evaluate(running, target, policy.mode),
        "ap_count": len(ups), "ap_up": sum(1 for a in ups if a.status == "up"),
        "note": "Version is reported by the Virtual Controller for the whole cluster; SIMS has no verified per-AP version source.",
    }
