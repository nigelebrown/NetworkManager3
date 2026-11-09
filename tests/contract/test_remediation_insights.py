"""E2E-20: per-interface alerts, LLDP topology and change -> fault correlation on the real collector path
(strict emulator socket) and the certified write engine (emulated)."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from backend.database.session import session_scope
from tests.contract.test_remediation_data_plane import add_switch, plane, poll  # noqa: F401
from tests.conftest import login


def test_port_alerts_are_per_interface_and_resolve_per_interface(plane):
    c, emus, logs = plane
    adm = login(c)
    did, pid = add_switch(c, adm, "10.70.0.24", "EMU-PORTS-0", "CX 6100 24G", "PL.10.14.1000")
    emu = emus["10.70.0.24"]
    t0 = datetime.now(timezone.utc) - timedelta(minutes=11)
    assert poll(pid, t0)["succeeded"] == 1
    # port 1/1/5 starts taking errors; 1/1/7 saturates (1 Gbps link, 5 minutes)
    emu.state["interfaces"]["1/1/5"]["statistics"]["rx_error"] = 50
    emu.state["interfaces"]["1/1/7"]["statistics"]["rx_bytes"] += int(0.95e9 / 8 * 300)
    assert poll(pid, t0 + timedelta(minutes=5))["succeeded"] == 1
    from backend.database.models_phases import InmAlert
    with session_scope() as db:
        rows = {(a.rule_id, a.entity): a for a in db.scalars(select(InmAlert).where(InmAlert.device_id == did,
                                                                                    InmAlert.state == "OPEN"))}
        assert ("PORT_ERRORS", "port:1/1/5") in rows and ("PORT_UTIL_HIGH", "port:1/1/7") in rows
        assert ("PORT_ERRORS", "port:1/1/7") not in rows and "1/1/5" in rows[("PORT_ERRORS", "port:1/1/5")].message
        corr = rows[("PORT_ERRORS", "port:1/1/5")].correlation_id
    assert corr
    api = c.get("/api/v1/alerts", headers=adm).json()
    assert any(a["entity"] == "port:1/1/5" and a["rule_id"] == "PORT_ERRORS" for a in api)
    # next poll: errors stop on 1/1/5, 1/1/7 traffic normal -> both resolve; nothing else touched
    assert poll(pid, t0 + timedelta(minutes=10))["succeeded"] == 1
    with session_scope() as db:
        assert not list(db.scalars(select(InmAlert).where(InmAlert.device_id == did, InmAlert.entity.is_not(None),
                                                           InmAlert.state == "OPEN")))


def test_lldp_topology_links_switch_ports_to_inventory_aps(plane):
    c, emus, logs = plane
    adm = login(c)
    did, pid = add_switch(c, adm, "10.70.0.24", "EMU-PORTS-0", "CX 6100 24G", "PL.10.14.1000")
    # an AP known to inventory under the LLDP system name the switch reports on port 1/1/2
    c.post("/api/v1/quick-devices", headers=adm, json={"site_name": "HQ", "family": "access_point", "hostname": "AP-2",
           "serial_number": "AP-SER-2", "model": "AP-505", "management_ip": "10.70.0.102", "status": "up", "metrics": {}})
    assert poll(pid, datetime.now(timezone.utc) - timedelta(minutes=1))["succeeded"] == 1
    g = c.get("/api/v1/topology", headers=adm).json()
    ap = next(n for n in g["nodes"] if n["label"] == "AP-2")
    assert ap["inventory"] is True
    link = next(e for e in g["edges"] if e["to"] == ap["id"])
    assert link["from"] == did and link["local_port"] == "1/1/2" and link["remote_port"] == "eth0"
    assert any(n["family"] == "external" and n["label"] == "AP-1" for n in g["nodes"])   # not in inventory


def test_change_fault_correlation_lists_recent_changes_on_device_and_neighbours(app_env, tmp_path):
    from tests.contract.test_production_read_write import approve, certify, plan
    from tests.conftest import make_user
    from backend.infrastructure import change_executor as X
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("SIMS_INM_PROFILE", "lab-readonly")
    app_env.setenv("INM_VAULT_DIR", str(tmp_path / "vault"))
    X._EMULATORS.clear()
    from backend.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        adm = login(c)
        c.post("/api/v1/demo-data/load", headers=adm)
        appr = make_user(c, adm, "corr.appr", "approver")
        sw = next(d for d in c.get("/api/v1/devices", headers=adm).json() if d["source_type"] == "DEMO" and d["family"] == "switch")
        certify(c, adm, appr, sw, test_port="1/1/10")
        p = approve(c, appr, plan(c, adm, sw, [{"op": "switch.port.admin_state", "port": "1/1/3", "value": "down"}], "Shut 1/1/3"))
        assert c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()["outcome"] == "APPLIED_VERIFIED"
        from backend.database.models_inm import InmDevice
        from backend.database.models_phases import InmAlert
        with session_scope() as db:
            d = db.get(InmDevice, sw["id"])
            a = InmAlert(device_id=d.id, site_id=d.site_id, rule_id="PORT_ERRORS", entity="port:1/1/4", severity="major",
                         message="test", state="OPEN", opened_at=datetime.now(timezone.utc) + timedelta(minutes=3),
                         last_seen_at=datetime.now(timezone.utc), source_type="DEMO")
            old = InmAlert(device_id=d.id, site_id=d.site_id, rule_id="CPU_HIGH", severity="major", message="old",
                           state="OPEN", opened_at=datetime.now(timezone.utc) - timedelta(hours=5),
                           last_seen_at=datetime.now(timezone.utc), source_type="DEMO")
            db.add_all([a, old])
            db.flush()
            aid, oid = a.id, old.id
        r = c.get(f"/api/v1/alerts/{aid}/related-changes", headers=adm).json()
        assert [x["plan_id"] for x in r["candidates"]] == [p["id"]] and r["candidates"][0]["same_device"]
        assert "not a proven cause" in r["method"]
        assert c.get(f"/api/v1/alerts/{oid}/related-changes", headers=adm).json()["candidates"] == []
        impact = c.get(f"/api/v1/devices/{sw['id']}/change-impact", headers=adm).json()
        ex = next(x for x in impact["executions"] if x["plan_id"] == p["id"])
        assert ex["verdict"] == "INVESTIGATE" and [y["id"] for y in ex["alerts_within_window"]] == [aid]
    X._EMULATORS.clear()
