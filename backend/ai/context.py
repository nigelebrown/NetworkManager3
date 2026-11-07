"""What the AI is allowed to see, and the only questions it can ask of the database.

Privacy rules (enforced here, not left to the model):
  * client MACs, IPs and hostnames are NEVER included; clients appear as "client-1", "client-2"... per request;
  * only scores, metrics and admin-defined names (APs, SSIDs, sites) are included;
  * the model cannot run queries: it can only pick one of the allow-listed read-only tools below.
"""
from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any, Callable

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database.models import AccessPoint, Alert, ApSample, ClientEvent, ClientRecord, Site, SiteSample, utcnow
from ..experience.config import load_config
from ..experience.scoring import summarize

NOT_MEASURED = ["channel utilization", "noise floor", "retransmission rate", "latency", "jitter", "packet loss",
                "DHCP failures", "application responsiveness"]


class Pseudonyms:
    """Stable per-request aliases so the model can refer to a client without seeing who it is."""

    def __init__(self) -> None:
        self._m: dict[str, str] = {}

    def __call__(self, mac: str) -> str:
        return self._m.setdefault(mac, f"client-{len(self._m) + 1}")


def _connected(db: Session, ids: list[int]) -> list[ClientRecord]:
    return list(db.scalars(select(ClientRecord).where(ClientRecord.site_id.in_(ids), ClientRecord.is_connected.is_(True))))


def _aps(db: Session, ids: list[int]) -> list[AccessPoint]:
    return list(db.scalars(select(AccessPoint).where(AccessPoint.site_id.in_(ids)).order_by(AccessPoint.name)))


def _disconnects(db: Session, ids: list[int], hours: int) -> list[ClientEvent]:
    return list(db.scalars(select(ClientEvent).where(
        ClientEvent.site_id.in_(ids), ClientEvent.kind == "disconnect", ClientEvent.ts >= utcnow() - timedelta(hours=hours))))


def client_row(c: ClientRecord, alias: Pseudonyms) -> dict[str, Any]:
    return {"client": alias(c.mac), "ap": c.ap_name, "ssid": c.ssid, "snr_db": c.snr_db, "speed_mbps": c.speed_mbps,
            "score": c.exp_score, "band": c.exp_band}


# ---- allow-listed tools (read-only; arguments are validated and bounded) --------------------------------------
class NoArgs(BaseModel):
    pass


class LimitArgs(BaseModel):
    limit: int = Field(5, ge=1, le=10)


class HoursArgs(BaseModel):
    hours: int = Field(24, ge=1, le=168)
    limit: int = Field(5, ge=1, le=10)


def t_ap_overview(db, ids, a, alias):
    return {"aps": [{"ap": x.name, "status": x.status, "clients": x.client_count, "score": x.exp_score, "band": x.exp_band,
                     "model": x.model} for x in _aps(db, ids)]}


def t_worst_clients(db, ids, a, alias):
    rows = sorted((c for c in _connected(db, ids) if c.exp_score is not None), key=lambda c: c.exp_score)[: a.limit]
    return {"worst_clients": [client_row(c, alias) for c in rows]}


def t_disconnects(db, ids, a, alias):
    ev = _disconnects(db, ids, a.hours)
    by_client = Counter(alias(e.mac) for e in ev)
    by_ap = Counter(e.ap_from or "unknown" for e in ev)
    return {"window_hours": a.hours, "inferred_from_polling": True, "total_disconnects": len(ev),
            "by_ap": by_ap.most_common(a.limit), "by_client": by_client.most_common(a.limit)}


def t_roaming(db, ids, a, alias):
    ev = list(db.scalars(select(ClientEvent).where(ClientEvent.site_id.in_(ids), ClientEvent.kind == "roam",
                                                   ClientEvent.ts >= utcnow() - timedelta(hours=a.hours))))
    return {"window_hours": a.hours, "inferred_from_polling": True, "total_roams": len(ev),
            "by_client": Counter(alias(e.mac) for e in ev).most_common(a.limit),
            "paths": Counter(f"{e.ap_from} -> {e.ap_to}" for e in ev).most_common(a.limit)}


