"""Migrate legacy SIMS AP sample history (ap_samples) into the INM telemetry store (rc18; register INTEG-P3-02).

  * Timezone: every sample time is converted to UTC. Legacy rows written by SQLite come back naive; SIMS always
    stored UTC (models.utcnow), so a naive value is interpreted as UTC, never as server-local time.
  * Provenance: each point carries collection_method LEGACY_SIMS_MIGRATION, tags {migrated_from, legacy_sample_id,
    legacy_ap_id}, and the device's source_type. A SIMULATED legacy sample is only ever migrated as DEMO; it is never
    attached to a REAL device (refused and counted), so demo history cannot leak into production telemetry.
  * Values: None stays missing (no point written), never zero. exp_score is NOT migrated as a measurement: it was a
    derived score, recomputed by the current model from measured inputs.
  * Idempotent: the source_event_id is derived from the legacy sample id, and the telemetry store's identity key
    (event, device, entity, metric, time) makes a re-run write nothing new.
  * Unmapped APs (no INM device with that legacy_ap_id) are skipped and reported, not guessed.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import ApSample
from ..database.models_inm import InmDevice
from ..domain.model import DataQuality, SourceType, TelemetryPoint

METRICS = (("client_count", "clients", "count"), ("cpu_percent", "cpu_percent", "percent"),
           ("memory_percent", "memory_percent", "percent"), ("util_2g_percent", "util_2g_percent", "percent"),
           ("util_5g_percent", "util_5g_percent", "percent"))
METHOD = "LEGACY_SIMS_MIGRATION"
NAMESPACE = uuid.UUID("6f6b4b3e-1c1e-4c6a-9f0a-5a1e2b7c9d00")


def utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def migrate_ap_samples(db: Session, store, *, dry_run: bool = False, batch: int = 2000,
                       after_id: int = 0) -> dict:
    devices = {d.legacy_ap_id: d for d in db.scalars(select(InmDevice).where(InmDevice.legacy_ap_id.is_not(None)))}
    out = {"samples": 0, "points_planned": 0, "points_written": 0, "unmapped_samples": 0,
           "refused_simulated_on_real": 0, "first_utc": None, "last_utc": None, "dry_run": dry_run}
    unmapped: set[int] = set()
    last = after_id
    while True:
        rows = list(db.scalars(select(ApSample).where(ApSample.id > last).order_by(ApSample.id).limit(batch)))
        if not rows:
            break
        points: list[TelemetryPoint] = []
        for s in rows:
            last = s.id
            out["samples"] += 1
            d = devices.get(s.ap_id)
            if d is None:
                out["unmapped_samples"] += 1
                unmapped.add(s.ap_id)
                continue
            if s.simulated and d.source_type == "REAL":
                out["refused_simulated_on_real"] += 1
                continue
            at = utc(s.ts)
            out["first_utc"] = min(out["first_utc"] or at.isoformat(), at.isoformat())
            out["last_utc"] = max(out["last_utc"] or at.isoformat(), at.isoformat())
            event_id = str(uuid.uuid5(NAMESPACE, f"ap_samples:{s.id}"))
            for col, metric, unit in METRICS:
                v = getattr(s, col)
                if v is None:
                    continue
                points.append(TelemetryPoint(
                    device_id=d.id, metric_name=metric, observed_at=at, value=float(v), unit=unit,
                    collection_method=METHOD, quality=DataQuality.OBSERVED,
                    source_type=SourceType("DEMO" if s.simulated else d.source_type), source_event_id=event_id,
                    tags={"migrated_from": "sims.ap_samples", "legacy_sample_id": s.id, "legacy_ap_id": s.ap_id,
                          "legacy_status": s.status}))
        out["points_planned"] += len(points)
        if points and not dry_run:
            out["points_written"] += store.write_points(points)
    out["unmapped_ap_ids"] = sorted(unmapped)[:100]
    out["checksum"] = hashlib.sha256(f"{out['samples']}:{out['points_planned']}:{last}".encode()).hexdigest()[:16]
    return out
