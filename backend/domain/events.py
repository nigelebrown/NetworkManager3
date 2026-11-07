"""EventEnvelope v1 (master design 3.2 / E.2). Pure functions: construction, integrity hash, validation."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone

SCHEMA_VERSION = "1.0"

# Stable subjects. Payload schemas are semantically versioned; consumers tolerate additive optional fields.
SUBJECT_PREFIX = "inm.events."
UI_SUBJECT_PREFIX = "inm.ui."
EVENT_TYPES = {
    "device.telemetry.observed",
    "site.poll.completed",
    "site.poll.failed",
    "inventory.synchronised",
    "synthetic.probe",
    "alert.changed",
    "recommendation.created",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class EventEnvelope:
    event_id: str
    event_type: str
    occurred_at_utc: str
    emitted_at_utc: str
    correlation_id: str
    source_service: str
    source_type: str  # DEMO | REAL
    actor_type: str  # service | human
    actor_id: str
    data_quality: str = "OBSERVED"
    adapter_version: str | None = None
    causation_id: str | None = None
    site_id: str | None = None
    legacy_site_id: int | None = None
    device_id: str | None = None
    payload_ref: str | None = None
    payload: dict = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    integrity_hash: str = ""

    @property
    def subject(self) -> str:
        return SUBJECT_PREFIX + self.event_type

    def to_json(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()


def integrity_hash(fields: dict) -> str:
    body = {k: v for k, v in fields.items() if k != "integrity_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def new_event(event_type: str, *, source_service: str, source_type: str, actor_id: str, actor_type: str = "service",
              correlation_id: str | None = None, causation_id: str | None = None, occurred_at: datetime | None = None,
              event_id: str | None = None, **kw) -> EventEnvelope:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown event_type {event_type!r}")
    if source_type not in ("DEMO", "REAL"):
        raise ValueError("source_type must be DEMO or REAL")
    now = utcnow()
    fields = dict(
        event_id=event_id or str(uuid.uuid4()), event_type=event_type,
        occurred_at_utc=_iso(occurred_at or now), emitted_at_utc=_iso(now),
        correlation_id=correlation_id or str(uuid.uuid4()), causation_id=causation_id,
        source_service=source_service, source_type=source_type, actor_type=actor_type, actor_id=actor_id,
        schema_version=SCHEMA_VERSION, **kw,
    )
    env = EventEnvelope(**fields)  # apply dataclass defaults first so the hash covers every serialised field
    return replace(env, integrity_hash=integrity_hash(json.loads(env.to_json())))


class EnvelopeError(ValueError):
    pass


def parse_event(raw: bytes) -> EventEnvelope:
    """Validate an incoming envelope. Unknown *optional* keys are ignored (additive compatibility)."""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise EnvelopeError("not JSON") from exc
    if not isinstance(data, dict):
        raise EnvelopeError("envelope must be an object")
    major = str(data.get("schema_version", "")).split(".")[0]
    if major != SCHEMA_VERSION.split(".")[0]:
        raise EnvelopeError(f"unsupported schema_version {data.get('schema_version')!r}")
    known = {f for f in EventEnvelope.__dataclass_fields__}
    missing = {"event_id", "event_type", "occurred_at_utc", "emitted_at_utc", "correlation_id", "source_service",
               "source_type", "actor_type", "actor_id", "integrity_hash"} - data.keys()
    if missing:
        raise EnvelopeError(f"missing fields: {sorted(missing)}")
    fields = {k: v for k, v in data.items() if k in known}
    if integrity_hash(data) != fields["integrity_hash"]:
        raise EnvelopeError("integrity_hash mismatch")
    try:
        uuid.UUID(fields["event_id"])
        uuid.UUID(fields["correlation_id"])
    except ValueError as exc:
        raise EnvelopeError("event_id / correlation_id must be UUIDs") from exc
    if fields["source_type"] not in ("DEMO", "REAL"):
        raise EnvelopeError("source_type must be DEMO or REAL")
    return EventEnvelope(**fields)
