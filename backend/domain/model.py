"""Generic inventory graph: Site, Cluster, Device, Capability (Device-agnostic; Aruba is one adapter)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class SourceType(str, Enum):
    DEMO = "DEMO"
    REAL = "REAL"


class DataQuality(str, Enum):
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    STALE = "STALE"
    INVALID = "INVALID"


class CapabilityStatus(str, Enum):
    """Never inferred from an API schema: a schema path is not certified hardware support (Appendix M)."""
    UNSUPPORTED = "UNSUPPORTED"
    UNVERIFIED = "UNVERIFIED"
    READ_ONLY = "READ_ONLY"
    CERTIFIED_WRITE = "CERTIFIED_WRITE"
    DEMO = "DEMO"


class DeviceFamily(str, Enum):
    ACCESS_POINT = "access_point"
    SWITCH = "switch"


@dataclass(frozen=True)
class Capability:
    operation: str
    status: CapabilityStatus
    evidence: str | None = None

    @property
    def is_write(self) -> bool:
        return self.operation.startswith(("write.", "config.", "firmware.", "certificate."))


@dataclass
class Site:
    id: str
    name: str
    source_type: SourceType


@dataclass
class Cluster:
    id: str
    name: str
    kind: str
    source_type: SourceType
    site_id: str | None = None
    legacy_site_id: int | None = None


@dataclass
class Device:
    """DeviceIdentity v1."""
    id: str
    source_type: SourceType
    vendor: str
    family: DeviceFamily
    model: str | None
    serial_number: str
    hostname: str | None = None
    firmware: str | None = None
    mgmt_addresses: list[str] = field(default_factory=list)
    site_id: str | None = None
    cluster_id: str | None = None
    status: str = "unknown"
    adapter: str = "unassigned"
    capabilities: list[Capability] = field(default_factory=list)
    last_observed_at: datetime | None = None

    @property
    def identity_key(self) -> tuple[str, str, str]:
        """Natural key. DEMO and REAL never collide even with identical hostnames (SEC-02)."""
        return (self.source_type.value, self.vendor, self.serial_number)

    def can(self, operation: str) -> bool:
        for c in self.capabilities:
            if c.operation == operation:
                return c.status in (CapabilityStatus.READ_ONLY, CapabilityStatus.CERTIFIED_WRITE, CapabilityStatus.DEMO) \
                    and (not c.is_write or c.status == CapabilityStatus.CERTIFIED_WRITE)
        return False


@dataclass(frozen=True)
class TelemetryPoint:
    """TelemetryPoint v1. value None means unavailable, never zero (SIMS convention retained)."""
    device_id: str
    metric_name: str
    observed_at: datetime
    value: float | None
    unit: str
    collection_method: str
    quality: DataQuality
    source_type: SourceType
    source_event_id: str
    site_id: str | None = None
    sample_period_s: int | None = None
    tags: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Recommendation:
    """Recommendation v1. engine is RULE in Phase 1; ML only after the Phase 8 gate."""
    rule_id: str
    rule_version: str
    engine: str
    risk: str
    title: str
    explanation: str
    evidence: dict
    input_window: str
    source_type: SourceType
    legacy_site_id: int | None = None
    device_id: str | None = None
    approval_state: str = "ADVISORY"
