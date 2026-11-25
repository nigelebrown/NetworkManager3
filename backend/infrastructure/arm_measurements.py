"""Persisted, bounded read-only ARM measurement collection for real Instant APs."""
from __future__ import annotations
import ipaddress
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import re
from sqlalchemy import select
from ..database.models import AppSetting, Site
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
    value=dict(row.value) if row else {"state":"NOT_COLLECTED","source":arm.SOURCE,"device_id":device_id}
    try:
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(value.get("observed_at",""))).total_seconds()
        value["freshness"]="FRESH" if value.get("state")=="MEASURED" and 0<=age<=900 else "STALE"
    except (ValueError,TypeError):
        value["freshness"]="MISSING"
    return value


def request(db, device_id, actor):
    value = snapshot(db,device_id)
    AD._put(db,PREFIX+device_id,{**value,"requested_at":datetime.now(timezone.utc).isoformat(),
                                "collection_state":"QUEUED"},actor)
    audit(db,"arm_measurement_requested",username=actor,target=device_id,detail="Fixed read-only ARM commands; no device writes")
    return {"state":"QUEUED","device_id":device_id}



def _identity(reader, outputs, device, target_ip=None):
    """Default MAC names and renamed APs are valid when local serial identity agrees."""
    name = re.search(r"(?im)^name\s*:\s*(.+)$", outputs.get("environment", ""))
    if name and name[1].strip().casefold() == (device.hostname or "").casefold():
        return {"verified": True, "method": "AP_ENV_NAME"}
    # show summary reports the local AP serial, unlike a cluster member list.
    summary = output_text(reader.show("show summary", iap_ip=target_ip) if target_ip else reader.show("show summary"))
    if len(summary) > 100000:
        raise ValueError("Identity output exceeds limit")
    serial = re.search(r"(?im)^Serial Number\s*:\s*(\S+)\s*$", summary)
    actual_ip = re.search(r"(?im)^IP Address\s*:\s*(\S+)\s*$", summary)
    expected = (device.serial_number or "").strip()
    verified = bool(serial and expected and serial[1].casefold() == expected.casefold())
    if target_ip:
        verified = verified and bool(actual_ip and actual_ip[1] == target_ip)
    return {"verified": verified, "method": "LOCAL_SERIAL_NUMBER",
            "serial_number": serial[1] if serial else None,
            "management_ip": actual_ip[1] if actual_ip else None}


