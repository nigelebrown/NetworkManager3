"""Cluster-aware AP auto-onboarding (PRTG / SolarWinds-style "add once, everything follows").

Problem it solves: Aruba Instant serves REST only on the cluster's Virtual Controller (VC), and REST is OFF by
default. Registering every AP individually and probing each AP's own IP therefore leaves most APs UNKNOWN.

Pipeline for each registered REAL AP that is not yet in a polled cluster:

1. Credentials — the profile bound to the AP, otherwise every enabled AP profile in turn (first that works is bound).
2. Read-only REST login on the AP's IP (:4343). If it answers, `show aps` gives the whole cluster and the VC.
3. REST off/unreachable → read-only SSH CLI (`show aps`) on a host whose key fingerprint an administrator approved
   (bulk scan → bulk approve; credentials are never sent to an unapproved host).
4. Register (or reuse) the cluster = legacy Site with vc_host = VC IP and the working credentials (encrypted).
5. REST on the VC: kept if enabled. If disabled and `auto_enable_rest` is requested, the fixed 4-command
   `allow-rest-api` bootstrap is attempted through `ssh.enable_rest_api`, which keeps every existing safety gate
   (write lock / explicit /32 approval / pinned key). If that is not permitted or fails, the cluster is polled over
   the read-only SSH CLI instead, labelled `ssh_cli` so data quality is visible.
6. Every member AP reported by the cluster is marked covered (one login per cluster, not per AP), the cluster is
   polled once, and the inventory projection links imported AP records to it (no duplicates).

Nothing here writes device configuration except step 5, and only through the pre-existing gated function.
"""
from __future__ import annotations

import ipaddress
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AppSetting, Site
from ..database.models_inm import InmDevice

log = logging.getLogger("inm.ap_autodiscovery")
JOB_KEY = "ap-autodiscovery:job"
_LOCK = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ settings helpers
def _get(db: Session, key: str) -> dict | None:
    row = db.get(AppSetting, key)
    return dict(row.value) if row and isinstance(row.value, dict) else None


def _put(db: Session, key: str, value: dict, actor: str) -> None:
    row = db.get(AppSetting, key)
    if row is None:
        db.add(AppSetting(key=key, value=value, updated_by=actor, updated_at=_now()))
    else:
        row.value, row.updated_by, row.updated_at = value, actor, _now()
    db.flush()


def pinned_fingerprint(db: Session, ip: str) -> str | None:
    """One trust store for SSH host keys. Keys approved per device by the onboarding REST-bootstrap flow
    (`onboarding-ssh-pin:<device>` = {ip, fingerprint}) are honoured too, so an AP approved there is not asked again."""
    v = _get(db, f"ssh-hostkey:{ip}")
    if v:
        return v.get("fingerprint")
    for row in db.scalars(select(AppSetting).where(AppSetting.key.startswith("onboarding-ssh-pin:"))):
        if isinstance(row.value, dict) and row.value.get("ip") == ip and row.value.get("fingerprint"):
            return row.value["fingerprint"]
    return None


def pending_fingerprints(db: Session) -> list[dict]:
    rows = db.scalars(select(AppSetting).where(AppSetting.key.startswith("ssh-hostkey-pending:")))
    return [{"ip": r.key.split(":", 1)[1], **r.value} for r in rows]


def site_transport(db: Session, site_id: int) -> dict:
    return _get(db, f"site-transport:{site_id}") or {"transport": "rest"}


def set_site_transport(db: Session, site_id: int, value: dict, actor: str) -> None:
    _put(db, f"site-transport:{site_id}", {**value, "updated_at": _now().isoformat()}, actor)


# ------------------------------------------------------------------ host keys (bulk, unauthenticated scan)
def scan_host_keys(db: Session, ips: list[str], actor: str,
                   scanner: Callable[[str], dict] | None = None) -> list[dict]:
    from ..aruba import ssh
    scanner = scanner or (lambda ip: ssh.scan_host_key(ip, 22, 5.0))
    todo = list(dict.fromkeys(ips))

    def one(ip: str) -> dict:
        try:
            return {"ip": ip, **scanner(ip)}
        except Exception as exc:  # unreachable / no SSH / negotiation failure
            return {"ip": ip, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(one, todo))
    for r in results:
        if not r.get("fingerprint"):
            continue
        pinned = pinned_fingerprint(db, r["ip"])
        if pinned == r["fingerprint"]:
            r["status"] = "ALREADY_APPROVED"
            continue
        # A different key on an already-approved host is NOT trusted automatically (AP replaced / reset, or
        # interception); it is queued for explicit re-approval and flagged as changed.
        r["status"] = "CHANGED" if pinned else "NEW"
        _put(db, f"ssh-hostkey-pending:{r['ip']}", {"fingerprint": r["fingerprint"],
             "key_type": r.get("key_type"), "legacy_algorithms": r.get("legacy_algorithms"),
             "changed_from": pinned, "scanned_at": _now().isoformat()}, actor)
    return results