def t_active_alerts(db, ids, a, alias):
    names = {x.id: x.name for x in _aps(db, ids)}
    rows = db.scalars(select(Alert).where(Alert.site_id.in_(ids), Alert.status.in_(("active", "acknowledged")))
                      .order_by(Alert.id.desc()).limit(25))
    return {"alerts": [{"kind": x.kind, "severity": x.severity, "status": x.status, "ap": names.get(x.ap_id),
                        "minutes_open": int((utcnow() - (x.raised_at if x.raised_at.tzinfo else x.raised_at.replace(tzinfo=utcnow().tzinfo))).total_seconds() // 60)}
                       for x in rows]}


def t_experience_trend(db, ids, a, alias):
    rows = [r for r in db.scalars(select(SiteSample).where(SiteSample.site_id.in_(ids), SiteSample.ts >= utcnow() - timedelta(hours=a.hours))
                                  .order_by(SiteSample.ts)) if r.exp_score is not None]
    if not rows:
        return {"window_hours": a.hours, "samples": 0}
    s = [r.exp_score for r in rows]
    return {"window_hours": a.hours, "samples": len(s), "first": round(s[0], 1), "last": round(s[-1], 1),
            "min": round(min(s), 1), "max": round(max(s), 1), "average": round(sum(s) / len(s), 1)}


def t_ssid_breakdown(db, ids, a, alias):
    groups: dict[str, list[int | None]] = {}
    for c in _connected(db, ids):
        groups.setdefault(c.ssid or "unknown", []).append(c.exp_score)
    out = []
    for ssid, sc in groups.items():
        vals = [x for x in sc if x is not None]
        out.append({"ssid": ssid, "clients": len(sc), "average_score": round(sum(vals) / len(vals), 1) if vals else None})
    return {"ssids": sorted(out, key=lambda r: -r["clients"])}


TOOLS: dict[str, tuple[str, type[BaseModel], Callable]] = {
    "ap_overview": ("Every AP with status, client count and experience score.", NoArgs, t_ap_overview),
    "worst_clients": ("Clients with the lowest experience scores. args: limit (1-10).", LimitArgs, t_worst_clients),
    "disconnects": ("Disconnect counts by AP and by client over a window. args: hours (1-168), limit (1-10).", HoursArgs, t_disconnects),
    "roaming": ("Roaming counts over a window. args: hours (1-168), limit (1-10).", HoursArgs, t_roaming),
    "active_alerts": ("Currently open alerts.", NoArgs, t_active_alerts),
    "experience_trend": ("How the overall experience score moved. args: hours (1-168).", HoursArgs, t_experience_trend),
    "ssid_breakdown": ("Client count and average experience per SSID.", NoArgs, t_ssid_breakdown),
}


def run_tool(db: Session, ids: list[int], name: str, args: dict[str, Any], alias: Pseudonyms) -> dict[str, Any]:
    desc, model, fn = TOOLS[name]
    return fn(db, ids, model.model_validate(args or {}), alias)


# ---- broad facts for summaries / recommendations --------------------------------------------------------------
def site_facts(db: Session, ids: list[int], hours: int, alias: Pseudonyms) -> dict[str, Any]:
    cfg = load_config(db)
    clients, aps = _connected(db, ids), _aps(db, ids)
    sites = [s.name for s in db.scalars(select(Site).where(Site.id.in_(ids)))]
    util_known = bool(db.scalar(select(func.count()).select_from(ApSample).where(
        ApSample.site_id.in_(ids), (ApSample.util_2g_percent.is_not(None)) | (ApSample.util_5g_percent.is_not(None)))))
    args = HoursArgs(hours=hours, limit=5)
    return {
        "sites": sites, "window_hours": hours,
        "clients_connected": len(clients), "experience": summarize([c.exp_score for c in clients], cfg),
        "aps": t_ap_overview(db, ids, NoArgs(), alias)["aps"],
        "worst_clients": t_worst_clients(db, ids, LimitArgs(limit=5), alias)["worst_clients"],
        "active_alerts": t_active_alerts(db, ids, NoArgs(), alias)["alerts"],
        "disconnects": t_disconnects(db, ids, args, alias),
        "roaming": t_roaming(db, ids, args, alias),
        "experience_trend": t_experience_trend(db, ids, args, alias),
        "ssids": t_ssid_breakdown(db, ids, NoArgs(), alias)["ssids"],
        "not_measured": [m for m in NOT_MEASURED if not (m == "channel utilization" and util_known)],
        "simulated": any(c.simulated for c in clients) or any(a.simulated for a in aps),
    }
