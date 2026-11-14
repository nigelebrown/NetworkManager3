"""Bounded, one-off SNMPv3 authPriv printer discovery; no device writes.

Uses only numeric, standards-defined OIDs; never installs print drivers or
downloads untrusted MIB code. Credentials live only in the request call stack.
The device must be within the configured private management scope.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
from typing import Any

from ..config import get_settings

SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT = "1.3.6.1.2.1.1.2.0"
MARKER_LIFE = "1.3.6.1.2.1.43.10.2.1.4"
MARKER_UNIT = "1.3.6.1.2.1.43.10.2.1.3"
PRINTER_SERIAL = "1.3.6.1.2.1.43.5.1.1.17"
# RFC 3805's prtMarkerCounterUnit must be checked before labelling a count.


def permitted_target(host: str, ranges: str | None = None) -> bool:
    try:
        address = ipaddress.ip_address(host)
        if not isinstance(address, ipaddress.IPv4Address) or not address.is_private:
            return False
        if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
            return False
        if ranges is None and os.environ.get("INM_ON_PREMISES_MANAGEMENT") == "true" and get_settings().mode == "live":
            return any(address in ipaddress.IPv4Network(net) for net in
                       ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
        raw = os.environ.get("INM_READONLY_DEVICE_CIDRS", "") if ranges is None else ranges
        nets = [ipaddress.ip_network(x.strip(), strict=True) for x in raw.split(",") if x.strip()]
        return bool(nets) and len(nets) <= 20 and all(
            isinstance(n, ipaddress.IPv4Network) and n.is_private and n.prefixlen >= 24 for n in nets
        ) and any(address in n for n in nets)
    except ValueError:
        return False


def parse_printer_marker(unit: int | None, count: int | None) -> dict[str, Any] | None:
    if count is None or count < 0 or unit is None:
        return None
    # RFC 3805 marker counter unit enum: 7 = impressions, 8 = sheets.
    mapping = {7: "impressions_total", 8: "sheets_total"}
    kind = mapping.get(unit)
    if not kind:
        return None
    return {"meter_kind": kind, "counter_value": count,
            "source": "SNMPV3", "quality": "REPORTED",
            "note": "Printer-MIB prtMarkerLifeCount; unit confirmed from prtMarkerCounterUnit"}


async def read_snmpv3(host: str, username: str, auth_key: str, privacy_key: str) -> dict[str, Any]:
    """Single SNMPv3 SHA-256/AES-128 target, timeout ≈ 8s, read-only GET/GETNEXT."""
    return await read_printer(host, "3", username, auth_key, privacy_key)


async def read_printer(host: str, version: str, identity: str, auth_key: str = "", privacy_key: str = "") -> dict[str, Any]:
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData, ContextData, ObjectIdentity, ObjectType, SnmpEngine, UdpTransportTarget,
        UsmUserData, get_cmd, next_cmd,
        USM_AUTH_HMAC192_SHA256, USM_PRIV_CFB128_AES,
    )

    if not permitted_target(host):
        raise ValueError("Target outside authorized read-only management ranges")
    engine = SnmpEngine()
    if version == "3":
        auth = UsmUserData(
            identity, authKey=auth_key, privKey=privacy_key,
            authProtocol=USM_AUTH_HMAC192_SHA256, privProtocol=USM_PRIV_CFB128_AES,
        )
    elif version in ("1", "2c"):
        auth = CommunityData(identity, mpModel=0 if version == "1" else 1)
    else:
        raise ValueError("Unsupported SNMP version")
    source = "SNMPV" + version.upper()
    try:
        transport = await UdpTransportTarget.create((host, 161), timeout=3, retries=1)
        async def get_one(oid: str) -> str | None:
            error, status, _, vars_ = await get_cmd(
                engine, auth, transport, ContextData(),
                ObjectType(ObjectIdentity(oid)), lookupMib=False,
            )
            if error or status or not vars_:
                return None
            result = vars_[0][1]
            if "No Such" in str(result) or "noSuch" in result.__class__.__name__:
                return None
            return result.prettyPrint()

        async def get_indexed(base: str):
            error, status, _, vars_ = await next_cmd(
                engine, auth, transport, ContextData(),
                ObjectType(ObjectIdentity(base)), lookupMib=False,
            )
            if error or status or not vars_:
                return None
            name, value = vars_[0]
            oid = name.prettyPrint()
            if not oid.startswith(base + "."):
                return None
            try:
                return oid[len(base):], int(value)
            except (ValueError, TypeError):
                return None

        name = await get_one(SYS_NAME)
        descr = await get_one(SYS_DESCR)
        obj = await get_one(SYS_OBJECT)
        marker = await get_indexed(MARKER_LIFE)
        counter = None
        if marker:
            suffix, total = marker
            unit_text = await get_one(MARKER_UNIT + suffix)
            try:
                unit = int(unit_text) if unit_text is not None else None
            except (ValueError, TypeError):
                unit = None
            counter = parse_printer_marker(unit, total)
            if counter:
                counter["source"] = source

        if not (name or descr or obj or marker):
            raise RuntimeError("SNMP authentication failed, timed out, or printer has no standard system MIB")
        serial = None
        if marker:
            try:
                serial = await asyncio.wait_for(get_one(PRINTER_SERIAL + "." + marker[0].split(".")[1]), timeout=1)
            except TimeoutError:
                pass
        serial = (serial or "").strip()
        if len(serial) > 100 or not serial.isprintable() or serial.casefold() in {"unknown", "n/a", "none"}:
            serial = ""
        return {"hostname": name, "system_description": descr,
                "serial_number": serial or None,
                "system_object_id": obj, "meter": counter, "printer_mib": marker is not None,
                "source": source, "device_writes": "LOCKED"}
    finally:
        engine.close_dispatcher()
