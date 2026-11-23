"""Verified, journalled printer configuration. Monitoring readers never call this module."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import select

from ..config import get_settings
from ..database.models import AppSetting
from ..database.models_printer_srs import PrinterMonitoring
from ..infrastructure.change_executor import _lease, _release, control
from ..security.crypto import decrypt_secret, encrypt_secret
from ..security.deps import audit
from . import service as S

PREFIX = "printer-config:"
LOCATION = "1.3.6.1.2.1.1.6.0"
SERIAL = "1.3.6.1.2.1.43.5.1.1.17.1"


def validate_location(value: str) -> str:
    if not value.isascii() or len(value.encode()) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Use up to 255 printable ASCII characters for the printer location")
    return value


def verified(db, printer) -> bool:
    rows = db.scalars(select(AppSetting).where(AppSetting.key.like("printer-test:%")))
    return any(r.value.get("printer_id") == printer.id and
               r.value.get("serial") == printer.serial_number and
               r.value.get("firmware") == printer.firmware and
               r.value.get("operation") == "system.location" and
               r.value.get("status") == "PASSED" and
               r.value.get("result", {}).get("restored") is True for r in rows)


def _guard(db, printer):
    if not get_settings().device_writes_permitted or control(db).emergency_stop:
        raise HTTPException(403, "Printer configuration writes are locked or emergency stop is active")
    if not verified(db, printer):
        raise HTTPException(409, "A successful hardware write and rollback test is required for this printer")
    mon = db.get(PrinterMonitoring, printer.id)
    if not mon or not mon.credential_profile_id:
        raise HTTPException(409, "Assign a printer credential profile first")
    return mon


def _public(row):
    v = row.value
    return {"id": row.key.removeprefix(PREFIX), "state": v["state"], "operation": "system.location",
            "before": decrypt_secret(v["before_enc"]), "after": decrypt_secret(v["after_enc"]),
            "created_at": v["created_at"], "created_by": v["created_by"],
            "result": v.get("result"), "finished_at": v.get("finished_at")}


def _save(db, row, state, result=None):
    row.value = {**row.value, "state": state, "result": result,
                 "finished_at": datetime.now(timezone.utc).isoformat()}
    db.commit()


async def _current(reader, serial):
    actual = await reader.get(SERIAL)
    if actual is None or str(actual) != serial:
        raise HTTPException(409, "Printer serial number does not match inventory; no write was sent")
    value = await reader.get(LOCATION)
    if not isinstance(value, str):
        raise HTTPException(502, "Could not read the printer location")
    return value


async def _set_location(reader, value):
    from pysnmp.hlapi.v3arch.asyncio import (
        ContextData, ObjectIdentity, ObjectType, OctetString, UdpTransportTarget, set_cmd,
    )
    await reader._ensure()
    target = await UdpTransportTarget.create((reader.host, 161), timeout=4, retries=0)
    error, status, _, _ = await set_cmd(
        reader._engine, reader._auth_data, target, ContextData(),
        ObjectType(ObjectIdentity(LOCATION), OctetString(value)), lookupMib=False,
    )
    # Never replay a SET on a timeout, including when its effect is uncertain.
    return not error and not status


async def view(db, printer):
    supported = verified(db, printer)
    mon = db.get(PrinterMonitoring, printer.id)
    result = {"hardware_verified": supported,
              "writes_enabled": bool(get_settings().device_writes_permitted and not control(db).emergency_stop),
              "current_location": None, "read_error": None, "history": [],
              "available": ["system.location"] if supported else []}
    rows = db.scalars(select(AppSetting).where(AppSetting.key.like(PREFIX + "%")).order_by(AppSetting.updated_at.desc()))
    result["history"] = [_public(r) for r in rows if r.value.get("printer_id") == printer.id][:20]
    if supported and mon and mon.credential_profile_id:
        reader = S.make_reader(db, printer.management_ip, mon.credential_profile_id)
        try:
            result["current_location"] = await _current(reader, printer.serial_number)
        except Exception:
            result["read_error"] = "Could not verify this printer's identity and current location"
        finally:
            await reader.close()
    return result


async def preview(db, printer, desired: str, actor: str):
    validate_location(desired)
    mon = _guard(db, printer)
    reader = S.make_reader(db, printer.management_ip, mon.credential_profile_id)
    try:
        previous = await _current(reader, printer.serial_number)
    finally:
        await reader.close()
    now = datetime.now(timezone.utc)
    row = AppSetting(key=PREFIX + uuid.uuid4().hex, updated_by=actor, value={
        "printer_id": printer.id, "serial": printer.serial_number, "host": printer.management_ip,
        "credential_id": mon.credential_profile_id, "state": "PREVIEW", "created_by": actor,
        "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "before_enc": encrypt_secret(previous), "after_enc": encrypt_secret(desired),
    })
    db.add(row)
    audit(db, "printer_configuration_preview", username=actor, target=printer.id,
          detail="Location change preview " + row.key)
    db.commit()
    return _public(row)


async def execute(db, printer, plan_id: str, actor: str, *, rollback: bool = False):
    mon = _guard(db, printer)
    holder = uuid.uuid4().hex
    device_key = "printer:" + printer.id
    if not _lease(db, device_key, holder):
        raise HTTPException(409, "Another configuration action owns this printer")
    reader = None
    row = None
    started = False
    try:
        row = db.get(AppSetting, PREFIX + plan_id, populate_existing=True)
        if row is None or row.value.get("printer_id") != printer.id:
            raise HTTPException(404, "Printer change not found")
        v = row.value
        if v["host"] != printer.management_ip or v["serial"] != printer.serial_number or v["credential_id"] != mon.credential_profile_id:
            raise HTTPException(409, "Printer identity, address or credential changed; create a new preview")
        allowed = ("APPLIED_VERIFIED", "RECOVERY_REQUIRED") if rollback else ("PREVIEW",)
        if v["state"] not in allowed:
            if not rollback and v["state"] in ("APPLIED_VERIFIED", "FAILED_RESTORED", "ROLLED_BACK", "RECOVERY_REQUIRED"):
                return _public(row)
            raise HTTPException(409, "This change is no longer available for that action")
        if not rollback and datetime.fromisoformat(v["expires_at"]) <= datetime.now(timezone.utc):
            raise HTTPException(409, "Preview expired; read the printer again")
        reader = S.make_reader(db, printer.management_ip, mon.credential_profile_id)
        previous, desired = decrypt_secret(v["before_enc"]), decrypt_secret(v["after_enc"])
        current = await _current(reader, printer.serial_number)
        expected, target = (desired, previous) if rollback else (previous, desired)
        if current == target and rollback:
            _save(db, row, "ROLLED_BACK", {"restored": True})
            return _public(row)
        if current != expected:
            raise HTTPException(409, "Printer settings changed since preview; no write was sent")
        _save(db, row, "ROLLING_BACK" if rollback else "EXECUTING")
        audit(db, "printer_configuration_started", username=actor, target=printer.id,
              detail=row.key + (" rollback" if rollback else " apply"))
        db.commit()
        started = True
        acknowledged = await _set_location(reader, target)
        observed = await _current(reader, printer.serial_number)
        if acknowledged and observed == target:
            state = "ROLLED_BACK" if rollback else "APPLIED_VERIFIED"
            result = {"readback_verified": True, "restored": rollback}
        elif rollback:
            state, result = "RECOVERY_REQUIRED", {"readback_verified": False, "restored": observed == previous}
        else:
            # Reconcile an uncertain write before one compensating rollback.
            if observed == desired:
                await _set_location(reader, previous)
                observed = await _current(reader, printer.serial_number)
            restored = observed == previous
            state = "FAILED_RESTORED" if restored else "RECOVERY_REQUIRED"
            result = {"readback_verified": False, "restored": restored}
        _save(db, row, state, result)
        audit(db, "printer_configuration_finished", username=actor, target=printer.id,
              detail=json.dumps({"change": plan_id, "state": state, **result}),
              success=state in ("APPLIED_VERIFIED", "ROLLED_BACK"))
        db.commit()
        return _public(row)
    except Exception:
        if started and row is not None and row.value.get("state") in ("EXECUTING", "ROLLING_BACK"):
            _save(db, row, "RECOVERY_REQUIRED", {"readback_verified": False, "restored": False})
            audit(db, "printer_configuration_recovery_required", username=actor, target=printer.id,
                  detail=row.key, success=False)
            db.commit()
            return _public(row)
        raise
    finally:
        if reader is not None:
            await reader.close()
        _release(db, device_key, holder)
        db.commit()
