"""Memory-bounded discovery-range definitions and /24 batch previews.

Supports administrative planning for IPv4 /9–/32 ranges (including
128.0.0.0/9). This module deliberately has NO socket, scan, scheduling,
credential, or device-write implementation. A plan never authorizes traffic.
"""
from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, ip_network

MIN_PREFIX = 9
BATCH_PREFIX = 24
MAX_SEGMENTS_PER_PAGE = 64
RFC1918 = tuple(IPv4Network(s) for s in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"
))


class RangeValidationError(ValueError):
    """The requested CIDR cannot be represented as a supported plan."""


def validate_range(value: str) -> IPv4Network:
    if not isinstance(value, str) or len(value) > 80 or not value.strip():
        raise RangeValidationError("Provide a valid IPv4 CIDR, e.g. 10.0.0.0/9")
    try:
        network = ip_network(value.strip(), strict=False)
    except ValueError as exc:
        raise RangeValidationError("Invalid IPv4 CIDR") from exc
    if not isinstance(network, IPv4Network):
        raise RangeValidationError("IPv6 range support is not enabled")
    if network.prefixlen < MIN_PREFIX:
        raise RangeValidationError("The broadest supported IPv4 range is /9")
    return network


def is_rfc1918(network: IPv4Network) -> bool:
    """Do not equate ipaddress.is_private with authorized RFC1918 ownership."""
    return any(network.subnet_of(parent) for parent in RFC1918)


def _segment_count(network: IPv4Network) -> int:
    return 1 if network.prefixlen >= BATCH_PREFIX else (1 << (BATCH_PREFIX - network.prefixlen))


def plan_range(cidr: str) -> dict:
    net = validate_range(cidr)
    private = is_rfc1918(net)
    total = _segment_count(net)
    return {
        "requested_cidr": cidr.strip(),
        "canonical_cidr": str(net),
        "start_address": str(net.network_address),
        "last_address": str(net.broadcast_address),
        "prefix_length": net.prefixlen,
        "address_count": net.num_addresses,
        "batch_prefix": max(net.prefixlen, BATCH_PREFIX),
        "batch_count": total,
        "max_batches_per_page": MAX_SEGMENTS_PER_PAGE,
        "rfc1918_scope": private,
        "execution_status": "APPROVAL_REQUIRED" if private else "DEFINITION_ONLY_PUBLIC_OR_RESERVED",
        "scan_started": False,
        "device_writes": "LOCKED",
        "notes": [
            "This is an offline planning preview; no packets are transmitted.",
            "A /9 contains 8,388,608 addresses; never allocate or probe the entire range at once.",
            "Individual targets still require explicit authorization and existing read-only device controls.",
        ] + ([] if private else [
            "The requested range is not wholly RFC1918; public/reserved targets are not eligible for automated scanning."
        ]),
    }


def preview_batches(cidr: str, *, offset: int = 0, limit: int = 16) -> dict:
    net = validate_range(cidr)
    if not isinstance(offset, int) or offset < 0:
        raise RangeValidationError("Offset must be a nonnegative integer")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_SEGMENTS_PER_PAGE:
        raise RangeValidationError(f"Limit must be 1–{MAX_SEGMENTS_PER_PAGE}")
    total = _segment_count(net)
    if offset > total:
        raise RangeValidationError("Offset exceeds available batches")
    subnet_prefix = max(net.prefixlen, BATCH_PREFIX)
    stride = 1 << (32 - subnet_prefix)
    first = int(net.network_address)
    end = min(offset + limit, total)
    # Arithmetic lookup: never materialize list(net.subnets()) or iter(net.hosts()).
    batches = [str(IPv4Network((IPv4Address(first + index * stride), subnet_prefix)))
               for index in range(offset, end)]
    return {
        "canonical_cidr": str(net),
        "batch_prefix": subnet_prefix,
        "batch_count": total,
        "offset": offset,
        "next_offset": end if end < total else None,
        "batches": batches,
        "scan_started": False,
        "rfc1918_scope": is_rfc1918(net),
        "execution_status": "APPROVAL_REQUIRED" if is_rfc1918(net)
        else "DEFINITION_ONLY_PUBLIC_OR_RESERVED",
    }
