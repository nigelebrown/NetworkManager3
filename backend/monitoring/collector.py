"""Polling: fetch a snapshot per site (live or demo), persist it, raise/resolve alerts."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..aruba.authentication import client_for_site
from ..aruba.errors import ArubaError
from ..aruba.monitoring import SiteSnapshot, collect_snapshot
from ..config import get_settings
from ..database.models import (
    AccessPoint, Alert, ApSample, ClientEvent, ClientRecord, Site, SiteSample, utcnow,
)
from ..database.session import session_scope
from ..experience.config import load_config
from ..experience.scoring import ApMetrics, ClientMetrics, score_ap, score_client, summarize
from . import rules
from .alerts import raise_alert as _raise_alert, resolve_alerts as _resolve_alerts
from .demo import DEMO_SITES, build_demo_snapshot

log = logging.getLogger("sims.collector")


# ---- site visibility -------------------------------------------------------------------
def visible_sites(db: Session) -> list[Site]:
    """Demo mode shows simulated sites; live mode exposes demo sites only when explicitly loaded."""
    if get_settings().mode == "demo":
        cond = Site.simulated.is_(True)
    else:
        from ..database.models import AppSetting
        row = db.get(AppSetting, "demo_dataset")
        cond = Site.simulated.in_([True, False]) if row and row.value.get("loaded") else Site.simulated.is_(False)
    sites = list(db.scalars(select(Site).where(cond).order_by(Site.name)))
    # INM: a site-scoped user (set per request in security.deps.get_current_user) only sees granted sites.
    # Background jobs never set a scope and therefore see every site.
    from ..security.context import get_scope
    scope = get_scope()
    return sites if scope is None else [s for s in sites if s.id in scope]


def ensure_demo_sites(db: Session) -> None:
    for spec in DEMO_SITES:
        if not db.scalar(select(Site).where(Site.name == spec["name"])):
            db.add(Site(name=spec["name"], group=spec["group"], description=spec["description"],
                        simulated=True))
    db.flush()


# ---- persistence -----------------------------------------------------------------------
def _ap_key(ap: dict) -> str:
    return ap.get("mac") or "na:" + hashlib.sha1(str(ap.get("name")).encode()).hexdigest()[:12]


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _event(db: Session, site: Site, mac: str, kind: str, ap_from, ap_to, ssid, ts) -> None:
    db.add(ClientEvent(site_id=site.id, mac=mac, ts=ts, kind=kind, ap_from=ap_from, ap_to=ap_to, ssid=ssid,
                       simulated=site.simulated))


def apply_snapshot(db: Session, site: Site, snap: SiteSnapshot) -> None:
    now = utcnow()
    sim = site.simulated
    existing = {a.mac: a for a in db.scalars(select(AccessPoint).where(AccessPoint.site_id == site.id))}
    seen: set[str] = set()
    prev_uptime = {a.id: a.uptime_seconds for a in existing.values()}
    ap_data: dict[int, dict] = {}
    ap_samples: dict[int, ApSample] = {}

    # Instant names an AP after its MAC until renamed, and `show aps` has no MAC column, so after a rename the
    # name-derived key changes. Match by serial number too so a rename never creates a duplicate AP.
    by_serial = {a.serial: a for a in existing.values() if a.serial}
    for d in snap.aps:
        key = _ap_key(d)
        ap = existing.get(key) or (by_serial.get(d["serial"]) if d.get("serial") else None)
        if ap is not None:
            key = ap.mac
        seen.add(key)
        if ap is None:
            ap = AccessPoint(site_id=site.id, mac=key, name=d.get("name") or key, simulated=sim)
            db.add(ap)
            db.flush()
            existing[key] = ap
        status = d.get("status", "up")
        ap.name = d.get("name") or ap.name
        ap.ip = d.get("ip") or ap.ip
        ap.model = d.get("model") or ap.model
        ap.serial = d.get("serial") or ap.serial
        if d.get("is_master") is not None:
            ap.is_master = d["is_master"]
        ap.firmware = snap.firmware or ap.firmware
        ap.status = status
        ap.simulated = sim
        if status == "up":
            ap.last_seen = now
            ap.uptime_seconds = d.get("uptime_seconds")
            ap.cpu_percent = d.get("cpu_percent")
            ap.memory_percent = d.get("memory_percent")
            ap.client_count = d.get("client_count")
            ap.radios = d.get("radios")
            _resolve_alerts(db, site_id=site.id, ap_id=ap.id, kind="ap_offline")
        else:
            ap.client_count = 0
            _raise_alert(db, site_id=site.id, ap_id=ap.id, kind="ap_offline", severity="critical",
                         message=f"AP {ap.name} is offline", simulated=sim)
        sample = ApSample(
            ap_id=ap.id, site_id=site.id, ts=now, status=status,
            client_count=ap.client_count, cpu_percent=d.get("cpu_percent"),
            memory_percent=d.get("memory_percent"),
            util_2g_percent=d.get("util_2g_percent"), util_5g_percent=d.get("util_5g_percent"),
            simulated=sim,
        )
        db.add(sample)
        ap_data[ap.id], ap_samples[ap.id] = d, sample

    # Known APs missing from this poll are down.
    for key, ap in existing.items():
        if key not in seen and ap.status != "down":
            ap.status = "down"
            ap.client_count = 0
            _raise_alert(db, site_id=site.id, ap_id=ap.id, kind="ap_offline", severity="critical",
                         message=f"AP {ap.name} is offline (missing from poll)", simulated=sim)
            db.add(ApSample(ap_id=ap.id, site_id=site.id, ts=now, status="down", simulated=sim))

    # Clients: upsert current ones, mark others disconnected, and INFER connect/disconnect/roam events
    # by comparing with the previous poll (events between two polls are invisible).
    cur = {c.mac: c for c in db.scalars(select(ClientRecord).where(ClientRecord.site_id == site.id))}
    live: list[ClientRecord] = []
    if snap.clients_ok:
        first_poll = not cur  # the initial inventory is not "connect" activity
        live_macs = set()
        for d in snap.clients:
            mac = d["mac"]
            live_macs.add(mac)
            rec = cur.get(mac)
            if rec is None:
                rec = ClientRecord(site_id=site.id, mac=mac, first_seen=now)
                db.add(rec)
                cur[mac] = rec
                if not first_poll:
                    _event(db, site, mac, "connect", None, d.get("ap_name"), d.get("ssid"), now)
            elif not rec.is_connected:
                _event(db, site, mac, "connect", None, d.get("ap_name"), d.get("ssid"), now)
            elif rec.ap_name and d.get("ap_name") and rec.ap_name != d.get("ap_name"):
                _event(db, site, mac, "roam", rec.ap_name, d.get("ap_name"), d.get("ssid"), now)
            for f in ("hostname", "ip", "ssid", "ap_name", "channel", "band", "snr_db", "rssi_dbm",
                      "speed_mbps", "quality", "auth_state", "vlan"):
                setattr(rec, f, d.get(f))
            rec.is_connected, rec.last_seen, rec.simulated = True, now, sim
            live.append(rec)
        for mac, rec in cur.items():
            if mac not in live_macs and rec.is_connected:
                rec.is_connected = False
                rec.exp_score, rec.exp_band, rec.poor_streak = None, None, 0
                _event(db, site, mac, "disconnect", rec.ap_name, None, rec.ssid, now)
    else:  # client list could not be read this poll: keep previous state, do not invent disconnects
        live = [r for r in cur.values() if r.is_connected]

    # Experience scoring (AP-reported inputs + inferred stability), then alert rules.
    cfg = load_config(db)
    window = now - timedelta(hours=cfg.client.stability.window_hours)
    disc = {m: n for m, n in db.execute(
        select(ClientEvent.mac, func.count()).where(
            ClientEvent.site_id == site.id, ClientEvent.kind == "disconnect", ClientEvent.ts >= window,
        ).group_by(ClientEvent.mac))}
    for rec in live:
        observed = (now - _aware(rec.first_seen)).total_seconds() / 60
        r = score_client(ClientMetrics(snr_db=rec.snr_db, rssi_dbm=rec.rssi_dbm, speed_mbps=rec.speed_mbps,
                                       disconnects=disc.get(rec.mac, 0), observed_minutes=observed), cfg)
        rec.exp_score, rec.exp_band = r["score"], r["band"]
        rec.poor_streak = (rec.poor_streak or 0) + 1 if r["band"] == "poor" else 0
    for ap in existing.values():
        if ap.status != "up":
            ap.exp_score, ap.exp_band = None, "offline"
            continue
        d = ap_data.get(ap.id, {})
        utils = [u for u in (d.get("util_2g_percent"), d.get("util_5g_percent")) if u is not None]
        r = score_ap(ApMetrics(status="up", client_scores=[c.exp_score for c in live if c.ap_name == ap.name and c.exp_score is not None],
                               util_pct=max(utils) if utils else None, client_count=ap.client_count,
                               cpu_pct=ap.cpu_percent, memory_pct=ap.memory_percent), cfg)
        ap.exp_score, ap.exp_band = r["score"], r["band"]
        if ap.id in ap_samples:
            ap_samples[ap.id].exp_score = r["score"]
    site_exp = summarize([c.exp_score for c in live], cfg)

    snrs = [c["snr_db"] for c in snap.clients if c.get("snr_db") is not None]
    rssis = [c["rssi_dbm"] for c in snap.clients if c.get("rssi_dbm") is not None]
    utils = [u for d in snap.aps for u in (d.get("util_2g_percent"), d.get("util_5g_percent")) if u is not None]
    db.add(SiteSample(
        site_id=site.id, ts=now,
        aps_total=len(existing), aps_up=sum(1 for a in existing.values() if a.status == "up"),
        clients_total=len(snap.clients),
        avg_snr_db=round(sum(snrs) / len(snrs), 1) if snrs else None,
        avg_rssi_dbm=round(sum(rssis) / len(rssis), 1) if rssis else None,
        avg_util_percent=round(sum(utils) / len(utils), 1) if utils else None,
        exp_score=site_exp["score"], exp_good=site_exp["good"], exp_fair=site_exp["fair"], exp_poor=site_exp["poor"],
        simulated=sim,
    ))
    rules.evaluate(db, site, aps=list(existing.values()), prev_uptime=prev_uptime, clients=live,
                   disconnects=disc, cfg=cfg, now=now)
    _resolve_alerts(db, site_id=site.id, ap_id=None, kind="poll_failed")
    site.last_poll_at, site.last_poll_ok, site.last_poll_error = now, True, None
    if snap.warnings:
        site.last_poll_error = "; ".join(snap.warnings)[:500]


def poll_site(db: Session, site: Site, transport: httpx.BaseTransport | None = None) -> bool:
    """Poll one site. Never raises for expected failures; returns success."""
    try:
        if site.simulated:
            snap = build_demo_snapshot(site.name)
        else:
            with client_for_site(site, transport) as c:
                snap = collect_snapshot(c)
        apply_snapshot(db, site, snap)
        return True
    except ArubaError as exc:
        site.last_poll_at, site.last_poll_ok, site.last_poll_error = utcnow(), False, str(exc)[:500]
        _raise_alert(db, site_id=site.id, ap_id=None, kind="poll_failed", severity="warning",
                     message=f"Cannot poll {site.name}: {exc}"[:500], simulated=site.simulated)
        log.warning("Poll failed for site %s: %s", site.name, exc)
        return False
    except Exception as exc:  # unexpected: keep the scheduler alive, log without secrets
        site.last_poll_at, site.last_poll_ok = utcnow(), False
        site.last_poll_error = f"Unexpected error: {type(exc).__name__}"
        log.exception("Unexpected error polling site %s", site.name)
        return False


def poll_all() -> None:
    """Scheduler entry point."""
    with session_scope() as db:
        for site in visible_sites(db):
            if site.enabled:
                poll_site(db, site)
                db.commit()
        prune_old(db)


def prune_old(db: Session) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=get_settings().retention_days)
    db.execute(delete(ApSample).where(ApSample.ts < cutoff))
    db.execute(delete(SiteSample).where(SiteSample.ts < cutoff))
    db.execute(delete(ClientEvent).where(ClientEvent.ts < cutoff))
