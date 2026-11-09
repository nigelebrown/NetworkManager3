"""WP-10 adapters, WP-11 reporting + role topology, WP-12 printer intelligence completion."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AuditLog, User
from ..database.models_inm import InmDevice, InmSite, RecommendationRecord
from ..database.models_phases import ChangeAttribution, CollectionPlan, ConfigArtifact, ReportExport
from ..database.models_printer import PrinterDevice, PrinterSupplyReading
from ..database.session import get_db
from ..domain import adapters as AD
from ..domain import collection as C
from ..domain import printer_intelligence as PI
from ..domain import reporting as R
from ..infrastructure.phase_services import report_signing_key
from ..security.deps import audit, require_any_role, require_role
from ._phase_common import iso, now, real_device, utc

router = APIRouter(prefix="/api/v1", tags=["wp10-12-platform"])


# ---- WP-10 adapters ----------------------------------------------------------------------
def _locked() -> bool:
    from ..infrastructure.posture import write_posture
    return write_posture()["hardware_writes"] == "LOCKED"


@router.get("/adapters")
def adapters(user: User = Depends(require_role("viewer"))):
    from ..infrastructure.posture import write_posture
    return {"adapters": [AD.public(s) for s in AD.REGISTRY], "operations": list(AD.OPERATIONS),
            "software_states": list(AD.SOFTWARE_STATES), "write_posture": write_posture(),
            "write_policy": "All writes route through the central change executor: approved plan, per-operation "
                            "certification for the model/firmware, verified pre-change backup, readback, rollback."}


@router.get("/devices/{device_id}/adapter")
def device_adapter(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    d = real_device(db, device_id)
    spec = AD.resolve(d.vendor, d.family, d.model)
    return {"device_id": d.id, "adapter": AD.public(spec),
            "operations": [AD.operation_result(spec, op, writes_locked=_locked()) for op in AD.OPERATIONS]}


def _device_capabilities(db: Session, d) -> dict:
    """E2E-08/12: one truthful row per typed operation for THIS device: is it implemented in software, what is
    its certification for this adapter + model family + firmware train + environment, and could it execute now."""
    from ..infrastructure.change_executor import certification_map, environment, firmware_train, model_family
    from ..infrastructure.posture import write_posture
    spec = AD.resolve(d.vendor, d.family, d.model)
    env = environment(d)
    certs = certification_map(db, d)
    posture = write_posture()
    rows = []
    for op in spec.typed_operations:
        cert = certs.get(op, "UNCERTIFIED")
        if cert != "CERTIFIED_WRITE":
            why = f"Not certified for {model_family(d.model)} firmware {firmware_train(d.firmware)} ({env})."
        elif env == "HARDWARE" and posture["hardware_writes"] == "LOCKED":
            why = "Certified; hardware writes are LOCKED by the platform posture."
        else:
            why = "Certified; executable through an approved change plan."
        rows.append({"operation": op, "software": "IMPLEMENTED", "certification": cert,
                     "executable_now": cert == "CERTIFIED_WRITE" and (env != "HARDWARE" or
                                                                      posture["hardware_writes"] == "ENABLED"),
                     "reason": why})
    if spec.family == "access_point":
        rows.append({"operation": "config.restore", "software": "GUIDED", "certification": "UNSUPPORTED",
                     "executable_now": False,
                     "reason": "Aruba Instant has no complete automatic restore; use the guided recovery procedure."})
    return {"device_id": d.id, "adapter": spec.key, "adapter_implemented": spec.implemented,
            "model_family": model_family(d.model), "firmware_train": firmware_train(d.firmware),
            "environment": env, "write_posture": posture, "operations": rows,
            "hardware_certified": env == "HARDWARE" and any(r["certification"] == "CERTIFIED_WRITE" for r in rows),
            "read_operations": [AD.operation_result(spec, op, writes_locked=posture["hardware_writes"] == "LOCKED")
                                for op in AD.OPERATIONS if op not in AD.WRITE_OPERATIONS]}


@router.get("/devices/{device_id}/capabilities")
def device_capabilities(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    return _device_capabilities(db, real_device(db, device_id))


@router.get("/capability-matrix")
def capability_matrix(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    """Supported-feature / API capability matrix (deliverable 9): every adapter's software operations and, for
    every model family + firmware train + environment ever certified, each operation's certification status."""
    from ..database.models_phases import DeviceCertification
    groups: dict[tuple, dict] = {}
    for c in db.scalars(select(DeviceCertification)):
        groups.setdefault((c.adapter_key, c.model_family, c.firmware_train, c.environment), {})[c.operation] = c.status
    return {"adapters": [AD.public(s) for s in AD.REGISTRY],
            "certifications": [{"adapter": k[0], "model_family": k[1], "firmware_train": k[2], "environment": k[3],
                                "operations": v} for k, v in sorted(groups.items())],
            "note": "EMULATED certification proves the software against the strict protocol emulator only. "
                    "HARDWARE certification is required before any physical device write."}


