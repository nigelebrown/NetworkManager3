"""Crash-safe change recovery from the encrypted journal (E2E-14).

* sweep(): an OPEN journal whose heartbeat is older than STALE_AFTER belongs to an execution that died mid-change
  (process crash, worker restart, host reboot). It becomes RECOVERY_REQUIRED and a critical alert is raised.
* recover(): rebuilds a writer for the device and restores the recorded prior state:
    DEVICE_CHECKPOINT  (AOS-CX)  -> PUT running-config from the device checkpoint; if the checkpoint is gone, the
                                    pre-change vault artifact is restored instead.
    INVERSE_OPERATIONS (Instant) -> the exported prior state (running config + radios) is re-imported into the
                                    writer and its inverse operations are re-applied.
  The result is verified by comparing the device configuration with the pre-change configuration. Only a match
  sets RECOVERED; otherwise the journal stays RECOVERY_REQUIRED with the reason.
* guided_restore(): Aruba Instant has no complete automatic configuration restore through REST/CLI. The platform
  publishes the documented manual procedure instead of advertising an automatic one.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models_inm import InmDevice
from ..database.models_remediation import ChangeRecoveryJournal
from ..security.crypto import decrypt_secret
from .writers import WriterError

STALE_AFTER = timedelta(minutes=10)


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def public(j: ChangeRecoveryJournal) -> dict:
    return {"id": j.id, "execution_id": j.execution_id, "plan_id": j.plan_id, "device_id": j.device_id,
            "environment": j.environment, "state": j.state, "strategy": j.strategy, "last_step": j.last_step,
            "owner": j.owner, "heartbeat_at": j.heartbeat_at.isoformat() if j.heartbeat_at else None,
            "created_at": j.created_at.isoformat() if j.created_at else None, "resolved_by": j.resolved_by,
            "notes": j.notes}


def sweep(db: Session, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    n = 0
    for j in db.scalars(select(ChangeRecoveryJournal).where(ChangeRecoveryJournal.state == "OPEN")):
        if (_aware(j.heartbeat_at) or _aware(j.created_at)) < now - STALE_AFTER:
            j.state, j.updated_at = "RECOVERY_REQUIRED", now
            j.notes = (f"Execution stopped responding during '{j.last_step}' (no heartbeat since "
                       f"{_aware(j.heartbeat_at).isoformat() if j.heartbeat_at else 'start'}). Investigate and recover.")
            n += 1
            device = db.get(InmDevice, j.device_id)
            if device is not None:
                from ..database.models_phases import InmAlert
                db.add(InmAlert(device_id=device.id, site_id=device.site_id, rule_id="CHANGE_INTERRUPTED",
                                severity="critical", message=f"{device.hostname or device.serial_number}: configuration "
                                f"change interrupted at '{j.last_step}' — recovery required"[:300],
                                state="OPEN", opened_at=now, last_seen_at=now, source_type=device.source_type,
                                correlation_id=j.plan_id))
    db.flush()
    return n


def recover(db: Session, journal: ChangeRecoveryJournal, *, actor: str, writer=None) -> dict:
    from .change_executor import _artifact_bytes, _norm, environment, writer_for, _lease, _release, _renew_lease
    from ..config import get_settings
    if journal.state not in ("RECOVERY_REQUIRED", "OPEN"):
        raise ValueError(f"Journal is {journal.state}; nothing to recover.")
    device = db.get(InmDevice, journal.device_id)
    if device is None:
        raise ValueError("Device no longer exists.")
    env = environment(device)
    if env == "HARDWARE" and (get_settings().device_write_lock or not get_settings().allow_config_changes):
        raise PermissionError("Recovery writes to the device: hardware writes are LOCKED. Enable authorized writes "
                              "or follow the guided procedure.")
    payload = json.loads(decrypt_secret(journal.payload_enc))
    if writer is None:
        writer, _ = writer_for(db, device)
    pre = _artifact_bytes(db, payload["pre_artifact_id"], device)
    steps = []
    holder = uuid.uuid4().hex
    if not _lease(db, device.id, holder):
        raise WriterError("DEVICE_BUSY", "A configuration operation already owns this device.")
    try:
        writer.open()
        try:
            if journal.strategy == "INVERSE_OPERATIONS":
                writer.import_checkpoint(payload["writer_checkpoint"])
                _renew_lease(db, device.id, holder)
                writer.rollback(payload["checkpoint"])
                steps.append("inverse_operations_applied")
            else:
                try:
                    _renew_lease(db, device.id, holder)
                    writer.rollback(payload["checkpoint"])
                    steps.append("device_checkpoint_restored")
                except WriterError:
                    _renew_lease(db, device.id, holder)
                    writer.restore(pre)                     # checkpoint lost (e.g. switch reboot): vault artifact
                    steps.append("pre_change_artifact_restored")
            matches = _norm(writer.backup(), device.family) == _norm(pre, device.family)
        except WriterError as exc:
            journal.notes = f"Recovery attempt by {actor} failed: {exc.category}: {str(exc)[:300]}"[:500]
            journal.updated_at = datetime.now(timezone.utc)
            db.flush()
            return {"recovered": False, "steps": steps, "error": journal.notes, "journal": public(journal)}
        finally:
            try:
                writer.close()
            except Exception:
                pass
        now = datetime.now(timezone.utc)
        journal.updated_at = now
        if matches:
            journal.state, journal.resolved_by = "RECOVERED", actor
            journal.notes = f"Recovered by {actor}: {', '.join(steps)}; configuration matches the pre-change backup."
            from ..database.models_phases import InmAlert
            for a in db.scalars(select(InmAlert).where(InmAlert.device_id == device.id,
                                                       InmAlert.rule_id == "CHANGE_INTERRUPTED",
                                                       InmAlert.state.in_(("OPEN", "ACKNOWLEDGED")))):
                a.state, a.resolved_at = "RESOLVED", now
        else:
            journal.notes = (f"Recovery by {actor} ran ({', '.join(steps)}) but the device configuration still differs "
                             "from the pre-change backup. Follow the guided procedure.")
        db.flush()
        return {"recovered": matches, "steps": steps, "journal": public(journal)}

    finally:
        _release(db, device.id, holder)
        db.commit()


def guided_restore(device: InmDevice) -> dict:
    if device.family == "switch":
        return {"automatic": True, "summary": "AOS-CX supports full running-configuration restore; use a vault "
                                              "restore plan (approval, backup, readback, rollback apply).", "steps": []}
    return {
        "automatic": False,
        "summary": "Aruba Instant exposes no complete automatic configuration restore through REST or CLI. Typed "
                   "changes are rolled back automatically with inverse operations; a full restore is manual.",
        "steps": [
            "Confirm the device identity (serial, model, firmware) matches the vault artifact's device.",
            "Download the pre-change (or chosen) vault artifact from Configuration vault — it is the exact "
            "'show running-config' text, decrypted only for your session.",
            "If the cluster is still reachable, compare the artifact with the current configuration (vault diff) and "
            "re-create only the differing SSID profiles, RADIUS servers and ARM settings through change plans.",
            "If the virtual controller was reset or replaced: perform first-boot provisioning (country code, "
            "virtual-controller IP), then use Replace device with the artifact to generate the change plans.",
            "For secrets (PSK passphrases, RADIUS keys) re-enter them from the credential library; the running "
            "configuration only holds encrypted forms that cannot be re-applied.",
            "Approve and apply the plans; each change is read back and backed up again. Close the recovery "
            "journal entry with a note referencing the plans.",
        ]}
