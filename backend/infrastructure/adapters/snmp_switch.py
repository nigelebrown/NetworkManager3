"""Read-only collectors for non-Aruba devices (rc17, GAP-T12): generic SNMPv1/v2c/v3 switches, Cisco IOS-XE over
SNMP (standard MIBs plus labelled vendor extensions), and the UniFi Network controller API.

Only standards-defined objects are trusted for health: IF-MIB (RFC 2863) 64-bit counters, ENTITY-MIB (RFC 6933)
serial numbers, LLDP-MIB (IEEE 802.1AB) neighbours and SNMPv2-MIB system data. Vendor objects (e.g. Cisco
CISCO-PROCESS-MIB CPU) are collected only for the matching enterprise and labelled VENDOR_UNVERIFIED until compared
with the device on hardware. Missing objects are omitted, never zero-filled. No SET/write path exists here.

Credentials: an SNMP collection plan references a saved SNMP credential profile (the same encrypted profiles used by
printer discovery, any version, any USM protocol). UniFi uses the device's bound encrypted onboarding credential.
"""
from __future__ import annotations
import logging

import asyncio
import ipaddress
import os
import threading
from typing import Any

_log = logging.getLogger(__name__)

IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
IF_TYPE = "1.3.6.1.2.1.2.2.1.3"
IF_ADMIN = "1.3.6.1.2.1.2.2.1.7"
IF_OPER = "1.3.6.1.2.1.2.2.1.8"
IF_IN_ERR = "1.3.6.1.2.1.2.2.1.14"
IF_OUT_ERR = "1.3.6.1.2.1.2.2.1.20"
IF_HC_IN = "1.3.6.1.2.1.31.1.1.1.6"
IF_HC_OUT = "1.3.6.1.2.1.31.1.1.1.10"
IF_HIGH_SPEED = "1.3.6.1.2.1.31.1.1.1.15"      # Mbit/s
IF_ALIAS = "1.3.6.1.2.1.31.1.1.1.18"
ENT_SERIAL = "1.3.6.1.2.1.47.1.1.1.1.11"
ENT_MODEL = "1.3.6.1.2.1.47.1.1.1.1.13"
LLDP_REM_SYSNAME = "1.0.8802.1.1.2.1.4.1.1.9"
SYS_DESCR, SYS_NAME, SYS_OBJECT = "1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.1.2.0"
CISCO_CPU_5MIN = "1.3.6.1.4.1.9.9.109.1.1.1.1.8"  # CISCO-PROCESS-MIB cpmCPUTotal5minRev (vendor, unverified)
ETHERNET_TYPES = {6, 62, 69, 117}                  # ethernetCsmacd, fastEther, fastEtherFX, gigabitEthernet

# Isolated test/demo agents (host -> EmulatedPrinterAgent-compatible reader). Used only when INM_SNMP_EMULATION=true.
EMULATORS: dict[str, Any] = {}


def _emulation() -> bool:
    return os.environ.get("INM_SNMP_EMULATION", "false").lower() in ("1", "true", "yes")