@router.get("/adapters/{key}/operations/{op}")
def adapter_operation(key: str, op: str, user: User = Depends(require_role("viewer"))):
    spec = next((s for s in AD.REGISTRY if s.key == key), None)
    if spec is None:
        raise HTTPException(404, "Unknown adapter.")
    return AD.operation_result(spec, op, writes_locked=_locked())


# ---- WP-11 executive reporting -------------------------------------------------------------
def _summary(db: Session) -> dict:
    t = now()
    sites = {s.id: s.name for s in db.scalars(select(InmSite))}
    plans = {p.device_id: p for p in db.scalars(select(CollectionPlan))}
    devices, fresh = [], {}
    for d in db.scalars(select(InmDevice)):
        devices.append({"id": d.id, "source_type": d.source_type, "site": sites.get(d.site_id), "model": d.model})
        p = plans.get(d.id)
        fresh[d.id] = C.freshness(p.last_success_at, p.interval_seconds, t) if p else "NEVER"
    from ..infrastructure.demo_dataset import status as demo_status
    sources = ("REAL", "DEMO") if demo_status(db)["loaded"] else ("REAL",)
    recs = [{"approval_state": r.approval_state} for r in db.scalars(
        select(RecommendationRecord).where(RecommendationRecord.source_type.in_(sources)))]
    attrs = [{"level": a.level} for a in db.scalars(select(ChangeAttribution).where(ChangeAttribution.supersedes_id.is_(None)))]
    vault = [{"restore_state": a.restore_state} for a in db.scalars(select(ConfigArtifact))]
    return R.executive_summary(devices, fresh, recs, attrs, vault, sources)


