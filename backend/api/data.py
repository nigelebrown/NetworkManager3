"""Read APIs: access points, clients, dashboard, alerts, audit log."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database.models import (
    AccessPoint, Alert, ApSample, AuditLog, ClientRecord, Site, SiteSample, User, utcnow,
)
from ..database.session import get_db
from ..aruba.errors import ArubaError
from ..configmgmt import service as config_service
from ..experience.config import load_config
from ..experience.scoring import summarize
from ..monitoring.collector import poll_site, visible_sites
from ..security.privacy import mask_mac
from ..security.deps import audit, require_role

router = APIRouter(prefix="/api", tags=["data"])


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _site_ids(db: Session, site_id: int | None) -> list[int]:
    ids = [s.id for s in visible_sites(db)]
    if site_id is not None:
        if site_id not in ids:
            raise HTTPException(404, "Site not found")
        return [site_id]
    return ids


# ---------------- access points ----------------
def ap_out(a: AccessPoint, site_name: str | None = None) -> dict:
    return {
        "id": a.id, "site_id": a.site_id, "site_name": site_name,
        "name": a.name, "mac": a.mac, "ip": a.ip, "model": a.model, "serial": a.serial,
        "firmware": a.firmware, "status": a.status, "uptime_seconds": a.uptime_seconds,
        "cpu_percent": a.cpu_percent, "memory_percent": a.memory_percent,
        "client_count": a.client_count, "radios": a.radios, "simulated": a.simulated,
        "exp_score": a.exp_score, "exp_band": a.exp_band,
        "last_seen": _iso(a.last_seen),
    }


class MoveBody(BaseModel):
    target_site_id: int
    confirm: bool = False


@router.post("/aps/{ap_id}/move")
def move_ap(ap_id: int, body: MoveBody, request: Request, db: Session = Depends(get_db),
            admin: User = Depends(require_role("admin"))):
    """Re-assign an AP to another site in SIMS, then read the destination site's config and inventory.

    This changes SIMS inventory only. Which Instant cluster an AP actually belongs to is decided by the
    network (the AP finds and joins a Virtual Controller); there is no documented REST call to force it.
    When the AP joins the destination cluster it takes that cluster's configuration.
    """
    if not body.confirm:
        raise HTTPException(422, "Explicit confirmation required (confirm: true).")
    ap = db.get(AccessPoint, ap_id)
    target = db.get(Site, body.target_site_id)
    if not ap or ap.simulated:
        raise HTTPException(404, "Access point not found (simulated APs cannot be moved)")
    if not target or target.simulated:
        raise HTTPException(404, "Target site not found (simulated sites are not valid targets)")
    if target.id == ap.site_id:
        raise HTTPException(422, "The AP is already in that site.")
    if db.scalar(select(AccessPoint).where(AccessPoint.site_id == target.id, AccessPoint.mac == ap.mac)):
        raise HTTPException(409, "The target site already has an AP with this MAC address.")

    source = ap.site.name
    for a in db.scalars(select(Alert).where(Alert.ap_id == ap.id)).all():
        db.delete(a)  # alerts belonged to the old site; they are re-evaluated by the next poll
    ap.site_id = target.id
    ap.status, ap.client_count = "unknown", None
    db.flush()
    audit(db, "ap_move", username=admin.username, target=f"ap:{ap.name}",
          detail=f"{source} -> {target.name} mac={ap.mac}", request=request)

    warnings: list[str] = []
    config_pulled = False
    try:  # read-only follow-up: destination inventory + configuration
        poll_site(db, target)
        config_service.pull_config(db, target, admin.username, "move")
        config_pulled = True
    except ArubaError as exc:
        warnings.append(f"Could not read the destination site's configuration: {exc}")
    db.flush()
    db.refresh(ap)
    seen = ap.status == "up"
    if not seen:
        warnings.append(
            "The AP is not reporting to the destination Virtual Controller yet. It shows as down there until it joins "
            "that cluster (and, if it still reports to the old cluster, the old site will list it again on its next poll).")
    return {"moved": True, "from": source, "to": target.name, "seen_in_destination": seen,
            "config_pulled": config_pulled, "warnings": warnings}


@router.delete("/aps/{ap_id}")
def delete_ap(ap_id: int, request: Request, db: Session = Depends(get_db),
              admin: User = Depends(require_role("admin"))):
    """Remove an AP record (and its history/alerts) from SIMS. Does NOT touch the device.

    In live mode an AP that is still part of the cluster is rediscovered by the next poll.
    """
    ap = db.get(AccessPoint, ap_id)
    if not ap or ap.simulated:
        raise HTTPException(404, "Access point not found (simulated APs cannot be removed)")
    name, site_name = ap.name, ap.site.name
    for a in db.scalars(select(Alert).where(Alert.ap_id == ap.id)).all():
        db.delete(a)
    audit(db, "ap_delete", username=admin.username, target=f"ap:{site_name}/{name}",
          detail=f"mac={ap.mac}", request=request)
    db.delete(ap)
    return {"deleted": True}


@router.get("/aps")
def list_aps(site_id: int | None = None, status: str | None = None,
             db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    ids = _site_ids(db, site_id)
    names = {s.id: s.name for s in db.scalars(select(Site).where(Site.id.in_(ids)))}
    q = select(AccessPoint).where(AccessPoint.site_id.in_(ids)).order_by(AccessPoint.site_id, AccessPoint.name)
    if status:
        q = q.where(AccessPoint.status == status)
    return [ap_out(a, names.get(a.site_id)) for a in db.scalars(q)]


@router.get("/aps/{ap_id}/history")
def ap_history(ap_id: int, hours: int = Query(24, ge=1, le=720),
               db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    ap = db.get(AccessPoint, ap_id)
    if not ap or ap.site_id not in _site_ids(db, None):
        raise HTTPException(404, "AP not found")
    since = utcnow() - timedelta(hours=hours)
    rows = db.scalars(select(ApSample).where(ApSample.ap_id == ap_id, ApSample.ts >= since)
                      .order_by(ApSample.ts))
    return [{"ts": _iso(r.ts), "status": r.status, "client_count": r.client_count,
             "cpu_percent": r.cpu_percent, "memory_percent": r.memory_percent,
             "util_2g_percent": r.util_2g_percent, "util_5g_percent": r.util_5g_percent} for r in rows]


# ---------------- clients ----------------
@router.get("/clients")
def list_clients(
    request: Request,
    site_id: int | None = None, q: str | None = Query(None, max_length=64),
    quality: str | None = Query(None, pattern="^(good|fair|poor|unavailable)$"),
    connected: bool = True, limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db), user: User = Depends(require_role("viewer")),
):
    ids = _site_ids(db, site_id)
    names = {s.id: s.name for s in db.scalars(select(Site).where(Site.id.in_(ids)))}
    stmt = select(ClientRecord).where(ClientRecord.site_id.in_(ids))
    if connected:
        stmt = stmt.where(ClientRecord.is_connected.is_(True))
    if quality:
        stmt = stmt.where(ClientRecord.quality == quality)
    if q:
        like = f"%{q.lower()}%"
        # Searching is limited to operators+: viewers get masked identifiers.
        if user.role == "viewer":
            stmt = stmt.where(or_(func.lower(ClientRecord.ssid).like(like),
                                  func.lower(ClientRecord.ap_name).like(like)))
        else:
            stmt = stmt.where(or_(func.lower(ClientRecord.mac).like(like),
                                  func.lower(ClientRecord.ip).like(like),
                                  func.lower(ClientRecord.hostname).like(like),
                                  func.lower(ClientRecord.ssid).like(like),
                                  func.lower(ClientRecord.ap_name).like(like)))
    stmt = stmt.order_by(ClientRecord.last_seen.desc()).limit(limit)
    full = user.role in ("operator", "admin")
    out = []
    for c in db.scalars(stmt):
        out.append({
            "id": c.id, "site_id": c.site_id, "site_name": names.get(c.site_id),
            "mac": c.mac if full else mask_mac(c.mac),
            "hostname": c.hostname if full else None,
            "ip": c.ip if full else None,
            "ap_name": c.ap_name, "ssid": c.ssid, "vlan": c.vlan, "band": c.band, "channel": c.channel,
            "rssi_dbm": c.rssi_dbm, "snr_db": c.snr_db, "speed_mbps": c.speed_mbps,
            "tx_rate_mbps": c.tx_rate_mbps, "rx_rate_mbps": c.rx_rate_mbps,
            "connected_seconds": c.connected_seconds, "auth_state": c.auth_state,
            "quality": c.quality, "exp_score": c.exp_score, "exp_band": c.exp_band,
            "is_connected": c.is_connected, "simulated": c.simulated,
            "first_seen": _iso(c.first_seen), "last_seen": _iso(c.last_seen),
        })
    return out


# ---------------- dashboard ----------------
@router.get("/dashboard/summary")
def dashboard_summary(site_id: int | None = None, db: Session = Depends(get_db),
                      _: User = Depends(require_role("viewer"))):
    sites = visible_sites(db)
    ids = _site_ids(db, site_id)
    aps = list(db.scalars(select(AccessPoint).where(AccessPoint.site_id.in_(ids))))
    up = sum(1 for a in aps if a.status == "up")
    clients = list(db.scalars(select(ClientRecord).where(
        ClientRecord.site_id.in_(ids), ClientRecord.is_connected.is_(True))))
    snrs = [c.snr_db for c in clients if c.snr_db is not None]
    quality = Counter(c.quality or "unavailable" for c in clients)

    # Channel utilization: latest sample per up AP; unavailable (None) if no AP reports it.
    utils: list[float] = []
    for a in aps:
        if a.status != "up":
            continue
        s = db.scalar(select(ApSample).where(ApSample.ap_id == a.id).order_by(ApSample.ts.desc()).limit(1))
        if s:
            utils += [u for u in (s.util_2g_percent, s.util_5g_percent) if u is not None]

    polled = [s for s in sites if s.id in ids]
    polls_ok = sum(1 for s in polled if s.last_poll_ok)
    # Phase 1 health score (see docs/SCORING.md): 70% AP availability + 30% sites polling OK.
    health = None
    if aps and polled:
        health = round(70 * up / len(aps) + 30 * polls_ok / len(polled), 1)

    alerts = list(db.scalars(select(Alert).where(
        Alert.status.in_(("active", "acknowledged")),
        or_(Alert.site_id.in_(ids), Alert.site_id.is_(None)))))
    sev = Counter(a.severity for a in alerts if a.status == "active")

    exp = summarize([c.exp_score for c in clients], load_config(db))
    return {
        "mode": get_settings().mode,
        "simulated": get_settings().mode == "demo",
        "sites_total": len(polled),
        "aps_total": len(aps), "aps_up": up, "aps_down": len(aps) - up,
        "clients_total": len(clients),
        "avg_snr_db": round(sum(snrs) / len(snrs), 1) if snrs else None,
        "client_quality": {k: quality.get(k, 0) for k in ("good", "fair", "poor", "unavailable")},
        "avg_channel_utilization_percent": round(sum(utils) / len(utils), 1) if utils else None,
        "health_score": health,
        "user_experience": exp,
        "user_experience_score": exp["score"],
        "alerts_active": sum(sev.values()),
        "alerts_by_severity": {"critical": sev.get("critical", 0), "warning": sev.get("warning", 0),
                               "info": sev.get("info", 0)},
        "firmware_versions": dict(Counter(a.firmware or "unknown" for a in aps)),
        "last_poll_at": _iso(max((s.last_poll_at for s in polled if s.last_poll_at), default=None)),
    }


@router.get("/dashboard/history")
def dashboard_history(site_id: int | None = None, hours: int = Query(24, ge=1, le=720),
                      db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    ids = _site_ids(db, site_id)
    since = utcnow() - timedelta(hours=hours)
    rows = db.scalars(select(SiteSample).where(SiteSample.site_id.in_(ids), SiteSample.ts >= since)
                      .order_by(SiteSample.ts))
    buckets: dict[str, dict] = {}
    for r in rows:
        key = r.ts.replace(second=0, microsecond=0).isoformat()
        b = buckets.setdefault(key, {"ts": key, "clients": 0, "aps_up": 0, "aps_total": 0,
                                     "_snr": [], "_util": [], "_exp": []})
        if r.exp_score is not None:
            b["_exp"].append(r.exp_score)
        b["clients"] += r.clients_total
        b["aps_up"] += r.aps_up
        b["aps_total"] += r.aps_total
        if r.avg_snr_db is not None:
            b["_snr"].append(r.avg_snr_db)
        if r.avg_util_percent is not None:
            b["_util"].append(r.avg_util_percent)
    out = []
    for b in buckets.values():
        snr, util, exp = b.pop("_snr"), b.pop("_util"), b.pop("_exp")
        b["experience_score"] = round(sum(exp) / len(exp), 1) if exp else None
        b["avg_snr_db"] = round(sum(snr) / len(snr), 1) if snr else None
        b["avg_util_percent"] = round(sum(util) / len(util), 1) if util else None
        out.append(b)
    return out


# ---------------- alerts ----------------
def alert_out(a: Alert) -> dict:
    return {"id": a.id, "site_id": a.site_id, "ap_id": a.ap_id, "kind": a.kind,
            "severity": a.severity, "message": a.message, "status": a.status,
            "raised_at": _iso(a.raised_at), "acknowledged_by": a.acknowledged_by,
            "acknowledged_at": _iso(a.acknowledged_at), "resolved_at": _iso(a.resolved_at),
            "simulated": a.simulated}


@router.get("/alerts")
def list_alerts(status: str | None = Query(None, pattern="^(active|acknowledged|resolved)$"),
                limit: int = Query(200, ge=1, le=1000),
                db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    simulated = get_settings().mode == "demo"
    stmt = select(Alert).where(Alert.simulated == simulated)
    if status:
        stmt = stmt.where(Alert.status == status)
    return [alert_out(a) for a in db.scalars(stmt.order_by(Alert.raised_at.desc()).limit(limit))]


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, request: Request, db: Session = Depends(get_db),
              user: User = Depends(require_role("operator"))):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    if a.status == "active":
        a.status, a.acknowledged_by, a.acknowledged_at = "acknowledged", user.username, utcnow()
        audit(db, "alert_ack", username=user.username, target=f"alert:{a.id}", request=request)
    return alert_out(a)


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, request: Request, db: Session = Depends(get_db),
                  user: User = Depends(require_role("operator"))):
    a = db.get(Alert, alert_id)
    if not a:
        raise HTTPException(404, "Alert not found")
    if a.status != "resolved":
        a.status, a.resolved_at = "resolved", utcnow()
        audit(db, "alert_resolve", username=user.username, target=f"alert:{a.id}", request=request)
    return alert_out(a)


# ---------------- audit ----------------
@router.get("/audit")
def audit_log(limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db),
              _: User = Depends(require_role("admin"))):
    return [{"id": r.id, "ts": _iso(r.ts), "username": r.username, "action": r.action,
             "target": r.target, "detail": r.detail, "source_ip": r.source_ip, "success": r.success}
            for r in db.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit))]
