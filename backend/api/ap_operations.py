"""Wireless operations: cluster-grouped AP fleet, AP auto-onboarding, AP health rating report (Gold/Silver/Bronze)
and client connectivity. Read-only except the auto-onboarding REST bootstrap, which goes through the pre-existing
gated `ssh.enable_rest_api`.
"""
from __future__ import annotations

import csv
import io
import ipaddress
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database.models import AccessPoint, ApSample, AppSetting, ClientEvent, ClientRecord, Site, User
from ..database.models_inm import InmCluster, InmDevice, InmSite
from ..database.session import get_db
from ..domain import ap_health as H
from ..infrastructure import ap_autodiscovery as AD
from ..monitoring.collector import visible_sites
from ..security.deps import audit, require_role
from ..security.privacy import mask_mac

from ..security.context import get_scope


def require_global_scope():
    """Fleet-wide workspaces are refused to site-scoped users (kept from the production branch)."""
    if get_scope() is not None:
        raise HTTPException(403, "This fleet-wide workspace requires access to all sites.")


router = APIRouter(prefix="/api/v1/wireless", tags=["wireless"], dependencies=[Depends(require_role("viewer")), Depends(require_global_scope)])
UNLINKED = "unlinked"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


# ---------------------------------------------------------------------------------------- guidance
def guidance(state: str, reason: str | None, *, has_ip: bool, has_cred: bool, in_cluster: bool,
             transport: str | None = None) -> str:
    r = (reason or "").lower()
    if state == "ONLINE":
        return ("Healthy and reporting." + (" Read over SSH CLI because REST is off on this cluster — enable REST for "
                                           "per-AP RF/CPU detail." if transport == "ssh_cli" else ""))
    if state == "DEGRADED":
        return "Reporting, but measured health is poor — open Device 360° and the AP health report for the trigger."
    if state == "OFFLINE":
        return "Not answering. Check PoE/switchport and uplink; confirm the AP is still a member of its cluster."
    if state == "STALE":
        return "Last observation is old — the cluster poll is failing or slow. Check the cluster's last poll error."
    if not has_ip:
        return "No management IP recorded. Add the IP (inventory) so the platform can find its cluster."
    if not has_cred:
        return "No credential profile bound. Assign an AP credential profile, then run Auto-onboard."
    if "controller poll failed" in r:
        return "This AP's cluster could not be polled. Open the cluster row to see the error (REST off, auth, TLS)."
    if not in_cluster:
        return ("Registered but not linked to a polled Instant cluster, so nothing reads it. Run Auto-onboard: it finds "
                "the Virtual Controller, registers the cluster once and links every member AP.")
    return "No persisted observation yet — wait for the next cluster poll (default 60 s)."


# ---------------------------------------------------------------------------------------- fleet
def _radio(radios: list | None, band: str) -> dict:
    for r in radios or []:
        if str(r.get("band") or "").startswith(band):
            return {"channel": r.get("channel"), "util_percent": r.get("util_percent"),
                    "noise_floor_dbm": r.get("noise_floor_dbm"), "tx_power_dbm": r.get("tx_power_dbm")}
    return {"channel": None, "util_percent": None, "noise_floor_dbm": None, "tx_power_dbm": None}


def _latest_tiers(db: Session) -> dict[str, dict]:
    row = db.get(AppSetting, "ap-health:latest")
    if not row:
        return {}
    return {r["ap"]: {"tier": r["tier"], "trigger": r["trigger"]} for r in row.value.get("rows", [])} | {
        "_generated_at": {"at": row.value.get("generated_at")}}


