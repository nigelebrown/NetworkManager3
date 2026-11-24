"""Scheduled device logs: simulated transport, real persisted history; no hardware claims."""
from datetime import datetime, timezone

import pytest

from backend.database.models_inm import InmDevice
from backend.database.session import session_scope
from backend.infrastructure import device_log_collection as DLC
from backend.infrastructure.writers import WriterError
from tests.conftest import make_user


@pytest.fixture
def switch(client):
    with session_scope() as db:
        d = InmDevice(source_type="REAL", environment="lab", vendor="Aruba",
                      family="switch", model="CX 6200F", serial_number="TEST-LOG-01",
                      hostname="log-lab", adapter="manual_snapshot",
                      mgmt_addresses=["192.0.2.15"])
        db.add(d)
        db.flush()
        return d.id


class Reader:
    def __init__(self, serial="TEST-LOG-01"):
        self.serial = serial
        self.closed = False
        self.calls = []

    def open(self):
        pass

    def close(self):
        self.closed = True

    def identity(self):
        return {"serial_number": self.serial}

    def read_audit_logs(self, *, kind, limit):
        self.calls.append((kind, limit))
        if kind == "event":
            raise WriterError("UNSUPPORTED", "test only")
        return [{"timestamp": "device time", "message": "password=test-secret",
                 "actor": "operator", "level": "warning", "action": "login"}]


def test_partial_poll_deduplication_and_identity(switch):
    now = datetime(2026, 11, 24, tzinfo=timezone.utc)
    with session_scope() as db:
        d = db.get(InmDevice, switch)
        reader = Reader()
        result = DLC.collect(db, d, reader_factory=lambda *_: (reader, "HARDWARE"),
                             clock=lambda: now)
        assert result["state"] == "PARTIAL"
        assert result["records_added"] == 1
        assert reader.closed and reader.calls == [("audit", 100), ("event", 100)]
        repeated = DLC.collect(db, d, reader_factory=lambda *_: (Reader(), "HARDWARE"),
                               clock=lambda: now)
        assert repeated["records_added"] == 0
        records = DLC.history(db, switch)
        assert len(records) == 1
        assert "test-secret" not in records[0]["message"]
        assert "[REDACTED]" in records[0]["message"]
        assert records[0]["scope"] == "device"
        wrong = Reader("WRONG-SERIAL")
        with pytest.raises(WriterError) as exc:
            DLC.collect(db, d, reader_factory=lambda *_: (wrong, "HARDWARE"))
        assert exc.value.category == "IDENTITY_UNVERIFIED"
        assert wrong.closed and not wrong.calls


def test_schedule_history_access_and_no_fake_attribution(client, admin, switch):
    schedule = f"/api/v1/devices/{switch}/device-log-collection"
    history = f"/api/v1/devices/{switch}/device-log-history"
    assert client.get(schedule, headers=admin).json()["eligible"]
    assert client.put(schedule, headers=admin, json={"enabled": True, "interval_minutes": 4}).status_code == 422
    assert client.put(schedule, headers=admin, json={"enabled": True, "interval_minutes": 15}).status_code == 200
    assert client.get(schedule, headers=admin).json()["enabled"]
    with session_scope() as db:
        DLC.collect(db, db.get(InmDevice, switch), reader_factory=lambda *_: (Reader(), "HARDWARE"))
    records = client.get(history, headers=admin).json()
    assert len(records["records"]) == 1 and records["attribution_verified"] is False
    timeline = client.get(f"/api/v1/devices/{switch}/change-timeline", headers=admin).json()
    assert not timeline["log_events"] and not timeline["attributions"]
    operator = make_user(client, admin, "log-scope-test", "operator", scope="granted", grants=[])
    assert client.get(schedule, headers=operator).status_code == 404
    assert client.get(history, headers=operator).status_code == 404
    assert client.put(schedule, headers=operator, json={"enabled": False}).status_code == 403
    assert client.put(schedule, headers=admin, json={"enabled": False}).status_code == 200
    assert len(client.get(history, headers=admin).json()["records"]) == 1
