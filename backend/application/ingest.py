"""Ingest use case: one bus event -> TimescaleDB points + management record + Loki line -> UI invalidation.

Every step is idempotent so a JetStream redelivery (crash, timeout, NATS outage) produces exactly one logical
result:
  * telemetry rows are unique on (source_event_id, device, metric, time)        -> ON CONFLICT DO NOTHING
  * the Loki line is deterministic (same stream, timestamp and body)           -> Loki drops exact duplicates
  * the management record and processed-marker commit together, LAST           -> a crash before commit = retry
The UI invalidation is published only after the commit.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..domain.events import EventEnvelope
from ..domain.model import DataQuality, SourceType, TelemetryPoint

# Which UI query groups an event type invalidates.
TOPICS = {
    "device.telemetry.observed": ["devices", "telemetry"],
    "site.poll.completed": ["dashboard", "aps", "clients", "alerts", "experience", "sites", "devices"],
    "site.poll.failed": ["dashboard", "sites", "alerts"],
    "inventory.synchronised": ["devices", "sites"],
    "synthetic.probe": ["system", "events"],
    "alert.changed": ["alerts", "dashboard"],
    "recommendation.created": ["recommendations"],
    "device.poll.failed": ["devices", "alerts", "dashboard"],
    "change.executed": ["changes", "devices", "alerts"],
}


@dataclass
class IngestResult:
    status: str  # applied | duplicate
    telemetry_rows: int
    topics: list[str]


def points_from_event(e: EventEnvelope) -> list[TelemetryPoint]:
    out = []
    for p in e.payload.get("points", []):
        out.append(TelemetryPoint(
            device_id=p.get("device_id") or e.device_id, metric_name=p["metric"],
            observed_at=datetime.fromisoformat(p.get("observed_at") or e.occurred_at_utc),
            value=None if p.get("value") is None else float(p["value"]), unit=p.get("unit", ""),
            collection_method=p.get("collection_method", "unknown"),
            quality=DataQuality(p.get("quality", e.data_quality)), source_type=SourceType(e.source_type),
            source_event_id=e.event_id, site_id=e.site_id, sample_period_s=p.get("sample_period_s"),
            tags=p.get("tags", {}), entity_type=p.get("entity_type") or "device",
            entity_id=str(p.get("entity_id") or "")))
    return out


def log_line(e: EventEnvelope) -> str:
    """Deterministic JSON line (no wall-clock fields) so a retried push is a Loki duplicate, not a new line."""
    body = {"event_id": e.event_id, "event_type": e.event_type, "correlation_id": e.correlation_id,
            "source_type": e.source_type, "source_service": e.source_service, "actor": f"{e.actor_type}:{e.actor_id}",
            "site_id": e.site_id, "legacy_site_id": e.legacy_site_id, "device_id": e.device_id,
            "data_quality": e.data_quality, "summary": e.payload.get("summary"),
            "points": len(e.payload.get("points", []))}
    for k in ("run_ids", "failure_category", "plan_id", "outcome", "environment"):
        if e.payload.get(k) is not None:
            body[k] = e.payload[k]
    if e.source_type == "DEMO":
        body["label"] = "DEMO"
    return json.dumps(body, sort_keys=True, separators=(",", ":"))


class PersistenceError(RuntimeError):
    """Telemetry that an event carries could not be stored / verified. The event is retried, never acked."""


class IngestService:
    def __init__(self, *, ledger, telemetry, logs, commit: Callable[[], None], rollback: Callable[[], None],
                 effects: Callable[[EventEnvelope], list[str] | None] | None = None):
        """effects: domain state changes for this event (collection run success, device status, alerts) applied
        in the SAME transaction as the ledger marker, after telemetry and logs were persisted."""
        self.ledger, self.telemetry, self.logs = ledger, telemetry, logs
        self._commit, self._rollback = commit, rollback
        self._effects = effects

    def handle(self, e: EventEnvelope) -> IngestResult:
        topics = TOPICS.get(e.event_type, ["events"])
        if self.ledger.is_processed(e.event_id):
            self.ledger.note_redelivery(e.event_id)
            self._commit()
            return IngestResult("duplicate", 0, [])
        try:
            points = points_from_event(e)
            if points and self.telemetry is None:
                raise PersistenceError("telemetry store is not configured; refusing to acknowledge telemetry")
            rows = self.telemetry.write_points(points) if points else 0
            expected = e.payload.get("expected_points")
            if points and expected is not None and hasattr(self.telemetry, "count_event"):
                stored = self.telemetry.count_event(e.event_id)
                if stored < int(expected):
                    raise PersistenceError(f"telemetry persisted {stored}/{expected} points for {e.event_id}")
            if self.logs is not None:
                self.logs.push({"service": e.source_service[:32].replace("-", "_"),
                                "source_type": e.source_type.lower(), "event_type": e.event_type.replace(".", "_")},
                               log_line(e), datetime.fromisoformat(e.occurred_at_utc))
            applied = self.ledger.record(e, e.payload.get("summary"))
            if applied and self._effects is not None:
                topics = sorted(set(topics) | set(self._effects(e) or []))
            self._commit()
        except Exception:
            self._rollback()
            raise
        return IngestResult("applied" if applied else "duplicate", rows, topics if applied else [])
