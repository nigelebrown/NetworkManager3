"""Durable, leased AP/switch onboarding using enabled saved credentials."""
from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from ..database.models import Site
from ..database.models_inm import InmDevice
from ..database.models_phases import CollectionPlan
from ..security.deps import audit
from . import ap_autodiscovery as AD
from .change_executor import _lease, _release, _renew_lease

POLICY_KEY = "automatic-onboarding:policy"
DEVICE_PREFIX = "auto-onboarding:device:"
LEASE_DEVICE = "automatic-onboarding"
SUCCESS = {"CLUSTER_REGISTERED", "LINKED", "ALREADY_MONITORED", "SWITCH_MONITORING_ENABLED"}
DEFAULT_POLICY = {"enabled": False, "credential_ids": None, "auto_enable_rest": False,
                  "auto_trust_new_hosts": True, "retry_seconds": 900}


def policy(db):
    return {**DEFAULT_POLICY, **(AD._get(db, POLICY_KEY) or {})}


def save_policy(db, value, actor):
    AD._put(db, POLICY_KEY, {**DEFAULT_POLICY, **value, "by": actor}, actor)
    audit(db, "automatic_onboarding_policy", username=actor, target="network_devices",
          detail=f"enabled={bool(value.get('enabled'))}; first-use keys after authenticated login")
    return policy(db)


def enroll_authenticated_result(db, profile_id, result, site_name, actor):
    """Called only with a fresh successful server-side identity probe."""
    cfg = policy(db)
    if not cfg["enabled"] or result.get("status") != "FOUND" or not result.get("enrollable"):
        return None
    if cfg["credential_ids"] is not None and profile_id not in cfg["credential_ids"]:
        return None
    from ..database.models_onboarding import OnboardingCredential
    from ..database.models_inm import InmSite
    from ..api.quick_devices import QuickDevice, record_observation
    profile = db.get(OnboardingCredential, profile_id)
    if not profile or not profile.enabled or not result.get("serial_number"):
        return None
    existing = db.scalar(select(InmDevice).where(InmDevice.source_type == "REAL",
        InmDevice.vendor == "Aruba", InmDevice.serial_number == result["serial_number"]))
    physical_site = db.get(InmSite, existing.site_id) if existing and existing.site_id else None
    payload = QuickDevice(family=profile.device_family,
        site_name=physical_site.name if physical_site else site_name,
        hostname=result.get("hostname") or result["management_ip"],
        serial_number=result["serial_number"], model=result.get("model") or "Unknown",
        firmware=result.get("firmware"), management_ip=result["management_ip"], status="up", metrics={})
    saved = record_observation(db, payload, actor, None, evidence="PROBED",
                               notes="automatic enrollment after saved credential authentication")
    _bind_device = db.get(InmDevice, saved["id"])
    AD._bind(db, _bind_device, profile, actor)
    audit(db, "automatic_discovery_registered", username=actor, target=saved["id"],
          detail="Authenticated discovery identity registered; monitoring enrollment queued")
    return saved["id"]


def _targets(db):
    return list(db.scalars(select(InmDevice).where(
        InmDevice.source_type == "REAL", InmDevice.lifecycle != "merged",
        InmDevice.family.in_(("access_point", "switch")))
        .order_by(InmDevice.created_at, InmDevice.id)))


def _monitored(db, d):
    if d.family == "access_point":
        site = db.get(Site, d.legacy_site_id) if d.legacy_site_id else None
        return bool(d.legacy_ap_id is not None and site and site.enabled and site.last_poll_ok)
    p = db.scalar(select(CollectionPlan).where(CollectionPlan.device_id == d.id))
    return bool(p and p.state == "ACTIVE" and p.last_success_at and not p.last_failure_category)


def queue(db, actor, *, device_ids=None, auto_enable_rest=False,
          auto_trust_new_hosts=True, credential_ids=None):
    current = AD._get(db, AD.JOB_KEY) or {}
    # A legacy API-thread run has no durable work list. Its saved clusters remain
    # reusable, but a new worker-owned work list replaces the interrupted run.
    if current.get("state") in ("QUEUED", "RUNNING") and "device_ids" in current:
        return {"state": current["state"], "run_id": current["run_id"], "already_queued": True}
    devices = _targets(db)
    if device_ids is not None:
        wanted = set(device_ids)
        devices = [d for d in devices if d.id in wanted]
    ids = [d.id for d in devices]
    now = AD._now().isoformat()
    job = {"state": "QUEUED", "run_id": uuid.uuid4().hex, "by": actor, "started_at": now,
           "device_ids": ids, "cursor": 0, "done": 0, "total": len(ids),
           "auto_enable_rest": auto_enable_rest, "auto_trust_new_hosts": auto_trust_new_hosts,
           "credential_ids": credential_ids, "clusters_saved": 0, "clusters": [], "results": [], "counts": {}}
    AD._put(db, AD.JOB_KEY, job, actor)
    db.commit()
    return {"state": "QUEUED", "run_id": job["run_id"], "total": len(ids)}