def build_fleet(db: Session) -> dict[str, Any]:
    from ..database.models_onboarding import OnboardingDeviceCredential
    from ..infrastructure.device_state import canonical_inventory, plans_by_device, state_of
    now = _now()
    sites = {s.id: s for s in visible_sites(db)}
    clusters_by_legacy = {c.legacy_site_id: c for c in db.scalars(select(InmCluster)) if c.legacy_site_id}
    phys = {s.id: s.name for s in db.scalars(select(InmSite))}
    legacy_aps = {a.id: a for a in db.scalars(select(AccessPoint).where(AccessPoint.site_id.in_(list(sites) or [-1])))}
    creds = {b.device_id for b in db.scalars(select(OnboardingDeviceCredential))}
    plans = plans_by_device(db)
    tiers = _latest_tiers(db)
    show_demo = any(s.simulated for s in sites.values())
    devices = canonical_inventory(list(db.scalars(select(InmDevice).where(
        InmDevice.family == "access_point",
        InmDevice.source_type.in_(["REAL", "DEMO"] if show_demo else ["REAL"])))))
    # Radio utilisation is stored per poll in ap_samples (the radio dicts only carry it on some firmware paths).
    latest_util: dict[int, ApSample] = {}
    for smp in db.scalars(select(ApSample).where(ApSample.ts >= now - timedelta(minutes=30),
                                                 ApSample.ap_id.in_(list(legacy_aps) or [-1])).order_by(ApSample.ts)):
        latest_util[smp.ap_id] = smp
    aps = []
    for d in devices:
        controller = sites.get(d.legacy_site_id) if d.legacy_site_id is not None else None
        if d.legacy_site_id is not None and controller is None:
            continue  # cluster not visible to this user
        a = legacy_aps.get(d.legacy_ap_id) if d.legacy_ap_id is not None else None
        st = state_of(d, plans.get(d.id), now=now, controller=controller)
        cond = {"state": st["state"]}
        r2, r5 = _radio(a.radios if a else None, "2"), _radio(a.radios if a else None, "5")
        smp = latest_util.get(a.id) if a else None
        if smp is not None:
            if r2["util_percent"] is None:
                r2["util_percent"] = smp.util_2g_percent
            if r5["util_percent"] is None:
                r5["util_percent"] = smp.util_5g_percent
        if st["state"] in ("ONLINE", "DEGRADED") and a:
            cond.update(util_2g_percent=r2["util_percent"], util_5g_percent=r5["util_percent"],
                        cpu_percent=a.cpu_percent, memory_percent=a.memory_percent, clients=a.client_count,
                        noise_2g_dbm=r2["noise_floor_dbm"], noise_5g_dbm=r5["noise_floor_dbm"])
        ctier, ctrig, unavailable = H.condition_tier(cond)
        name = d.hostname or d.serial_number
        ev = tiers.get(name)
        tier = min([t for t in (ctier, ev and ev["tier"]) if t] or ["UNRATED"], key=lambda t: H.TIER_RANK[t])
        cl = clusters_by_legacy.get(d.legacy_site_id)
        transport = AD.site_transport(db, controller.id)["transport"] if controller else None
        aps.append({
            "device_id": d.id, "name": name, "serial": d.serial_number, "model": d.model, "firmware": d.firmware,
            "ip": (d.mgmt_addresses or [None])[0], "mac": a.mac if a else None, "source_type": d.source_type,
            "site": phys.get(d.site_id), "site_id": d.site_id,
            "cluster_key": str(controller.id) if controller else UNLINKED,
            "cluster": (cl.name if cl else controller.name) if controller else None,
            "is_vc": bool(a and a.is_master), "state": st["state"], "state_reason": st["state_reason"],
            "last_seen": _iso(a.last_seen) if a else st.get("last_persisted_at"),
            "clients": a.client_count if a else None, "uptime_seconds": a.uptime_seconds if a else None,
            "cpu_percent": a.cpu_percent if a else None, "memory_percent": a.memory_percent if a else None,
            "radio_2g": r2, "radio_5g": r5, "exp_score": a.exp_score if a else None,
            "tier": tier, "condition_tier": ctier, "condition_triggers": ctrig,
            "event_tier": ev["tier"] if ev else None, "event_trigger": ev["trigger"] if ev else None,
            "unavailable_inputs": unavailable, "transport": transport,
            "guidance": guidance(st["state"], st["state_reason"], has_ip=bool(d.mgmt_addresses),
                                 has_cred=d.id in creds or controller is not None, in_cluster=controller is not None,
                                 transport=transport),
        })
    groups: dict[str, dict] = {}
    for s in sites.values():
        t = AD.site_transport(db, s.id)
        groups[str(s.id)] = {"key": str(s.id), "name": (clusters_by_legacy.get(s.id).name
                             if clusters_by_legacy.get(s.id) else s.name), "vc_host": s.vc_host,
                             "simulated": s.simulated, "enabled": s.enabled, "last_poll_at": _iso(s.last_poll_at),
                             "last_poll_ok": s.last_poll_ok, "last_poll_error": s.last_poll_error,
                             "transport": t.get("transport"), "rest_state": t.get("rest_state")}
    groups[UNLINKED] = {"key": UNLINKED, "name": "Not linked to a polled cluster", "vc_host": None, "simulated": False,
                        "enabled": None, "last_poll_at": None, "last_poll_ok": None, "last_poll_error": None,
                        "transport": None, "rest_state": None}
    for g in groups.values():
        rows = [x for x in aps if x["cluster_key"] == g["key"]]
        u2 = [x["radio_2g"]["util_percent"] for x in rows if x["radio_2g"]["util_percent"] is not None]
        u5 = [x["radio_5g"]["util_percent"] for x in rows if x["radio_5g"]["util_percent"] is not None]
        g.update(aps_total=len(rows), aps_online=sum(1 for x in rows if x["state"] == "ONLINE"),
                 states=dict(Counter(x["state"] for x in rows)), tiers=dict(Counter(x["tier"] for x in rows)),
                 clients=sum(x["clients"] or 0 for x in rows),
                 util_2g_avg=round(sum(u2) / len(u2), 1) if u2 else None,
                 util_5g_avg=round(sum(u5) / len(u5), 1) if u5 else None)
    cluster_list = sorted([g for g in groups.values() if g["aps_total"] or g["key"] != UNLINKED],
                          key=lambda g: (g["key"] == UNLINKED, g["name"].casefold()))
    states = Counter(x["state"] for x in aps)
    return {"generated_at": now.isoformat(), "clusters": cluster_list, "aps": aps,
            "summary": {"aps_total": len(aps), "states": dict(states), "tiers": dict(Counter(x["tier"] for x in aps)),
                        "monitored": sum(1 for x in aps if x["cluster_key"] != UNLINKED),
                        "unlinked": sum(1 for x in aps if x["cluster_key"] == UNLINKED),
                        "attention": sum(v for k, v in states.items() if k != "ONLINE"),
                        "clients": sum(x["clients"] or 0 for x in aps),
                        "clusters_polled_ok": sum(1 for g in cluster_list if g["last_poll_ok"]),
                        "clusters_failing": sum(1 for g in cluster_list if g["last_poll_ok"] is False),
                        "health_report_at": (tiers.get("_generated_at") or {}).get("at")}}


