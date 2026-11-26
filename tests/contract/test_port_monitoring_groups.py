"""Switch-port monitoring groups end-to-end over the CX protocol emulator (EMULATED evidence, not hardware).

Covers: selecting specific ports, only selected ports produce per-port telemetry, cross-site group dashboard,
location roll-up, fresh/stale/no-data distinction, link-down and threshold anomalies, PoE unsupported on a
non-PoE port, history without interpolation, viewer read-only access, site isolation, delete + audit.
"""
from datetime import datetime, timedelta, timezone

from backend.api.port_groups import evaluate
from tests.conftest import login, make_user
from tests.contract.test_monitoring_pipeline import live, poll, setup_switch  # noqa: F401  (fixture)

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def test_evaluate_never_reports_missing_or_stale_as_healthy():
    fresh = NOW - timedelta(seconds=60)
    assert evaluate({}, {}, None, 900, NOW)[0] == "No data"
    assert evaluate({}, {"port_link_up": 1}, NOW - timedelta(hours=2), 900, NOW)[0] == "Stale"
    assert evaluate({}, {"port_link_up": 1}, fresh, 900, NOW)[0] == "Healthy"
    assert evaluate({}, {"port_link_up": 0}, fresh, 900, NOW)[0] == "Critical"
    # administratively disabled port is not reported as a failure
    assert evaluate({}, {"port_link_up": 0}, fresh, 900, NOW, {"admin": "down"})[0] == "Warning"
    # threshold configured but its metric missing -> No data, not Healthy
    st, reasons, _ = evaluate({"error_threshold": 5}, {"port_link_up": 1}, fresh, 900, NOW)
    assert st == "No data" and "error counters" in reasons[0]
    assert evaluate({"error_threshold": 5}, {"port_link_up": 1, "port_errors_delta": 9}, fresh, 900, NOW)[0] == "Warning"
    assert evaluate({"utilization_threshold": 80}, {"port_link_up": 1, "port_util_percent": 91.0}, fresh, 900,
                    NOW)[0] == "Warning"
    st, reasons, unsupported = evaluate({"minimum_watts": 10}, {"port_link_up": 1}, fresh, 900, NOW,
                                        {"poe": "UNSUPPORTED"})
    assert st == "Unsupported" and unsupported == ["PoE power is not reported by this port"]