def approve_fingerprints(db: Session, items: list[dict], actor: str) -> list[dict]:
    """Pin only fingerprints that match what the unauthenticated scan observed."""
    out = []
    for it in items:
        ip, fp = str(it.get("ip")), str(it.get("fingerprint") or "")
        pend = _get(db, f"ssh-hostkey-pending:{ip}")
        if not pend or pend.get("fingerprint") != fp:
            out.append({"ip": ip, "approved": False, "reason": "fingerprint does not match the latest scan"})
            continue
        _put(db, f"ssh-hostkey:{ip}", {"fingerprint": fp, "key_type": pend.get("key_type"),
                                       "approved_by": actor, "approved_at": _now().isoformat()}, actor)
        # controllers already read over SSH pick up the re-approved key on the next poll
        for row in db.scalars(select(AppSetting).where(AppSetting.key.startswith("site-transport:"))):
            v = dict(row.value or {})
            if v.get("transport") == "ssh_cli" and v.get("ssh_host") == ip:
                v["fingerprint"] = fp
                row.value = v
        row = db.get(AppSetting, f"ssh-hostkey-pending:{ip}")
        if row:
            db.delete(row)
        out.append({"ip": ip, "approved": True})
    db.flush()
    return out


# ------------------------------------------------------------------ discovery
def _candidate_profiles(db: Session, device: InmDevice):
    from ..database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
    binding = db.get(OnboardingDeviceCredential, device.id)
    if binding:
        p = db.get(OnboardingCredential, binding.credential_id)
        if p is not None and p.enabled:
            return [p]
    return list(db.scalars(select(OnboardingCredential).where(
        OnboardingCredential.device_family == "access_point", OnboardingCredential.enabled.is_(True))
        .order_by(OnboardingCredential.created_at)))


def _bind(db: Session, device: InmDevice, profile, actor: str) -> None:
    from ..database.models_onboarding import OnboardingDeviceCredential
    if db.get(OnboardingDeviceCredential, device.id) is None:
        db.add(OnboardingDeviceCredential(device_id=device.id, credential_id=profile.id, bound_by=actor))
        db.flush()


def _rest_members(ip: str, username: str, password: str) -> tuple[list[dict], dict]:
    from ..aruba import parsers
    from ..aruba.client import ArubaClient, ArubaTarget
    from ..config import get_settings
    s = get_settings()
    with ArubaClient(ArubaTarget(ip, 4343, username, password), timeout=min(s.aruba_timeout_seconds, 8),
                     verify=s.tls_verify_for(ip)) as c:
        c.login()
        aps = parsers.normalize_aps(parsers.extract_rows(c.show("show aps"), "show aps"))
    return aps, {"transport": "rest"}


def _ssh_members(ip: str, username: str, password: str, fp: str) -> tuple[list[dict], dict]:
    from ..aruba import parsers
    from ..aruba.ssh import SshShowClient
    with SshShowClient(ip, username, password, fp, timeout=15) as c:
        aps = parsers.normalize_aps(parsers.extract_rows(c.show("show aps"), "show aps"))
    return aps, {"transport": "ssh_cli", "ssh_host": ip, "fingerprint": fp}


def _classify_rest_error(exc: Exception) -> str:
    from ..aruba.errors import ArubaAuthError, ArubaRestDisabledError
    if isinstance(exc, ArubaRestDisabledError):
        return "REST_DISABLED"
    if isinstance(exc, ArubaAuthError):
        return "AUTH_REJECTED"
    return "UNREACHABLE"


def _ensure_site(db: Session, vc_ip: str, profile, name_hint: str | None) -> Site:
    site = db.scalar(select(Site).where(Site.vc_host == vc_ip, Site.simulated.is_(False)))
    if site is None:
        base = f"Instant {name_hint or vc_ip}"[:90]
        name, n = base, 2
        while db.scalar(select(Site).where(Site.name == name)) is not None:
            name, n = f"{base} ({n})", n + 1
        site = Site(name=name, vc_host=vc_ip, vc_port=4343, api_username=profile.username,
                    api_password_enc=profile.password_enc, simulated=False, enabled=True,
                    description="Registered automatically by AP auto-onboarding")
        db.add(site)
        db.flush()
    elif not site.api_username:
        site.api_username, site.api_password_enc = profile.username, profile.password_enc
    return site


