"""Digital Experience Monitoring API: scores, history, per-client breakdown, inferred events, configuration."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database.models import AccessPoint, ClientEvent, ClientRecord, Site, SiteSample, User, utcnow
from ..database.session import get_db
from ..experience.config import ExperienceConfig, load_config, save_config
from ..experience.scoring import ClientMetrics, band_for, score_client, summarize
from ..monitoring.collector import visible_sites
from ..security.deps import audit, require_role
from ..security.privacy import mask_mac

router = APIRouter(prefix="/api/experience", tags=["experience"])


def _site_ids(db: Session, site_id: int | None) -> list[int]:
    ids = [s.id for s in visible_sites(db)]
    if site_id is not None:
        if site_id not in ids:
            raise HTTPException(404, "Site not found")
        return [site_id]
    return ids


def _aware(dt):
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("/overview")
def overview(site_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    ids = _site_ids(db, site_id)
    cfg = load_config(db)
    full = user.role in ("operator", "admin")
    clients = list(db.scalars(select(ClientRecord).where(ClientRecord.site_id.in_(ids), ClientRecord.is_connected.is_(True))))
    aps = list(db.scalars(select(AccessPoint).where(AccessPoint.site_id.in_(ids))))
    sites = {s.id: s.name for s in db.scalars(select(Site).where(Site.id.in_(ids)))}
    scored = sorted((c for c in clients if c.exp_score is not None), key=lambda c: c.exp_score)
    return {
        "summary": summarize([c.exp_score for c in clients], cfg),
        "simulated": any(c.simulated for c in clients) or any(a.simulated for a in aps),
        "bands": {"good_min": cfg.bands.good_min, "fair_min": cfg.bands.fair_min},
        "worst_clients": [{
            "id": c.id, "mac": c.mac if full else mask_mac(c.mac), "hostname": c.hostname if full else None,
            "site_name": sites.get(c.site_id), "ap_name": c.ap_name, "ssid": c.ssid,
            "score": c.exp_score, "band": c.exp_band,
        } for c in scored[:10]],
        "aps": [{"id": a.id, "name": a.name, "site_name": sites.get(a.site_id), "status": a.status,
                 "score": a.exp_score, "band": a.exp_band, "client_count": a.client_count}
                for a in sorted(aps, key=lambda a: (a.exp_score is None, a.exp_score if a.exp_score is not None else 0))],
    }


@router.get("/history")
def history(site_id: int | None = None, hours: int = Query(24, ge=1, le=720),
            db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    ids = _site_ids(db, site_id)
    since = utcnow() - timedelta(hours=hours)
    rows = db.scalars(select(SiteSample).where(SiteSample.site_id.in_(ids), SiteSample.ts >= since).order_by(SiteSample.ts))
    buckets: dict[str, dict] = {}
    for r in rows:
        key = r.ts.replace(second=0, microsecond=0).isoformat()
        b = buckets.setdefault(key, {"ts": key, "_s": [], "good": 0, "fair": 0, "poor": 0, "has": False})
        if r.exp_score is not None:
            b["_s"].append(r.exp_score)
            b["good"] += r.exp_good or 0
            b["fair"] += r.exp_fair or 0
            b["poor"] += r.exp_poor or 0
    out = []
    for b in buckets.values():
        s = b.pop("_s"); b.pop("has")
        b["score"] = round(sum(s) / len(s), 1) if s else None
        out.append(b)
    return out


def client_score_result(db: Session, c: ClientRecord) -> dict:
    """Re-derive a client's score (with its full input breakdown) using the current configuration."""
    cfg = load_config(db)
    now = utcnow()
    n = db.scalar(select(func.count()).select_from(ClientEvent).where(
        ClientEvent.mac == c.mac, ClientEvent.site_id == c.site_id, ClientEvent.kind == "disconnect",
        ClientEvent.ts >= now - timedelta(hours=cfg.client.stability.window_hours))) or 0
    return score_client(ClientMetrics(snr_db=c.snr_db, rssi_dbm=c.rssi_dbm, speed_mbps=c.speed_mbps, disconnects=n,
                                      observed_minutes=(now - _aware(c.first_seen)).total_seconds() / 60), cfg)


@router.get("/clients/{client_id}")
def client_detail(client_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    """Why does this client have this score? Re-derives the score with the current configuration."""
    c = db.get(ClientRecord, client_id)
    if not c or c.site_id not in _site_ids(db, None):
        raise HTTPException(404, "Client not found")
    result = client_score_result(db, c)
    full = user.role in ("operator", "admin")
    return {"id": c.id, "mac": c.mac if full else mask_mac(c.mac), "ap_name": c.ap_name, "ssid": c.ssid,
            "connected": c.is_connected, "result": result}


@router.get("/events")
def events(site_id: int | None = None, kind: str | None = Query(None, pattern="^(connect|disconnect|roam)$"),
           limit: int = Query(100, ge=1, le=500), db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    """Connect / disconnect / roam events. INFERRED from successive polls, not reported by the AP."""
    ids = _site_ids(db, site_id)
    q = select(ClientEvent).where(ClientEvent.site_id.in_(ids))
    if kind:
        q = q.where(ClientEvent.kind == kind)
    full = user.role in ("operator", "admin")
    return {"inferred": True, "events": [{
        "id": e.id, "ts": e.ts.isoformat(), "kind": e.kind, "mac": e.mac if full else mask_mac(e.mac),
        "ap_from": e.ap_from, "ap_to": e.ap_to, "ssid": e.ssid, "simulated": e.simulated,
    } for e in db.scalars(q.order_by(ClientEvent.id.desc()).limit(limit))]}


@router.get("/config")
def get_config(db: Session = Depends(get_db), _: User = Depends(require_role("operator"))):
    return load_config(db).model_dump()


@router.put("/config")
def put_config(body: ExperienceConfig, request: Request, db: Session = Depends(get_db),
               admin: User = Depends(require_role("admin"))):
    save_config(db, body, admin.username)
    audit(db, "experience_config", username=admin.username, target="experience", detail="scoring configuration updated",
          request=request)
    return body.model_dump()


@router.delete("/config")
def reset_config(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    save_config(db, ExperienceConfig(), admin.username)
    audit(db, "experience_config", username=admin.username, target="experience", detail="reset to defaults", request=request)
    return ExperienceConfig().model_dump()