@router.get("/reports/executive")
def executive(db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor", "approver"))):
    return dict(_summary(db), generated_at=iso(now()),
                generation_sha=os.environ.get("INM_GIT_COMMIT", "unknown"))


class ExportInput(BaseModel):
    kind: Literal["audit", "inventory", "attribution"]
    start: datetime
    end: datetime
    reveal_identities: bool = False


@router.post("/reports/exports", status_code=201)
def export(body: ExportInput, request: Request, db: Session = Depends(get_db),
           user: User = Depends(require_any_role("admin", "auditor"))):
    start, end = utc(body.start), utc(body.end)
    if not start < end or end - start > timedelta(days=366):
        raise HTTPException(422, "Range must be ordered and at most 366 days.")
    reveal = body.reveal_identities and user.role == "admin"
    if body.kind == "audit":
        cols = ["ts", "username", "action", "target", "success", "detail"]
        rows = [{"ts": iso(a.ts), "username": R.mask_user(a.username, reveal), "action": a.action,
                 "target": a.target, "success": a.success, "detail": a.detail}
                for a in db.scalars(select(AuditLog).where(AuditLog.ts >= start, AuditLog.ts < end)
                                    .order_by(AuditLog.id).limit(50000))]
    elif body.kind == "inventory":
        cols = ["id", "source_type", "vendor", "family", "model", "serial_number", "hostname", "firmware", "status"]
        rows = [{c: getattr(d, c) for c in cols} for d in db.scalars(select(InmDevice).where(InmDevice.source_type == "REAL"))]
    else:
        cols = ["change_at", "device_id", "level", "actor", "change_type", "supersedes_id"]
        rows = [{"change_at": iso(a.change_at), "device_id": a.device_id, "level": a.level,
                 "actor": R.mask_user(a.actor, reveal), "change_type": a.change_type, "supersedes_id": a.supersedes_id}
                for a in db.scalars(select(ChangeAttribution).where(ChangeAttribution.change_at >= start,
                                                                    ChangeAttribution.change_at < end))]
    export_id = str(uuid.uuid4())
    meta = {"export_id": export_id, "kind": body.kind, "requested_by": user.username, "scope": "all-sites",
            "range_utc": [iso(start), iso(end)], "identities": "REVEALED" if reveal else "MASKED",
            "classification": "INTERNAL", "generation_sha": os.environ.get("INM_GIT_COMMIT", "unknown"),
            "generated_at": iso(now())}
    ex = R.build_export(rows=rows, columns=cols, meta=meta, signing_key=report_signing_key())
    db.add(ReportExport(id=export_id, kind=body.kind, requested_by=user.username, scope="all-sites",
                        range_start=start, range_end=end, sha256=ex["sha256"], signature=ex["signature"],
                        row_count=ex["row_count"], meta=meta))
    audit(db, "report_exported", username=user.username, target=export_id,
          detail=f"{body.kind} rows={ex['row_count']} sha256={ex['sha256'][:16]} identities={meta['identities']}",
          request=request)
    return ex


class VerifyInput(BaseModel):
    body: str
    sha256: str
    signature: str
    meta: dict


@router.post("/reports/exports/verify")
def verify_export(body: VerifyInput, user: User = Depends(require_any_role("admin", "auditor"))):
    return {"valid": R.verify_export(body.model_dump(), report_signing_key())}


# ---- WP-11 role topology (multi-VM readiness; no secrets) ----------------------------------
ROLE_ENV = [("01 Web/API", "INM_API_PUBLIC_URL"), ("02 Network worker", "INM_WORKER_URL"),
            ("03 Management DB", "SIMS_DATABASE_URL"), ("04 Time-series DB", "INM_TELEMETRY_DATABASE_URL"),
            ("05 Logging", "INM_LOKI_URL"), ("06 Event bus", "INM_NATS_URL"), ("07 AI", "INM_AI_URL")]


def _endpoint(value: str | None) -> dict:
    if not value:
        return {"configured": False}
    u = urlsplit(value)
    host = u.hostname or ""
    return {"configured": True, "scheme": u.scheme.split("+")[0], "host": host, "port": u.port,
            "co_located": host in ("127.0.0.1", "localhost", "") or "." not in host,
            "tls": u.scheme in ("https", "tls", "nats+tls") or "sslmode=require" in (u.query or "")}


@router.get("/topology/roles")
def topology(user: User = Depends(require_any_role("admin", "auditor"))):
    roles = [{"role": r, "env": e, **_endpoint(os.environ.get(e))} for r, e in ROLE_ENV]
    return {"roles": roles,
            "deployment": "SINGLE_VM" if all(r.get("co_located", True) for r in roles) else "DISTRIBUTED",
            "note": "Re-pointing a role is configuration-only; credentials are never returned."}


# ---- WP-12 printer intelligence completion -------------------------------------------------
@router.get("/printers/mib-packs")
def mib_packs(user: User = Depends(require_role("admin"))):
    return {"packs": PI.MIB_PACKS, "write_oids_permitted": False}


class CounterCheck(BaseModel):
    previous: int | None
    current: int | None
    width_bits: Literal[32, 64] = 32


@router.post("/printers/counter-check")
def counter_check(body: CounterCheck, user: User = Depends(require_role("admin"))):
    return PI.classify_counter(body.previous, body.current, width_bits=body.width_bits)


@router.get("/printers/{printer_id}/forecast")
def printer_forecast(printer_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("admin"))):
    p = db.get(PrinterDevice, printer_id)
    if p is None:
        raise HTTPException(404, "Printer not found.")
    out = {}
    readings = list(db.scalars(select(PrinterSupplyReading).where(PrinterSupplyReading.printer_id == p.id)
                               .order_by(PrinterSupplyReading.observed_at)))
    for key in sorted({(r.colour, r.supply_name) for r in readings}):
        rows = [{"observed_at": r.observed_at, "percent_remaining": r.percent_remaining, "source": r.source}
                for r in readings if (r.colour, r.supply_name) == key]
        out[f"{key[0]}:{key[1]}"] = PI.supply_forecast(rows)
    return {"printer_id": p.id, "forecasts": out, "model_kind": "STATISTICAL",
            "trained_ml": False, "note": "No trained maintenance model without labelled failures."}
