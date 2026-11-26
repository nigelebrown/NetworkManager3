"""rc18 CX RADIUS/AAA lifecycle, port authentication policy, auth status monitoring and single-area OSPF through
the real writer against the labelled CX emulator (EMULATED): create/modify/delete, primary/secondary priorities,
accounting, readback, checkpoint rollback, secrets never in plans or records, platform capability (6100 vs 6200)."""
import pytest

from backend.domain import change_control as CC
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.emulator import CxEmulator


def _w(model="6200F 48G"):
    e = CxEmulator(model=model)
    w = e.writer()
    w.open()
    return e, w


def v(op):
    return CC.validate_operation(op, family="switch", uplink_ports={"1/1/49"})


def run(w, op, secret=None):
    vo = v(op)
    if secret:
        vo = {**vo, "_secret": secret}
    cp = w.checkpoint()
    sent = w.apply(vo)
    ok, got = w.readback(vo)
    return cp, sent, ok, got


S1 = {"op": "switch.aaa.radius_server", "address": "192.0.2.10", "secret_ref": "prof-1", "timeout": 5, "retries": 2}
S2 = {"op": "switch.aaa.radius_server", "address": "192.0.2.11", "secret_ref": "prof-1", "vrf": "default"}


def test_radius_server_lifecycle_secret_never_recorded():
    e, w = _w()
    _, sent, ok, got = run(w, S1, secret="Lab-Shared-1")
    assert ok, got
    assert all("Lab-Shared-1" not in str(c) for c in sent)                       # executor record is masked
    srv = e.state["radius_servers"]["default"]["192.0.2.10,1812,udp"]
    assert srv["passkey"] == "Lab-Shared-1" and srv["timeout"] == 5
    with pytest.raises(WriterError, match="could not be resolved"):
        w.apply(v(S2))                                                            # no secret resolved -> nothing sent
    _, _, ok, got = run(w, {**S1, "action": "modify", "timeout": 9, "secret_ref": None})
    assert ok and got["timeout"] == 9
    _, _, ok, _ = run(w, {"op": "switch.aaa.radius_server", "action": "delete", "address": "192.0.2.10"})
    assert ok and "192.0.2.10,1812,udp" not in e.state["radius_servers"]["default"]


def test_server_group_primary_secondary_accounting_and_rollback():
    e, w = _w()
    run(w, S1, secret="a-secret-1"); run(w, S2, secret="a-secret-2")
    grp = {"op": "switch.aaa.server_group", "group_name": "CORP-RADIUS",
           "servers": [{"address": "192.0.2.11"}, {"address": "192.0.2.10"}]}
    cp, _, ok, got = run(w, grp)
    assert ok and got["priorities"] == {"192.0.2.11": 1, "192.0.2.10": 2}          # first listed = primary
    srv = e.state["radius_servers"]["default"]["192.0.2.10,1812,udp"]["server_group"]
    assert len(srv) == 2                                                          # built-in 'radius' membership kept
    _, _, ok, got = run(w, {**grp, "action": "modify", "load_balancing_mode": "round-robin",
                            "servers": [{"address": "192.0.2.10"}]})
    assert ok and got["priorities"] == {"192.0.2.10": 1}
    assert len(e.state["radius_servers"]["default"]["192.0.2.11,1812,udp"]["server_group"]) == 1
    _, _, ok, got = run(w, {"op": "switch.aaa.accounting", "accounting_mode": "start-stop", "interim_update_interval": 15})
    assert ok and got["interim_update_interval"] == 15
    _, _, ok, _ = run(w, {"op": "switch.aaa.accounting", "accounting_mode": "stop-only"})
    assert ok
    w.rollback(cp)                                                                 # checkpoint restore
    assert "CORP-RADIUS" not in e.state["aaa_server_groups"] and not e.state["aaa_accounting"]
    run(w, grp)
    _, _, ok, _ = run(w, {"op": "switch.aaa.server_group", "action": "delete", "group_name": "CORP-RADIUS"})
    assert ok and "CORP-RADIUS" not in e.state["aaa_server_groups"]


