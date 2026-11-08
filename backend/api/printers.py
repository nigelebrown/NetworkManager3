"""Printer Intelligence P0: working read-only fleet intake and evidence-based analytics.

All endpoints require an administrator until JCF physical-location permissions
are integrated. Registration and human observation writes affect OUR DATABASE,
never the printer configuration, spool or firmware. No print contents/job metadata.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from ipaddress import IPv4Address
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_printer import (
    PrinterDevice, PrinterMeterReading, PrinterSupplyReading,
    PrinterConsumableCycle, PrinterMaintenanceEvent,
)
from ..database.session import get_db
from ..security.deps import audit, require_role
from ..printers.analytics import cycle_analysis, low_supply_alert, meter_change, compare_model_cycles
from ..printers.snmp import permitted_target, read_snmpv3

router = APIRouter(prefix="/api/printers", tags=["printer-intelligence"])
admin_only = Depends(require_role("admin"))

METER_KINDS = Literal[
    "impressions_total", "sheets_total", "bw_impressions", "colour_impressions",
    "copy_impressions", "scan_count", "fax_impressions",
]
COLORS = Literal["black", "cyan", "magenta", "yellow", "other"]
SOURCE = Literal["MANUAL", "SNMPV3", "IPP"]
QUALITY_CLASSES = Literal["genuine", "compatible", "remanufactured", "unknown"]


class PrinterInput(BaseModel):
    manufacturer: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=120)
    serial_number: str = Field(min_length=1, max_length=100)
    hostname: str | None = Field(None, max_length=120)
    management_ip: IPv4Address | None = None
    firmware: str | None = Field(None, max_length=80)
    status: Literal["up", "down", "unknown", "fault"] = "unknown"
    area: str | None = Field(None, max_length=100)
    division: str | None = Field(None, max_length=100)
    branch: str | None = Field(None, max_length=100)
    station: str | None = Field(None, max_length=100)
    section_unit: str | None = Field(None, max_length=100)
    building: str | None = Field(None, max_length=100)

    @field_validator("manufacturer", "model", "serial_number")
    @classmethod
    def clean_required(cls, v: str) -> str:
        v = v.strip()
        if not v or any(ord(ch) < 32 for ch in v):
            raise ValueError("A nonempty printable value is required")
        return v


class MeterInput(BaseModel):
    meter_kind: METER_KINDS
    counter_value: int = Field(ge=0, le=2147483647)
    observed_at: datetime | None = None
    note: str | None = Field(None, max_length=255)


class SupplyInput(BaseModel):
    supply_name: str = Field(min_length=1, max_length=100)
    colour: COLORS
    percent_remaining: Decimal | None = Field(None, ge=0, le=100)
    reported_remaining: int | None = Field(None, ge=0)
    reported_capacity: int | None = Field(None, ge=1)
    reported_unit: str | None = Field(None, max_length=30)


class CycleInput(BaseModel):
    consumable_kind: Literal["toner", "cartridge", "ink_tank", "ink_bottle", "drum", "other"]
    colour: COLORS
    part_number: str | None = Field(None, max_length=100)
    supply_brand: str | None = Field(None, max_length=80)
    supplier: str | None = Field(None, max_length=100)
    quality_class: QUALITY_CLASSES = "unknown"
    meter_kind: METER_KINDS = "impressions_total"
    start_meter: int = Field(ge=0)
    partial_refill: bool = False
    quantity_added_ml: Decimal | None = Field(None, ge=0)
    currency: Literal["JMD", "USD"] = "JMD"
    cost_amount: Decimal | None = Field(None, ge=0)
    declared_yield: int | None = Field(None, gt=0)
    notes: str | None = Field(None, max_length=1000)


class CloseCycle(BaseModel):
    end_meter: int = Field(ge=0)
    measured_consumed_ml: Decimal | None = Field(None, ge=0)
    notes: str | None = Field(None, max_length=1000)


class MaintenanceInput(BaseModel):
    component: str = Field(min_length=1, max_length=80)
    event_type: Literal["fault", "inspection", "service", "replacement"]
    severity: Literal["info", "warning", "critical"] = "info"
    fault_code: str | None = Field(None, max_length=50)
    description: str = Field(min_length=1, max_length=1500)
    cost_amount: Decimal | None = Field(None, ge=0)
    currency: Literal["JMD", "USD"] = "JMD"


class ProbeInput(BaseModel):
    management_ip: IPv4Address
    username: str = Field(min_length=1, max_length=100)
    auth_passphrase: str = Field(min_length=8, max_length=200)
    privacy_passphrase: str = Field(min_length=8, max_length=200)


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _device(d: PrinterDevice) -> dict:
    return {k: getattr(d, k) for k in (
        "id", "manufacturer", "model", "serial_number", "hostname", "management_ip",
        "firmware", "status", "provenance", "area", "division", "branch", "station",
        "section_unit", "building",
    )} | {"last_checked_at": _dt(d.last_checked_at), "created_at": _dt(d.created_at),
         "capabilities": d.capabilities or {}}


def _printer(db: Session, printer_id: str) -> PrinterDevice:
    obj = db.get(PrinterDevice, printer_id)
    if obj is None:
        raise HTTPException(404, "Printer not found")
    return obj


def _cycle(c: PrinterConsumableCycle) -> dict:
    return {
        "id": c.id, "consumable_kind": c.consumable_kind, "colour": c.colour,
        "part_number": c.part_number, "supply_brand": c.supply_brand,
        "supplier": c.supplier, "quality_class": c.quality_class,
        "start_meter": c.start_meter, "end_meter": c.end_meter,
        "meter_kind": c.meter_kind, "status": c.status,
        "currency": c.currency, "cost_amount": float(c.cost_amount) if c.cost_amount is not None else None,
        "quantity_added_ml": float(c.quantity_added_ml) if c.quantity_added_ml is not None else None,
        "measured_consumed_ml": float(c.measured_consumed_ml) if c.measured_consumed_ml is not None else None,
        "declared_yield": c.declared_yield, "partial_refill": c.partial_refill,
        "installed_at": _dt(c.installed_at), "removed_at": _dt(c.removed_at),
        "analysis": cycle_analysis(c),
    }


@router.get("")
def list_printers(db: Session = Depends(get_db), user: User = admin_only):
    rows = db.scalars(select(PrinterDevice).order_by(PrinterDevice.manufacturer, PrinterDevice.model)).all()
    return [_device(p) for p in rows]


@router.post("", status_code=201)
def register_printer(body: PrinterInput, request: Request, db: Session = Depends(get_db),
                     admin: User = admin_only):
    manufacturer = body.manufacturer.strip()
    serial = body.serial_number.strip()
    obj = db.scalar(select(PrinterDevice).where(
        func.lower(PrinterDevice.manufacturer) == manufacturer.lower(),
        func.lower(PrinterDevice.serial_number) == serial.lower(),
    ))
    if obj:
        if obj.model != body.model:
            raise HTTPException(409, "This manufacturer/serial is registered with a different model; review before changing identity")
    else:
        obj = PrinterDevice(manufacturer=manufacturer, model=body.model, serial_number=serial,
                            provenance="MANUAL", status=body.status,
                            capabilities={"snmpv3": "NOT_TESTED", "ipp": "NOT_TESTED"})
        db.add(obj)
    for field in ("hostname", "firmware", "status", "area", "division", "branch", "station",
                  "section_unit", "building"):
        setattr(obj, field, getattr(body, field))
    obj.management_ip = str(body.management_ip) if body.management_ip else None
    db.flush()
    audit(db, "printer_registered", username=admin.username, target=obj.id,
          detail=f"{manufacturer} {obj.model} registered as MANUAL; device writes disabled", request=request)
    return _device(obj)


@router.get("/dashboard")
def printer_dashboard(db: Session = Depends(get_db), user: User = admin_only):
    devices = list(db.scalars(select(PrinterDevice)))
    low = []
    for printer in devices:
        readings = list(db.scalars(
            select(PrinterSupplyReading)
            .where(PrinterSupplyReading.printer_id == printer.id)
            .order_by(PrinterSupplyReading.observed_at.desc(), PrinterSupplyReading.id.desc())
            .limit(100)
        ))
        latest = {}
        for reading in readings:
            key = (reading.supply_name, reading.colour)
            latest.setdefault(key, reading)
        for r in latest.values():
            sev = low_supply_alert(r.percent_remaining)
            if sev:
                low.append({"printer_id": printer.id, "hostname": printer.hostname or printer.serial_number,
                            "supply": r.supply_name, "colour": r.colour,
                            "percent_remaining": float(r.percent_remaining), "severity": sev,
                            "source": r.source, "observed_at": _dt(r.observed_at)})
    return {
        "total": len(devices), "online": sum(p.status == "up" for p in devices),
        "faulted": sum(p.status == "fault" for p in devices),
        "manufacturers": sorted(set(p.manufacturer for p in devices)),
        "low_supplies": sorted(low, key=lambda v: v["percent_remaining"]),
        "data_status": "MANUAL_OR_READ_ONLY", "model_rankings": "INSUFFICIENT_EVIDENCE",
    }


@router.get("/comparison")
def compare_models(db: Session = Depends(get_db), user: User = admin_only):
    rows = db.execute(select(PrinterDevice.model, PrinterConsumableCycle)
                      .join(PrinterConsumableCycle, PrinterConsumableCycle.printer_id == PrinterDevice.id)
                      .where(PrinterConsumableCycle.status == "closed")).all()
    return {"results": compare_model_cycles([(model, c) for model, c in rows]),
            "disclaimer": "Comparable cost only, never a procurement recommendation. Require ≥3 valid cycles per model; workloads and colours may differ."}


@router.post("/probe-snmpv3")
async def probe_snmp(body: ProbeInput, request: Request, db: Session = Depends(get_db),
                     admin: User = admin_only):
    host = str(body.management_ip)
    if not permitted_target(host):
        raise HTTPException(403, "SNMPv3 probe disabled or IP outside approved private management range")
    try:
        details = await read_snmpv3(host, body.username, body.auth_passphrase, body.privacy_passphrase)
    except Exception as exc:
        # No password, raw protocol output, exception text or SNMP OID payloads in error log.
        audit(db, "printer_snmp_probe_failed", username=admin.username, target=host,
              detail=f"read-only SNMPv3 probe failed ({type(exc).__name__})", success=False, request=request)
        db.commit()
        raise HTTPException(502, "Printer SNMPv3 read failed. Confirm connectivity, authPriv credentials and MIB support.")
    audit(db, "printer_snmp_probe", username=admin.username, target=host,
          detail="SNMPv3 authPriv standard system and marker data read; no device writes", request=request)
    return details


@router.get("/{printer_id}")
def printer_details(printer_id: str, db: Session = Depends(get_db), user: User = admin_only):
    obj = _printer(db, printer_id)
    meters = list(db.scalars(select(PrinterMeterReading).where(PrinterMeterReading.printer_id == obj.id)
                             .order_by(PrinterMeterReading.observed_at.desc(), PrinterMeterReading.id.desc()).limit(100)))
    supplies = list(db.scalars(select(PrinterSupplyReading).where(PrinterSupplyReading.printer_id == obj.id)
                              .order_by(PrinterSupplyReading.observed_at.desc(), PrinterSupplyReading.id.desc()).limit(100)))
    cycles = list(db.scalars(select(PrinterConsumableCycle).where(PrinterConsumableCycle.printer_id == obj.id)
                            .order_by(PrinterConsumableCycle.installed_at.desc()).limit(100)))
    maintenance = list(db.scalars(select(PrinterMaintenanceEvent).where(PrinterMaintenanceEvent.printer_id == obj.id)
                                 .order_by(PrinterMaintenanceEvent.observed_at.desc()).limit(100)))
    meter_json = [{"id": m.id, "meter_kind": m.meter_kind, "counter_value": m.counter_value,
                   "observed_at": _dt(m.observed_at), "source": m.source, "quality": m.quality}
                  for m in meters]
    by_kind = {}
    for m in meters:
        by_kind.setdefault(m.meter_kind, []).append(m)
    recent_deltas = {}
    for kind, records in by_kind.items():
        if len(records) >= 2:
            recent_deltas[kind] = meter_change(records[1].counter_value, records[0].counter_value)
    return {
        "device": _device(obj), "meters": meter_json, "meter_deltas": recent_deltas,
        "supplies": [{"id": p.id, "supply_name": p.supply_name, "colour": p.colour,
                      "percent_remaining": float(p.percent_remaining) if p.percent_remaining is not None else None,
                      "observed_at": _dt(p.observed_at), "source": p.source,
                      "alert": low_supply_alert(p.percent_remaining)} for p in supplies],
        "cycles": [_cycle(c) for c in cycles],
        "maintenance": [{"id": m.id, "component": m.component, "event_type": m.event_type,
                         "description": m.description, "severity": m.severity,
                         "observed_at": _dt(m.observed_at), "source": m.source,
                         "cost_amount": float(m.cost_amount) if m.cost_amount is not None else None,
                         "currency": m.currency} for m in maintenance],
    }


@router.post("/{printer_id}/meters", status_code=201)
def record_meter(printer_id: str, body: MeterInput, request: Request, db: Session = Depends(get_db),
                 admin: User = admin_only):
    obj = _printer(db, printer_id)
    timestamp = body.observed_at or datetime.now(timezone.utc)
    if timestamp > datetime.now(timezone.utc):
        raise HTTPException(422, "Future meter readings are not accepted")
    reading = PrinterMeterReading(printer_id=obj.id, meter_kind=body.meter_kind,
                                  counter_value=body.counter_value, observed_at=timestamp,
                                  source="MANUAL", quality="OPERATOR_REPORTED", note=body.note)
    db.add(reading)
    db.flush()
    audit(db, "printer_meter_recorded", username=admin.username, target=obj.id,
          detail=f"{body.meter_kind}={body.counter_value} source=MANUAL", request=request)
    return {"id": reading.id, "meter_kind": reading.meter_kind,
            "counter_value": reading.counter_value, "source": "MANUAL"}


@router.post("/{printer_id}/supplies", status_code=201)
def record_supply(printer_id: str, body: SupplyInput, request: Request, db: Session = Depends(get_db),
                  admin: User = admin_only):
    obj = _printer(db, printer_id)
    reading = PrinterSupplyReading(printer_id=obj.id, supply_name=body.supply_name,
        colour=body.colour, percent_remaining=body.percent_remaining,
        reported_remaining=body.reported_remaining, reported_capacity=body.reported_capacity,
        reported_unit=body.reported_unit, source="MANUAL")
    db.add(reading)
    db.flush()
    alert = low_supply_alert(reading.percent_remaining)
    audit(db, "printer_supply_recorded", username=admin.username, target=obj.id,
          detail=f"{body.supply_name} level reported source=MANUAL alert={alert}", request=request)
    return {"id": reading.id, "alert": alert, "source": "MANUAL"}


@router.post("/{printer_id}/cycles", status_code=201)
def open_cycle(printer_id: str, body: CycleInput, request: Request, db: Session = Depends(get_db),
               admin: User = admin_only):
    obj = _printer(db, printer_id)
    # A second simultaneous lifecycle on the same cartridge type and colour would
    # double count consumption. The operator must close the original first.
    duplicate = db.scalar(select(PrinterConsumableCycle).where(
        PrinterConsumableCycle.printer_id == obj.id, PrinterConsumableCycle.status == "open",
        PrinterConsumableCycle.consumable_kind == body.consumable_kind,
        PrinterConsumableCycle.colour == body.colour))
    if duplicate:
        raise HTTPException(409, "Close the existing consumable cycle for this type/colour first")
    c = PrinterConsumableCycle(
        printer_id=obj.id, **body.model_dump(), status="open")
    db.add(c)
    db.flush()
    audit(db, "printer_consumable_installed", username=admin.username, target=obj.id,
          detail=f"cycle={c.id} kind={c.consumable_kind} colour={c.colour} start_meter={c.start_meter}", request=request)
    return _cycle(c)


@router.post("/{printer_id}/cycles/{cycle_id}/close")
def close_cycle(printer_id: str, cycle_id: str, body: CloseCycle, request: Request,
                db: Session = Depends(get_db), admin: User = admin_only):
    _printer(db, printer_id)
    c = db.scalar(select(PrinterConsumableCycle).where(
        PrinterConsumableCycle.id == cycle_id, PrinterConsumableCycle.printer_id == printer_id))
    if c is None:
        raise HTTPException(404, "Consumable cycle not found")
    if c.status == "closed":
        raise HTTPException(409, "Consumable cycle already closed")
    c.end_meter, c.removed_at, c.status = body.end_meter, datetime.now(timezone.utc), "closed"
    c.measured_consumed_ml = body.measured_consumed_ml
    if body.notes:
        c.notes = (c.notes + "\n" if c.notes else "") + body.notes
    db.flush()
    audit(db, "printer_consumable_removed", username=admin.username, target=printer_id,
          detail=f"cycle={c.id} end_meter={c.end_meter}; counter quality={cycle_analysis(c)['quality']}", request=request)
    return _cycle(c)


@router.post("/{printer_id}/maintenance", status_code=201)
def record_maintenance(printer_id: str, body: MaintenanceInput, request: Request,
                       db: Session = Depends(get_db), admin: User = admin_only):
    _printer(db, printer_id)
    event = PrinterMaintenanceEvent(printer_id=printer_id, logged_by=admin.username,
                                    **body.model_dump(), source="MANUAL")
    db.add(event)
    db.flush()
    audit(db, "printer_maintenance_recorded", username=admin.username, target=printer_id,
          detail=f"{body.event_type} {body.component} {body.severity}", request=request)
    return {"id": event.id, "event_type": event.event_type, "severity": event.severity}


@router.post("/analyse-now")
def analyse_now(db: Session = Depends(get_db), admin: User = admin_only):
    summary = printer_dashboard(db=db, user=admin)
    comparison = compare_models(db=db, user=admin)
    actions = [
        {"printer_id": x["printer_id"], "condition": "LOW_CONSUMABLE", "severity": x["severity"],
         "evidence": x, "recommended_action": "Confirm supply level and prepare replacement",
         "engine": "RULE", "confidence": "REPORTED_READING", "approval": "ADVISORY_ONLY"}
        for x in summary["low_supplies"]
    ]
    return {"engine": "RULES_V1_NOT_TRAINED_ML", "recommendations": actions,
            "model_comparison": comparison["results"],
            "disclaimer": "These are rules based on recorded levels. No trained failure prediction, device write or procurement rank is claimed."}
