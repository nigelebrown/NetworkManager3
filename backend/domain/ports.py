"""Port contracts (Appendix L.3). Infrastructure implements these; application code depends only on them."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .events import EventEnvelope
from .model import Capability, Device, Recommendation, TelemetryPoint


class DeviceWriteDenied(PermissionError):
    """Raised by every DeviceWritePort in Phase 1 and Phase 2. Absence of an approved gate means deny."""


@runtime_checkable
class DeviceReadPort(Protocol):
    adapter_id: str
    adapter_version: str

    def advertise_capabilities(self) -> list[Capability]: ...
    def fetch_inventory(self) -> list[Device]: ...
    def fetch_metrics(self, device: Device) -> list[TelemetryPoint]: ...


@runtime_checkable
class DeviceWritePort(Protocol):
    def apply(self, device: Device, change: dict[str, Any], *, actor: str, approval_id: str | None) -> None: ...


@runtime_checkable
class DeviceDiscoveryPort(Protocol):
    def discover(self, targets: list[str], credential_profile_id: str) -> list[Device]: ...


@runtime_checkable
class EventBusPort(Protocol):
    async def publish(self, event: EventEnvelope) -> int: ...


@runtime_checkable
class TelemetryStorePort(Protocol):
    def write_points(self, points: list[TelemetryPoint]) -> int: ...
    def query(self, *, device_id: str | None, metric_name: str | None, since: datetime, limit: int) -> list[dict]: ...
    def schema_revision(self) -> str | None: ...


@runtime_checkable
class LogStorePort(Protocol):
    def push(self, labels: dict[str, str], line: str, ts: datetime) -> None: ...
    def query(self, logql: str, since: datetime, limit: int) -> list[dict]: ...


@runtime_checkable
class AuditTrailPort(Protocol):
    def append(self, action: str, *, actor: str | None, target: str | None, detail: str | None, success: bool) -> str: ...
    def verify(self) -> dict: ...


@runtime_checkable
class ConfigVaultPort(Protocol):
    """Phase 5. Phase 1 exposes only the contract; legacy redacted backups are NEVER restorable."""
    def list_restorable(self, device_id: str) -> list[dict]: ...


@runtime_checkable
class ExperiencePort(Protocol):
    def score_client(self, metrics: Any) -> dict: ...


@runtime_checkable
class RecommendationPort(Protocol):
    def analyze(self, context: dict) -> list[Recommendation]: ...


@runtime_checkable
class WebEventPort(Protocol):
    async def broadcast(self, topics: list[str], *, legacy_site_ids: list[int] | None, event_id: str | None) -> None: ...


@dataclass(frozen=True)
class DependencyCheck:
    """Measured result of one readiness probe. `status` is never assumed: no probe -> UNKNOWN."""
    name: str
    role: str
    status: str  # UP | DEGRADED | DOWN | UNKNOWN
    latency_ms: float | None
    version: str | None
    detail: str
    checked_at: datetime
