"""One-click DEMO dataset: load and remove.

Everything created here is source_type DEMO (or simulated=True in legacy SIMS tables), lives on
demo sites, and is never contacted over the network. Removing it deletes ONLY demo rows; REAL
devices, sites, telemetry, audit history and users are untouched. The audit log is append-only and
keeps the record that demo data was loaded/removed.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..database.models import Alert, AppSetting, ClientEvent, Site
from ..database.models_inm import EventRecord, InmCluster, InmDevice, InmSite, RecommendationRecord
from ..database.models_phases import (AuthSessionEvidence, ChangeAttribution, ChangeExecution, ChangePlan,
                                      CollectionPlan, CollectionRun, ConfigArtifact, DecisionFeedback,
                                      DeviceAccess, DeviceCertification, DeviceLogEvent, DevicePortSnapshot,
                                      ExecutionDecision, InmAlert, OptimizationEnrollment, OutcomeEvaluation,
                                      ReplacementPlan, RestoreValidation, VaultSchedule)
from ..database.models_templates import InmConfigTemplate
from ..domain import vault as V
from ..domain.model import DataQuality, SourceType, TelemetryPoint
from .phase_services import LocalObjectStore, vault_master_key

SETTING = "demo_dataset"
NS = uuid.UUID("5d0f6a2e-41a4-4f0e-a0f3-4b4fd9b3c0d7")


def status(db: Session) -> dict:
    row = db.get(AppSetting, SETTING)
    v = row.value if row else {}
    return {"loaded": bool(v.get("loaded")), "loaded_at": v.get("at"), "by": v.get("by"),
            "removed_explicitly": bool(v.get("removed")),
            "demo_devices": len(list(db.scalars(select(InmDevice.id).where(InmDevice.source_type == "DEMO"))))}


def _set(db: Session, value: dict, actor: str) -> None:
    row = db.get(AppSetting, SETTING)
    if row is None:
        row = AppSetting(key=SETTING, value=value, updated_by=actor)
        db.add(row)
    row.value, row.updated_by, row.updated_at = value, actor, datetime.now(timezone.utc)


def demo_allowed_at_startup(db: Session) -> bool:
    """Startup seeding respects an explicit 'remove demo data'."""
    row = db.get(AppSetting, SETTING)
    return not (row and row.value.get("removed"))


# ---- realistic configuration texts (mixture of good and poor practice) --------------------
def cx_config(host: str, good: bool) -> str:
    lines = [f"hostname {host}", "clock timezone america/bogota" if good else "",
             "ssh server vrf mgmt", "https-server vrf mgmt",
             "ntp server 10.10.0.10 iburst" if good else "", "ntp enable" if good else "",
             "logging 10.10.0.20 udp 1514 vrf mgmt" if good else "",
             "tacacs-server host 10.10.0.30 key ciphertext AQBapXYZ==" if good else "telnet server",
             "aaa accounting all-mgmt default start-stop group tacacs" if good else "",
             "snmpv3 user inm-ro auth sha auth-pass ciphertext AQBa== priv aes priv-pass ciphertext AQBb=="
             if good else "snmp-server community public",
             "banner motd !Authorised access only!" if good else "",
             "spanning-tree", "vlan 1", "vlan 100", "    name Corp", "vlan 200", "    name Voice",
             "vlan 300" if good else "", "    name Guest" if good else "", "vlan 400", "    name IoT"]
    for i in range(1, 9):
        lines += [f"interface 1/1/{i}", "    no shutdown",
                  f"    vlan access {100 if i % 2 else (200 if good else 1)}",
                  "    spanning-tree bpdu-guard" if good else "", "    loop-protect" if good else ""]
    lines += ["interface 1/1/49", "    no shutdown", "    vlan trunk native 1", "    vlan trunk allowed 100,200,300,400"]
    return "\n".join(x for x in lines if x) + "\n"


def iap_config(name: str, good: bool) -> str:
    lines = [f"name {name}", "virtual-controller-country US", "clock timezone Bogota -05 00" if good else "",
             "ntp-server 10.10.0.10" if good else "", "syslog-server 10.10.0.20",
             "arm", "    client-match" if good else "", f"    min-tx-power {12 if good else 3}",
             "    max-tx-power 18", "    band-steering-mode prefer-5ghz",
             "    air-time-fairness-mode fair-access" if good else "",
             "rf dot11a-radio-profile", f"    channel-width {40 if good else 80}",
             "wlan ssid-profile Corp-WiFi", "    essid Corp-WiFi", "    opmode wpa3-aes-ccm-128" if good else "    opmode wpa2-aes",
             "    vlan 100", "    a-basic-rates 12 24" if good else "    a-basic-rates 6 12", "    max-clients-threshold 64",
             "    dot11k" if good else "", "    dot11v" if good else "",
             "wlan ssid-profile Guest-WiFi", "    essid Guest-WiFi", "    opmode opensystem", "    vlan 300",
             "    captive-portal internal" if good else "",
             "" if good else "snmp-server community public"]
    return "\n".join(x for x in lines if x) + "\n"


def load(db: Session, *, actor: str, store=None) -> dict:
    from ..monitoring.collector import ensure_demo_sites, poll_site
    from .persistence.inventory import sync_inventory
    now = datetime.now(timezone.utc)
    rng = random.Random(42)
    counts: dict[str, int] = {}
    ensure_demo_sites(db)
    for s in db.scalars(select(Site).where(Site.simulated.is_(True))):
        s.enabled = True
        poll_site(db, s)
    db.flush()
    sync_inventory(db)
    db.flush()
    devices = list(db.scalars(select(InmDevice).where(InmDevice.source_type == "DEMO").order_by(InmDevice.hostname)))
    counts["devices"] = len(devices)
    # Management addresses for demo devices (documentation range — never routable / never contacted)
    for i, d in enumerate(devices):
        d.mgmt_addresses = d.mgmt_addresses or [f"192.0.2.{10 + i}"]
        if not d.firmware:
            d.firmware = "8.10.0.21" if d.family == "access_point" else "10.14.1020"
        d.vendor = "Aruba"
    # 24h telemetry history every 15 minutes
    pts = 0
    if store is not None:
        for d in devices:
            base_clients = rng.randint(4, 30) if d.family == "access_point" else 0
            hot = d.family == "access_point" and rng.random() < 0.25
            for k in range(96, -1, -1):
                t = now - timedelta(minutes=15 * k)
                hour = (t.hour - 5) % 24
                load_f = 0.25 + 0.75 * max(0.0, 1 - abs(hour - 13) / 7)
                ev = str(uuid.uuid5(NS, f"{d.id}:{k}:{t:%Y%m%d%H}"))
                vals = {"cpu_percent": round(10 + 40 * load_f + rng.uniform(-4, 4), 1),
                        "memory_percent": round(45 + 10 * load_f + rng.uniform(-2, 2), 1)}
                if d.family == "access_point":
                    vals.update({"clients": float(round(base_clients * load_f + rng.uniform(0, 3))),
                                 "util_5g_percent": round(min(99, (55 if hot else 20) + 40 * load_f + rng.uniform(-5, 5)), 1),
                                 "util_2g_percent": round(min(99, 30 + 35 * load_f + rng.uniform(-5, 5)), 1),
                                 "noise_floor_dbm": round(-95 + rng.uniform(-2, 4), 1),
                                 "retry_percent": round(5 + (12 if hot else 3) * load_f + rng.uniform(0, 2), 1)})
                else:
                    vals.update({"ports_up": float(rng.randint(10, 20)), "ports_total": 24.0,
                                 "poe_power_w": round(60 + 80 * load_f, 1)})
                batch = [TelemetryPoint(device_id=d.id, metric_name=m, observed_at=t, value=v,
                                        unit="%" if m.endswith("percent") else "dBm" if m.endswith("dbm") else "W" if m.endswith("_w") else "count",
                                        collection_method="DEMO_SYNTHETIC", quality=DataQuality.OBSERVED,
                                        source_type=SourceType.DEMO, source_event_id=ev, site_id=d.site_id,
                                        sample_period_s=900, tags={"demo": True}) for m, v in vals.items()]
                pts += store.write_points(batch)
    counts["telemetry_points"] = pts
    # Collection plans (healthy, one stale, one failing)
    for i, d in enumerate(devices):
        if db.scalar(select(CollectionPlan).where(CollectionPlan.device_id == d.id)):
            continue
        fam = ["system", "radios", "clients"] if d.family == "access_point" else ["system", "interfaces", "poe"]
        p = CollectionPlan(device_id=d.id, site_id=d.site_id, adapter_key="demo", metric_families=fam,
                           interval_seconds=300, state="ACTIVE", created_by=actor,
                           next_due_at=now + timedelta(minutes=5),
                           last_success_at=now - timedelta(minutes=2 if i % 7 else 45))
        if i % 11 == 5:
            p.last_failure_at, p.last_failure_category, p.consecutive_failures = now, "UNREACHABLE", 3
        db.add(p)
    # Vault artifacts with realistic configs: alternate good and poor practice
    key, obj = vault_master_key(), LocalObjectStore()
    for i, d in enumerate(devices):
        good = i % 3 != 0
        text = (cx_config(d.hostname or d.serial_number, good) if d.family == "switch"
                else iap_config(d.hostname or d.serial_number, good)).encode()
        sealed = V.seal(text, master_key=key, identity={
            "device_id": d.id, "serial": d.serial_number, "vendor": d.vendor, "model": d.model,
            "firmware": d.firmware, "capture_source": "DEMO_FIXTURE", "fidelity": V.FIDELITY_FULL})
        if db.scalar(select(ConfigArtifact).where(ConfigArtifact.device_id == d.id,
                                                  ConfigArtifact.plaintext_sha256 == sealed.plaintext_sha256)):
            continue
        db.add(ConfigArtifact(device_id=d.id, serial=d.serial_number, vendor=d.vendor, model=d.model,
                              firmware=d.firmware, fidelity=V.FIDELITY_FULL, capture_source="DEMO_FIXTURE",
                              trigger="SCHEDULE", object_path=obj.put(sealed.ciphertext, sealed.ciphertext_sha256),
                              wrapped_key=sealed.wrapped_key, key_id=sealed.key_id,
                              plaintext_sha256=sealed.plaintext_sha256, ciphertext_sha256=sealed.ciphertext_sha256,
                              manifest=sealed.manifest, signature=sealed.signature, size=len(text),
                              created_by=actor, created_at=now - timedelta(hours=6)))
    counts["vault_artifacts"] = len(devices)
    # Syslog events + attributions (VERIFIED, CORRELATED, UNKNOWN examples)
    switches = [d for d in devices if d.family == "switch"]
    aps = [d for d in devices if d.family == "access_point"]
    for j, d in enumerate(switches[:3]):
        at = now - timedelta(hours=3 + j)
        if j == 0:
            db.add(AuthSessionEvidence(device_serial=d.serial_number, username="j.brown", start_at=at - timedelta(minutes=4),
                                       end_at=at + timedelta(minutes=6), source="TACACS+", recorded_by=actor))
        user = "j.brown" if j == 0 else None
        msg = f"User {user} configuration change committed" if user else "configuration change committed via console"
        ev = DeviceLogEvent(device_id=d.id, device_serial=d.serial_number, source_ip=(d.mgmt_addresses or ["192.0.2.1"])[0],
                            vendor="Aruba", event_code="6801", severity="notice", message=msg, username=user,
                            is_config_change=True, observed_at=at, parser_version="syslog-norm-1.0",
                            transport_quality="UDP_ALLOWLISTED", raw_hash=uuid.uuid5(NS, f"log{d.id}{j}").hex * 2)
        db.add(ev); db.flush()
        level = ["VERIFIED", "CORRELATED", "UNKNOWN"][j]
        db.add(ChangeAttribution(device_id=d.id, log_event_id=ev.id, change_at=at, level=level,
                                 actor="j.brown" if level == "VERIFIED" else None, change_type="DIRECT_DEVICE",
                                 evidence={"demo": True}, candidates=["j.brown", "k.smith"] if level == "CORRELATED" else
                                 (["j.brown"] if level == "VERIFIED" else []), created_by="attribution-engine"))
    for d in devices[:6]:
        for k in range(3):
            db.add(DeviceLogEvent(device_id=d.id, device_serial=d.serial_number, source_ip="192.0.2.1", vendor="Aruba",
                                  event_code=str(1200 + k), severity=["info", "warning", "notice"][k],
                                  message=["Interface 1/1/3 link up", "High channel utilisation on radio 1",
                                           "Client roamed between APs"][k], username=None, is_config_change=False,
                                  observed_at=now - timedelta(minutes=20 * (k + 1)), parser_version="syslog-norm-1.0",
                                  transport_quality="UDP_ALLOWLISTED", raw_hash=uuid.uuid5(NS, f"ev{d.id}{k}").hex * 2))
    # Recommendations + feedback + outcome
    for i, d in enumerate(aps[:5]):
        rec = RecommendationRecord(device_id=d.id, engine="RULE", rule_id="PHASE4_UTIL_5G_PERCENT_SUSTAINED",
                                   rule_version="1.0", source_type="DEMO", risk=["medium", "high", "low"][i % 3],
                                   title="Sustained 5 GHz utilisation",
                                   explanation="Two independent samples above 85%. Review channel plan and client density; "
                                               "do not auto-change RF.",
                                   evidence={"samples": [{"value": 88 + i}, {"value": 90 + i}], "metric": "util_5g_percent"},
                                   input_window="30m", approval_state=["ADVISORY", "ACKNOWLEDGED", "DISMISSED"][i % 3],
                                   requested_by=actor, correlation_id=str(uuid.uuid4()),
                                   created_at=now - timedelta(hours=i * 5))
        db.add(rec); db.flush()
        if i % 3 == 1:
            db.add(DecisionFeedback(recommendation_id=rec.id, operator="noc.analyst", decision="ACCEPTED",
                                    reason="Confirmed high density at lunchtime."))
            db.add(OutcomeEvaluation(recommendation_id=rec.id, action_applied=False, metric="util_5g_percent",
                                     before_window={"n": 6}, after_window={"n": 6},
                                     result={"status": "NO_CHANGE", "success": None, "confounders": ["NO_ACTION_APPLIED"]},
                                     evaluated_by="noc.analyst"))
    # Change plans in several states + one denied execution
    if switches:
        d = switches[0]
        p1 = ChangePlan(device_id=d.id, title="Add Guest VLAN 300 to uplink trunk",
                        operations=[{"op": "switch.vlan.define", "value": 300},
                                    {"op": "switch.port.trunk_vlans", "port": "1/1/49", "value": [100, 200, 300, 400]}],
                        capability_report=[{"op": "switch.vlan.define", "capability": "DEMO"},
                                           {"op": "switch.port.trunk_vlans", "capability": "DEMO"}],
                        plan_hash=uuid.uuid4().hex * 2, rollback_plan={"strategy": "RESTORE_PRE_CHANGE_VAULT_ARTIFACT"},
                        state="PENDING_APPROVAL", requester="net.engineer", firmware_at_request=d.firmware)
        p2 = ChangePlan(device_id=d.id, title="Move port 1/1/4 to Voice VLAN", operations=[
            {"op": "switch.port.access_vlan", "port": "1/1/4", "value": 200}],
            capability_report=[{"op": "switch.port.access_vlan", "capability": "DEMO"}], plan_hash=uuid.uuid4().hex * 2,
            rollback_plan={"strategy": "RESTORE_PRE_CHANGE_VAULT_ARTIFACT"}, state="APPROVED",
            requester="net.engineer", approver="change.manager", approved_at=now - timedelta(hours=1),
            firmware_at_request=d.firmware)
        db.add_all([p1, p2]); db.flush()
        db.add(ExecutionDecision(kind="CHANGE_PLAN", plan_id=p2.id, device_id=d.id, global_lock_state="LOCKED",
                                 decision="DENY", guards=[{"guard": "global_write_lock_off", "pass": False}],
                                 denial_reason="global_write_lock_off", evaluated_by=actor))
    if aps:
        a = aps[0]
        db.add(OptimizationEnrollment(device_id=a.id, immutable_serial=a.serial_number, vendor_model=f"Aruba {a.model}",
                                      firmware_bound=a.firmware, mode="SHADOW", allowed_operation_types=["ap.radio.channel"],
                                      value_constraints={"allowed_widths_mhz": [20, 40]}, permitted_windows={},
                                      cooldown_seconds=7200, max_actions_per_day=1, impact_stop_threshold={},
                                      expires_at_utc=now + timedelta(days=30), requester_id="net.engineer",
                                      status="PENDING_APPROVAL"))
    # A demo inheritance template on the demo site (scope 'global' only for demo family)
    if switches and switches[0].site_id and not db.scalar(
            select(InmConfigTemplate).where(InmConfigTemplate.name == "DEMO Corporate VLAN standard")):
        db.add(InmConfigTemplate(name="DEMO Corporate VLAN standard", family="switch", scope="site",
                                 site_id=switches[0].site_id, priority=100,
                                 config_json={"vlans": [100, 200, 300, 400],
                                              "interfaces": [{"name": "1/1/2", "admin_state": "up", "access_vlan": 200,
                                                              "trunk_vlans": []}]},
                                 enabled=True, created_by=actor))
    _set(db, {"loaded": True, "at": now.isoformat(), "by": actor, "removed": False}, actor)
    db.flush()
    return counts


def remove(db: Session, *, actor: str, store=None) -> dict:
    ids = list(db.scalars(select(InmDevice.id).where(InmDevice.source_type == "DEMO")))
    serials = list(db.scalars(select(InmDevice.serial_number).where(InmDevice.source_type == "DEMO")))
    out: dict[str, int] = {}

    def rm(model, *conds):
        res = db.execute(delete(model).where(*conds).execution_options(synchronize_session=False))
        out[model.__tablename__] = out.get(model.__tablename__, 0) + (res.rowcount or 0)
    if ids:
        art_ids = list(db.scalars(select(ConfigArtifact.id).where(ConfigArtifact.device_id.in_(ids))))
        rec_ids = list(db.scalars(select(RecommendationRecord.id).where(RecommendationRecord.device_id.in_(ids))))
        if art_ids:
            rm(RestoreValidation, RestoreValidation.artifact_id.in_(art_ids))
        if rec_ids:
            rm(DecisionFeedback, DecisionFeedback.recommendation_id.in_(rec_ids))
            rm(OutcomeEvaluation, OutcomeEvaluation.recommendation_id.in_(rec_ids))
        plan_ids = list(db.scalars(select(CollectionPlan.id).where(CollectionPlan.device_id.in_(ids))))
        if plan_ids:
            rm(CollectionRun, CollectionRun.plan_id.in_(plan_ids))
        for model in (CollectionPlan, ConfigArtifact, ChangeAttribution, DeviceLogEvent, ChangePlan,
                      ExecutionDecision, OptimizationEnrollment, ChangeExecution, DevicePortSnapshot, InmAlert,
                      VaultSchedule, DeviceAccess):
            rm(model, model.device_id.in_(ids))
        rm(ReplacementPlan, ReplacementPlan.old_device_id.in_(ids))
        rm(AuthSessionEvidence, AuthSessionEvidence.device_serial.in_(serials))
        rm(InmConfigTemplate, InmConfigTemplate.device_id.in_(ids))
    rm(InmConfigTemplate, InmConfigTemplate.name.like("DEMO %"))
    rm(RecommendationRecord, RecommendationRecord.source_type == "DEMO")
    rm(DeviceCertification, DeviceCertification.environment == "EMULATED")
    from .change_executor import _EMULATORS
    for i in ids:
        _EMULATORS.pop(i, None)
    rm(EventRecord, EventRecord.source_type == "DEMO")
    rm(InmDevice, InmDevice.source_type == "DEMO")
    rm(InmCluster, InmCluster.source_type == "DEMO")
    rm(InmSite, InmSite.source_type == "DEMO")
    rm(Alert, Alert.simulated.is_(True))
    rm(ClientEvent, ClientEvent.simulated.is_(True))
    for s in list(db.scalars(select(Site).where(Site.simulated.is_(True)))):
        db.delete(s)   # ORM + FK cascade removes simulated APs, clients and samples
        out["sites"] = out.get("sites", 0) + 1
    if store is not None:
        out["telemetry_points"] = store.delete_source("DEMO")
    _set(db, {"loaded": False, "removed": True, "at": datetime.now(timezone.utc).isoformat(), "by": actor}, actor)
    db.flush()
    return out
