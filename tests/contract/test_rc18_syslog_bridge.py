"""rc18 (P3-01, INTEG-P3-03, GAP-T03, P4-08): device syslog in Loki reaches the device timeline and the change
attribution engine; logging-health alerts make missing syslog / AAA evidence visible.

The Loki store is a faithful in-memory fake of LokiLogStore.query (time-bounded, forward/backward, limit). The real
Alloy -> Loki path (including the sender `source_ip` label) is exercised by tests/fullstack/test_fullstack.py.
"""
from datetime import datetime, timedelta, timezone

import pytest

from backend.database.models_inm import InmDevice
from backend.database.models_phases import AuthSessionEvidence, ChangeAttribution, DeviceLogEvent, InmAlert
from backend.database.session import session_scope
from backend.infrastructure import syslog_bridge as SB
from tests.conftest import login

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


class FakeLoki:
    def __init__(self):
        self.entries = []

    def add(self, at, line, *, host, source_ip=None):
        labels = {"source": "syslog", "host": host}
        if source_ip:
            labels["source_ip"] = source_ip
        ns = int(at.timestamp() * 1e9)
        self.entries.append({"ts": at.isoformat(), "ns": str(ns), "labels": labels, "line": line})

    def query(self, logql, since, limit=200, until=None, direction="backward"):
        assert logql == '{source="syslog"}'
        rows = [e for e in self.entries if since <= datetime.fromisoformat(e["ts"]) <= (until or T0 + timedelta(days=9))]
        rows.sort(key=lambda e: int(e["ns"]), reverse=direction == "backward")
        return [dict(e) for e in rows[:limit]]


@pytest.fixture
def env(app_env):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("SIMS_INM_PROFILE", "lab-readonly")
    from backend.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        h = login(c)
        with session_scope() as db:
            d = InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family="switch", model="CX 6200F 24G",
                          serial_number="SN-BRIDGE-1", hostname="cx-core", adapter="aruba_cx_10_14",
                          mgmt_addresses=["10.20.0.5"])
            db.add(d)
            db.flush()
            did = d.id
        yield c, h, did


CX_CHANGE = "<190>Event|6801|LOG_INFO|AMM|-|User netops configuration change committed"


def test_verified_sender_reaches_timeline_and_attribution(env):
    c, h, did = env
    loki = FakeLoki()
    with session_scope() as db:
        db.add(AuthSessionEvidence(device_serial="SN-BRIDGE-1", username="netops", start_at=T0 - timedelta(minutes=5),
                                   end_at=T0 + timedelta(minutes=30), source="TACACS+", recorded_by="aaa-feed"))
    loki.add(T0 + timedelta(minutes=1), CX_CHANGE, host="cx-core", source_ip="10.20.0.5")
    loki.add(T0 + timedelta(minutes=2), "<190>Event|1001|LOG_INFO|LLDP|-|Neighbor added on 1/1/3",
             host="cx-core", source_ip="10.20.0.5")
    loki.add(T0 + timedelta(minutes=3), "<190>link down", host="someone-else", source_ip="10.99.0.1")
    with session_scope() as db:
        out = SB.bridge(db, loki, now=T0 + timedelta(minutes=10), lookback_minutes=60)
    assert out | {"pages": 0} == {"read": 3, "stored": 2, "duplicates": 0, "attributed": 1, "unmapped": 1,
                                  "host_only": 0, "pages": 0}
    tl = c.get(f"/api/v1/devices/{did}/change-timeline", headers=h).json()
    assert len(tl["log_events"]) == 2 and "syslog" in tl["coverage"]["present"]
    a = tl["attributions"][0]
    assert a["level"] == "VERIFIED" and a["actor"] == "netops" and a["change_type"] == "DIRECT_DEVICE"
    # idempotent: the cursor moved past the page; a forced re-read stores nothing twice
    with session_scope() as db:
        assert SB.bridge(db, loki, now=T0 + timedelta(minutes=10))["read"] == 0
        from backend.database.models import AppSetting
        db.delete(db.get(AppSetting, SB.CURSOR))
    with session_scope() as db:
        again = SB.bridge(db, loki, now=T0 + timedelta(minutes=10), lookback_minutes=60)
        assert again["stored"] == 0 and again["duplicates"] == 2
        assert db.query(DeviceLogEvent).count() == 2 and db.query(ChangeAttribution).count() == 1