@router.get("/fleet")
def fleet(db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    return build_fleet(db)


# ---------------------------------------------------------------------------------------- auto-onboarding
class Targets(BaseModel):
    device_ids: list[str] | None = Field(default=None, max_length=2000)


class KeyApproval(BaseModel):
    items: list[dict] = Field(min_length=1, max_length=2000)
    confirm: str = Field(min_length=1, max_length=64)


class AutoOnboard(Targets):
    auto_enable_rest: bool = False


@router.post("/autodiscover/scan-keys")
def scan_keys(body: Targets, request: Request, db: Session = Depends(get_db),
              admin: User = Depends(require_role("admin"))):
    """Unauthenticated SSH host-key scan of registered APs that are not yet linked (no credentials are sent)."""
    from ..api.quick_devices import _permit_probe
    ips = []
    for ip in AD.wanted_ips(db, body.device_ids):
        try:
            _permit_probe(ipaddress.IPv4Address(ip))
            ips.append(ip)
        except Exception:
            continue
    results = AD.scan_host_keys(db, ips, admin.username)
    audit(db, "ap_hostkey_scan", username=admin.username, target="access_points",
          detail=f"scanned={len(results)} with_key={sum(1 for r in results if r.get('fingerprint'))}", request=request)
    return {"scanned": len(results), "results": results, "pending": AD.pending_fingerprints(db)}


@router.get("/autodiscover/pending-keys")
def pending_keys(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return AD.pending_fingerprints(db)


@router.post("/autodiscover/approve-keys")
def approve_keys(body: KeyApproval, request: Request, db: Session = Depends(get_db),
                 admin: User = Depends(require_role("admin"))):
    if body.confirm != "APPROVE HOST KEYS":
        raise HTTPException(422, "Type APPROVE HOST KEYS to confirm you verified these fingerprints.")
    out = AD.approve_fingerprints(db, body.items, admin.username)
    audit(db, "ap_hostkey_approved", username=admin.username, target="access_points",
          detail=f"approved={sum(1 for x in out if x['approved'])} of {len(out)}", request=request)
    return out


@router.post("/autodiscover", status_code=202)
def autodiscover(body: AutoOnboard, request: Request, db: Session = Depends(get_db),
                 admin: User = Depends(require_role("admin"))):
    try:
        out = AD.start_job(admin.username, device_ids=body.device_ids, auto_enable_rest=body.auto_enable_rest)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    audit(db, "ap_auto_onboarding_started", username=admin.username, target="access_points",
          detail=f"auto_enable_rest={body.auto_enable_rest} scope={len(body.device_ids or []) or 'all'}", request=request)
    return out


@router.get("/autodiscover/status")
def autodiscover_status(db: Session = Depends(get_db), _: User = Depends(require_role("operator"))):
    return AD.job_status(db)


# ---------------------------------------------------------------------------------------- AP health report
_LOKI_CATS = [  # (category, include regex, exclude regex) — exclusive like domain.classify ordering
    (H.DEAUTH, r"(?i)de-?auth|dis-?assoc", None),
    (H.REJECT, r"(?i)(re)?assoc\w*.{0,40}(reject|denied|refus|fail)|(reject|denied).{0,40}(re)?assoc",
     r"(?i)de-?auth|dis-?assoc"),
    (H.DHCP_TIMEOUT, r"(?i)dhcp.{0,60}(time ?-?out|timed out|no (response|offer)|fail)",
     r"(?i)de-?auth|dis-?assoc|assoc\w*.{0,40}(reject|denied)"),
    (H.DHCP_ACK, r"(?i)dhcp.{0,30}\back\b|dhcpack", r"(?i)de-?auth|dis-?assoc|time ?-?out|timed out"),
    (H.AUTH, r"(?i)auth\w*.{0,40}(fail|reject|timeout|denied|invalid)|802\.1x|\beap\b|radius|mic failure|4-?way|"
             r"key handshake|security", r"(?i)de-?auth|dis-?assoc|assoc\w*.{0,40}(reject|denied)|dhcp"),
    (H.ARM, r"(?i)\barm\b|channel change|changed channel|power change|radar detect|dfs",
     r"(?i)de-?auth|dis-?assoc|reject|dhcp|auth"),
]


def _loki_events(hours: int) -> tuple[list[dict], str | None]:
    from ..infrastructure.runtime import log_store
    store = log_store()
    if store is None or not hasattr(store, "count_by"):
        return [], "Log service not configured"
    rows: list[dict] = []
    try:
        totals = store.count_by(f'sum by (host) (count_over_time({{source="syslog"}} [{hours}h]))')
        for host, n in totals.items():
            rows.append({"ap": host, "category_id": H.OTHER, "count": int(n)})
        for cat, inc, exc in _LOKI_CATS:
            q = f'{{source="syslog"}} |~ `{inc}`' + (f' !~ `{exc}`' if exc else "")
            for host, n in store.count_by(f"sum by (host) (count_over_time({q} [{hours}h]))").items():
                rows.append({"ap": host, "category_id": cat, "count": int(n)})
                rows.append({"ap": host, "category_id": H.OTHER, "count": -int(n)})  # moved out of "other"
    except Exception as exc:
        return [], f"Loki query failed: {str(exc)[:160]}"
    return rows, None


def _db_events(db: Session, since: datetime) -> list[dict]:
    from ..database.models_phases import DeviceLogEvent
    names = {d.id: d.hostname or d.serial_number for d in db.scalars(select(InmDevice).where(
        InmDevice.family == "access_point"))}
    out = []
    for e in db.scalars(select(DeviceLogEvent).where(DeviceLogEvent.observed_at >= since).limit(200000)):
        ap = names.get(e.device_id) if e.device_id else None
        if ap:
            out.append({"ap": ap, "text": e.message, "hour": e.observed_at.hour})
    return out


def _aliases(fleet_rows: list[dict]) -> dict[str, str]:
    out = {}
    for r in fleet_rows:
        for k in (r.get("ip"), r.get("mac"), r.get("serial"), r.get("name")):
            if k:
                out[str(k)] = r["name"]
    return out


def _conditions(fleet_rows: list[dict], cluster: str | None) -> dict[str, dict]:
    out = {}
    for r in fleet_rows:
        if cluster and r["cluster_key"] != cluster:
            continue
        out[r["name"]] = {"device_id": r["device_id"], "site": r["site"], "cluster": r["cluster"], "model": r["model"],
                          "state": r["state"], "state_reason": r["state_reason"], "clients_now": r["clients"],
                          "util_2g_percent": r["radio_2g"]["util_percent"], "util_5g_percent": r["radio_5g"]["util_percent"],
                          "cpu_percent": r["cpu_percent"], "memory_percent": r["memory_percent"],
                          "channel_2g": r["radio_2g"]["channel"], "channel_5g": r["radio_5g"]["channel"],
                          "clients": r["clients"] if r["state"] in ("ONLINE", "DEGRADED") else None,
                          "noise_2g_dbm": r["radio_2g"]["noise_floor_dbm"], "noise_5g_dbm": r["radio_5g"]["noise_floor_dbm"]}
        if r["state"] not in ("ONLINE", "DEGRADED"):
            for k in ("util_2g_percent", "util_5g_percent", "cpu_percent", "memory_percent", "noise_2g_dbm", "noise_5g_dbm"):
                out[r["name"]][k] = None
    return out


def build_report(db: Session, *, hours: int, cluster: str | None, events: list[dict] | None = None,
                 source_label: str | None = None) -> dict:
    fl = build_fleet(db)
    since = _now() - timedelta(hours=hours)
    notes = []
    if events is None:
        loki, err = _loki_events(hours)
        if err:
            notes.append(err + " — event evidence from syslog unavailable; condition evidence still rated.")
        events = loki + _db_events(db, since)
        source_label = "Syslog (Loki) + ingested device log events"
    raw = H.aggregate([e for e in events if (e.get("count") or 1) > 0])
    # negative "other" corrections from the Loki path
    for e in events:
        if (e.get("count") or 1) < 0 and e["ap"] in raw:
            raw[e["ap"]].counts[H.OTHER] += e["count"]
    for ev in raw.values():
        ev.total = sum(max(v, 0) for v in ev.counts.values())
        ev.counts[H.OTHER] = max(ev.counts[H.OTHER], 0)
    conditions = _conditions(fl["aps"], cluster)
    aliases = _aliases(fl["aps"])
    if cluster:
        allowed = set(conditions)
        raw = {k: v for k, v in raw.items() if aliases.get(k, k) in allowed}
    rows = H.build_rows(raw, conditions, aliases)
    if not raw:
        notes.append("No AP events in this window: tiers are based on the measured point-in-time condition only. "
                     "Point Instant syslog at the collector (UDP 514) or import an event export.")
    cname = next((c["name"] for c in fl["clusters"] if c["key"] == cluster), None) if cluster else "All clusters"
    return {"id": str(uuid.uuid4()), "generated_at": _now().isoformat(), "window_hours": hours,
            "window_start": since.isoformat(), "window_end": _now().isoformat(), "scope": cname,
            "cluster": cluster, "source": source_label, "notes": notes,
            **H.summarize(rows, raw), "method": H.METHOD, "rows": rows}


@router.get("/health-report")
def health_report(hours: int = Query(24, ge=1, le=168), cluster: str | None = Query(None, max_length=40),
                  db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    rep = build_report(db, hours=hours, cluster=cluster)
    if cluster is None:
        _store(db, "ap-health:latest", rep, user.username)
    return rep


def _store(db: Session, key: str, value: dict, actor: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by=actor, updated_at=_now()))
    else:
        row.value, row.updated_by, row.updated_at = value, actor, _now()
    db.flush()


_COLS = {
    "ap": ("ap name", "ap", "device name", "device", "access point", "ap_name", "hostname", "source"),
    "text": ("description", "event description", "event", "message", "details", "event type", "type", "reason"),
    "category": ("category", "event category", "class"),
    "client": ("client mac", "client mac address", "client", "station", "sta mac", "user mac", "mac address"),
    "ssid": ("ssid", "essid", "network", "wlan"),
    "band": ("band", "radio band", "radio"),
    "time": ("occurred on", "time", "timestamp", "date", "event time", "occurred at", "created at"),
}


def parse_event_csv(text: str) -> list[dict]:
    reader = csv.reader(io.StringIO(text))
    header, rows = None, []
    for raw in reader:
        if header is None:
            low = [h.strip().lower() for h in raw]
            if any(h in _COLS["ap"] for h in low) and any(h in _COLS["text"] for h in low):
                header = low
            continue
        if not any(raw):
            continue
        rec = {h: (raw[i].strip() if i < len(raw) else "") for i, h in enumerate(header)}

        def pick(k):
            return next((rec[a] for a in _COLS[k] if rec.get(a)), None)
        hour = None
        t = pick("time")
        if t:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S", "%d %b %Y %H:%M:%S",
                        "%m/%d/%Y %I:%M:%S %p", "%Y-%m-%d %H:%M"):
                try:
                    hour = datetime.strptime(t.split(".")[0].replace("Z", "")[:len(fmt) + 6].strip(), fmt).hour
                    break
                except ValueError:
                    continue
        text_parts = [pick("text") or ""]
        rows.append({"ap": pick("ap"), "text": " ".join(text_parts), "category": pick("category"),
                     "client": pick("client"), "ssid": pick("ssid"), "band": pick("band"), "hour": hour})
    if header is None:
        raise ValueError("No header row with an AP/device column and a description/message column was found.")
    return rows


@router.post("/health-report/import")
async def import_events(file: UploadFile = File(...), label: str = Form("", max_length=120),
                        cluster: str | None = Form(None), db: Session = Depends(get_db),
                        user: User = Depends(require_role("operator"))):
    """Rate APs from an Aruba event/log export (CSV). The export is parsed in memory; only the computed report is
    kept (as a snapshot), never the raw rows."""
    data = await file.read()
    if len(data) > 60 * 1024 * 1024:
        raise HTTPException(413, "Export larger than 60 MB; split it by time window.")
    try:
        events = parse_event_csv(data.decode("utf-8-sig", errors="replace"))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    rep = build_report(db, hours=24, cluster=cluster or None, events=events,
                       source_label=f"Imported export: {file.filename} ({len(events):,} events)")
    rep["label"] = label or file.filename
    rep["window_hours"] = None
    _save_snapshot(db, rep, user.username)
    audit(db, "ap_health_import", username=user.username, target="ap-health",
          detail=f"{file.filename}: {len(events)} events, {len(rep['rows'])} APs, tiers={rep['tiers']}")
    return rep


def _save_snapshot(db: Session, rep: dict, actor: str) -> None:
    rep.setdefault("label", f"Snapshot {rep['generated_at'][:16].replace('T', ' ')}")
    rep["saved_by"] = actor
    _store(db, f"ap-health-snap:{rep['id'][:20]}", rep, actor)


class SnapshotIn(BaseModel):
    hours: int = Field(24, ge=1, le=168)
    cluster: str | None = Field(None, max_length=40)
    label: str = Field("", max_length=120)


@router.post("/health-report/snapshots", status_code=201)
def save_snapshot(body: SnapshotIn, db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    rep = build_report(db, hours=body.hours, cluster=body.cluster)
    if body.label:
        rep["label"] = body.label
    _save_snapshot(db, rep, user.username)
    audit(db, "ap_health_snapshot", username=user.username, target="ap-health", detail=f"tiers={rep['tiers']}")
    return {"id": rep["id"][:20], "label": rep["label"]}


@router.get("/health-report/snapshots")
def list_snapshots(db: Session = Depends(get_db), _: User = Depends(require_role("viewer"))):
    out = []
    for r in db.scalars(select(AppSetting).where(AppSetting.key.startswith("ap-health-snap:"))):
        v = r.value
        out.append({"id": r.key.split(":", 1)[1], "label": v.get("label"), "generated_at": v.get("generated_at"),
                    "scope": v.get("scope"), "source": v.get("source"), "tiers": v.get("tiers"), "saved_by": v.get("saved_by")})
    return sorted(out, key=lambda x: x["generated_at"] or "", reverse=True)


@router.get("/health-report/snapshots/{snap_id}")
def get_snapshot(snap_id: str, compare_to: str | None = None, db: Session = Depends(get_db),
                 _: User = Depends(require_role("viewer"))):
    row = db.get(AppSetting, f"ap-health-snap:{snap_id[:20]}")
    if not row:
        raise HTTPException(404, "Snapshot not found")
    rep = dict(row.value)
    if compare_to:
        other = db.get(AppSetting, f"ap-health-snap:{compare_to[:20]}")
        if not other:
            raise HTTPException(404, "Comparison snapshot not found")
        rep["movement"] = H.movement(other.value.get("rows", []), rep.get("rows", []))
        rep["compared_to"] = {"id": compare_to, "label": other.value.get("label"),
                              "generated_at": other.value.get("generated_at")}
    return rep


@router.delete("/health-report/snapshots/{snap_id}")
def delete_snapshot(snap_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("operator"))):
    row = db.get(AppSetting, f"ap-health-snap:{snap_id[:20]}")
    if not row:
        raise HTTPException(404, "Snapshot not found")
    db.delete(row)
    audit(db, "ap_health_snapshot_deleted", username=user.username, target=f"ap-health:{snap_id}")
    return {"deleted": snap_id}


# ---------------------------------------------------------------------------------------- client connectivity
@router.get("/clients")
def clients(hours: int = Query(24, ge=1, le=168), cluster: str | None = Query(None, max_length=40),
            ap: str | None = Query(None, max_length=100), ssid: str | None = Query(None, max_length=64),
            band: str | None = Query(None, pattern="^(2.4GHz|5GHz)$"),
            quality: str | None = Query(None, pattern="^(good|fair|poor|unavailable)$"),
            connected: bool | None = None, q: str = Query("", max_length=64), limit: int = Query(1000, ge=1, le=5000),
            db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    sites = {s.id: s for s in visible_sites(db)}
    clusters_by_legacy = {c.legacy_site_id: c.name for c in db.scalars(select(InmCluster)) if c.legacy_site_id}
    ids = list(sites)
    if cluster:
        try:
            ids = [int(cluster)] if int(cluster) in sites else []
        except ValueError:
            ids = []
    since = _now() - timedelta(hours=hours)
    stmt = select(ClientRecord).where(ClientRecord.site_id.in_(ids or [-1]), ClientRecord.last_seen >= since)
    if ap:
        stmt = stmt.where(ClientRecord.ap_name == ap)
    if ssid:
        stmt = stmt.where(ClientRecord.ssid == ssid)
    if band:
        stmt = stmt.where(ClientRecord.band == band)
    if quality:
        stmt = stmt.where(func.coalesce(ClientRecord.quality, "unavailable") == quality)
    if connected is not None:
        stmt = stmt.where(ClientRecord.is_connected.is_(connected))
    if q.strip():
        like = f"%{q.strip().lower()}%"
        stmt = stmt.where(func.lower(func.coalesce(ClientRecord.hostname, "")).like(like)
                          | func.lower(ClientRecord.mac).like(like) | func.lower(func.coalesce(ClientRecord.ap_name, "")).like(like))
    recs = list(db.scalars(stmt.order_by(ClientRecord.last_seen.desc()).limit(limit)))
    ev = defaultdict(Counter)
    for e in db.scalars(select(ClientEvent).where(ClientEvent.site_id.in_(ids or [-1]), ClientEvent.ts >= since)):
        ev[(e.site_id, e.mac)][e.kind] += 1
    full = user.role in ("operator", "admin")
    rows = []
    for c in recs:
        k = ev.get((c.site_id, c.mac), Counter())
        issues = []
        if (c.snr_db is not None and c.snr_db < 15):
            issues.append("Low SNR")
        if k["disconnect"] >= 5:
            issues.append("Frequent disconnects")
        if k["roam"] >= 10:
            issues.append("Excessive roaming")
        if c.band == "2.4GHz" and c.snr_db is not None and c.snr_db >= 25:
            issues.append("Strong client on 2.4 GHz (band-steer candidate)")
        rows.append({"id": c.id, "mac": c.mac if full else mask_mac(c.mac), "hostname": c.hostname if full else None,
                     "ip": c.ip if full else None, "cluster_key": str(c.site_id),
                     "cluster": clusters_by_legacy.get(c.site_id, sites[c.site_id].name), "ap": c.ap_name, "ssid": c.ssid,
                     "vlan": c.vlan, "band": c.band, "channel": c.channel, "snr_db": c.snr_db, "rssi_dbm": c.rssi_dbm,
                     "speed_mbps": c.speed_mbps, "quality": c.quality or "unavailable", "exp_score": c.exp_score,
                     "exp_band": c.exp_band, "connected": c.is_connected,
                     "connected_seconds": c.connected_seconds, "first_seen": _iso(c.first_seen),
                     "last_seen": _iso(c.last_seen), "connects": k["connect"], "disconnects": k["disconnect"],
                     "roams": k["roam"], "issues": issues, "simulated": c.simulated})

    def breakdown(key):
        acc: dict[str, dict] = {}
        for r in rows:
            name = r[key] or "—"
            b = acc.setdefault(name, {"name": name, "clients": 0, "connected": 0, "poor": 0, "disconnects": 0,
                                      "roams": 0, "_snr": []})
            b["clients"] += 1
            b["connected"] += int(r["connected"])
            b["poor"] += int(r["quality"] == "poor")
            b["disconnects"] += r["disconnects"]
            b["roams"] += r["roams"]
            if r["snr_db"] is not None:
                b["_snr"].append(r["snr_db"])
        for b in acc.values():
            s = b.pop("_snr")
            b["avg_snr_db"] = round(sum(s) / len(s), 1) if s else None
        return sorted(acc.values(), key=lambda b: (-b["poor"], -b["clients"]))

    connected_rows = [r for r in rows if r["connected"]]
    snr = [r["snr_db"] for r in connected_rows if r["snr_db"] is not None]
    return {"generated_at": _now().isoformat(), "window_hours": hours,
            "clusters": [{"key": str(s.id), "name": clusters_by_legacy.get(s.id, s.name)} for s in sites.values()],
            "summary": {"clients_seen": len(rows), "connected": len(connected_rows),
                        "quality": dict(Counter(r["quality"] for r in connected_rows)),
                        "avg_snr_db": round(sum(snr) / len(snr), 1) if snr else None,
                        "bands": dict(Counter(r["band"] or "unknown" for r in connected_rows)),
                        "connects": sum(r["connects"] for r in rows), "disconnects": sum(r["disconnects"] for r in rows),
                        "roams": sum(r["roams"] for r in rows),
                        "with_issues": sum(1 for r in rows if r["issues"])},
            "by_cluster": breakdown("cluster"), "by_ap": breakdown("ap")[:50], "by_ssid": breakdown("ssid"),
            "clients": rows,
            "evidence_note": "Connect / disconnect / roam are inferred by comparing successive cluster polls; "
                             "sessions shorter than one poll interval are invisible. No authentication-path step is "
                             "invented."}


@router.get("/clients/{client_id}/journey")
def client_journey(client_id: int, hours: int = Query(24, ge=1, le=168), db: Session = Depends(get_db),
                   user: User = Depends(require_role("viewer"))):
    from .experience import client_score_result
    sites = {s.id: s for s in visible_sites(db)}
    c = db.get(ClientRecord, client_id)
    if not c or c.site_id not in sites:
        raise HTTPException(404, "Client not found")
    full = user.role in ("operator", "admin")
    since = _now() - timedelta(hours=hours)
    events = [{"ts": _iso(e.ts), "kind": e.kind, "ap_from": e.ap_from, "ap_to": e.ap_to, "ssid": e.ssid}
              for e in db.scalars(select(ClientEvent).where(ClientEvent.site_id == c.site_id, ClientEvent.mac == c.mac,
                                                            ClientEvent.ts >= since).order_by(ClientEvent.ts))]
    aps_visited = Counter(e["ap_to"] for e in events if e["ap_to"])
    return {"id": c.id, "mac": c.mac if full else mask_mac(c.mac), "hostname": c.hostname if full else None,
            "cluster": sites[c.site_id].name, "current": {"ap": c.ap_name, "ssid": c.ssid, "band": c.band,
            "channel": c.channel, "snr_db": c.snr_db, "speed_mbps": c.speed_mbps, "connected": c.is_connected,
            "quality": c.quality, "vlan": c.vlan, "last_seen": _iso(c.last_seen)},
            "score": client_score_result(db, c), "events": events,
            "aps_visited": [{"ap": k, "times": v} for k, v in aps_visited.most_common()],
            "window_hours": hours}


# ---------------------------------------------------------------------------------------- Device 360 insight
METRIC_LABELS = {
    "cpu_percent": ("CPU", "%"), "memory_percent": ("Memory", "%"), "uptime_seconds": ("Uptime", "s"),
    "temp_max_c": ("Max temperature", "°C"), "fans_failed": ("Failed fans", ""), "psu_failed": ("Failed PSUs", ""),
    "util_5g_percent": ("5 GHz utilisation", "%"), "util_2g_percent": ("2.4 GHz utilisation", "%"),
    "noise_floor_dbm": ("Noise floor", " dBm"), "retry_percent": ("Retries", "%"), "clients": ("Clients", ""),
    "client_count": ("Clients", ""), "ports_up": ("Ports up", ""), "ports_total": ("Ports total", ""),
    "port_errors_total": ("Port errors (total)", ""), "port_errors_delta": ("Port errors (new)", ""),
    "link_resets_delta": ("Link resets (new)", ""), "max_port_util_percent": ("Busiest port", "%"),
    "poe_power_w": ("PoE power", " W"), "poe_ports_powered": ("PoE ports powered", ""),
    "lldp_neighbors": ("LLDP neighbours", ""), "ap_up": ("AP up", ""), "exp_score": ("Client experience", ""),
}


@router.get("/devices/{device_id}/insight")
def device_insight(device_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..api._phase_common import real_device
    from ..domain import noc as N
    from ..infrastructure.device_state import plans_by_device, state_of
    from ..infrastructure.runtime import telemetry_store
    from ..database.models_phases import ConfigArtifact, InmAlert
    from .incidents import _public as incident_public, _work
    d = real_device(db, device_id)
    now = _now()
    store = telemetry_store()
    rows = store.query(device_id=d.id, metric_name=None, since=now - timedelta(hours=24), limit=5000,
                       entity_type="device") if store else []
    latest = N.latest_by_metric(rows)
    sites = {s.id: s for s in visible_sites(db)}
    controller = sites.get(d.legacy_site_id) if d.legacy_site_id is not None else None
    st = state_of(d, plans_by_device(db).get(d.id), now=now, controller=controller)
    health = N.health(d.family, latest if st["state"] in ("ONLINE", "DEGRADED") else {}, st["freshness"],
                      "down" if st["state"] == "OFFLINE" else None)
    metrics = [{"metric": k, "label": METRIC_LABELS.get(k, (k.replace("_", " "), ""))[0],
                "unit": METRIC_LABELS.get(k, ("", ""))[1], "value": v["value"], "observed_at": str(v["observed_at"]),
                "quality": v.get("quality")} for k, v in sorted(latest.items())]
    series_available = sorted({r["metric_name"] for r in rows})
    out: dict[str, Any] = {"device_id": d.id, "family": d.family, "state": st, "health": health, "metrics": metrics,
                           "series_available": series_available, "ap": None, "clients": [], "incidents": []}
    if d.family == "access_point":
        fl = build_fleet(db)
        out["ap"] = next((a for a in fl["aps"] if a["device_id"] == d.id), None)
        if controller is not None and d.hostname:
            full = user.role in ("operator", "admin")
            for c in db.scalars(select(ClientRecord).where(ClientRecord.site_id == controller.id,
                                                           ClientRecord.ap_name == d.hostname,
                                                           ClientRecord.is_connected.is_(True)).limit(500)):
                out["clients"].append({"id": c.id, "mac": c.mac if full else mask_mac(c.mac),
                                       "hostname": c.hostname if full else None, "ssid": c.ssid, "band": c.band,
                                       "channel": c.channel, "snr_db": c.snr_db, "speed_mbps": c.speed_mbps,
                                       "quality": c.quality, "exp_score": c.exp_score})
            # client counts per band from this AP's connected clients
            out["client_bands"] = dict(Counter(c["band"] or "unknown" for c in out["clients"]))
        out["ap_extras"] = ap_extras(db, d, controller)
    phys = {s.id: s.name for s in db.scalars(select(InmSite))}
    for a in db.scalars(select(InmAlert).where(InmAlert.device_id == d.id).order_by(InmAlert.opened_at.desc()).limit(25)):
        out["incidents"].append(incident_public(a, _work(db, a.id), d, phys))
    art = db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == d.id)
                    .order_by(ConfigArtifact.created_at.desc()).limit(1))
    out["config"] = {"captured": art is not None, "captured_at": _iso(art.created_at) if art else None}
    return out


RSSI_BUCKETS = ((-60, "excellent (≥ -60 dBm)"), (-67, "good (-60 to -67)"), (-75, "fair (-67 to -75)"),
                (-200, "poor (< -75)"))


def ap_extras(db: Session, d, controller) -> dict:
    """0013 AP dashboard: free memory (MiB), uplink rx/tx rates from verified counter deltas, observed radio
    neighbours/stations by band, and the per-band signal distribution of connected stations. Gaps are explicit."""
    from ..database.models import AccessPoint
    ap = db.get(AccessPoint, d.legacy_ap_id) if d.legacy_ap_id is not None else None
    obs = (ap.observations if ap is not None else None) or {}
    mem = obs.get("memory") or {}
    rates = obs.get("uplink_rates") or {"quality": "NO_COUNTERS"}
    dist: dict[str, dict[str, int]] = {}
    if controller is not None and d.hostname:
        for c in db.scalars(select(ClientRecord).where(ClientRecord.site_id == controller.id, ClientRecord.ap_name == d.hostname,
                                                       ClientRecord.is_connected.is_(True)).limit(2000)):
            band = c.band or "unknown"
            label = "unknown" if c.rssi_dbm is None else next(lbl for lim, lbl in RSSI_BUCKETS if c.rssi_dbm >= lim)
            dist.setdefault(band, {})[label] = dist.setdefault(band, {}).get(label, 0) + 1
    return {"observed_at": obs.get("observed_at"),
            "memory_free_mib": mem.get("memory_free_mib"), "memory_basis": mem.get("basis") or mem.get("reason"),
            "uplink": {"interface": (obs.get("uplink_counters") or {}).get("interface"), "rx_bps": rates.get("rx_bps"),
                       "tx_bps": rates.get("tx_bps"), "quality": rates.get("quality"), "interval_s": rates.get("interval_s")},
            "neighbors": {"aps": obs.get("monitor_aps") or {"total": None, "reason": "not collected yet"},
                          "stations": obs.get("monitor_clients") or {"total": None, "reason": "not collected yet"},
                          "source": "over-the-air monitor tables (never inferred from our own same-channel inventory)"},
            "signal_distribution": {"by_band": dist, "source": "connected stations (show clients, RSSI)"},
            "client_match": obs.get("client_match") or {"state": "NOT_COLLECTED", "reason": "not collected yet"},
            "gaps": obs.get("gaps", []) if obs else ["no AP observations collected yet"],
            "format_verification": "PARSED_FORMAT_UNVERIFIED until a hardware fixture for this model/firmware is captured"}


# ---------------------------------------------------------------------------------------- diagnosis + AI
def _ai_narrative(db: Session, request: Request, user: User, kind: str, facts: dict) -> dict:
    """Optional local-LLM narrative over the deterministic findings. Advisory only; never changes anything."""
    from ..config import get_settings
    if not get_settings().ollama_enabled:
        return {"available": False, "reason": "AI assistant disabled (SIMS_OLLAMA_ENABLED=false); rule-based "
                                              "diagnosis shown."}
    if user.role not in ("operator", "admin"):
        return {"available": False, "reason": "AI explanations need an operator or admin account."}
    from . import ai as A
    from ..ai import prompts
    prompt = ("You are a senior Aruba Wi-Fi engineer. Using ONLY the data below, explain in plain language why this "
              f"{kind} performs poorly, rank the causes, and give a short, ordered fix plan (what to check first, what "
              "to change, how to verify). Do not invent measurements. Channel widths must stay 20/40 MHz. Say when the "
              "data is insufficient.\n\n" + prompts.data_block(facts))
    try:
        with A._Session(db) as s:
            text = A._chat(s, prompt, max_tokens=700)
            A._audit(db, request, user, f"diagnose_{kind}", s.client.model or "")
            return {"available": True, "text": text, "model": s.client.model, "ai_generated": True}
    except HTTPException as exc:
        return {"available": False, "reason": str(exc.detail)}


def _pseudo(c: dict) -> dict:
    return {k: v for k, v in c.items() if k not in ("mac", "hostname", "ip", "id")}


@router.get("/diagnose/ap/{device_id}")
def diagnose_ap(device_id: str, request: Request, ai: bool = False, db: Session = Depends(get_db),
                user: User = Depends(require_role("viewer"))):
    from ..domain import wifi_diagnosis as W
    fl = build_fleet(db)
    ap = next((a for a in fl["aps"] if a["device_id"] == device_id or a["name"] == device_id), None)
    if ap is None:
        raise HTTPException(404, "Access point not found in the visible fleet.")
    neighbours = [a for a in fl["aps"] if a["cluster_key"] == ap["cluster_key"] and ap["cluster_key"] != UNLINKED]
    clients_on: list[dict] = []
    if ap["cluster_key"] != UNLINKED:
        for c in db.scalars(select(ClientRecord).where(ClientRecord.site_id == int(ap["cluster_key"]),
                                                       ClientRecord.ap_name == ap["name"],
                                                       ClientRecord.is_connected.is_(True)).limit(500)):
            clients_on.append({"snr_db": c.snr_db, "band": c.band, "speed_mbps": c.speed_mbps, "quality": c.quality})
    latest = db.get(AppSetting, "ap-health:latest")
    ev = next((r for r in (latest.value.get("rows", []) if latest else []) if r["ap"] == ap["name"]), None)
    result = W.diagnose_ap(ap, neighbours, clients_on, ev)
    same5 = [n["name"] for n in neighbours if n["device_id"] != ap["device_id"] and ap["radio_5g"]["channel"]
             and n["radio_5g"]["channel"] == ap["radio_5g"]["channel"]]
    same2 = [n["name"] for n in neighbours if n["device_id"] != ap["device_id"] and ap["radio_2g"]["channel"]
             and n["radio_2g"]["channel"] == ap["radio_2g"]["channel"]]
    snr = [c["snr_db"] for c in clients_on if c["snr_db"] is not None]
    checked = {"State": f"{ap['state']} ({ap['state_reason']})", "Clients": ap["clients"],
               "2.4 GHz channel / busy": f"ch {ap['radio_2g']['channel']} / {ap['radio_2g']['util_percent'] if ap['radio_2g']['util_percent'] is not None else '?'}%",
               "5 GHz channel / busy": f"ch {ap['radio_5g']['channel']} / {ap['radio_5g']['util_percent'] if ap['radio_5g']['util_percent'] is not None else '?'}%",
               "Noise 2.4 / 5 GHz": f"{ap['radio_2g']['noise_floor_dbm']} / {ap['radio_5g']['noise_floor_dbm']} dBm"
               if ap['radio_2g']['noise_floor_dbm'] is not None or ap['radio_5g']['noise_floor_dbm'] is not None else None,
               "Same 5 GHz channel in cluster": ", ".join(same5) or "none",
               "Same 2.4 GHz channel in cluster": ", ".join(same2) or "none",
               "Clients with SNR < 15 dB": f"{sum(1 for x in snr if x < 15)} of {len(snr)}" if snr else None,
               "CPU / memory": f"{ap['cpu_percent']}% / {ap['memory_percent']}%" if ap["cpu_percent"] is not None else None,
               "Event evidence": ev["trigger"] if ev else None}
    out = {"subject": {"type": "access_point", "device_id": ap["device_id"], "name": ap["name"],
                       "cluster": ap["cluster"], "state": ap["state"], "tier": ap["tier"]}, **result, "checked": checked}
    if ai:
        facts = {"ap": {k: ap[k] for k in ("name", "state", "clients", "cpu_percent", "memory_percent", "radio_2g",
                                           "radio_5g", "tier", "condition_triggers", "event_trigger")},
                 "neighbour_channels": [{"ap": n["name"], "ch2": n["radio_2g"]["channel"], "ch5": n["radio_5g"]["channel"]}
                                        for n in neighbours if n["device_id"] != ap["device_id"]][:30],
                 "client_snr": [c["snr_db"] for c in clients_on if c["snr_db"] is not None][:200],
                 "client_bands": dict(Counter(c["band"] for c in clients_on)),
                 "rule_findings": [{k: f[k] for k in ("title", "why", "actions")} for f in result["findings"]]}
        out["ai"] = _ai_narrative(db, request, user, "access point", facts)
    return out


@router.get("/diagnose/client/{client_id}")
def diagnose_client(client_id: int, request: Request, ai: bool = False, hours: int = Query(24, ge=1, le=168),
                    db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    from ..domain import wifi_diagnosis as W
    from .experience import client_score_result
    sites = {s.id for s in visible_sites(db)}
    c = db.get(ClientRecord, client_id)
    if not c or c.site_id not in sites:
        raise HTTPException(404, "Client not found")
    since = _now() - timedelta(hours=hours)
    k = Counter(e.kind for e in db.scalars(select(ClientEvent).where(
        ClientEvent.site_id == c.site_id, ClientEvent.mac == c.mac, ClientEvent.ts >= since)))
    fl = build_fleet(db)
    ap = next((a for a in fl["aps"] if a["cluster_key"] == str(c.site_id) and a["name"] == c.ap_name), None)
    cluster_aps = sum(1 for a in fl["aps"] if a["cluster_key"] == str(c.site_id))
    score = client_score_result(db, c)
    row = {"ap": c.ap_name, "ssid": c.ssid, "band": c.band, "channel": c.channel, "snr_db": c.snr_db,
           "speed_mbps": c.speed_mbps, "disconnects": k["disconnect"], "roams": k["roam"], "quality": c.quality,
           "exp_score": c.exp_score}
    result = W.diagnose_client(row, ap, cluster_aps, score)
    full = user.role in ("operator", "admin")
    checked = {"AP / SSID": f"{c.ap_name} / {c.ssid}", "Band / channel": f"{c.band} / {c.channel}", "SNR": c.snr_db,
               "Data rate (Mbps)": c.speed_mbps, f"Disconnects ({hours} h)": k["disconnect"], f"Roams ({hours} h)": k["roam"],
               "Score": f"{c.exp_score} ({c.exp_band})" if c.exp_score is not None else None,
               "AP 5 GHz busy": f"{ap['radio_5g']['util_percent']}%" if ap and ap['radio_5g']['util_percent'] is not None else None}
    out = {"subject": {"type": "client", "id": c.id, "mac": c.mac if full else mask_mac(c.mac), "ap": c.ap_name,
                       "ssid": c.ssid, "score": c.exp_score, "band": c.exp_band}, **result, "score": score, "checked": checked}
    if ai:
        facts = {"client": _pseudo(row), "ap": ({k2: ap[k2] for k2 in ("name", "state", "clients", "radio_2g", "radio_5g",
                                                                      "tier")} if ap else None),
                 "score_inputs": score.get("inputs"), "rule_findings": [{k2: f[k2] for k2 in ("title", "why", "actions")}
                                                                         for f in result["findings"]]}
        out["ai"] = _ai_narrative(db, request, user, "client connection", facts)
    return out


# ---------------------------------------------------------------------------------------- cluster-scoped optimization
class ClusterProposal(BaseModel):
    cluster: str = Field(min_length=1, max_length=40)
    mode: str = Field("SHADOW", pattern="^(SHADOW|DEVICE_OPT_IN)$")
    device_ids: list[str] | None = Field(default=None, max_length=500)  # explicit subset; default = all eligible
    expires_days: int = Field(30, ge=1, le=90)


@router.get("/optimization/cluster/{cluster}")
def cluster_optimization(cluster: str, db: Session = Depends(get_db), user: User = Depends(require_role("viewer"))):
    """Per-AP optimisation view of one cluster: enrollment, blockers and the diagnosis-driven RF proposals."""
    from ..database.models_phases import ConfigArtifact, OptimizationEnrollment
    from ..domain import wifi_diagnosis as W
    from ..config import get_settings
    from ..infrastructure.phase_services import control
    from ..infrastructure.optimizer import feature_enabled
    fl = build_fleet(db)
    aps = [a for a in fl["aps"] if a["cluster_key"] == cluster]
    if not aps:
        raise HTTPException(404, "Cluster not found or has no APs.")
    enr = {}
    for e in db.scalars(select(OptimizationEnrollment).order_by(OptimizationEnrollment.created_at)):
        enr[e.device_id] = e
    backups = {d for d in db.scalars(select(ConfigArtifact.device_id).where(
        ConfigArtifact.device_id.in_([a["device_id"] for a in aps])))}
    ctl = control(db)
    platform_blockers = []
    if not feature_enabled():
        platform_blockers.append("Automatic optimization feature is off (INM_AUTOMATIC_OPTIMIZATION=false) — SHADOW "
                                 "simulation still works.")
    if get_settings().device_write_lock:
        platform_blockers.append("Global device write lock is ON — no automatic or manual hardware change can run.")
    if ctl.emergency_stop:
        platform_blockers.append(f"Emergency stop is ACTIVE ({ctl.reason or 'no reason recorded'}) — enrollments are "
                                 "paused until cleared and re-reviewed.")
    rows = []
    for a in aps:
        diag = W.diagnose_ap(a, aps, [], None)
        e = enr.get(a["device_id"])
        blockers = []
        if a["state"] != "ONLINE":
            blockers.append(f"AP is {a['state']}: {a['guidance']}")
        if a["device_id"] not in backups:
            blockers.append("No sealed configuration backup — capture one (Device 360° → Operations) before approval.")
        if e is not None and e.status == "PENDING_APPROVAL":
            blockers.append(f"Waiting for an independent approver (requested by {e.requester_id}).")
        if e is not None and e.status == "PAUSED":
            blockers.append(f"Paused: {e.status_reason or 'review required'} — disable and re-request after review.")
        rows.append({"device_id": a["device_id"], "name": a["name"], "serial": a["serial"], "state": a["state"],
                     "tier": a["tier"], "radio_2g": a["radio_2g"], "radio_5g": a["radio_5g"], "clients": a["clients"],
                     "enrollment": {"id": e.id, "mode": e.mode, "status": e.status, "status_reason": e.status_reason,
                                    "requester": e.requester_id, "approver": e.independent_approver_id,
                                    "expires_at": _iso(e.expires_at_utc)} if e else None,
                     "headline": diag["headline"], "findings": diag["findings"][:4], "proposals": diag["proposals"],
                     "blockers": blockers, "eligible": a["state"] == "ONLINE" and (e is None or e.status in ("DISABLED", "EXPIRED", "REVOKED"))})
    cl = next(c for c in fl["clusters"] if c["key"] == cluster)
    return {"cluster": cl, "platform_blockers": platform_blockers, "aps": rows,
            "summary": {"aps": len(rows), "with_proposals": sum(1 for r in rows if r["proposals"]),
                        "enrolled": sum(1 for r in rows if r["enrollment"]), "eligible": sum(1 for r in rows if r["eligible"])}}


@router.post("/optimization/cluster-proposal", status_code=201)
def cluster_proposal(body: ClusterProposal, request: Request, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin"))):
    """Select a cluster → a batch PROPOSAL of separate per-device enrollments (each named by ID+serial, each needing
    its own independent approval). Nothing is inherited from the cluster and nothing is armed here."""
    from .optimization import BulkInput, EnrollInput, bulk
    view = cluster_optimization(body.cluster, db, user)
    chosen = [r for r in view["aps"] if r["eligible"] and (not body.device_ids or r["device_id"] in body.device_ids)]
    if not chosen:
        raise HTTPException(409, "No eligible APs: they must be ONLINE and not already enrolled.")
    tmpl = EnrollInput(serial="", mode=body.mode, allowed_operation_types=["ap.radio.channel", "ap.radio.tx_power"],
                       value_constraints={"allowed_widths_mhz": [20, 40]}, cooldown_seconds=7200,
                       max_actions_per_day=2, expires_at_utc=_now() + timedelta(days=body.expires_days))
    out = []
    for i in range(0, len(chosen), 50):
        part = chosen[i:i + 50]
        out += bulk(BulkInput(devices=[{"device_id": r["device_id"], "serial": r["serial"]} for r in part],
                              template=tmpl), request, db, user)["proposals"]
    audit(db, "optimization_cluster_proposal", username=user.username, target=f"cluster:{body.cluster}",
          detail=f"{len(out)} separate pending {body.mode} enrollments", request=request)
    return {"proposals": len(out), "note": "Each AP has its own pending enrollment and needs its own independent approval."}
