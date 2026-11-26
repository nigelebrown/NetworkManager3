"""Internal supply purchase requests (rc17, owner choice 2026-11-25).

Requests are created manually or automatically from MEASURED supply state (status LOW/EMPTY) or a statistical
forecast of days-to-empty within the configured lead time. Automatic generation is OFF until enabled in the
purchasing policy. One open request per printer + supply (dedupe). Every request needs a decision by an approver
who did not create it (automatic requests: any approver/admin). Approved requests can be marked ORDERED (with an
external order reference) and RECEIVED. Nothing is sent to an external procurement system; CSV export and the
platform notification channels are the hand-off.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AppSetting
from ..database.models_printer import PrinterDevice
from ..database.models_printer_srs import PrinterConsumable
from ..database.models_rc17 import PrinterPurchaseRequest
from ..security.deps import audit

POLICY_KEY = "printer_purchasing_policy"
DEFAULT_POLICY = {"automatic": False, "lead_time_days": 14, "default_quantity": 1}
OPEN = ("PENDING_APPROVAL", "APPROVED", "ORDERED")
TRANSITIONS = {"approve": ("PENDING_APPROVAL", "APPROVED"), "reject": ("PENDING_APPROVAL", "REJECTED"),
               "order": ("APPROVED", "ORDERED"), "receive": ("ORDERED", "RECEIVED"), "cancel": (None, "CANCELLED")}


class PurchaseError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def policy(db: Session) -> dict:
    row = db.get(AppSetting, POLICY_KEY)
    return DEFAULT_POLICY | (row.value if row else {})


def set_policy(db: Session, value: dict, actor: str, request=None) -> dict:
    v = DEFAULT_POLICY | {k: value[k] for k in DEFAULT_POLICY if k in value}
    if not (isinstance(v["lead_time_days"], int) and 1 <= v["lead_time_days"] <= 180):
        raise PurchaseError("Lead time must be 1–180 days.", 422)
    if not (isinstance(v["default_quantity"], int) and 1 <= v["default_quantity"] <= 50):
        raise PurchaseError("Default quantity must be 1–50.", 422)
    row = db.get(AppSetting, POLICY_KEY) or AppSetting(key=POLICY_KEY, value={})
    row.value, row.updated_by, row.updated_at = v, actor, _now()
    db.add(row)
    audit(db, "printer_purchasing_policy_set", username=actor, target=POLICY_KEY, detail=str(v), request=request)
    return v


def _key(printer_id: str, supply_index: str | None, description: str) -> str:
    return f"{printer_id}:{supply_index or description.casefold()[:60]}"


def public(r: PrinterPurchaseRequest, printers: dict | None = None) -> dict:
    p = (printers or {}).get(r.printer_id)
    return {"id": r.id, "printer_id": r.printer_id,
            "printer": (p.hostname or f"{p.manufacturer} {p.model}") if p else None,
            "serial_number": p.serial_number if p else None, "supply_index": r.supply_index,
            "supply_description": r.supply_description, "colour": r.colour, "part_number": r.part_number,
            "quantity": r.quantity, "estimated_unit_cost": float(r.estimated_unit_cost) if r.estimated_unit_cost is not None else None,
            "currency": r.currency, "reason": r.reason, "evidence": r.evidence, "origin": r.origin, "status": r.status,
            "requested_by": r.requested_by, "decided_by": r.decided_by,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None, "decision_note": r.decision_note,
            "order_reference": r.order_reference, "created_at": r.created_at.isoformat() if r.created_at else None}


def _open_exists(db: Session, key: str) -> PrinterPurchaseRequest | None:
    return db.scalar(select(PrinterPurchaseRequest).where(PrinterPurchaseRequest.dedupe_key == key,
                                                          PrinterPurchaseRequest.status.in_(OPEN)).limit(1))


def create(db: Session, *, printer_id: str, supply_index: str | None, description: str, quantity: int, reason: str,
           actor: str, origin: str = "MANUAL", evidence: dict | None = None, colour: str | None = None,
           part_number: str | None = None, request=None) -> PrinterPurchaseRequest:
    printer = db.get(PrinterDevice, printer_id)
    if printer is None:
        raise PurchaseError("Printer not found.", 404)
    if not 1 <= int(quantity) <= 50:
        raise PurchaseError("Quantity must be 1–50.", 422)
    key = _key(printer_id, supply_index, description)
    if (existing := _open_exists(db, key)) is not None:
        raise PurchaseError(f"An open request already exists for this supply ({existing.status}).")
    cost = None
    if supply_index is not None:
        c = db.scalar(select(PrinterConsumable).where(PrinterConsumable.printer_id == printer_id,
                                                      PrinterConsumable.supply_index == supply_index))
        if c is not None:
            from . import service as S
            ident = S.identity(db, printer_id)
            cost = S._verified_cost(db, ident.model_id, c.supply_type, c.colour)
            colour = colour or c.colour
            part_number = part_number or c.part_number
    r = PrinterPurchaseRequest(printer_id=printer_id, supply_index=supply_index, supply_description=description[:255],
                               colour=colour, part_number=part_number, quantity=int(quantity),
                               estimated_unit_cost=cost[0] if cost else None, currency=cost[1] if cost else "JMD",
                               reason=reason[:255], evidence=evidence or {}, origin=origin, status="PENDING_APPROVAL",
                               dedupe_key=key, requested_by=actor)
    db.add(r)
    db.flush()
    audit(db, "printer_purchase_request_created", username=actor, target=r.id, request=request,
          detail=f"{origin} {quantity}x {description[:60]} for printer {printer_id}: {reason[:80]}")
    _notify(db, r, "created")
    return r


def transition(db: Session, request_id: str, action: str, actor: str, *, note: str | None = None,
               order_reference: str | None = None, request=None) -> PrinterPurchaseRequest:
    r = db.get(PrinterPurchaseRequest, request_id)
    if r is None:
        raise PurchaseError("Purchase request not found.", 404)
    if action not in TRANSITIONS:
        raise PurchaseError("Unknown action.", 422)
    src, dst = TRANSITIONS[action]
    if src is not None and r.status != src:
        raise PurchaseError(f"Request is {r.status}; cannot {action}.")
    if action == "cancel" and r.status not in ("PENDING_APPROVAL", "APPROVED"):
        raise PurchaseError(f"Request is {r.status}; cannot cancel.")
    if action in ("approve", "reject") and r.origin == "MANUAL" and r.requested_by.casefold() == actor.casefold():
        audit(db, "printer_purchase_self_approval_denied", username=actor, target=r.id, success=False, request=request)
        db.commit()
        raise PurchaseError("An independent approver must decide this request.", 403)
    if action == "order" and not (order_reference or "").strip():
        raise PurchaseError("Record the external purchase-order reference.", 422)
    r.status, r.updated_at = dst, _now()
    if action in ("approve", "reject"):
        r.decided_by, r.decided_at, r.decision_note = actor, _now(), (note or "")[:500] or None
    if action == "order":
        r.order_reference = order_reference.strip()[:100]
    audit(db, f"printer_purchase_request_{dst.lower()}", username=actor, target=r.id, request=request,
          detail=f"{r.supply_description[:60]} -> {dst}" + (f" ref={r.order_reference}" if action == "order" else ""))
    if action in ("approve", "reject"):
        _notify(db, r, dst.lower())
    return r


def generate(db: Session, *, actor: str = "purchasing-scheduler", now: datetime | None = None) -> dict:
    """Automatic requests from measured LOW/EMPTY supplies or forecast depletion within the lead time."""
    pol = policy(db)
    if not pol["automatic"]:
        return {"automatic": False, "created": 0}
    from . import service as S
    created = skipped = 0
    for c in db.scalars(select(PrinterConsumable).where(PrinterConsumable.supply_type.not_in(("waste_toner", "waste_ink")))):
        label = (c.description or c.supply_type)[:100]
        reason, evidence = None, {}
        if c.status in ("LOW", "EMPTY") and c.level_state == "VALID":
            reason = f"Measured supply level {c.percent_remaining}% ({c.status})"
            evidence = {"percent_remaining": c.percent_remaining, "status": c.status,
                        "observed_at": c.last_observed_at.isoformat() if c.last_observed_at else None}
        else:
            f = next((x for x in S.supply_forecasts(db, c.printer_id, now) if x["supply_index"] == c.supply_index), None)
            days = f.get("days_to_empty") if f else None
            if isinstance(days, (int, float)) and days <= pol["lead_time_days"]:
                reason = f"Forecast empty in about {round(days)} days (lead time {pol['lead_time_days']})"
                evidence = {k: f.get(k) for k in ("days_to_empty", "low", "high", "status", "points") if k in f}
        if reason is None:
            continue
        if _open_exists(db, _key(c.printer_id, c.supply_index, label)) is not None:
            skipped += 1
            continue
        create(db, printer_id=c.printer_id, supply_index=c.supply_index, description=label, quantity=pol["default_quantity"],
               reason=reason, actor=actor, origin="AUTOMATIC", evidence=evidence)
        created += 1
    return {"automatic": True, "created": created, "skipped_open": skipped}


def export_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    cols = ["id", "status", "printer", "serial_number", "supply_description", "part_number", "colour", "quantity",
            "estimated_unit_cost", "currency", "reason", "origin", "requested_by", "decided_by", "order_reference", "created_at"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([("'" + str(v)) if isinstance(v, str) and v[:1] in "=+-@" else ("" if v is None else v)
                    for v in (r.get(c) for c in cols)])
    return buf.getvalue()


def _notify(db: Session, r: PrinterPurchaseRequest, what: str) -> None:
    """Queue an outbox notification (email/webhook/syslog channels); delivery and retry run in the worker."""
    from ..infrastructure.notifications import enqueue_event
    enqueue_event(db, kind="printer_purchase_request", severity="info", ref=r.id, event=what,
                  message=f"Supply purchase request {what}: {r.quantity}x {r.supply_description} ({r.status})",
                  detail={"printer_id": r.printer_id, "status": r.status, "origin": r.origin,
                          "part_number": r.part_number, "order_reference": r.order_reference})
