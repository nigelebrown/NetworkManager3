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
# rc18: standard, writable objects only (RFC 3418 SNMPv2-MIB system group, RFC 6933 ENTITY-MIB, RFC 3805
# Printer-MIB prtGeneralTable index 1). Each operation must pass its own hardware write+restore test per printer
# serial+firmware before it can be used. Energy-saving has no standard writable object: UNSUPPORTED (vendor MIB
# not verified), never simulated.
SETTINGS: dict[str, dict] = {
    "system.location": {"oid": LOCATION, "type": "text", "max": 255, "mib": "SNMPv2-MIB sysLocation.0"},
    "system.contact": {"oid": "1.3.6.1.2.1.1.4.0", "type": "text", "max": 255, "mib": "SNMPv2-MIB sysContact.0"},
    "system.name": {"oid": "1.3.6.1.2.1.1.5.0", "type": "name", "max": 63, "mib": "SNMPv2-MIB sysName.0"},
    "entity.asset_id": {"oid": "1.3.6.1.2.1.47.1.1.1.1.15.1", "type": "text", "max": 32,
                        "mib": "ENTITY-MIB entPhysicalAssetID.1"},
    "printer.name": {"oid": "1.3.6.1.2.1.43.5.1.1.16.1", "type": "text", "max": 127,
                     "mib": "Printer-MIB prtGeneralPrinterName.1"},
    "printer.default_input": {"oid": "1.3.6.1.2.1.43.5.1.1.6.1", "type": "index", "table": "1.3.6.1.2.1.43.8.2.1.12.1",
                              "mib": "Printer-MIB prtInputDefaultIndex.1 (default tray = default paper)"},
    "printer.default_media_path": {"oid": "1.3.6.1.2.1.43.5.1.1.9.1", "type": "index", "table": "1.3.6.1.2.1.43.13.4.1.9.1",
                                   "mib": "Printer-MIB prtMediaPathDefaultIndex.1 (duplex / simplex default path)"},
}
UNSUPPORTED_SETTINGS = {"printer.energy_saving": "No standard writable MIB object (PWG Power MIB is not implemented "
                                                 "by the supported models); a vendor MIB must be verified on hardware first."}
DUPLEX_TYPES = {2: "longEdgeBindingDuplex", 3: "shortEdgeBindingDuplex", 4: "simplex"}


def validate_setting(operation: str, value):
    spec = SETTINGS.get(operation)
    if spec is None:
        raise ValueError(UNSUPPORTED_SETTINGS.get(operation, "Unknown printer setting"))
    if spec["type"] == "index":
        if type(value) is not int or not 1 <= value <= 65535:
            raise ValueError("Select an existing index (1-65535)")
        return value
    value = str(value)
    if operation == "system.location":
        return validate_location(value)
    if not value.isascii() or len(value.encode()) > spec["max"] or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError(f"Use up to {spec['max']} printable ASCII characters")
    if spec["type"] == "name" and not __import__("re").fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", value):
        raise ValueError("Device name: letters, digits, '.', '_' or '-' (max 63)")
    return value


def validate_location(value: str) -> str:
    if not value.isascii() or len(value.encode()) > 255 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("Use up to 255 printable ASCII characters for the printer location")
    return value


def verified(db, printer, operation: str = "system.location") -> bool:
    rows = db.scalars(select(AppSetting).where(AppSetting.key.like("printer-test:%")))
    return any(r.value.get("printer_id") == printer.id and
               r.value.get("serial") == printer.serial_number and
               r.value.get("firmware") == printer.firmware and
               r.value.get("operation") == operation and
               r.value.get("status") == "PASSED" and
               r.value.get("result", {}).get("restored") is True for r in rows)


