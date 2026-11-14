"""Project legacy SIMS inventory into the generic Site / Cluster / Device / Capability graph.

Legacy `sites` rows are Instant clusters. Each becomes an InmCluster linked by legacy_site_id; a physical
InmSite is created alongside it with the same display name and the SAME source type (DEMO data can never be
attached to a REAL site, and vice versa). Legacy access_points become InmDevice rows keyed by
(source_type, vendor, serial). Nothing here talks to a device.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...database.models import AccessPoint, AppSetting, Site
from ...database.models_inm import DeviceCapability, InmCluster, InmDevice, InmSite
from ...domain.model import Capability, CapabilityStatus
from ..adapters.demo_switch import DemoSwitchAdapter

AP_CAPS_DEMO = [
    Capability("read.inventory", CapabilityStatus.DEMO, "SIMS demo generator"),
    Capability("read.clients", CapabilityStatus.DEMO, "SIMS demo generator"),
    Capability("read.rf", CapabilityStatus.DEMO, "SIMS demo generator"),
    Capability("config.ssid", CapabilityStatus.UNSUPPORTED, "device writes locked (Phase 1)"),
    Capability("config.hostname", CapabilityStatus.UNSUPPORTED, "device writes locked (Phase 1)"),
    Capability("firmware.stage", CapabilityStatus.UNSUPPORTED, "device writes locked (Phase 1)"),
]
AP_CAPS_REAL = [
    Capability("read.inventory", CapabilityStatus.UNVERIFIED, "Phase 2 hardware evidence required"),
    Capability("read.clients", CapabilityStatus.UNVERIFIED, "Phase 2 hardware evidence required"),
    Capability("read.rf", CapabilityStatus.UNVERIFIED, "Phase 2 hardware evidence required"),
    Capability("config.ssid", CapabilityStatus.UNSUPPORTED, "device writes locked (Phase 1-2)"),
    Capability("config.hostname", CapabilityStatus.UNSUPPORTED, "device writes locked (Phase 1-2)"),
    Capability("firmware.stage", CapabilityStatus.UNSUPPORTED, "device writes locked (Phase 1-2)"),
]


def _src(simulated: bool) -> str:
    return "DEMO" if simulated else "REAL"


def _set_caps(db: Session, device: InmDevice, caps: list[Capability]) -> None:
    have = {c.operation: c for c in db.scalars(select(DeviceCapability).where(DeviceCapability.device_id == device.id))}
    now = datetime.now(timezone.utc)
    for cap in caps:
        row = have.get(cap.operation)
        if row is None:
            db.add(DeviceCapability(device_id=device.id, operation=cap.operation, status=cap.status.value,
                                    evidence=cap.evidence, updated_at=now))
        elif row.status != cap.status.value:
            row.status, row.evidence, row.updated_at = cap.status.value, cap.evidence, now


def sync_inventory(db: Session) -> dict[str, int]:
    """Idempotent projection. Returns counts for the inventory.synchronised event."""
    counts = {"sites": 0, "clusters": 0, "aps": 0, "switches": 0}
    clusters_by_legacy: dict[int, InmCluster] = {c.legacy_site_id: c for c in db.scalars(select(InmCluster)) if c.legacy_site_id}
    for s in db.scalars(select(Site)):
        src = _src(s.simulated)
        site = db.scalar(select(InmSite).where(InmSite.source_type == src, InmSite.name == s.name))
        if site is None:
            site = InmSite(name=s.name, source_type=src,
                           description="Physical location mapped 1:1 from a legacy SIMS site (verify in Phase 2)")
            db.add(site)
            db.flush()
        cl = clusters_by_legacy.get(s.id)
        if cl is None:
            cl = InmCluster(name=s.name, kind="aruba_instant_cluster", source_type=src, site_id=site.id, legacy_site_id=s.id)
            db.add(cl)
            db.flush()
            clusters_by_legacy[s.id] = cl
        counts["sites"] += 1
        counts["clusters"] += 1

    site_overrides = {row.key.removeprefix('device-site:'): row.value['site_id'] for row in db.scalars(
        select(AppSetting).where(AppSetting.key.startswith('device-site:')))}
    for ap in db.scalars(select(AccessPoint)):
        cl = clusters_by_legacy.get(ap.site_id)
        src = _src(ap.simulated)
        serial = ap.serial or f"MAC:{ap.mac}"
        dev = db.scalar(select(InmDevice).where(InmDevice.source_type == src, InmDevice.vendor == "aruba",
                                                InmDevice.serial_number == serial))
        if dev is None:
            dev = InmDevice(source_type=src, environment="demo" if ap.simulated else "lab", vendor="aruba",
                            family="access_point", serial_number=serial,
                            adapter="demo.aruba_instant" if ap.simulated else "aruba_instant_8_10", lifecycle="monitored")
            db.add(dev)
        dev.model, dev.hostname, dev.firmware = ap.model, ap.name, ap.firmware
        dev.mgmt_addresses = [ap.ip] if ap.ip else []
        dev.cluster_id = cl.id if cl else None
        dev.site_id = site_overrides.get(dev.id, cl.site_id if cl else None)
        dev.legacy_ap_id, dev.legacy_site_id, dev.status = ap.id, ap.site_id, ap.status
        if ap.last_seen:
            dev.last_observed_at = ap.last_seen
        db.flush()
        _set_caps(db, dev, AP_CAPS_DEMO if ap.simulated else AP_CAPS_REAL)
        counts["aps"] += 1

    # DEMO switches attach to the demo sites only (never to REAL sites).
    adapter = DemoSwitchAdapter()
    for site_name, d, _ports in adapter.fetch_inventory():
        legacy = db.scalar(select(Site).where(Site.name == site_name, Site.simulated.is_(True)))
        if legacy is None:
            continue
        cl = clusters_by_legacy.get(legacy.id)
        dev = db.scalar(select(InmDevice).where(InmDevice.source_type == "DEMO", InmDevice.vendor == "aruba",
                                                InmDevice.serial_number == d.serial_number))
        if dev is None:
            dev = InmDevice(source_type="DEMO", environment="demo", vendor="aruba", family="switch",
                            serial_number=d.serial_number, adapter=adapter.adapter_id, lifecycle="monitored")
            db.add(dev)
        dev.model, dev.hostname, dev.firmware, dev.status = d.model, d.hostname, d.firmware, "up"
        dev.site_id = cl.site_id if cl else None
        dev.legacy_site_id = legacy.id
        dev.last_observed_at = datetime.now(timezone.utc)
        db.flush()
        _set_caps(db, dev, d.capabilities)
        counts["switches"] += 1
    return counts