def test_hostname_only_events_are_kept_but_never_attributed(env):
    c, h, did = env
    loki = FakeLoki()
    loki.add(T0, CX_CHANGE, host="cx-core")                              # no sender label: older Alloy config
    loki.add(T0 + timedelta(seconds=1), CX_CHANGE + " ", host="cx-core", source_ip="10.66.0.9")  # spoofed sender
    with session_scope() as db:
        out = SB.bridge(db, loki, now=T0 + timedelta(minutes=1), lookback_minutes=5)
        assert out["stored"] == 2 and out["host_only"] == 2 and out["attributed"] == 0
        assert db.query(ChangeAttribution).count() == 0


def test_pages_are_read_in_order_without_loss(env, monkeypatch):
    c, h, did = env
    monkeypatch.setattr(SB, "PAGE", 3)
    loki = FakeLoki()
    for i in range(8):
        loki.add(T0 + timedelta(seconds=i), f"<190>event {i}", host="cx-core", source_ip="10.20.0.5")
    with session_scope() as db:
        out = SB.bridge(db, loki, now=T0 + timedelta(minutes=1), lookback_minutes=5)
        assert out["read"] == 8 and out["stored"] == 8 and out["pages"] == 3
        msgs = [e.message for e in db.query(DeviceLogEvent).order_by(DeviceLogEvent.observed_at)]
        assert msgs == [f"event {i}" for i in range(8)]


def test_logging_health_raises_and_resolves_syslog_silence_and_aaa_gap(env):
    c, h, did = env
    loki = FakeLoki()
    loki.add(T0, CX_CHANGE, host="cx-core", source_ip="10.20.0.5")       # change with NO AAA session
    with session_scope() as db:
        db.add(AuthSessionEvidence(device_serial="SN-BRIDGE-1", username="netops", start_at=T0 - timedelta(days=3),
                                   end_at=T0 - timedelta(days=3, minutes=-20), source="RADIUS", recorded_by="aaa-feed"))
    with session_scope() as db:
        SB.bridge(db, loki, now=T0 + timedelta(seconds=30), lookback_minutes=5)
        ev = db.query(DeviceLogEvent).one()
        ev.ingested_at = T0
        assert db.query(ChangeAttribution).one().level == "UNKNOWN"
    with session_scope() as db:                                         # within the silence threshold
        out = SB.logging_health(db, now=T0 + timedelta(minutes=30))
        assert out["syslog_silent"] == 0 and out["aaa_gap"] == 1
    with session_scope() as db:
        out = SB.logging_health(db, now=T0 + timedelta(minutes=90))
        assert out["syslog_silent"] == 1
        alerts = {a.rule_id: a for a in db.query(InmAlert).filter(InmAlert.device_id == did)}
        assert alerts["SYSLOG_SILENT"].state == "OPEN" and "No syslog" in alerts["SYSLOG_SILENT"].message
        assert alerts["AAA_EVIDENCE_GAP"].state == "OPEN" and "UNKNOWN" in alerts["AAA_EVIDENCE_GAP"].message
    loki.add(T0 + timedelta(minutes=95), "<190>back", host="cx-core", source_ip="10.20.0.5")
    with session_scope() as db:
        SB.bridge(db, loki, now=T0 + timedelta(minutes=96))
        for e in db.query(DeviceLogEvent).filter(DeviceLogEvent.message == "back"):
            e.ingested_at = T0 + timedelta(minutes=95)
    with session_scope() as db:                                         # syslog resumed; AAA gap still open
        out = SB.logging_health(db, now=T0 + timedelta(minutes=100))
        assert out["syslog_resolved"] == 1 and out["aaa_resolved"] == 0
        assert {a.rule_id: a.state for a in db.query(InmAlert).filter(InmAlert.device_id == did)} == {
            "SYSLOG_SILENT": "RESOLVED", "AAA_EVIDENCE_GAP": "OPEN"}
    with session_scope() as db:                                         # the unattributed change left the window
        out = SB.logging_health(db, now=T0 + timedelta(hours=26))
        assert out["aaa_resolved"] == 1
    shown = [a for a in c.get("/api/v1/alerts?state=ALL", headers=h).json() if a["device_id"] == did]
    assert {"SYSLOG_SILENT", "AAA_EVIDENCE_GAP"} <= {a["rule_id"] for a in shown}   # NOC / Device 360 alert list


