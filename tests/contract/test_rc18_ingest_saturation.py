"""rc18 (register P3-08): saturate the ingest path with bounded synthetic events.

3,000 labelled DEMO synthetic events (plus a burst of duplicates and one poison event) are queued at once. Each relay
cycle processes at most its bound (200), the backlog drains completely, every event is applied exactly once
(idempotent ledger), duplicates are not re-applied, the poison event is dead-lettered after MAX_ATTEMPTS without
blocking anything behind it, and the per-cycle memory peak does not grow with the backlog."""
import json
import tracemalloc
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from backend.database.models_remediation import EventOutbox
from backend.database.session import session_scope
from backend.domain.events import new_event
from backend.infrastructure import outbox

N, BOUND = 3000, 200


def _event(i):
    return new_event("synthetic.probe", source_service="saturation-test", source_type="DEMO", actor_id="test",
                     payload={"seq": i, "note": "bounded synthetic saturation"})


def test_bounded_saturation_drains_exactly_once_and_dead_letters_poison(client):
    assert outbox.mode() == "inprocess"
    events = [_event(i) for i in range(N)]
    with session_scope() as db:
        for e in events:
            outbox.enqueue(db, e)
        for e in events[:500]:                    # producer retry burst: same event ids again
            outbox.enqueue(db, e)
        poison = _event(-1)
        row = outbox.enqueue(db, poison)
        row.envelope = json.dumps({"event_id": poison.event_id, "broken": True})   # unparseable envelope
    with session_scope() as db:
        assert db.scalar(select(func.count()).select_from(EventOutbox)) == N + 1   # duplicates not queued twice
    now = datetime.now(timezone.utc)
    cycles, peaks = 0, []
    while True:
        tracemalloc.start()
        with session_scope() as db:
            out = outbox.relay(db, now=now, limit=BOUND)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
        cycles += 1
        handled = out["delivered"] + out["failed"] + out["dead_lettered"]
        assert handled <= BOUND                      # bounded work per cycle, whatever the backlog
        now += timedelta(minutes=10)                 # let the poison event's backoff elapse
        with session_scope() as db:
            pending = db.scalar(select(func.count()).select_from(EventOutbox).where(EventOutbox.state == "PENDING"))
        if pending == 0 or cycles > 60:
            break
    with session_scope() as db:
        st = outbox.stats(db)
        assert st["pending"] == 0 and st["delivered"] == N and st["dlq"] == 1
        dead = db.get(EventOutbox, poison.event_id)
        assert dead.state == "DLQ" and dead.attempts == outbox.MAX_ATTEMPTS and dead.last_error
    assert cycles >= N // BOUND
    # re-delivering already delivered events (e.g. a replayed relay) applies nothing twice
    from backend.application.ingest import IngestService  # noqa: F401  (ledger is the idempotency key)
    with session_scope() as db:
        again = outbox.relay(db, now=now, limit=BOUND, event_ids=[e.event_id for e in events[:50]])
        assert again == {"delivered": 0, "failed": 0, "dead_lettered": 0}
    # memory per cycle stays flat while the backlog shrinks from 3,000 to 0 (bounded batches, no backlog load)
    busy = [p for p in peaks[: N // BOUND]]
    assert max(busy) < 4 * min(busy) + 2_000_000, peaks
