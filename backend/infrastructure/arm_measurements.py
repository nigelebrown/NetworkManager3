"""Persisted, bounded read-only ARM measurement collection for real Instant APs."""
from __future__ import annotations
import ipaddress
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from ..database.models import AccessPoint, AppSetting, Site
from ..database.models_inm import InmDevice
from ..database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
from ..aruba import arm
from ..aruba.parsers import output_text
from ..security.crypto import decrypt_secret
from ..security.deps import audit
from . import ap_autodiscovery as AD
from .change_executor import _lease, _release

PREFIX = "arm-rf:"


def snapshot(db, device_id):
    row = db.get(AppSetting, PREFIX+device_id)
    return dict(row.value) if row else {"state":"NOT_COLLECTED","source":arm.SOURCE,"device_id":device_id}


def request(db, device_id, actor):
    value = snapshot(db,device_id)
    AD._put(db,PREFIX+device_id,{**value,"requested_at":datetime.now(timezone.utc).isoformat(),
                                "collection_state":"QUEUED"},actor)
    audit(db,"arm_measurement_requested",username=actor,target=device_id,detail="Fixed read-only ARM commands; no device writes")
    return {"state":"QUEUED","device_id":device_id}


def collect(db, device, actor="arm-collector", *, client=None):
    """SSH always targets the individual AP, never labels VC measurements as a member's."""
    from ..api.quick_devices import _permit_probe
    from ..aruba.ssh import SshShowClient
    from ..aruba.client import ArubaClient, ArubaTarget
    from ..config import get_settings
    if device.source_type != "REAL" or device.family != "access_point" or device.vendor.lower() != "aruba":
        raise ValueError("ARM collection requires a real Aruba access point")
    if not device.mgmt_addresses:
        raise ValueError("No AP management address")
    ip = str(device.mgmt_addresses[0])
    _permit_probe(ipaddress.IPv4Address(ip))
    binding = db.get(OnboardingDeviceCredential,device.id)
    profile = db.get(OnboardingCredential,binding.credential_id) if binding else None
    site = db.get(Site,device.legacy_site_id) if device.legacy_site_id else None
    if profile and profile.enabled:
        username,password = profile.username,decrypt_secret(profile.password_enc)
    elif site and not site.simulated:
        username,password = site.api_username,decrypt_secret(site.api_password_enc)
    else:
        raise ValueError("No enabled saved credential is available")
    outputs, errors = {}, {}
    target_ip = None
    owned = client is None
    transport = "TEST_INJECTED" if client else None
    if client is None:
        fp = AD.pinned_fingerprint(db,ip)
        if fp:
            client = SshShowClient(ip,username,password,fp,timeout=8)
            transport = "PINNED_SSH_DIRECT_AP"
        else:
            # A directly authenticated AP REST session gives exact measurement attribution.
            host=site.vc_host if site and site.vc_host else ip
            _permit_probe(ipaddress.IPv4Address(host))
            target_ip=ip if host!=ip else None
            client = ArubaClient(ArubaTarget(host,site.vc_port if site else 4343,username,password),
                                 timeout=8,verify=get_settings().tls_verify_arg)
            transport = "REST_TARGETED_AP" if target_ip else "REST_DIRECT_AP"
    try:
        for key,command in arm.COMMANDS.items():
            try:
                text = output_text(client.show(command,iap_ip=target_ip) if target_ip else client.show(command))
                if len(text)>100000:
                    errors[key] = "OUTPUT_LIMIT"; continue
                outputs[key] = text
            except Exception as exc:
                errors[key] = type(exc).__name__
                if not outputs:  # one unreachable AP must not consume five command timeouts
                    break
    finally:
        if owned:
            client.close()
    now = datetime.now(timezone.utc)
    old = snapshot(db,device.id)
    parsed = arm.parse(outputs)
    import re
    observed_name=re.search(r"(?im)^name\\s*:\\s*(.+)$",outputs.get("environment",""))
    if not observed_name or observed_name[1].strip().casefold()!=(device.hostname or "").casefold():
        parsed["state"]="TARGET_UNVERIFIED"
        errors["identity"]="AP name did not match the requested inventory target"
    # Direct command data is a measurement, not proof that all configuration operations are writable.
    value = {**parsed,"device_id":device.id,"observed_at":now.isoformat(),"transport":transport,
             "collection_state":"SUCCEEDED" if parsed["state"]=="MEASURED" else "FAILED",
             "errors":errors,"last_attempt_at":now.isoformat(),"next_due_at":(now+timedelta(minutes=10)).isoformat()}
    previous = old.get("recent",[])
    if old.get("observed_at") and old.get("state")=="MEASURED":
        previous = [*previous,{"observed_at":old["observed_at"],"assignments":old.get("assignments",[]),
                              "channels":old.get("channels",[])}]
    value["recent"] = previous[-6:]
    AD._put(db,PREFIX+device.id,value,actor)
    audit(db,"arm_measurement_collected",username=actor,target=device.id,
          detail=f"state={parsed['state']} transport={transport} commands={len(outputs)}; read-only",
          success=parsed["state"]=="MEASURED")
    return value


def tick(session_factory, owner, limit=2):
    holder = owner+"-"+uuid.uuid4().hex[:8]
    with session_factory() as db:
        if not _lease(db,"arm-measurement-scheduler",holder,ttl=180):
            return {"busy":True}
    try:
        now = datetime.now(timezone.utc)
        with session_factory() as db:
            devices = list(db.scalars(select(InmDevice).where(InmDevice.source_type=="REAL",
                InmDevice.family=="access_point",InmDevice.lifecycle!="merged").limit(2000)))
            states = {r.key[len(PREFIX):]:r.value for r in db.scalars(select(AppSetting).where(AppSetting.key.startswith(PREFIX)))}
            due = []
            for d in devices:
                state = states.get(d.id,{})
                queued = state.get("collection_state")=="QUEUED"
                next_due = state.get("next_due_at")
                if not queued and next_due and datetime.fromisoformat(next_due)>now:
                    continue
                ap = db.get(AccessPoint,d.legacy_ap_id)
                site = db.get(Site,d.legacy_site_id) if d.legacy_site_id else None
                if not queued and (not ap or (ap.status or "").lower() not in ("up","online") or not site or not site.enabled or not site.last_poll_ok):
                    continue
                due.append((not queued,state.get("last_attempt_at",""),d.id))
            ids = [x[2] for x in sorted(due)[:limit]]
        def one(did):
            try:
                with session_factory() as db:
                    d = db.get(InmDevice,did)
                    result = collect(db,d)
                    db.commit()
                    return result["state"]
            except Exception as exc:
                with session_factory() as db:
                    v = snapshot(db,did)
                    AD._put(db,PREFIX+did,{**v,"collection_state":"FAILED","last_attempt_at":now.isoformat(),
                        "next_due_at":(now+timedelta(minutes=10)).isoformat(),"errors":{"collection":type(exc).__name__}},"arm-collector")
                    db.commit()
                return "FAILED"
        with ThreadPoolExecutor(max_workers=limit) as pool:
            results = list(pool.map(one,ids))
        return {"collected":len(ids),"measured":results.count("MEASURED")}
    finally:
        with session_factory() as db:
            _release(db,"arm-measurement-scheduler",holder)
            db.commit()