def discover(db: Session, actor: str, *, device_ids: list[str] | None = None, auto_enable_rest: bool = False,
             progress: Callable[[dict], None] | None = None,
             rest_members=_rest_members, ssh_members=_ssh_members, enable_rest=None, poll=None) -> dict:
    from ..api.quick_devices import _permit_probe
    from ..security.crypto import decrypt_secret
    from .persistence.inventory import sync_inventory

    q = select(InmDevice).where(InmDevice.source_type == "REAL", InmDevice.family == "access_point",
                                InmDevice.lifecycle != "merged")
    devices = list(db.scalars(q))
    if device_ids:
        wanted = set(device_ids)
        devices = [d for d in devices if d.id in wanted]
    polled = {s.id for s in db.scalars(select(Site).where(Site.simulated.is_(False), Site.last_poll_ok.is_(True)))}
    covered: dict[str, int] = {}  # serial/mac (casefold) -> site id
    results: list[dict] = []
    sites_touched: dict[int, dict] = {}
    for i, d in enumerate(devices):
        if progress:
            progress({"done": i, "total": len(devices)})
        key = (d.serial_number or "").casefold()
        if d.legacy_site_id in polled and d.legacy_ap_id is not None:
            results.append({"device_id": d.id, "hostname": d.hostname, "result": "ALREADY_MONITORED"})
            continue
        if key in covered or (d.hostname or "").casefold() in covered:
            results.append({"device_id": d.id, "hostname": d.hostname, "result": "LINKED",
                            "site_id": covered.get(key) or covered.get((d.hostname or "").casefold()),
                            "via": "cluster member list"})
            continue
        ip = (d.mgmt_addresses or [None])[0]
        if not ip:
            results.append({"device_id": d.id, "hostname": d.hostname, "result": "NO_MANAGEMENT_IP"})
            continue
        try:
            _permit_probe(ipaddress.IPv4Address(str(ip)))
        except Exception as exc:
            results.append({"device_id": d.id, "hostname": d.hostname, "ip": ip, "result": "TARGET_NOT_ALLOWED",
                            "detail": str(getattr(exc, "detail", exc))[:160]})
            continue
        profiles = _candidate_profiles(db, d)
        if not profiles:
            results.append({"device_id": d.id, "hostname": d.hostname, "ip": ip, "result": "NO_CREDENTIAL"})
            continue
        members, transport, used, rest_state, last = None, None, None, None, "UNREACHABLE"
        for p in profiles:
            pw = decrypt_secret(p.password_enc)
            try:
                members, transport = rest_members(ip, p.username, pw)
                used, rest_state = p, "ENABLED"
                break
            except Exception as exc:
                last = _classify_rest_error(exc)
                rest_state = last
                if last == "AUTH_REJECTED":
                    continue
            fp = pinned_fingerprint(db, ip)
            if fp:
                try:
                    members, transport = ssh_members(ip, p.username, pw, fp)
                    used = p
                    break
                except Exception as exc:
                    last = "SSH_" + type(exc).__name__
        if members is None:
            pending = db.get(AppSetting, f"ssh-hostkey-pending:{ip}") is not None
            need_key = not pinned_fingerprint(db, ip) and (last == "REST_DISABLED" or pending)
            detail = ("REST is off on this AP's cluster; approve its SSH host key so the platform can read the cluster "
                      "over SSH and (if permitted) enable REST.") if need_key else (
                "No answer on REST (4343) or SSH (22). Check the AP is powered, its IP is correct and the management "
                "VLAN is routed.") if last == "UNREACHABLE" else None
            results.append({"device_id": d.id, "hostname": d.hostname, "ip": ip,
                            "result": "NEEDS_HOSTKEY_APPROVAL" if need_key else last, "detail": detail})
            continue
        _bind(db, d, used, actor)
        master = next((m for m in members if m.get("is_master")), None)
        vc_ip = (master or {}).get("ip") or ip
        if transport["transport"] == "ssh_cli" and vc_ip != ip and pinned_fingerprint(db, vc_ip):
            transport = {**transport, "ssh_host": vc_ip, "fingerprint": pinned_fingerprint(db, vc_ip)}
        site = _ensure_site(db, vc_ip, used, (master or {}).get("name"))
        if transport["transport"] == "ssh_cli" and auto_enable_rest and pinned_fingerprint(db, vc_ip):
            try:
                from ..aruba import ssh
                (enable_rest or ssh.enable_rest_api)(vc_ip, used.username, decrypt_secret(used.password_enc),
                                                     pinned_fingerprint(db, vc_ip), approved_ap_rest_only=True)
                try:
                    rest_members(vc_ip, used.username, decrypt_secret(used.password_enc))
                    transport, rest_state = {"transport": "rest", "rest_enabled_by": actor}, "ENABLED_BY_BOOTSTRAP"
                except Exception:
                    rest_state = "BOOTSTRAP_SENT_REST_NOT_ANSWERING"
            except Exception as exc:
                rest_state = f"BOOTSTRAP_NOT_PERMITTED: {str(exc)[:140]}"
        set_site_transport(db, site.id, {**transport, "rest_state": rest_state}, actor)
        sites_touched[site.id] = {"site_id": site.id, "name": site.name, "vc_ip": vc_ip, "members": len(members),
                                  "transport": transport["transport"], "rest_state": rest_state}
        for m in members:
            for k in (m.get("serial"), m.get("name"), m.get("mac")):
                if k:
                    covered[str(k).casefold()] = site.id
        results.append({"device_id": d.id, "hostname": d.hostname, "ip": ip, "result": "CLUSTER_REGISTERED",
                        "site_id": site.id, "vc_ip": vc_ip, "transport": transport["transport"],
                        "rest_state": rest_state, "members": len(members)})
    # Second pass: an AP that could not be probed itself is still linked when a later AP's cluster lists it.
    by_id = {d.id: d for d in devices}
    for r in results:
        if r["result"] in ("CLUSTER_REGISTERED", "LINKED", "ALREADY_MONITORED"):
            continue
        d = by_id[r["device_id"]]
        sid = covered.get((d.serial_number or "").casefold()) or covered.get((d.hostname or "").casefold())
        if sid:
            r.update(result="LINKED", site_id=sid, via="cluster member list", detail=None)
    db.flush()
    if poll is None:
        from ..monitoring.collector import poll_site
        poll = poll_site
    for sid in sites_touched:
        site = db.get(Site, sid)
        ok = poll(db, site)
        sites_touched[sid]["first_poll_ok"] = bool(ok)
        sites_touched[sid]["first_poll_error"] = site.last_poll_error if not ok else None
    sync = sync_inventory(db)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    if progress:
        progress({"done": len(devices), "total": len(devices)})
    return {"finished_at": _now().isoformat(), "devices": len(devices), "counts": counts,
            "clusters": list(sites_touched.values()), "results": results, "inventory_sync": sync}