def _bootstrap_switch(db, d, profile, actor, options):
    from ..aruba import ssh
    from ..aruba.rest_enablement import enable_cx_rest_api
    from ..security.crypto import decrypt_secret
    from ..config import get_settings
    from .change_executor import control
    if not options.get("auto_enable_rest") or not get_settings().device_writes_permitted or control(db).emergency_stop:
        return False
    ip = str(d.mgmt_addresses[0])
    observed = ssh.scan_host_key(ip)
    pinned = AD.pinned_fingerprint(db, ip)
    if pinned and pinned != observed["fingerprint"]:
        AD._put(db, f"ssh-hostkey-pending:{ip}", {
            **observed, "changed_from": pinned, "scanned_at": AD._now().isoformat()}, actor)
        return False
    if not pinned and not options.get("auto_trust_new_hosts", True):
        return False
    fp = pinned or observed["fingerprint"]
    password = decrypt_secret(profile.password_enc)
    with ssh.SshShowClient(ip, profile.username, password, fp, timeout=12) as client:
        version = client.show("show version")
        output = str(version.get("Command output") or "")
        if "arubaos-cx" not in output.lower():
            return False
    if not pinned:
        AD._put(db, f"ssh-hostkey:{ip}", {**observed,
            "approved_by": actor, "approved_at": AD._now().isoformat(),
            "trust": "FIRST_USE_AUTHENTICATED"}, actor)
    try:
        enable_cx_rest_api(ip, profile.username, password, fp, vrf="default")
    except Exception:
        return False
    audit(db, "automatic_switch_rest_bootstrap", username=actor, target=d.id,
          detail="Authenticated SSH; fixed REST bootstrap on default VRF")
    return True


def _switch(db, d, actor, options, probe=None):
    from ..api.onboarding_workflow import _probe
    from ..api.quick_devices import _permit_probe
    from ..domain import collection as C
    from ..domain.adapters import resolve

    spec = resolve(d.vendor, d.family, d.model)
    if not spec.implemented:
        return {"device_id": d.id, "hostname": d.hostname, "result": "UNSUPPORTED_ADAPTER"}
    if not d.mgmt_addresses:
        return {"device_id": d.id, "hostname": d.hostname, "result": "NO_MANAGEMENT_IP"}
    ip = ipaddress.IPv4Address(str(d.mgmt_addresses[0]))
    _permit_probe(ip)
    profiles = AD._candidate_profiles(db, d, options.get("credential_ids"))
    result = {"device_id": d.id, "hostname": d.hostname, "ip": str(ip), "result": "NO_CREDENTIAL"}
    for profile in profiles:
        try:
            try:
                observed = (probe or _probe)(profile, ip)
            except Exception:
                if not _bootstrap_switch(db, d, profile, actor, options):
                    raise
                observed = (probe or _probe)(profile, ip)
            actual = observed.get("serial_number")
            if actual and d.serial_number and not d.serial_number.startswith("UNKNOWN-") and actual != d.serial_number:
                return {**result, "result": "IDENTITY_MISMATCH", "detail": "Reported switch serial differs from inventory."}
            AD._bind(db, d, profile, actor)
            for name in ("model", "firmware", "hostname"):
                if observed.get(name):
                    setattr(d, name, observed[name])
            plan = db.scalar(select(CollectionPlan).where(CollectionPlan.device_id == d.id))
            if plan is None:
                plan = CollectionPlan(device_id=d.id, site_id=d.site_id, adapter_key=spec.key,
                    credential_profile_id=profile.id, metric_families=list(C.METRIC_FAMILIES["switch"]),
                    interval_seconds=300, max_concurrency=1, state="ACTIVE",
                    next_due_at=AD._now(), created_by=actor)
                db.add(plan)
            else:
                plan.credential_profile_id = profile.id
                plan.state, plan.next_due_at = "ACTIVE", AD._now()
            d.status = "up"
            return {**result, "result": "SWITCH_MONITORING_ENABLED", "credential_id": profile.id}
        except Exception as exc:
            result["result"] = "AUTH_OR_CONNECTION_FAILED"
            result["detail"] = type(exc).__name__
    return result


def process_device(db, d, actor, options):
    if _monitored(db, d):
        return {"device_id": d.id, "hostname": d.hostname, "result": "ALREADY_MONITORED"}
    if d.family == "switch":
        return _switch(db, d, actor, options)
    out = AD.discover(db, actor, device_ids=[d.id],
                     auto_enable_rest=options.get("auto_enable_rest", False),
                     auto_trust_new_hosts=options.get("auto_trust_new_hosts", True),
                     credential_ids=options.get("credential_ids"))
    result = next((r for r in out["results"] if r["device_id"] == d.id),
                  {"device_id": d.id, "hostname": d.hostname, "result": "NO_RESULT"})
    return {**result, "_clusters": out.get("clusters", [])}


