"""Transactional outbox for the scheduled collector and change engine (E2E-03/15).

An EventEnvelope is written to `event_outbox` in the SAME management-DB transaction as the state it describes
(collection run, plan, execution). Delivery is then at-least-once and idempotent end to end:

* JetStream mode (INM_NATS_URL set): the worker's relay publishes PENDING rows with Nats-Msg-Id = event_id
  (broker de-duplication) and marks them DELIVERED; the worker's ingest consumer applies them through
  IngestService exactly like every other event.
* In-process mode (no NATS: standalone install, tests): the relay hands the event to the same IngestService
  directly. Same contract, same idempotency ledger, same effects.

A crash between commit and delivery leaves the row PENDING; the next relay pass delivers it. Failed deliveries
back off and after MAX_ATTEMPTS go to DLQ (state DLQ, visible in System health) — never silently dropped.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from ..database.models_remediation import EventOutbox
from ..domain.events import EventEnvelope, parse_event

log = logging.getLogger("inm.outbox")
MAX_ATTEMPTS = 8
CONSUMER = "ingest"

_ui_notifier: Callable[[list[str], str | None, str | None], None] | None = None


def set_ui_notifier(fn: Callable[[list[str], str | None, str | None], None] | None) -> None:
    """The API process registers the WebSocket hub here so in-process deliveries invalidate browsers too."""
    global _ui_notifier
    _ui_notifier = fn


def notify_ui(topics: list[str], correlation_id: str | None, event_id: str | None) -> None:
    if topics and _ui_notifier is not None:
        try:
            _ui_notifier(topics, correlation_id, event_id)
        except Exception as exc:          # UI notice is best-effort; browsers also poll
            log.warning("UI invalidation failed: %s", exc)



@event.listens_for(Session, "after_commit")
def _notify_after_root_commit(db: Session) -> None:
    """Never notify a browser while the state it must requery is uncommitted.

    SQLAlchemy also emits after_commit for SAVEPOINTs; nested commits must not flush the UI queue.
    """
    if db.in_nested_transaction():
        return
    for topics, correlation_id, event_id in db.info.pop("_inm_ui_after_commit", []):
        notify_ui(topics, correlation_id, event_id)


@event.listens_for(Session, "after_rollback")
def _clear_aborted_ui_notices(db: Session) -> None:
    if not db.in_nested_transaction():
        db.info.pop("_inm_ui_after_commit", None)


def _queue_postcommit_notice(db: Session, topics: list[str], correlation_id: str, event_id: str) -> None:
    if topics:
        db.info.setdefault("_inm_ui_after_commit", []).append((topics, correlation_id, event_id))


def mode() -> str:
    from .runtime import inm_settings
    return "jetstream" if inm_settings().nats_url else "inprocess"


def enqueue(db: Session, e: EventEnvelope) -> EventOutbox:
    row = db.get(EventOutbox, e.event_id)
    if row is None:
        row = EventOutbox(event_id=e.event_id, event_type=e.event_type, correlation_id=e.correlation_id,
                          device_id=e.device_id, envelope=e.to_json().decode(), state="PENDING", attempts=0)
        db.add(row)
        db.flush()
    return row


def _backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2 ** attempts, 300))


def _ingest_inprocess(db: Session, e: EventEnvelope):
    """Apply one event with the shared IngestService inside a SAVEPOINT of the caller's transaction."""
    from ..application.ingest import IngestService
    from .event_effects import apply_effects
    from .persistence.repositories import SqlEventLedger
    from .runtime import log_store, telemetry_store
    sp = db.begin_nested()
    svc = IngestService(ledger=SqlEventLedger(db, CONSUMER), telemetry=telemetry_store(), logs=log_store(),
                        commit=sp.commit, rollback=sp.rollback, effects=lambda ev: apply_effects(db, ev))
    return svc.handle(e)


def deliver_row(db: Session, row: EventOutbox, *, now: datetime | None = None,
                publish: Callable[[EventEnvelope], None] | None = None) -> bool:
    """Deliver one outbox row. publish=None -> in-process ingest. Returns True when delivered."""
    now = now or datetime.now(timezone.utc)
    e = parse_event(row.envelope.encode())
    row.attempts += 1
    try:
        if publish is None:
            result = _ingest_inprocess(db, e)
            _queue_postcommit_notice(db, result.topics, e.correlation_id, e.event_id)
        else:
            publish(e)
    except Exception as exc:
        row.last_error = f"{type(exc).__name__}: {exc}"[:300]
        if row.attempts >= MAX_ATTEMPTS:
            row.state = "DLQ"
            log.error("outbox event %s dead-lettered after %s attempts: %s", row.event_id, row.attempts, exc)
        else:
            row.next_attempt_at = now + _backoff(row.attempts)
        db.flush()
        return False
    row.state, row.delivered_at, row.last_error, row.next_attempt_at = "DELIVERED", now, None, None
    db.flush()
    return True


def relay(db: Session, *, now: datetime | None = None, limit: int = 200,
          publish: Callable[[EventEnvelope], None] | None = None, event_ids: list[str] | None = None) -> dict:
    """Deliver due PENDING rows oldest-first (per-device ordering preserved by created_at)."""
    now = now or datetime.now(timezone.utc)
    q = select(EventOutbox).where(EventOutbox.state == "PENDING")
    if event_ids is not None:
        q = q.where(EventOutbox.event_id.in_(event_ids))
    out = {"delivered": 0, "failed": 0, "dead_lettered": 0}
    for row in db.scalars(q.order_by(EventOutbox.created_at).limit(limit)).all():
        due = row.next_attempt_at
        if due is not None and (due if due.tzinfo else due.replace(tzinfo=timezone.utc)) > now and event_ids is None:
            continue
        if deliver_row(db, row, now=now, publish=publish):
            out["delivered"] += 1
        else:
            out["dead_lettered" if row.state == "DLQ" else "failed"] += 1
    return out


def stats(db: Session) -> dict:
    from sqlalchemy import func
    rows = dict(db.execute(select(EventOutbox.state, func.count()).group_by(EventOutbox.state)).all())
    oldest = db.scalar(select(func.min(EventOutbox.created_at)).where(EventOutbox.state == "PENDING"))
    return {"pending": rows.get("PENDING", 0), "delivered": rows.get("DELIVERED", 0), "dlq": rows.get("DLQ", 0),
            "oldest_pending_at": oldest.isoformat() if oldest else None, "mode": mode()}


def envelope_json(e: EventEnvelope) -> dict:
    return json.loads(e.to_json())
