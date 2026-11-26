"""rc17: independent active probe, device clock drift (P3-05 / INTEG-P3-05), roaming + interference analytics."""
from datetime import datetime, timedelta, timezone


def _device(client, admin, ip, serial, family="switch"):
    r = client.post("/api/v1/quick-devices", headers=admin, json={
        "site_name": "HQ", "family": family, "hostname": serial.lower(), "serial_number": serial,
        "model": "6300M" if family == "switch" else "AP-535", "management_ip": ip, "status": "unknown", "metrics": {}})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def test_probe_reachability_and_clock_drift_are_independent_measurements(client, admin, monkeypatch):
    from backend.infrastructure import device_probes as P
    from backend.infrastructure.runtime import telemetry_store
    monkeypatch.setenv("INM_PROBE_EMULATION", "true")
    monkeypatch.setenv("INM_READONLY_DEVICE_CIDRS", "10.88.0.0/24")
    telemetry_store().migrate()
    ok = _device(client, admin, "10.88.0.2", "SW-OK")
    drift = _device(client, admin, "10.88.0.3", "SW-DRIFT")
    down = _device(client, admin, "10.88.0.4", "SW-DOWN")
    outside = _device(client, admin, "10.99.0.5", "SW-OUTSIDE")      # not in the approved CIDRs: never contacted
    now = datetime.now(timezone.utc)
    P.EMULATED.clear()
    P.EMULATED.update({"10.88.0.2": {"reachable": True, "rtt_ms": 1.8, "device_time": now + timedelta(seconds=3)},
                       "10.88.0.3": {"reachable": True, "rtt_ms": 2.1, "device_time": now + timedelta(seconds=600)},
                       "10.88.0.4": {"reachable": False}})
    for _ in range(3):
        out = client.post("/api/v1/devices/probes/run", headers=admin).json()
    assert out["probed"] == 3 and out["reachable"] == 2 and out["unreachable"] == 1 and out["skipped_not_allowed"] == 1
    s_ok = client.get(f"/api/v1/devices/{ok}/probe", headers=admin).json()
    assert s_ok["probe"]["reachable"] is True and s_ok["clock"]["state"] == "OK" and s_ok["probe"]["method"] == "EMULATED"
    s_drift = client.get(f"/api/v1/devices/{drift}/probe", headers=admin).json()
    assert s_drift["clock"]["state"] == "DRIFT" and s_drift["clock"]["offset_s"] >= 590
    s_down = client.get(f"/api/v1/devices/{down}/probe", headers=admin).json()
    assert s_down["probe"]["reachable"] is False and s_down["clock"]["state"] == "UNKNOWN"   # not invented
    assert client.get(f"/api/v1/devices/{outside}/probe", headers=admin).json()["probe"]["measured_at"] is None
    alerts = client.get("/api/v1/alerts?state=OPEN", headers=admin).json()
    rows = alerts if isinstance(alerts, list) else alerts.get("alerts", alerts.get("items", []))
    rules = {(a["device_id"], a["rule_id"]) for a in rows}
    assert (down, "PROBE_UNREACHABLE") in rules and (drift, "CLOCK_DRIFT") in rules
    assert (ok, "CLOCK_DRIFT") not in rules
    pts = telemetry_store().query(device_id=down, metric_name="probe_reachable", since=now - timedelta(minutes=5))
    assert pts and all(p["value"] == 0.0 and p["collection_method"] == "ACTIVE_PROBE" for p in pts)
    # an unreachable probe never writes RF / interface counters or an RTT
    assert telemetry_store().query(device_id=down, metric_name="probe_rtt_ms", since=now - timedelta(minutes=5)) == []


def test_clock_drift_corrects_and_downgrades_attribution():
    from backend.domain.attribution import AuthSession, attribute
    t = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    s = AuthSession(id="s1", username="jbrown", device_serial="SN1", start=t - timedelta(minutes=5), end=t + timedelta(minutes=5), source="RADIUS")
    ok = attribute(device_serial="SN1", change_at=t, device_event_user="jbrown", sessions=[s])
    assert ok["level"] == "VERIFIED"
    # device clock 1 hour fast: the raw log time falls outside the session; corrected time falls inside it
    drift = attribute(device_serial="SN1", change_at=t + timedelta(hours=1), device_event_user="jbrown", sessions=[s],
                      clock_offset_s=3600.0)
    assert drift["level"] == "CORRELATED" and drift["candidates"] == ["jbrown"]
    assert drift["evidence"]["device_clock_offset_s"] == 3600.0 and "tolerance" in drift["uncertainty"]
    small = attribute(device_serial="SN1", change_at=t + timedelta(seconds=30), device_event_user="jbrown",
                      sessions=[s], clock_offset_s=30.0)
    assert small["level"] == "VERIFIED"                                   # within tolerance: still verifiable


def test_roaming_and_interference_analytics_units():
    from backend.domain import roaming_analytics as RA
    t = datetime(2026, 11, 25, 9, 0, tzinfo=timezone.utc)
    ev = [{"mac": "aa", "ts": t, "kind": "roam", "ap_from": "ap1", "ap_to": "ap2"},
          {"mac": "aa", "ts": t + timedelta(minutes=4), "kind": "roam", "ap_from": "ap2", "ap_to": "ap1"},
          {"mac": "bb", "ts": t, "kind": "roam", "ap_from": "ap1", "ap_to": "ap3"},
          {"mac": "bb", "ts": t + timedelta(minutes=30), "kind": "roam", "ap_from": "ap3", "ap_to": "ap1"},
          {"mac": "cc", "ts": t, "kind": "connect", "ap_from": None, "ap_to": "ap1"}]
    r = RA.roaming(ev, hours=1)
    assert r["roams"] == 4 and r["clients_roaming"] == 2 and r["ping_pong_clients"] == 1
    assert r["ping_pong_pairs"] == [{"aps": ["ap1", "ap2"], "count": 1}]
    sticky = RA.sticky_candidates([{"mac": "x", "is_connected": True, "rssi_dbm": -80, "connected_seconds": 900},
                                   {"mac": "y", "is_connected": True, "rssi_dbm": None, "connected_seconds": 900},
                                   {"mac": "z", "is_connected": True, "rssi_dbm": -60, "connected_seconds": 900}])
    assert [s["mac"] for s in sticky] == ["x"]
    pts = lambda vals: [(t + timedelta(minutes=5 * i), v) for i, v in enumerate(vals)]   # noqa: E731
    noisy = RA.interference({"noise_floor_dbm": pts([-90, -90, -78, -77, -76, -91, -75, -90]),
                             "retry_percent": pts([4, 5, 22, 25, 28, 4, 30, 5])})
    assert noisy["state"] == "INTERFERENCE_SUSPECTED" and noisy["noise_retry_correlation"] > 0.9
    sparse = RA.interference({"noise_floor_dbm": pts([-78, -77]), "retry_percent": pts([30, None])})
    assert sparse["state"] == "INSUFFICIENT_DATA" and sparse["paired_samples"] == 1
    assert RA.interference({})["state"] == "INSUFFICIENT_DATA"


def test_roaming_interference_endpoint_shape(client, admin):
    r = client.get("/api/v1/analytics/roaming-interference?hours=24", headers=admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"roaming", "sticky_candidates", "interference", "caveat"} <= set(body)