def test_port_auth_policy_lifecycle_and_status_monitoring():
    e, w = _w()
    run(w, S1, secret="s-1")
    run(w, {"op": "switch.aaa.server_group", "group_name": "PORT-RADIUS", "servers": [{"address": "192.0.2.10"}]})
    port = "1/1/10"
    pol = {"op": "switch.port.auth_policy", "port": port, "method": "dot1x", "auth_enable": True, "reauth_enable": True,
           "reauth_period": 3600, "max_requests": 2, "radius_server_group": "PORT-RADIUS", "acknowledge_auth_change": True}
    _, _, ok, got = run(w, pol)
    assert ok and got["radius_server_group"] == "PORT-RADIUS"
    mab = {**pol, "method": "mac-auth", "max_requests": None}
    mab.pop("max_requests")
    _, _, ok, _ = run(w, mab)
    assert ok
    with pytest.raises(CC.PolicyViolation, match="Unsupported attribute"):
        v({**mab, "eapol_timeout": 30})                                          # 802.1X-only attribute on MAB
    _, _, ok, got = run(w, {**pol, "action": "modify", "auth_enable": False, "reauth_period": 7200})
    assert ok and got["auth_enable"] is False
    _, _, ok, _ = run(w, {"op": "switch.port.auth_policy", "action": "delete", "port": port, "method": "mac-auth",
                          "acknowledge_auth_change": True})
    assert ok
    with pytest.raises(CC.PolicyViolation, match="protected uplink"):
        v({**pol, "port": "1/1/49"})
    clients = w.port_access_clients("1/1/24")
    assert clients == [{"mac": "00:11:22:33:44:55", "status": "authenticated", "methods": ["dot1x"],
                        "role": "EMULATED-EMPLOYEE", "vlan": 20}]


def test_ospf_single_area_on_6200_and_refused_on_6100():
    e, w = _w("6200F 48G")
    o = {"op": "switch.route.ospf", "router_id": "10.255.0.1", "area": "0.0.0.0", "interfaces": ["vlan20", "1/1/48"],
         "redistribute": ["connected"], "acknowledge_routing_change": True}
    cp, _, ok, got = run(w, o)
    assert ok and got["interfaces"] == ["1/1/48", "vlan20"]
    _, _, ok, _ = run(w, {**o, "action": "modify", "interfaces": ["vlan20"], "area_type": "default"})
    assert ok
    w.rollback(cp)
    assert e.state["ospf_routers"] == {}
    e2, w2 = _w("6100 24G")
    with pytest.raises(WriterError, match="static routing only"):
        w2.apply(v(o))
    for bad, msg in ((dict(o, redistribute=["bgp"]), "connected and static"),
                     (dict(o, acknowledge_routing_change=False), "acknowledge"),
                     (dict(o, vrf="mgmt"), "default VRF")):
        with pytest.raises(CC.PolicyViolation, match=msg):
            v(bad)


@pytest.mark.parametrize("bad,msg", [
    (dict(S1, passkey="x"), "Secrets are never placed"),
    ({"op": "switch.aaa.radius_server", "address": "192.0.2.10"}, "secret_ref"),
    (dict(S1, port_type="tcp"), "RadSec"),
    (dict(S1, timeout=99), "timeout"),
    ({"op": "switch.aaa.server_group", "group_name": "radius", "servers": [{"address": "192.0.2.1"}]}, "built-in"),
    ({"op": "switch.aaa.server_group", "group_name": "G1", "servers": [{"address": "192.0.2.1"}] * 2}, "twice"),
    ({"op": "switch.aaa.accounting", "accounting_mode": "start-stop", "interim_update_interval": 0}, "interim"),
])
def test_validation_refusals(bad, msg):
    with pytest.raises(CC.PolicyViolation, match=msg):
        v(bad)


def test_capabilities_are_detected_per_model_ospf_6200_only_and_bgp_unsupported(client, admin):
    from backend.database.models_inm import InmDevice
    from backend.database.session import session_scope
    ids = {}
    with session_scope() as db:
        for model in ("CX 6100 12G", "CX 6200F 24G"):
            d = InmDevice(source_type="REAL", environment="live", vendor="Aruba", family="switch", model=model,
                          serial_number="SN-" + model[-6:].replace(" ", ""), hostname=model.replace(" ", "-"),
                          mgmt_addresses=["10.9.9." + str(len(ids) + 10)], firmware="10.14.1000", adapter="aruba_cx_10_14")
            db.add(d); db.flush(); ids[model] = d.id
    rows = {m: {r["operation"]: r for r in client.get(f"/api/v1/devices/{i}/capabilities", headers=admin).json()["operations"]}
            for m, i in ids.items()}
    assert rows["CX 6100 12G"]["switch.route.ospf"]["software"] == "UNSUPPORTED_BY_MODEL"
    assert rows["CX 6200F 24G"]["switch.route.ospf"]["software"] == "IMPLEMENTED"
    for m in ids:
        assert rows[m]["switch.route.bgp"]["software"] == "UNSUPPORTED_BY_MODEL" and not rows[m]["switch.route.bgp"]["executable_now"]
