"""Bounded, one-off SNMPv3 authPriv printer discovery; no device writes.

Uses only numeric, standards-defined OIDs; never installs print drivers or
downloads untrusted MIB code. Credentials live only in the request call stack.
The device must be within the configured private management scope.
"""
from __future__ import annotations
import logging

import asyncio
import ipaddress
import os
from typing import Any

from ..config import get_settings

_log = logging.getLogger(__name__)

SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT = "1.3.6.1.2.1.1.2.0"
MARKER_LIFE = "1.3.6.1.2.1.43.10.2.1.4"
MARKER_UNIT = "1.3.6.1.2.1.43.10.2.1.3"
PRINTER_SERIAL = "1.3.6.1.2.1.43.5.1.1.17"
# RFC 3805's prtMarkerCounterUnit must be checked before labelling a count.

# SNMPv3 USM protocol names accepted in saved printer profiles. Profiles created before 1.3.0-rc16 carry no
# protocol fields and keep the original SHA-256/AES-128 behaviour. MD5/DES remain selectable because many
# deployed printers only offer them, but they are reported as WEAK so administrators can plan replacement.
AUTH_PROTOCOLS = {"SHA256": "USM_AUTH_HMAC192_SHA256", "SHA": "USM_AUTH_HMAC96_SHA",
                  "SHA224": "USM_AUTH_HMAC128_SHA224", "SHA384": "USM_AUTH_HMAC256_SHA384",
                  "SHA512": "USM_AUTH_HMAC384_SHA512", "MD5": "USM_AUTH_HMAC96_MD5"}
PRIV_PROTOCOLS = {"AES128": "USM_PRIV_CFB128_AES", "AES192": "USM_PRIV_CFB192_AES",
                  "AES256": "USM_PRIV_CFB256_AES", "DES": "USM_PRIV_CBC56_DES", "3DES": "USM_PRIV_CBC168_3DES",
                  "NONE": "USM_PRIV_NONE"}
WEAK_PROTOCOLS = {"MD5", "DES"}
DEFAULT_AUTH, DEFAULT_PRIV = "SHA256", "AES128"


def usm_options(value: dict | None) -> dict[str, str]:
    """Protocol selection stored beside (never inside) the encrypted secret of a saved profile."""
    value = value or {}
    auth = str(value.get("auth_protocol") or DEFAULT_AUTH).upper()
    priv = str(value.get("priv_protocol") or DEFAULT_PRIV).upper()
    if auth not in AUTH_PROTOCOLS or priv not in PRIV_PROTOCOLS:
        raise ValueError("Unsupported SNMPv3 protocol in saved profile")
    return {"auth_protocol": auth, "priv_protocol": priv}


def build_auth_data(version: str, identity: str, auth_key: str = "", privacy_key: str = "", *,
                    auth_protocol: str = DEFAULT_AUTH, priv_protocol: str = DEFAULT_PRIV):
    """pysnmp authentication object for v1/v2c community or v3 USM (authPriv or authNoPriv)."""
    import pysnmp.hlapi.v3arch.asyncio as hl
    if version == "3":
        auth_protocol, priv_protocol = auth_protocol.upper(), priv_protocol.upper()
        if auth_protocol not in AUTH_PROTOCOLS or priv_protocol not in PRIV_PROTOCOLS:
            raise ValueError("Unsupported SNMPv3 protocol")
        kwargs = {"authKey": auth_key, "authProtocol": getattr(hl, AUTH_PROTOCOLS[auth_protocol])}
        if priv_protocol != "NONE":
            kwargs |= {"privKey": privacy_key, "privProtocol": getattr(hl, PRIV_PROTOCOLS[priv_protocol])}
        return hl.UsmUserData(identity, **kwargs)
    if version in ("1", "2c"):
        return hl.CommunityData(identity, mpModel=0 if version == "1" else 1)
    raise ValueError("Unsupported SNMP version")


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


def permitted_network(network: ipaddress.IPv4Network, ranges: str | None = None) -> bool:
    """Validate a complete CIDR without expanding millions of target addresses."""
    if not isinstance(network, ipaddress.IPv4Network):
        return False
    if ranges is None and os.environ.get("INM_ON_PREMISES_MANAGEMENT") == "true" and get_settings().mode == "live":
        nets = [ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    else:
        raw = os.environ.get("INM_READONLY_DEVICE_CIDRS", "") if ranges is None else ranges
        try:
            nets = [ipaddress.IPv4Network(n.strip(), strict=True) for n in raw.split(",") if n.strip()]
        except ValueError:
            return False
        if not nets or len(nets) > 20 or any(not n.is_private or n.prefixlen < 24 for n in nets):
            return False
    return any(network.subnet_of(n) for n in ipaddress.collapse_addresses(nets))


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


async def read_printer(host: str, version: str, identity: str, auth_key: str = "", privacy_key: str = "", *,
                       auth_protocol: str = DEFAULT_AUTH, priv_protocol: str = DEFAULT_PRIV) -> dict[str, Any]:
    from pysnmp.hlapi.v3arch.asyncio import (
        ContextData, ObjectIdentity, ObjectType, SnmpEngine, UdpTransportTarget, get_cmd, next_cmd,
    )

    if not permitted_target(host):
        raise ValueError("Target outside authorized read-only management ranges")
    auth = build_auth_data(version, identity, auth_key, privacy_key,
                           auth_protocol=auth_protocol, priv_protocol=priv_protocol)
    engine = SnmpEngine()
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
            except TimeoutError as _exc:
                _log.warning("printer serial not returned within 1 s; recorded as missing: %s", type(_exc).__name__, exc_info=True)
        serial = (serial or "").strip()
        if len(serial) > 100 or not serial.isprintable() or serial.casefold() in {"unknown", "n/a", "none"}:
            serial = ""
        return {"hostname": name, "system_description": descr,
                "serial_number": serial or None,
                "system_object_id": obj, "meter": counter, "printer_mib": marker is not None,
                "source": source, "device_writes": "LOCKED"}
    finally:
        engine.close_dispatcher()