def _run(coro):
    """Run a coroutine from sync collector code, even if this thread already has an event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box: dict = {}
    t = threading.Thread(target=lambda: box.setdefault("r", asyncio.run(coro)))
    t.start()
    t.join()
    return box["r"]


def _suffix(oid: str, base: str) -> str:
    return oid[len(base) + 1:] if oid.startswith(base + ".") else oid


def _int(v):
    try:
        return int(v) if v is not None and not isinstance(v, (bytes, bytearray)) else None
    except (TypeError, ValueError):
        return None


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace").strip("\x00 ").strip() or None
    return str(v).strip() or None


async def read_switch(reader, *, vendor: str = "generic") -> dict:
    """One read-only SNMP collection pass. Returns the collector result shape used by collection_runner."""
    from ...printers.collector import SnmpTruncated, SnmpUnreachable
    descr = await reader.get(SYS_DESCR)
    if descr is None and await reader.get(SYS_NAME) is None:
        raise SnmpUnreachable("no system MIB response")

    async def table(base):
        try:
            return {_suffix(o, base): v for o, v in await reader.walk(base, 4096)}
        except SnmpTruncated as exc:     # keep rows read; counters for missing rows are simply absent
            return {_suffix(o, base): v for o, v in exc.rows}
    names, types = await table(IF_NAME), await table(IF_TYPE)
    admin, oper = await table(IF_ADMIN), await table(IF_OPER)
    hin, hout = await table(IF_HC_IN), await table(IF_HC_OUT)
    ein, eout = await table(IF_IN_ERR), await table(IF_OUT_ERR)
    speed, alias = await table(IF_HIGH_SPEED), await table(IF_ALIAS)
    ports = []
    for idx, name in names.items():
        if _int(types.get(idx)) not in ETHERNET_TYPES:
            continue
        mbps = _int(speed.get(idx))
        ports.append({"name": _text(name) or f"if{idx}", "if_index": idx,
                      "admin": {1: "up", 2: "down"}.get(_int(admin.get(idx))),
                      "link": {1: "up", 2: "down"}.get(_int(oper.get(idx)), "unknown"),
                      "speed_bps": mbps * 1_000_000 if mbps else None,
                      "description": (_text(alias.get(idx)) or "")[:64],
                      "rx_bytes": _int(hin.get(idx)), "tx_bytes": _int(hout.get(idx)),
                      "rx_errors": _int(ein.get(idx)), "tx_errors": _int(eout.get(idx)),
                      "link_resets": None, "poe_w": None, "poe_status": None, "lldp": None})
    lldp = await table(LLDP_REM_SYSNAME)
    serials = [s for s in (_text(v) for v in (await table(ENT_SERIAL)).values()) if s]
    models = [s for s in (_text(v) for v in (await table(ENT_MODEL)).values()) if s]
    metrics: dict[str, float] = {"ports_total": float(len(ports)),
                                 "ports_up": float(sum(1 for p in ports if p["link"] == "up")),
                                 "lldp_neighbors": float(len(lldp))}
    vendor_metrics: dict[str, str] = {}
    if vendor == "cisco":
        cpu = [_int(v) for v in (await table(CISCO_CPU_5MIN)).values()]
        cpu = [c for c in cpu if c is not None and 0 <= c <= 100]
        if cpu:
            metrics["cpu_percent"] = float(max(cpu))
            vendor_metrics["cpu_percent"] = "VENDOR_UNVERIFIED (CISCO-PROCESS-MIB cpmCPUTotal5minRev)"
    return {"metrics": metrics, "ports": ports, "hardware": {"serials": serials[:8], "models": models[:8]},
            "firmware": None, "identity": {"system_description": _text(descr), "serial_number": serials[0] if serials else None},
            "vendor_metrics": vendor_metrics, "source": "SNMP_READ"}


def _reader(db, host: str, profile_id: str | None):
    from ...printers.collector import PysnmpReader
    from ...printers.service import _credentials
    from ...printers.snmp import permitted_target
    if _emulation() and host in EMULATORS:
        return EMULATORS[host]
    if not permitted_target(host):
        raise ValueError("Target outside approved read-only management ranges")
    creds = _credentials(db, profile_id)
    if creds is None:
        raise LookupError("No SNMP credential profile is assigned to this collection plan")
    version, secrets, options = creds
    return PysnmpReader(host, version, *secrets, **options)


def snmp_switch_collector(device, plan, db) -> dict:
    from ..collection_runner import CollectionFailure
    from ...printers.collector import SnmpUnreachable
    addrs = device.mgmt_addresses or []
    if not addrs:
        raise CollectionFailure("TARGET_NOT_ALLOWED", "No management address recorded.")
    try:
        host = str(ipaddress.IPv4Address(str(addrs[0])))
        reader = _reader(db, host, plan.credential_profile_id)
    except ValueError as exc:
        raise CollectionFailure("TARGET_NOT_ALLOWED", str(exc)) from None
    except LookupError as exc:
        raise CollectionFailure("AUTH_REJECTED", str(exc)) from None
    vendor = "cisco" if plan.adapter_key.startswith("cisco") else "generic"
    try:
        result = _run(read_switch(reader, vendor=vendor))
    except SnmpUnreachable as exc:
        raise CollectionFailure("UNREACHABLE", f"No SNMP response ({exc})") from None
    finally:
        try:
            _run(reader.close())
        except Exception as _exc:  # noqa: BLE001
            _log.debug("best-effort closing the SNMP reader: %s", type(_exc).__name__)
    return result


# ---- UniFi Network controller (read-only) ---------------------------------------------------------------
# Endpoints of the UniFi Network Application (classic API): POST /api/login, GET /api/s/{site}/stat/device. They are
# not a formally versioned public API; responses are validated and anything unexpected is reported as PARSE_ERROR.
def unifi_controller_collector(device, plan, db) -> dict:
    import httpx
    from fastapi import HTTPException
    from ..collection_runner import CollectionFailure
    from ...api.quick_devices import _permit_probe
    from ...config import get_settings
    from ...database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
    from ...security.crypto import decrypt_secret
    binding = db.get(OnboardingDeviceCredential, device.id)
    profile = db.get(OnboardingCredential, binding.credential_id) if binding else None
    if profile is None or not getattr(profile, "enabled", True):
        raise CollectionFailure("AUTH_REJECTED", "No enabled controller credential bound to this device.")
    addrs = device.mgmt_addresses or []
    if not addrs:
        raise CollectionFailure("TARGET_NOT_ALLOWED", "Record the UniFi controller address as the management address.")
    try:
        ip = ipaddress.IPv4Address(str(addrs[0]))
        _permit_probe(ip)
    except (HTTPException, ValueError) as exc:
        raise CollectionFailure("TARGET_NOT_ALLOWED", str(getattr(exc, "detail", exc))) from None
    site = (plan.metric_families and next((f.split(":", 1)[1] for f in plan.metric_families if f.startswith("site:")), None)) or "default"
    transport = EMULATORS.get(f"unifi:{ip}") if _emulation() else None
    try:
        with httpx.Client(base_url=f"https://{ip}:8443", verify=get_settings().tls_verify_for(str(ip)), timeout=15,
                          transport=transport) as c:
            r = c.post("/api/login", json={"username": profile.username, "password": decrypt_secret(profile.password_enc)})
            if r.status_code in (400, 401, 403):
                raise CollectionFailure("AUTH_REJECTED", "Controller rejected the credential.")
            r.raise_for_status()
            data = c.get(f"/api/s/{site}/stat/device").json()
    except httpx.HTTPError as exc:
        raise CollectionFailure("UNREACHABLE", f"Controller request failed: {type(exc).__name__}") from None
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise CollectionFailure("PARSE_ERROR", "Unexpected controller response shape.")
    me = next((d for d in rows if isinstance(d, dict) and str(d.get("serial", "")).casefold() == device.serial_number.casefold()), None)
    if me is None:
        raise CollectionFailure("PARSE_ERROR", "This device's serial number is not in the controller's device list.")
    metrics: dict[str, float] = {}
    if isinstance(me.get("num_sta"), (int, float)):
        metrics["clients"] = float(me["num_sta"])
    stats = me.get("system-stats") if isinstance(me.get("system-stats"), dict) else {}
    for key, metric in (("cpu", "cpu_percent"), ("mem", "memory_percent")):
        try:
            v = float(stats[key])
            if 0 <= v <= 100:
                metrics[metric] = v
        except (KeyError, TypeError, ValueError):
            pass  # metric absent or out of range: not reported (never defaulted)
    state = {1: "up", 0: "down"}.get(me.get("state"), "unknown")
    return {"metrics": metrics, "firmware": me.get("version"), "status": state, "source": "UNIFI_CONTROLLER_READ",
            "identity": {"model": me.get("model"), "serial_number": me.get("serial")}}


COLLECTORS = {"generic_snmpv3": snmp_switch_collector, "cisco_ios_xe_snmpv3": snmp_switch_collector,
              "unifi_controller": unifi_controller_collector}