def collect(db, device, actor="arm-collector", *, client=None):
    """SSH always targets the individual AP, never labels VC measurements as a member's."""
    from ..api.quick_devices import _permit_probe
    from ..aruba.errors import ArubaCommandError
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
        if not fp:
            from .automatic_onboarding import policy
            from ..aruba.ssh import scan_host_key
            cfg=policy(db)
            selected=cfg.get("credential_ids")
            first_use_allowed=(cfg.get("enabled") and cfg.get("auto_trust_new_hosts") and
                               (profile is None or selected is None or profile.id in selected))
            if first_use_allowed:
                try:
                    observed=scan_host_key(ip,timeout=8)
                    candidate=SshShowClient(ip,username,password,observed["fingerprint"],timeout=8)
                    try:
                        environment=output_text(candidate.show("show ap-env"))
                        version=output_text(candidate.show("show version"))
                        identity=_identity(candidate, {"environment":environment}, device)
                        if not identity["verified"] or "aruba" not in version.lower():
                            raise ValueError("Authenticated AP identity did not match inventory")
                        AD._put(db,"ssh-hostkey:"+ip,{**observed,"approved_by":actor,
                            "approved_at":datetime.now(timezone.utc).isoformat(),
                            "trust":"FIRST_USE_AUTHENTICATED_ARM"},actor)
                        audit(db,"arm_ssh_first_use_authenticated",username=actor,target=device.id,
                              detail="Saved credentials and local AP identity verified before pinning first SSH key")
                        db.commit()  # release the audit-chain row before any further network I/O
                        client=candidate
                        outputs["environment"]=environment
                        transport="PINNED_SSH_DIRECT_AP"
                    except Exception:
                        candidate.close()
                        raise
                except Exception as exc:
                    errors["ssh_first_use"]=type(exc).__name__
        if fp:
            client = SshShowClient(ip,username,password,fp,timeout=8)
            transport = "PINNED_SSH_DIRECT_AP"
        elif client is None:
            # A directly authenticated AP REST session gives exact measurement attribution.
            host=site.vc_host if site and site.vc_host else ip
            _permit_probe(ipaddress.IPv4Address(host))
            target_ip=ip if host!=ip else None
            client = ArubaClient(ArubaTarget(host,site.vc_port if site else 4343,username,password),
                                 timeout=8,verify=get_settings().tls_verify_arg)
            transport = "REST_TARGETED_AP" if target_ip else "REST_DIRECT_AP"
    identity = {"verified":False,"method":"UNAVAILABLE"}
    native_attempt = None
    native_result = None
    native_error = None
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
                    if key == "rf" and isinstance(exc, ArubaCommandError):
                        try:
                            version = output_text(client.show("show version",iap_ip=target_ip) if target_ip else client.show("show version"))
                            if "arubaos-cx" in version.lower():
                                errors["device_type"] = "Expected an Instant AP; this management address reports ArubaOS-CX"
                        except Exception:
                            pass
                    break
        if outputs:
            try:
                identity = _identity(client, outputs, device, target_ip)
            except Exception as exc:
                errors["identity_read"] = type(exc).__name__
        if os.environ.get("INM_CLIENT_MATCH_HISTORY", "false").lower() == "true" and site and site.enabled and not site.simulated:
            from . import client_match_history as CM
            native_attempt = datetime.now(timezone.utc)
            try:
                if not _identity(client, {}, device, target_ip)["verified"]:
                    raise ValueError("ClientMatch target serial not verified")
                raw = output_text(client.show(CM.COMMAND, iap_ip=target_ip) if target_ip else client.show(CM.COMMAND))
                native_attempt = datetime.now(timezone.utc)
                native_result = CM.parse(raw, site_id=device.legacy_site_id, collected=native_attempt)
            except Exception as exc:
                native_error = type(exc).__name__
    finally:
        if owned:
            client.close()
    now = datetime.now(timezone.utc)
    if native_attempt is not None:
        CM.save(db, device, native_result, collected=native_attempt, failure=native_error)
    old = snapshot(db,device.id)
    parsed = arm.parse(outputs)
    if outputs and not identity["verified"]:
        parsed["state"]="TARGET_UNVERIFIED"
        errors["identity"]="Local AP identity did not match the requested inventory target"
    if errors.get("device_type"):
        parsed["state"]="DEVICE_TYPE_MISMATCH"
    # Direct command data is a measurement, not proof that all configuration operations are writable.
    value = {**parsed,"device_id":device.id,"observed_at":now.isoformat(),"transport":transport,
             "collection_state":"SUCCEEDED" if parsed["state"]=="MEASURED" else "FAILED",
             "errors":errors,"identity":identity,"last_attempt_at":now.isoformat(),"next_due_at":(now+timedelta(minutes=10)).isoformat()}
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


def tick(session_factory, owner, limit=4):
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
                site = db.get(Site,d.legacy_site_id) if d.legacy_site_id else None
                binding = db.get(OnboardingDeviceCredential,d.id)
                profile = db.get(OnboardingCredential,binding.credential_id) if binding else None
                # A local SSH measurement does not depend on a successful VC REST poll.
                # Imported APs with saved credentials must also get their first attempt.
                if not queued and ((site and not site.enabled) or
                    not d.mgmt_addresses or d.vendor.lower() != "aruba" or
                    not ((profile and profile.enabled) or (site and site.enabled and not site.simulated))):
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
