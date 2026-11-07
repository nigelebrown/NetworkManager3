"""SQLAlchemy repositories for INM management data (role 03: PostgreSQL)."""
from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...database.models_inm import EventRecord, JobLease, ProcessedEvent, ServiceHeartbeat
from ...domain.events import EventEnvelope


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class SqlEventLedger:
    """Consumer-side idempotency + management record of each logical event."""

    def __init__(self, db: Session, consumer: str):
        self.db, self.consumer = db, consumer

    def is_processed(self, event_id: str) -> bool:
        return self.db.get(ProcessedEvent, (self.consumer, event_id)) is not None

    def note_redelivery(self, event_id: str) -> None:
        rec = self.db.get(EventRecord, event_id)
        if rec:
            rec.delivery_count = (rec.delivery_count or 1) + 1

    def record(self, e: EventEnvelope, summary: str | None) -> bool:
        """Insert the event record and the processed marker in the caller's transaction. False if already there."""
        if self.is_processed(e.event_id):
            return False
        self.db.add(ProcessedEvent(consumer=self.consumer, event_id=e.event_id))
        if not self.db.get(EventRecord, e.event_id):
            self.db.add(EventRecord(
                event_id=e.event_id, event_type=e.event_type, schema_version=e.schema_version,
                occurred_at=_dt(e.occurred_at_utc), emitted_at=_dt(e.emitted_at_utc), correlation_id=e.correlation_id,
                causation_id=e.causation_id, source_service=e.source_service, source_type=e.source_type,
                actor_type=e.actor_type, actor_id=e.actor_id, site_id=e.site_id, legacy_site_id=e.legacy_site_id,
                device_id=e.device_id, data_quality=e.data_quality, integrity_hash=e.integrity_hash,
                summary=(summary or "")[:300] or None, delivery_count=1))
        try:
            self.db.flush()
        except IntegrityError:  # a concurrent consumer won the race: treat as duplicate
            self.db.rollback()
            return False
        return True


class LeaseRepo:
    """Time-slot leases. A slot (e.g. poll interval number) runs at most once across all workers."""

    def __init__(self, db: Session, holder: str | None = None):
        self.db = db
        self.holder = holder or socket.gethostname()

    def try_acquire_slot(self, job: str, slot: int, ttl_s: int) -> bool:
        now = datetime.now(timezone.utc)
        lease = self.db.get(JobLease, job, with_for_update=True)
        if lease is None:
            try:
                self.db.add(JobLease(job=job, holder=self.holder, expires_at=now + timedelta(seconds=ttl_s), last_run_slot=slot))
                self.db.flush()
                return True
            except IntegrityError:
                self.db.rollback()
                return False
        # Conditional update: only one writer can move last_run_slot forward to this slot.
        res = self.db.execute(
            update(JobLease)
            .where(JobLease.job == job)
            .where((JobLease.last_run_slot.is_(None)) | (JobLease.last_run_slot < slot))
            .where((JobLease.expires_at < now) | (JobLease.holder == self.holder) | (JobLease.last_run_slot < slot))
            .values(holder=self.holder, expires_at=now + timedelta(seconds=ttl_s), last_run_slot=slot)
            .execution_options(synchronize_session=False))
        return res.rowcount == 1


class HeartbeatRepo:
    def __init__(self, db: Session):
        self.db = db

    def beat(self, service: str, instance: str, version: str, detail: dict | None = None) -> None:
        hb = self.db.get(ServiceHeartbeat, service)
        now = datetime.now(timezone.utc)
        if hb is None:
            self.db.add(ServiceHeartbeat(service=service, instance=instance, version=version, beat_at=now, detail=detail))
        else:
            hb.instance, hb.version, hb.beat_at, hb.detail = instance, version, now, detail

    def get(self, service: str) -> ServiceHeartbeat | None:
        hb = self.db.get(ServiceHeartbeat, service)
        if hb:
            hb.beat_at = _aware(hb.beat_at)
        return hb

    def all(self) -> list[ServiceHeartbeat]:
        return list(self.db.scalars(select(ServiceHeartbeat)))
