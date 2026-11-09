"""Topology from LLDP and change -> fault correlation (audit E2E-20 / gap items M10, M18, M19).

* /topology: switches and the neighbours their ports report via LLDP (collected every poll). A neighbour is matched to
  an inventory device by hostname or serial; anything else is shown as an external node. Links carry the switch port.
* /alerts/{id}/related-changes and /devices/{id}/change-impact: alerts that opened on the changed device, or on a
  directly connected LLDP neighbour, within the correlation window after a verified configuration change. This is a
  time/topology correlation to guide investigation — it is labelled as such, never as a proven root cause.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_inm import InmDevice
from ..database.models_phases import ChangeExecution, ChangePlan, DevicePortSnapshot, InmAlert
from ..database.session import get_db
from ..security.deps import require_role
from ._phase_common import iso, real_device

router = APIRouter(prefix="/api/v1", tags=["insights"])
WINDOW = timedelta(hours=2)


def _aw(dt):
    from datetime import timezone
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _graph(db: Session) -> dict:
    devices = list(db.scalars(select(InmDevice).where(InmDevice.source_type.in_(("REAL", "DEMO")))))
    by_name = {}
    for d in devices:
        for k in (d.hostname, d.serial_number):
            if k:
                by_name[str(k).casefold()] = d
    nodes = {d.id: {"id": d.id, "label": d.hostname or d.serial_number, "family": d.family, "model": d.model,
                    "source_type": d.source_type, "inventory": True} for d in devices}
    edges = []
    for snap in db.scalars(select(DevicePortSnapshot)):
        if snap.device_id not in nodes:
            continue
        for p in snap.ports or []:
            nb = p.get("lldp")
            if not nb:
                continue
            name = str(nb.get("system_name") or "").strip()
            peer = by_name.get(name.casefold())
            pid = peer.id if peer else f"ext:{name or 'unknown'}"
            if peer is None and pid not in nodes:
                nodes[pid] = {"id": pid, "label": name or "unknown neighbour", "family": "external", "model": None,
                              "source_type": None, "inventory": False}
            edges.append({"from": snap.device_id, "to": pid, "local_port": p.get("name"),
                          "remote_port": nb.get("port"), "link": p.get("link"), "poe_w": p.get("poe_w"),
                          "collected_at": iso(snap.collected_at)})
    return {"nodes": list(nodes.values()), "edges": edges}


@router.get("/topology")
def topology(db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    g = _graph(db)
    g["note"] = "Built from LLDP neighbours reported by switch ports at the last poll. Devices without LLDP data appear unlinked."
    return g


def _neighbours(db: Session, device_id: str) -> set[str]:
    g = _graph(db)
    out = set()
    for e in g["edges"]:
        if e["from"] == device_id:
            out.add(e["to"])
        if e["to"] == device_id:
            out.add(e["from"])
    return {x for x in out if not x.startswith("ext:")}


def _alert_brief(a: InmAlert) -> dict:
    return {"id": a.id, "device_id": a.device_id, "rule_id": a.rule_id, "entity": a.entity, "severity": a.severity,
            "message": a.message, "state": a.state, "opened_at": iso(a.opened_at), "correlation_id": a.correlation_id}


@router.get("/alerts/{alert_id}/related-changes")
def related_changes(alert_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    a = db.get(InmAlert, alert_id)
    if a is None:
        raise HTTPException(404, "Alert not found.")
    scope = {a.device_id} | _neighbours(db, a.device_id)
    opened = _aw(a.opened_at)
    rows = []
    for ex in db.scalars(select(ChangeExecution).where(ChangeExecution.device_id.in_(scope))):
        fin = _aw(ex.finished_at or ex.started_at)
        if fin is None or not (opened - WINDOW <= fin <= opened):
            continue
        p = db.get(ChangePlan, ex.plan_id)
        rows.append({"plan_id": ex.plan_id, "title": p.title if p else None, "device_id": ex.device_id,
                     "same_device": ex.device_id == a.device_id, "outcome": ex.outcome, "environment": ex.environment,
                     "finished_at": iso(fin), "minutes_before_alert": round((opened - fin).total_seconds() / 60, 1),
                     "operations": [o.get("op") for o in (p.operations if p else [])]})
    rows.sort(key=lambda r: r["minutes_before_alert"])
    return {"alert": _alert_brief(a), "window_minutes": int(WINDOW.total_seconds() // 60), "candidates": rows,
            "method": "Time window + LLDP adjacency correlation. A candidate is a lead for investigation, not a "
                      "proven cause."}


@router.get("/devices/{device_id}/change-impact")
def change_impact(device_id: str, days: int = 7, db: Session = Depends(get_db),
                  user: User = Depends(require_role("viewer"))):
    d = real_device(db, device_id)
    scope = {d.id} | _neighbours(db, d.id)
    alerts = list(db.scalars(select(InmAlert).where(InmAlert.device_id.in_(scope))))
    out = []
    for ex in db.scalars(select(ChangeExecution).where(ChangeExecution.device_id == d.id)
                         .order_by(ChangeExecution.started_at.desc()).limit(max(1, min(days, 30)) * 20)):
        fin = _aw(ex.finished_at or ex.started_at)
        after = [a for a in alerts if fin <= _aw(a.opened_at) <= fin + WINDOW]
        p = db.get(ChangePlan, ex.plan_id)
        out.append({"plan_id": ex.plan_id, "title": p.title if p else None, "outcome": ex.outcome,
                    "environment": ex.environment, "finished_at": iso(fin),
                    "alerts_within_window": [_alert_brief(a) for a in after],
                    "verdict": "NO_NEW_FAULTS" if not after else "INVESTIGATE"})
    return {"device_id": d.id, "neighbours": sorted(scope - {d.id}), "executions": out,
            "window_minutes": int(WINDOW.total_seconds() // 60)}
