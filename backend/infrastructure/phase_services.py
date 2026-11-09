"""Shared infrastructure services for phases 5–11: vault object store, key provider,
global optimization control and enrollment revocation hooks."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models_phases import OptimizationControl, OptimizationEnrollment
from ..domain.vault import derive_master_key

ACTIVE_ENROLLMENT = ("PENDING_APPROVAL", "ARMED")


# ---- vault key + object store (local provider for the single test VM) ----------------------
def vault_master_key() -> bytes:
    """INM_VAULT_MASTER_KEY (Fernet key) if provided; otherwise derived from the deployment's
    Fernet secret with a vault-specific label so it is never the credential-encryption key."""
    explicit = os.environ.get("INM_VAULT_MASTER_KEY", "").strip()
    if explicit:
        return explicit.encode()
    return derive_master_key(get_settings().fernet_key)


def report_signing_key() -> bytes:
    return hashlib.sha256(b"inm-report-signing-v1:" + get_settings().jwt_secret.encode()).digest()


class LocalObjectStore:
    """Write-once object store. Objects are content-addressed by ciphertext hash."""

    def __init__(self, root: str | None = None):
        self.root = Path(root or os.environ.get("INM_VAULT_DIR") or Path(get_settings().data_dir) / "vault")

    def put(self, ciphertext: bytes, sha256: str) -> str:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        rel = f"{sha256[:2]}/{sha256}.bin"
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(ciphertext)
            os.chmod(tmp, 0o400)
            tmp.replace(path)
        return rel

    def get(self, rel: str) -> bytes:
        path = (self.root / rel).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("Object path escapes vault root.")
        return path.read_bytes()


# ---- global optimization control -------------------------------------------------------------
def control(db: Session) -> OptimizationControl:
    row = db.get(OptimizationControl, 1)
    if row is None:
        row = OptimizationControl(id=1, emergency_stop=False)
        db.add(row)
        db.flush()
    return row


def pause_enrollments(db: Session, device_id: str, trigger: str, *, actor: str = "system") -> int:
    """GAP-T22: firmware change / replacement / site transfer / drift pauses or revokes enrollment."""
    now = datetime.now(timezone.utc)
    n = 0
    for e in db.scalars(select(OptimizationEnrollment).where(
            OptimizationEnrollment.device_id == device_id,
            OptimizationEnrollment.status.in_(ACTIVE_ENROLLMENT))):
        e.status = "REVOKED" if trigger in ("DEVICE_REPLACED", "SITE_TRANSFERRED") else "PAUSED"
        e.status_reason = trigger
        if e.status == "REVOKED":
            e.revoked_at_utc, e.revoked_by = now, actor
        n += 1
    return n