def test_scheduled_job_runs_bridge_and_health(env, monkeypatch):
    from backend.infrastructure import runtime, scheduled_jobs as J
    loki = FakeLoki()
    loki.add(datetime.now(timezone.utc) - timedelta(minutes=1), "<190>hello", host="cx-core", source_ip="10.20.0.5")
    def fake_store():
        return loki
    fake_store.cache_clear = lambda: None          # conftest clears runtime caches at teardown
    monkeypatch.setattr(runtime, "log_store", fake_store)
    out = J.syslog_bridge()
    assert out["bridge"]["stored"] == 1 and "syslog_silent" in out["logging_health"]
    monkeypatch.setenv("INM_SYSLOG_BRIDGE", "false")
    assert J.syslog_bridge() is None


def test_cisco_ios_config_event_is_parsed_and_attributed(env):
    """P10-02 (rc18): Cisco IOS/IOS-XE %SYS-5-CONFIG_I through the same bridge; the technician is VERIFIED only with
    a matching named AAA session (no session -> UNKNOWN). The Cisco adapter itself is read-only SNMP."""
    from backend.domain import attribution as A
    ev = A.normalize("<189>123: *Nov 25 12:00:01: %SYS-5-CONFIG_I: Configured from console by jdoe on vty0 (10.1.1.9)",
                     source_ip="10.20.0.9", device_serial="FOC1", vendor="Cisco", received_at=T0)
    assert (ev.event_code, ev.severity, ev.user, ev.is_config_change) == ("SYS-5-CONFIG_I", "notice", "jdoe", True)
    up = A.normalize("<187>%LINEPROTO-5-UPDOWN: Line protocol on Interface Gi1/0/3, changed state to down",
                     source_ip="10.20.0.9", device_serial="FOC1", vendor="Cisco", received_at=T0)
    assert up.event_code == "LINEPROTO-5-UPDO" and not up.is_config_change and up.user is None
    c, h, _ = env
    with session_scope() as db:
        d = InmDevice(source_type="REAL", environment="lab", vendor="Cisco", family="switch", model="Catalyst 9200",
                      serial_number="FOC1", hostname="cat-1", adapter="cisco_ios_xe_snmpv3", mgmt_addresses=["10.20.0.9"])
        db.add(d)
        db.flush()
        db.add(AuthSessionEvidence(device_serial="FOC1", username="jdoe", start_at=T0 - timedelta(minutes=1),
                                   end_at=T0 + timedelta(minutes=10), source="TACACS+", recorded_by="aaa-feed"))
        cid = d.id
    loki = FakeLoki()
    loki.add(T0 + timedelta(seconds=5), "<189>%SYS-5-CONFIG_I: Configured from console by jdoe on vty0 (10.1.1.9)",
             host="cat-1", source_ip="10.20.0.9")
    with session_scope() as db:
        assert SB.bridge(db, loki, now=T0 + timedelta(minutes=1), lookback_minutes=5)["attributed"] == 1
        a = db.query(ChangeAttribution).filter(ChangeAttribution.device_id == cid).one()
        assert (a.level, a.actor) == ("VERIFIED", "jdoe")