def _guard(db, printer, operation: str = "system.location", *, testing: bool = False):
    if not get_settings().device_writes_permitted or control(db).emergency_stop:
        raise HTTPException(403, "Printer configuration writes are locked or emergency stop is active")
    if not testing and not verified(db, printer, operation):
        raise HTTPException(409, "A successful hardware write and rollback test is required for this printer")
    mon = db.get(PrinterMonitoring, printer.id)
    if not mon or not mon.credential_profile_id:
        raise HTTPException(409, "Assign a printer credential profile first")
    return mon


def _public(row):
    v = row.value
    return {"id": row.key.removeprefix(PREFIX), "state": v["state"], "operation": v.get("operation", "system.location"),
            "before": decrypt_secret(v["before_enc"]), "after": decrypt_secret(v["after_enc"]),
            "created_at": v["created_at"], "created_by": v["created_by"],
            "result": v.get("result"), "finished_at": v.get("finished_at")}


def _save(db, row, state, result=None):
    row.value = {**row.value, "state": state, "result": result,
                 "finished_at": datetime.now(timezone.utc).isoformat()}
    db.commit()


async def _current(reader, serial, operation: str = "system.location"):
    actual = await reader.get(SERIAL)
    if actual is None or str(actual) != serial:
        raise HTTPException(409, "Printer serial number does not match inventory; no write was sent")
    spec = SETTINGS[operation]
    value = await reader.get(spec["oid"])
    if spec["type"] == "index":
        try:
            return str(int(value))
        except (TypeError, ValueError):
            raise HTTPException(502, f"Could not read {spec['mib']}") from None
    if not isinstance(value, str):
        raise HTTPException(502, "Could not read the printer location" if operation == "system.location"
                            else f"Could not read {spec['mib']}")
    return value


async def _index_exists(reader, operation: str, index: int) -> bool:
    spec = SETTINGS[operation]
    return (await reader.get(f"{spec['table']}.{index}")) is not None


async def _set_value(reader, operation: str, value: str) -> bool:
    if operation == "system.location":
        return await _set_location(reader, value)
    from pysnmp.hlapi.v3arch.asyncio import (
        ContextData, Integer32, ObjectIdentity, ObjectType, OctetString, UdpTransportTarget, set_cmd,
    )
    spec = SETTINGS[operation]
    await reader._ensure()
    target = await UdpTransportTarget.create((reader.host, 161), timeout=4, retries=0)
    typed = Integer32(int(value)) if spec["type"] == "index" else OctetString(value)
    error, status, _, _ = await set_cmd(reader._engine, reader._auth_data, target, ContextData(),
                                        ObjectType(ObjectIdentity(spec["oid"]), typed), lookupMib=False)
    return not error and not status           # never replayed on timeout


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
              "available": [op for op in SETTINGS if verified(db, printer, op)],
              "settings": {op: {"mib": spec["mib"], "type": spec["type"],
                                "status": "VERIFIED" if verified(db, printer, op) else "NOT_VERIFIED"}
                           for op, spec in SETTINGS.items()}
                          | {op: {"status": "UNSUPPORTED", "reason": why} for op, why in UNSUPPORTED_SETTINGS.items()}}
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


async def preview(db, printer, desired, actor: str, operation: str = "system.location"):
    try:
        desired = validate_setting(operation, desired)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    mon = _guard(db, printer, operation)
    reader = S.make_reader(db, printer.management_ip, mon.credential_profile_id)
    try:
        previous = await _current(reader, printer.serial_number, operation)
        if SETTINGS[operation]["type"] == "index" and not await _index_exists(reader, operation, desired):
            raise HTTPException(422, "That index does not exist on this printer; no change was planned")
        desired = str(desired)
    finally:
        await reader.close()
    now = datetime.now(timezone.utc)
    row = AppSetting(key=PREFIX + uuid.uuid4().hex, updated_by=actor, value={
        "printer_id": printer.id, "serial": printer.serial_number, "host": printer.management_ip,
        "credential_id": mon.credential_profile_id, "state": "PREVIEW", "created_by": actor, "operation": operation,
        "created_at": now.isoformat(), "expires_at": (now + timedelta(minutes=10)).isoformat(),
        "before_enc": encrypt_secret(previous), "after_enc": encrypt_secret(desired),
    })
    db.add(row)
    audit(db, "printer_configuration_preview", username=actor, target=printer.id,
          detail=f"{operation} change preview " + row.key)
    db.commit()
    return _public(row)


