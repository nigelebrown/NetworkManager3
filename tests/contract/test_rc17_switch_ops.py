"""rc17 CX typed operations on the CX protocol emulator (EMULATED — not hardware certification):
ACL define/apply, static route, DSCP QoS policy, VSF member provisioning (6200 only), validation negatives."""
import pytest

from backend.domain.change_control import PolicyViolation, validate_operation
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.emulator import CxEmulator

ACL = {"op": "switch.acl.define", "name": "GUEST-IN", "entries": [
    {"seq": 10, "action": "deny", "protocol": "any", "src": "any", "dst": "10.0.0.0/8"},
    {"seq": 20, "action": "permit", "protocol": "tcp", "src": "any", "dst": "any", "dst_port": 443}]}


def run(w, op):
    validate_operation(op, family="switch")
    w.apply(op)
    return w.readback(op)


def test_acl_route_qos_apply_readback_and_checkpoint_rollback():
    emu = CxEmulator(model="6200F 24G", ports=24)
    w = emu.writer()
    w.open()
    try:
        cp = w.checkpoint()
        assert run(w, ACL)[0]
        ok, ev = run(w, {"op": "switch.acl.apply", "port": "1/1/5", "value": "GUEST-IN"})
        assert ok and ev["aclv4_in_cfg"].endswith("/system/acls/GUEST-IN,ipv4")
        ok, ev = run(w, {"op": "switch.route.static", "prefix": "10.50.0.0/16", "next_hop": "10.1.1.1"})
        assert ok and ev["next_hops"] == ["10.1.1.1"]
        ok, _ = run(w, {"op": "switch.route.static", "prefix": "10.50.0.0/16", "next_hop": "10.1.1.2"})  # second hop
        assert ok
        ok, ev = run(w, {"op": "switch.qos.dscp_policy", "name": "VOICE", "protocol": "udp", "dst_port": 5060,
                         "dscp": 46, "ports": ["1/1/6", "1/1/7"]})
        assert ok and ev["applied_ports"] == ["1/1/6", "1/1/7"]
        ace = emu.state["acls"]["GUEST-IN,ipv4"]["cfg_aces"]["10"]
        assert ace["dst_ip"] == "10.0.0.0/255.0.0.0" and ace["action"] == "deny"
        w.rollback(cp)                                     # checkpoint restore removes every rc17 object
        assert emu.state["acls"] == {} and emu.state["static_routes"] == {}
        assert emu.state["interfaces"]["1/1/5"].get("aclv4_in_cfg") is None
        with pytest.raises(WriterError):                    # unknown ACL reference rejected by the device
            w.apply({"op": "switch.acl.apply", "port": "1/1/5", "value": "MISSING"})
    finally:
        w.close()


def test_readback_detects_a_device_that_ignores_the_write():
    emu = CxEmulator(model="6200F 24G", ports=24)
    emu.lie = True
    w = emu.writer()
    w.open()
    try:
        w.apply(ACL)
        assert w.readback(ACL)[0] is False
    finally:
        w.close()


def test_vsf_member_on_6200_and_refused_on_6100():
    op = {"op": "switch.vsf.member", "model_family": "CX 6200", "member": 2, "member_type": "JL724A",
          "links": {"1": ["1/1/27"], "2": ["1/1/28"]}, "acknowledge_stack_change": True}
    emu = CxEmulator(model="6200F 24G", ports=28)
    w = emu.writer()
    w.open()
    try:
        ok, ev = run(w, op)
        assert ok and ev["links"] == {"1": ["1/1/27"], "2": ["1/1/28"]}
    finally:
        w.close()
    emu6100 = CxEmulator(model="6100 24G", ports=28)
    w = emu6100.writer()
    w.open()
    try:
        with pytest.raises(WriterError) as exc:
            w.apply(op)
        assert exc.value.category == "UNSUPPORTED" and emu6100.writes == 0
    finally:
        w.close()


@pytest.mark.parametrize("op,msg", [
    ({**ACL, "entries": [{"seq": 10, "action": "deny", "src": "any", "dst": "any"}]}, "acknowledge_deny_all"),
    ({**ACL, "entries": [{"seq": 10, "action": "permit", "protocol": "any", "dst_port": 80}]}, "tcp or udp"),
    ({**ACL, "entries": [{"seq": 10, "action": "permit", "dst": "10.0.0.1/8"}]}, "IPv4 network"),
    ({"op": "switch.route.static", "prefix": "0.0.0.0/0", "next_hop": "10.0.0.1"}, "default route"),
    ({"op": "switch.route.static", "prefix": "10.0.0.0/8", "next_hop": "10.0.0.1", "vrf": "mgmt"}, "default VRF"),
    ({"op": "switch.qos.dscp_policy", "name": "Q", "protocol": "udp", "dst_port": 5060, "dscp": 64}, "DSCP"),
    ({"op": "switch.vsf.member", "model_family": "CX 6100", "member": 2, "member_type": "JL678A",
      "links": {"1": ["1/1/27"]}, "acknowledge_stack_change": True}, "6200 family only"),
    ({"op": "switch.vsf.member", "model_family": "CX 6200", "member": 2, "member_type": "JL724A",
      "links": {"1": ["1/1/27"]}}, "acknowledge_stack_change"),
])
def test_validation_refuses_unsafe_or_malformed_requests(op, msg):
    with pytest.raises(PolicyViolation) as exc:
        validate_operation(op, family="switch")
    assert msg in str(exc.value)


def test_acl_on_protected_uplink_refused():
    with pytest.raises(PolicyViolation):
        validate_operation({"op": "switch.acl.apply", "port": "1/1/48", "value": "X1"}, family="switch",
                           uplink_ports={"1/1/48"})
