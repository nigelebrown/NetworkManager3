"""DEMO ArubaOS-CX switch fixtures (CX 6100 / CX 6200). Opens no network session, ever.

These exist so Phase 1 can demonstrate the generic Device/Capability model and switch UI with clearly
labelled synthetic data. Real CX support is Phase 2 (read-only, per-model hardware evidence required).
"""
from __future__ import annotations

import math
import time
import zlib
from datetime import datetime, timezone

from ...domain.model import Capability, CapabilityStatus, DataQuality, Device, DeviceFamily, SourceType, TelemetryPoint

ADAPTER_ID, ADAPTER_VERSION = "demo.aruba_cx", "0.1.0"

DEMO_SWITCHES = [
    # (legacy demo site name, hostname, model, serial, ports)
    ("Demo HQ", "demo-hq-cx6200-01", "CX 6200F 48G", "DEMO-CX62-0001", 48),
    ("Demo HQ", "demo-hq-cx6100-01", "CX 6100 24G", "DEMO-CX61-0001", 24),
    ("Demo Warehouse", "demo-wh-cx6100-01", "CX 6100 24G", "DEMO-CX61-0002", 24),
    ("Demo Branch A", "demo-br-cx6100-01", "CX 6100 12G", "DEMO-CX61-0003", 12),
]

# What the platform *would* call on a real switch, all marked DEMO (no hardware evidence yet).
CAPABILITIES = [
    Capability("read.system", CapabilityStatus.DEMO, "fixture"),
    Capability("read.interfaces", CapabilityStatus.DEMO, "fixture"),
    Capability("read.vlans", CapabilityStatus.DEMO, "fixture"),
    Capability("read.poe", CapabilityStatus.DEMO, "fixture"),
    Capability("read.lldp", CapabilityStatus.DEMO, "fixture"),
    Capability("read.logs.audit", CapabilityStatus.UNVERIFIED, "Phase 3-4; schema path only"),
    Capability("config.vlan", CapabilityStatus.UNSUPPORTED, "writes disabled until Phase 6 certification"),
    Capability("config.port", CapabilityStatus.UNSUPPORTED, "writes disabled until Phase 6 certification"),
]


def _wave(seed: str, lo: float, hi: float, period_s: int = 900) -> float:
    phase = (zlib.crc32(seed.encode()) % 1000) / 1000 * 2 * math.pi
    x = (math.sin(time.time() / period_s * 2 * math.pi + phase) + 1) / 2
    return round(lo + (hi - lo) * x, 1)


class DemoSwitchAdapter:
    adapter_id, adapter_version = ADAPTER_ID, ADAPTER_VERSION

    def advertise_capabilities(self) -> list[Capability]:
        return list(CAPABILITIES)

    def fetch_inventory(self) -> list[tuple[str, Device, int]]:
        out = []
        for site_name, host, model, serial, ports in DEMO_SWITCHES:
            out.append((site_name, Device(
                id="", source_type=SourceType.DEMO, vendor="aruba", family=DeviceFamily.SWITCH, model=model,
                serial_number=serial, hostname=host, firmware="PL.10.14.DEMO", status="up", adapter=ADAPTER_ID,
                capabilities=list(CAPABILITIES)), ports))
        return out

    def fetch_metrics(self, device: Device, ports: int, event_id: str, site_id: str | None) -> list[TelemetryPoint]:
        now = datetime.now(timezone.utc)
        up = int(_wave(device.serial_number + "p", ports * 0.55, ports * 0.95))

        def p(name, value, unit):
            return TelemetryPoint(device_id=device.id, metric_name=name, observed_at=now, value=value, unit=unit,
                                  collection_method="demo_fixture", quality=DataQuality.OBSERVED,
                                  source_type=SourceType.DEMO, source_event_id=event_id, site_id=site_id, sample_period_s=60)
        return [p("ports_up", float(up), "count"), p("ports_total", float(ports), "count"),
                p("poe_power_w", _wave(device.serial_number + "w", 40, 260), "W"),
                p("cpu_percent", _wave(device.serial_number + "c", 4, 38), "%")]
