"""Demo Mode data generator.

EVERYTHING produced here is SIMULATED. Rows written from it carry simulated=True and the API/UI
label them as such. Values are random-walk-ish but seeded per (site, AP, time bucket) so
successive polls look like a living network without any hardware.
"""
from __future__ import annotations

import hashlib
import math
import random
import time
from typing import Any

from ..aruba.monitoring import SiteSnapshot
from ..aruba.parsers import quality_from_snr

DEMO_SITES = [
    {"name": "Demo HQ", "group": "Demo - Main", "description": "Simulated headquarters", "aps": 6, "clients": 46, "offline": []},
    {"name": "Demo Branch A", "group": "Demo - Branches", "description": "Simulated branch office", "aps": 3, "clients": 17, "offline": []},
    {"name": "Demo Warehouse", "group": "Demo - Branches", "description": "Simulated warehouse (one AP offline)", "aps": 3, "clients": 9, "offline": [2]},
]

_MODELS = ["AP-505", "AP-505", "AP-515", "AP-505", "AP-535", "AP-505"]
_SSIDS = ["Corp-WiFi", "Corp-WiFi", "Corp-WiFi", "Guest-WiFi", "IoT-Devices"]
_OS = ["Windows", "iOS", "Android", "macOS", "Linux", "Printer"]
_FIRMWARE = "8.10.0.21-8.10.0.21"


def _mac(seed: str, prefix: str = "02") -> str:
    h = hashlib.sha1(seed.encode()).hexdigest()
    return ":".join([prefix] + [h[i:i + 2] for i in range(0, 10, 2)])


def _wave(rng: random.Random, period_s: float, phase: float = 0.0) -> float:
    """Slow sinusoid in [0,1] so charts show believable trends."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * (time.time() + phase) / period_s)


def build_demo_snapshot(site_name: str, now: float | None = None) -> SiteSnapshot:
    spec = next((s for s in DEMO_SITES if s["name"] == site_name), DEMO_SITES[0])
    bucket = int((now or time.time()) // 60)
    rng = random.Random(f"{site_name}:{bucket}")
    base_rng = random.Random(site_name)  # stable identities

    snap = SiteSnapshot(firmware=_FIRMWARE)
    slug = "".join(w[0] for w in site_name.split()).upper()
    ap_names: list[str] = []
    load = 0.35 + 0.45 * _wave(rng, 3600 * 8)

    for i in range(spec["aps"]):
        name = f"{slug}-AP{i + 1}"
        if i in spec["offline"]:
            snap.aps.append({
                "name": name, "mac": _mac(f"{site_name}:{i}", "02"),
                "ip": f"10.250.{(sum(map(ord, site_name)) % 200) + 1}.{10 + i}",
                "model": _MODELS[i % len(_MODELS)], "status": "down",
                "client_count": 0, "radios": None,
            })
            continue
        ap_names.append(name)
        ch5 = base_rng.choice([36, 40, 44, 48, 100, 149, 153, 157, 161])
        ch24 = [1, 6, 11][i % 3]
        util5 = max(2, min(95, 100 * load * (0.3 + rng.random() * 0.5)))
        util24 = max(4, min(98, util5 + 15 + rng.uniform(-8, 12)))
        snap.aps.append({
            "name": name,
            "mac": _mac(f"{site_name}:{i}", "02"),
            "ip": f"10.250.{(sum(map(ord, site_name)) % 200) + 1}.{10 + i}",
            "model": _MODELS[i % len(_MODELS)],
            "serial": "DEMO" + hashlib.sha1(f"{site_name}:{i}".encode()).hexdigest()[:7].upper(),
            "client_count": 0,  # filled below
            "uptime_seconds": 86400 * (3 + i * 5) + bucket % 3600,
            "radios": [
                {"index": 0, "channel": str(ch5), "band": "5GHz", "tx_power_dbm": 12 + (i % 3) * 2},
                {"index": 1, "channel": str(ch24), "band": "2.4GHz", "tx_power_dbm": 9 + (i % 2) * 3},
            ],
            "mode": "access",
            "cpu_percent": round(10 + 30 * load + rng.uniform(-3, 5), 1),
            "memory_percent": round(40 + 15 * load + rng.uniform(-2, 2), 1),
            "util_5g_percent": round(util5, 1),
            "util_2g_percent": round(util24, 1),
        })

    n_clients = max(0, int(spec["clients"] * (0.6 + 0.8 * load)))
    for j in range(n_clients):
        crng = random.Random(f"{site_name}:client:{j}")
        ap = ap_names[j % len(ap_names)] if ap_names else None
        on5 = crng.random() < 0.72
        snr = int(crng.gauss(34 if on5 else 28, 9)) + rng.randint(-3, 3)
        snr = max(5, min(60, snr))
        mac = _mac(f"{site_name}:client:{j}", "06")
        snap.clients.append({
            "mac": mac,
            "hostname": f"{crng.choice(_OS).lower()}-{j:03d}",
            "ip": f"10.{20 + j % 4}.{(sum(map(ord, site_name)) % 200)}.{20 + j}",
            "ssid": crng.choice(_SSIDS),
            "ap_name": ap,
            "channel": str(crng.choice([36, 44, 149]) if on5 else crng.choice([1, 6, 11])),
            "band": "5GHz" if on5 else "2.4GHz",
            "snr_db": snr,
            "rssi_dbm": snr - 92,  # simulated noise floor of -92 dBm (SIMULATED data only)
            "speed_mbps": float(crng.choice([72, 144, 286, 433, 576, 866, 1201]) if on5 else crng.choice([24, 54, 72, 144])),
            "quality": quality_from_snr(snr),
            "quality_source": "simulated",
            "auth_state": "authenticated",
            "vlan": crng.choice([10, 10, 10, 20, 30]),
        })
    for a in snap.aps:
        a["client_count"] = sum(1 for c in snap.clients if c["ap_name"] == a["name"])
    return snap


def demo_running_config(site_name: str) -> str:
    """SIMULATED running-config text (same CLI-style layout the parser expects)."""
    guest = 90 + (sum(map(ord, site_name)) % 9)
    return f"""version 8.10.0.21-8.10.0.21
virtual-controller-country US
name {site_name.replace(' ', '-')}-VC

wlan ssid-profile Corp-WiFi
 enable
 type employee
 essid Corp-WiFi
 wpa-passphrase <redacted>
 opmode wpa2-psk-aes
 vlan 30
 rf-band all

wlan ssid-profile Guest-WiFi
 enable
 type guest
 essid Guest-WiFi
 opmode opensystem
 vlan {guest}
 rf-band all

wlan ssid-profile IoT-Devices
 enable
 type employee
 essid IoT-Devices
 wpa-passphrase <redacted>
 opmode wpa2-psk-aes
 vlan 50
 rf-band 2.4
"""