def test_group_lifecycle_dashboard_history_and_isolation(live):  # noqa: F811
    c, emu = live
    adm = login(c)
    did, pid = setup_switch(c, adm, emu)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=12)
    poll(pid, t0)                                    # faceplate snapshot needed before ports can be selected
    ports = [p["name"] for p in c.get(f"/api/v1/devices/{did}/ports", headers=adm).json()["ports"]]
    assert "1/1/1" in ports and "1/1/2" in ports

    # unknown port is rejected; duplicate port rejected
    bad = c.post("/api/v1/port-groups", headers=adm, json={"name": "AP Uplink",
                 "members": [{"device_id": did, "port": "9/9/99"}]})
    assert bad.status_code == 422
    dup = c.post("/api/v1/port-groups", headers=adm, json={"name": "AP Uplink", "members": [
        {"device_id": did, "port": "1/1/1"}, {"device_id": did, "port": "1/1/1"}]})
    assert dup.status_code == 422

    created = c.post("/api/v1/port-groups", headers=adm, json={"name": "AP Uplink", "members": [
        {"device_id": did, "port": "1/1/1", "label": "AP-1", "minimum_watts": 5, "error_threshold": 10},
        {"device_id": did, "port": "1/1/2", "label": "AP-2", "utilization_threshold": 80},
    ]})
    assert created.status_code == 201, created.text
    gid = created.json()["id"]
    same_name = c.post("/api/v1/port-groups", headers=adm, json={"name": "ap uplink", "members": []})
    assert same_name.status_code == 409

    # Two more polls: traffic saturates 1/1/2, errors on 1/1/1
    for it in emu.state["interfaces"].values():
        it["statistics"]["rx_bytes"] += 70_000_000_000
    emu.state["interfaces"]["1/1/1"]["statistics"]["rx_error"] = 50
    poll(pid, t0 + timedelta(minutes=10))

    dash = c.get(f"/api/v1/port-groups/{gid}/dashboard", headers=adm)
    assert dash.status_code == 200, dash.text
    body = dash.json()
    by_port = {p["port"]: p for p in body["ports"]}
    assert by_port["1/1/1"]["status"] == "Warning" and "Errors reached the per-poll threshold" in by_port["1/1/1"]["reasons"]
    assert by_port["1/1/2"]["status"] == "Warning" and by_port["1/1/2"]["metrics"]["port_util_percent"] > 80
    assert body["locations"][0]["site"] == "HQ" and body["locations"][0]["anomalies"] == 2
    assert body["summary"]["Warning"] == 2

    # once a switch has group members, only those ports are recorded per port (choose specific ports)
    from backend.infrastructure.runtime import telemetry_store
    unselected = telemetry_store().query(device_id=did, metric_name="port_link_up", since=t0 + timedelta(minutes=5),
                                         entity_type="port", entity_id="1/1/3")
    selected = telemetry_store().query(device_id=did, metric_name="port_link_up", since=t0 + timedelta(minutes=5),
                                       entity_type="port", entity_id="1/1/1")
    assert unselected == [] and selected

    hist = c.get(f"/api/v1/port-groups/{gid}/history",
                 params={"device_id": did, "port": "1/1/2", "hours": 2}, headers=adm).json()
    util = hist["series"]["port_util_percent"]
    assert hist["interpolated"] is False and util and util == sorted(util, key=lambda r: str(r["observed_at"]))

    # viewer can read but not change; a viewer scoped to another site sees no members
    viewer = make_user(c, adm, "noc-viewer", "viewer")
    assert c.get(f"/api/v1/port-groups/{gid}/dashboard", headers=viewer).status_code == 200
    assert c.delete(f"/api/v1/port-groups/{gid}", headers=viewer).status_code == 403
    scoped = make_user(c, adm, "branch-viewer", "viewer", scope="granted", grants=[])
    assert c.get(f"/api/v1/port-groups/{gid}/dashboard", headers=scoped).json()["ports"] == []
    assert c.get(f"/api/v1/port-groups/{gid}/history", params={"device_id": did, "port": "1/1/1"},
                 headers=scoped).status_code == 404

    # delete keeps telemetry history and is audited
    assert c.delete(f"/api/v1/port-groups/{gid}", headers=adm).status_code == 204
    assert c.get(f"/api/v1/port-groups/{gid}/dashboard", headers=adm).status_code == 404
    kept = telemetry_store().query(device_id=did, metric_name="port_link_up", since=t0 - timedelta(hours=1),
                                   entity_type="port", entity_id="1/1/1")
    assert kept
    audit = c.get("/api/audit", headers=adm).json()
    rows = audit["items"] if isinstance(audit, dict) else audit
    assert any(r.get("action") == "port_monitoring_group_deleted" for r in rows)


def test_stale_group_member_is_not_healthy(live):  # noqa: F811
    c, emu = live
    adm = login(c)
    did, pid = setup_switch(c, adm, emu)
    poll(pid, datetime.now(timezone.utc) - timedelta(hours=3))
    gid = c.post("/api/v1/port-groups", headers=adm, json={"name": "BWC Dock", "members": [
        {"device_id": did, "port": "1/1/4"}]}).json()["id"]
    poll(pid, datetime.now(timezone.utc) - timedelta(hours=2))
    row = c.get(f"/api/v1/port-groups/{gid}/dashboard", headers=adm).json()["ports"][0]
    assert row["status"] == "Stale" and row["observed_at"]