def tick(session_factory, owner, *, batch_size=2, process=None):
    """A bounded batch commits after each device; API/worker restarts resume its cursor."""
    holder = owner + "-" + uuid.uuid4().hex[:8]
    with session_factory() as db:
        if not _lease(db, LEASE_DEVICE, holder, ttl=300):
            return {"busy": True}
    try:
        with session_factory() as db:
            job = AD._get(db, AD.JOB_KEY) or {}
            cfg = policy(db)
            if job.get("state") == "RUNNING" and "device_ids" not in job:
                queue(db, job.get("by", "automatic-onboarding"),
                      device_ids=[d.id for d in _targets(db) if d.family == "access_point"],
                      auto_enable_rest=False, auto_trust_new_hosts=True,
                      credential_ids=cfg.get("credential_ids"))
                job = AD._get(db, AD.JOB_KEY)
            if job.get("state") not in ("QUEUED", "RUNNING") or "device_ids" not in job:
                if not cfg["enabled"]:
                    return {"idle": True}
                ready = []
                for d in _targets(db):
                    if _monitored(db, d):
                        continue
                    last = AD._get(db, DEVICE_PREFIX + d.id) or {}
                    retry_at = last.get("retry_at")
                    if retry_at and datetime.fromisoformat(retry_at) > AD._now():
                        continue
                    ready.append(d.id)
                    if len(ready) >= 200:
                        break
                if not ready:
                    return {"idle": True}
                queue(db, cfg.get("by", "automatic-onboarding"), device_ids=ready,
                      auto_enable_rest=cfg["auto_enable_rest"],
                      auto_trust_new_hosts=cfg["auto_trust_new_hosts"], credential_ids=cfg["credential_ids"])
                job = AD._get(db, AD.JOB_KEY)
            AD._put(db, AD.JOB_KEY, {**job, "state": "RUNNING", "worker": owner,
                                     "heartbeat_at": AD._now().isoformat()}, job["by"])
            db.commit()
        for _ in range(batch_size):
            with session_factory() as db:
                _renew_lease(db, LEASE_DEVICE, holder, ttl=300)
                job = AD._get(db, AD.JOB_KEY)
                cursor = job["cursor"]
                if cursor >= len(job["device_ids"]):
                    break
                did = job["device_ids"][cursor]
                device = db.get(InmDevice, did)
                try:
                    result = ((process or process_device)(db, device, job["by"], job)
                              if device else {"device_id": did, "result": "DEVICE_REMOVED"})
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    result = {"device_id": did, "result": "FAILED", "detail": type(exc).__name__}
                _renew_lease(db, LEASE_DEVICE, holder, ttl=300)
                # Re-read progress after the device function commits cluster/inventory data.
                job = AD._get(db, AD.JOB_KEY)
                clusters = {c["vc_ip"]: c for c in job.get("clusters", [])}
                for c in result.pop("_clusters", []):
                    clusters[c["vc_ip"]] = c
                results = [*job.get("results", []), result]
                counts = {}
                for r in results:
                    counts[r["result"]] = counts.get(r["result"], 0) + 1
                done = cursor + 1
                finished = done >= len(job["device_ids"])
                update = {**job, "cursor": done, "done": done, "results": results, "counts": counts,
                          "clusters": list(clusters.values()), "clusters_saved": len(clusters),
                          "heartbeat_at": AD._now().isoformat(), "state": "SUCCEEDED" if finished else "RUNNING"}
                if finished:
                    update["finished_at"] = AD._now().isoformat()
                AD._put(db, AD.JOB_KEY, update, job["by"])
                AD._put(db, DEVICE_PREFIX + did, {"result": result["result"],
                    "attempted_at": AD._now().isoformat(),
                    "retry_at": (AD._now() + timedelta(seconds=cfg.get("retry_seconds", 900))).isoformat()}, job["by"])
                audit(db, "automatic_device_onboarded", username=job["by"], target=did,
                      detail=result["result"], success=result["result"] in SUCCESS)
                db.commit()
                if finished:
                    return {"state": "SUCCEEDED", "done": done}
        with session_factory() as db:
            job = AD._get(db, AD.JOB_KEY)
            if job["cursor"] >= len(job["device_ids"]):
                AD._put(db, AD.JOB_KEY, {**job, "state": "SUCCEEDED", "finished_at": AD._now().isoformat()}, job["by"])
            return {"state": (AD._get(db, AD.JOB_KEY) or {}).get("state"), "done": job["done"]}
    finally:
        with session_factory() as db:
            _release(db, LEASE_DEVICE, holder)
            db.commit()
