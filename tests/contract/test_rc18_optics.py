"""rc18 optical monitoring: parsing (single + multi-lane), unit normalisation, manufacturer thresholds, missing /
unsupported never healthy, degradation trend, alert open/update/recovery, persistence, collection pipeline, API."""
from datetime import datetime, timedelta, timezone

from backend.infrastructure import optics as O
from backend.infrastructure.writers.emulator import CxEmulator


def test_parse_normalise_and_evaluate():
    e = CxEmulator(model="6200F 48G")
    parsed = O.parse_transceiver_detail(e.transceiver_text())
    assert parsed["state"] == "COLLECTED" and set(parsed["ports"]) == {"1/1/49", "1/1/50", "1/1/51", "1/1/52"}
    p49 = parsed["ports"]["1/1/49"]
    assert p49["vendor_name"].startswith("HPE") and p49["serial_number"] == "EMU-OPT-49" and p49["wavelength_nm"] == 850
    assert p49["thresholds"]["rx_power_dbm"] == {"high_alarm": 0.0, "high_warn": -1.0, "low_warn": -14.0, "low_alarm": -18.0}
    assert O.evaluate(p49)["state"] == "OK" and O.evaluate(p49)["threshold_source"] == "MODULE"
    assert O.evaluate(parsed["ports"]["1/1/50"])["state"] == "WARNING"            # -15.2 dBm < -14 low warning
    assert O.evaluate(parsed["ports"]["1/1/51"])["state"] == "UNSUPPORTED"        # no DOM: never OK
    q = parsed["ports"]["1/1/52"]
    assert q["lane_count"] == 4 and q["lanes"][0]["temperature_c"] == 41.0         # module values on every lane
    assert q["lanes"][0]["rx_power_dbm"] == O.mw_to_dbm(0.51)                      # mW -> dBm normalised
    ev = O.evaluate(q)
    lane3 = ev["lanes"][2]["metrics"]["rx_power_dbm"]
    assert ev["state"] == "CRITICAL" and lane3["state"] == "CRITICAL" and "loss of signal" in lane3["reason"]
    assert O.evaluate(q)["threshold_source"] == "PLATFORM_DEFAULT"                 # QSFP section had no table
    assert O.mw_to_dbm(0) is None and O.mw_to_dbm(1.0) == 0.0
    missing = {"dom_supported": True, "lanes": [{"lane": 1, "rx_power_dbm": None}], "thresholds": {}}
    assert O.evaluate(missing)["lanes"][0]["metrics"]["rx_power_dbm"]["state"] == "NOT_MEASURED"
    assert O.evaluate(missing)["state"] == "NOT_MEASURED"                          # missing is never OK
    assert O.parse_transceiver_detail("garbage")["state"] == "UNRECOGNIZED"


def test_degradation_trend():
    t0 = datetime(2026, 11, 1, tzinfo=timezone.utc)
    falling = [(t0 + timedelta(days=d), -8.0 - 0.2 * d) for d in range(10)]
    tr = O.trend(falling, -14.0)
    assert tr["state"] == "DEGRADING" and tr["slope_db_per_day"] == -0.2 and 0 < tr["days_to_low_warning"] <= 30
    flat = [(t0 + timedelta(days=d), -5.0 + (0.01 if d % 2 else 0)) for d in range(10)]
    assert O.trend(flat, -14.0)["state"] == "STABLE"
    assert O.trend(falling[:3], -14.0)["state"] == "INSUFFICIENT_HISTORY"