async def execute(db, printer, plan_id: str, actor: str, *, rollback: bool = False):
    pre = db.get(AppSetting, PREFIX + plan_id)
    operation = (pre.value.get("operation") if pre is not None else None) or "system.location"
    mon = _guard(db, printer, operation, testing=bool(pre is not None and pre.value.get("test")))
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
        current = await _current(reader, printer.serial_number, operation)
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
        acknowledged = await _set_value(reader, operation, target)
        observed = await _current(reader, printer.serial_number, operation)
        if acknowledged and observed == target:
            state = "ROLLED_BACK" if rollback else "APPLIED_VERIFIED"
            result = {"readback_verified": True, "restored": rollback}
        elif rollback:
            state, result = "RECOVERY_REQUIRED", {"readback_verified": False, "restored": observed == previous}
        else:
            # Reconcile an uncertain write before one compensating rollback.
            if observed == desired:
                await _set_value(reader, operation, previous)
                observed = await _current(reader, printer.serial_number, operation)
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



async def hardware_test(db, printer, operation: str, actor: str) -> dict:
    """Per-printer, per-operation hardware certification: write a test value, read it back, restore the original,
    read back again. Only for printers whose serial is listed in INM_CERTIFICATION_LAB_SERIALS. Records
    printer-test:<id> PASSED/FAILED bound to serial + firmware (a firmware change invalidates it)."""
    import os
    if operation not in SETTINGS:
        raise HTTPException(422, UNSUPPORTED_SETTINGS.get(operation, "Unknown printer setting"))
    lab = {x.strip() for x in os.environ.get("INM_CERTIFICATION_LAB_SERIALS", "").split(",") if x.strip()}
    if printer.serial_number not in lab:
        raise HTTPException(403, "Hardware tests run only on printers listed in INM_CERTIFICATION_LAB_SERIALS")
    mon = _guard(db, printer, operation, testing=True)
    reader = S.make_reader(db, printer.management_ip, mon.credential_profile_id)
    result: dict = {"restored": False}
    try:
        original = await _current(reader, printer.serial_number, operation)
        spec = SETTINGS[operation]
        test_value = original if spec["type"] == "index" else ("INM-TEST-" + uuid.uuid4().hex[:6])[:spec["max"]]
        acked = await _set_value(reader, operation, test_value)
        observed = await _current(reader, printer.serial_number, operation)
        result["write_readback"] = bool(acked and observed == test_value)
        await _set_value(reader, operation, original)
        result["restored"] = (await _current(reader, printer.serial_number, operation)) == original
        if spec["type"] == "index":
            result["note"] = "index settings are tested by re-writing the current value (no behaviour change)"
    except HTTPException as exc:
        result["error"] = exc.detail
    finally:
        await reader.close()
    status = "PASSED" if result.get("write_readback") and result.get("restored") else "FAILED"
    key = "printer-test:" + uuid.uuid4().hex
    db.add(AppSetting(key=key, updated_by=actor, value={"printer_id": printer.id, "serial": printer.serial_number,
                                                        "firmware": printer.firmware, "operation": operation,
                                                        "status": status, "result": result,
                                                        "tested_at": datetime.now(timezone.utc).isoformat()}))
    audit(db, "printer_configuration_hardware_test", username=actor, target=printer.id,
          detail=f"{operation}: {status} {json.dumps(result)[:160]}", success=status == "PASSED")
    db.commit()
    return {"operation": operation, "status": status, "result": result}