# ------------------------------------------------------------------ background job wrapper
def start_job(actor: str, *, device_ids: list[str] | None, auto_enable_rest: bool) -> dict:
    from ..database.session import session_scope
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("An AP auto-onboarding run is already in progress.")

    def run():
        try:
            with session_scope() as db:
                _put(db, JOB_KEY, {"state": "RUNNING", "started_at": _now().isoformat(), "by": actor,
                                   "done": 0, "total": 0}, actor)

            def progress(p):
                with session_scope() as pdb:
                    cur = _get(pdb, JOB_KEY) or {}
                    _put(pdb, JOB_KEY, {**cur, **p}, actor)

            with session_scope() as db:
                out = discover(db, actor, device_ids=device_ids, auto_enable_rest=auto_enable_rest,
                               progress=progress)
                from ..security.deps import audit
                audit(db, "ap_auto_onboarding", username=actor, target="access_points",
                      detail=f"counts={out['counts']} clusters={len(out['clusters'])} auto_enable_rest={auto_enable_rest}")
            with session_scope() as db:
                _put(db, JOB_KEY, {"state": "SUCCEEDED", **out}, actor)
        except Exception as exc:
            log.exception("AP auto-onboarding failed")
            with session_scope() as db:
                _put(db, JOB_KEY, {"state": "FAILED", "error": f"{type(exc).__name__}: {str(exc)[:300]}",
                                   "finished_at": _now().isoformat()}, actor)
        finally:
            _LOCK.release()

    threading.Thread(target=run, name="ap-autodiscovery", daemon=True).start()
    return {"state": "STARTED"}


def job_status(db: Session) -> dict:
    return _get(db, JOB_KEY) or {"state": "NEVER_RUN"}


def wanted_ips(db: Session, device_ids: list[str] | None = None) -> list[str]:
    """Unlinked APs plus every controller read over SSH (so a changed host key can be re-approved)."""
    q = select(InmDevice).where(InmDevice.source_type == "REAL", InmDevice.family == "access_point")
    out = []
    if not device_ids:
        for row in db.scalars(select(AppSetting).where(AppSetting.key.startswith("site-transport:"))):
            v = row.value or {}
            if v.get("transport") == "ssh_cli" and v.get("ssh_host"):
                out.append(str(v["ssh_host"]))
    for d in db.scalars(q):
        if device_ids and d.id not in device_ids:
            continue
        if d.legacy_ap_id is None and d.mgmt_addresses:
            out.append(str(d.mgmt_addresses[0]))
    return out


def describe() -> dict[str, Any]:
    return {"steps": ["credential", "rest_login", "ssh_cli_fallback", "cluster_registration",
                      "rest_bootstrap_if_permitted", "first_poll", "inventory_link"]}