def test_collection_pipeline_persists_alerts_and_recovers(client, admin, monkeypatch):
    from backend.database.models_phases import InmAlert
    from backend.database.session import session_scope
    from backend.infrastructure.runtime import telemetry_store
    telemetry_store().migrate()
    assert client.post("/api/v1/demo-data/load", headers=admin).status_code in (200, 201)
    from backend.database.models_inm import InmDevice
    with session_scope() as db:
        sw = db.query(InmDevice).filter(InmDevice.family == "switch", InmDevice.source_type == "DEMO").first()
        did = sw.id
    from backend.infrastructure.optics import ingest
    e = CxEmulator(model="6200F 48G")
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        out = ingest(db, db.get(InmDevice, did), O.parse_transceiver_detail(e.transceiver_text()), now=now,
                     store=telemetry_store())
    assert out["ports"] == 4 and out["warning"] == 1 and out["critical"] == 1 and out["unsupported"] == 1
    assert out["points"] > 10
    view = client.get(f"/api/v1/devices/{did}/optics", headers=admin).json()
    assert view["ports"]["1/1/50"]["evaluation"]["state"] == "WARNING"
    fleet = client.get("/api/v1/optics", headers=admin).json()
    assert [r["state"] for r in fleet if r["device_id"] == did][:2] == ["CRITICAL", "WARNING"]
    with session_scope() as db:
        alerts = {a.entity: a for a in db.query(InmAlert).filter(InmAlert.device_id == did, InmAlert.entity.like("optic:%"))}
        assert alerts["optic:1/1/50"].state == "OPEN" and alerts["optic:1/1/52"].severity == "critical"
        assert "optic:1/1/51" not in alerts                                        # unsupported is not an alert
    e.optics_rx_dbm = -4.0                                                         # fibre cleaned: recovery
    with session_scope() as db:
        ingest(db, db.get(InmDevice, did), O.parse_transceiver_detail(e.transceiver_text()),
               now=now + timedelta(minutes=5), store=telemetry_store())
        a = db.query(InmAlert).filter(InmAlert.device_id == did, InmAlert.entity == "optic:1/1/50").one()
        assert a.state == "RESOLVED"
    pts = telemetry_store().query(device_id=did, metric_name="optic_rx_power_dbm", since=now - timedelta(minutes=1),
                                  entity_type="transceiver", entity_id="1/1/50")
    assert sorted(p["value"] for p in pts) == [-15.2, -4.0]


def test_writer_reads_optics_through_advertised_show_command():
    e = CxEmulator(model="6200F 48G")
    w = e.writer()
    w.open()
    assert w.optics()["ports"]["1/1/49"]["lanes"][0]["rx_power_dbm"] == -3.1
    e.cli_commands.remove("show interface transceiver detail")
    assert w.optics()["state"] == "UNSUPPORTED"                                    # not advertised -> not invented


def test_optics_family_through_the_collection_runner(client, admin, monkeypatch):
    """A REAL switch plan with the 'optics' family: the runner reads optics via the writer (emulator transport
    injected) and persists state + alerts; the rest of the poll is unaffected."""
    from backend.database.session import session_scope
    from backend.infrastructure import change_executor as X
    from backend.infrastructure import collection_runner as CR
    from backend.infrastructure.runtime import telemetry_store
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.66.0.0/24")
    telemetry_store().migrate()
    did = client.post("/api/v1/quick-devices", headers=admin, json={
        "site_name": "HQ", "family": "switch", "hostname": "core-optics", "serial_number": "OPT-SW-1",
        "model": "6200F 48G", "management_ip": "10.66.0.2", "status": "unknown", "metrics": {}}).json()["id"]
    emu = CxEmulator(model="6200F 48G")
    monkeypatch.setattr(X, "writer_for", lambda db, d: (emu.writer(), "EMULATED"))
    with session_scope() as db:
        from backend.database.models_phases import CollectionPlan
        db.add(CollectionPlan(device_id=did, site_id=None, adapter_key="aruba_cx_10_14", credential_profile_id=None,
                              metric_families=["system", "optics"], interval_seconds=300, state="ACTIVE",
                              next_due_at=datetime.now(timezone.utc) - timedelta(seconds=1), created_by="t"))
    collector = lambda device, plan, db: {"metrics": {"cpu_percent": 11.0}, "firmware": None}   # noqa: E731
    with session_scope() as db:
        CR.run_due(db, collector=collector, store=telemetry_store(), worker="t")
    view = client.get(f"/api/v1/devices/{did}/optics", headers=admin).json()
    assert view["ports"]["1/1/49"]["evaluation"]["state"] == "OK" and view["ports"]["1/1/52"]["lane_count"] == 4
