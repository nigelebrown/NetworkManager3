"""rc18 Instant RADIUS management through the real writer against the Instant emulator (EMULATED): multiple
servers, primary/failover assignment to an SSID, load balancing, accounting, timeout/retry, secret rotation with
previous secret for rollback, modification and removal, verification and inverse rollback."""
import pytest

from backend.aruba import ssh as S
from backend.domain import change_control as CC
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.emulator import InstantEmulator


def _w():
    e = InstantEmulator()
    w = e.writer()
    w.open()
    return e, w


def run(w, op, **secrets):
    v = CC.validate_operation(op, family="access_point") | secrets
    w.checkpoint()
    w.apply(v)
    return v, w.readback(v)


def norm(t):
    return sorted(x for x in t.split("\n") if x.strip() and not x.strip().startswith("key "))


R2 = {"op": "ap.radius.server", "name": "NPS-02", "ip": "192.0.2.82", "timeout": 5, "retry_count": 2, "secret_ref": "p2"}


def test_server_create_assign_failover_and_full_rollback():
    e, w = _w()
    base = e.text
    v, (ok, got) = run(w, R2, _secret="Second-Secret-1")
    assert ok and got["timeout"] == "5" and got["key_set"], got
    asg = {"op": "ap.ssid.auth_servers", "ssid": "INM-LAB-1X", "servers": ["NPS-02", "INM-LAB-RADIUS"],
           "load_balancing": True, "accounting": "enabled", "interval_minutes": 10, "acknowledge_auth_change": True}
    v2, (ok, got) = run(w, asg)
    assert ok and got["servers"] == ["NPS-02", "INM-LAB-RADIUS"] and got["load_balancing"], got   # primary first
    w.rollback("cp")                              # undoes the assignment (checkpoint taken before it)
    assert w.readback(v2)[0] is False
    blk = [x for x in e.text.split("\n") if x.startswith(" auth-server")]
    assert " auth-server INM-LAB-RADIUS" in blk and " auth-server NPS-02" not in blk
    w.checkpoint()
    with pytest.raises(WriterError, match="already exists"):
        w.apply(CC.validate_operation(R2, family="access_point") | {"_secret": "x-secret-1"})
    assert "NPS-02" in e.text
    # remove the server created above; then roll back -> it is recreated with its secret
    d = {"op": "ap.radius.server", "action": "delete", "name": "NPS-02", "secret_ref": "p2"}
    v3, (ok, _) = run(w, d, _secret="Second-Secret-1")
    assert ok and "wlan auth-server NPS-02" not in e.text
    w.rollback("cp")
    assert "wlan auth-server NPS-02" in e.text and w.readback(CC.validate_operation(R2, family="access_point"))[0]
    assert norm(e.text) != norm(base)              # (only NPS-02 remains, created in the first step)


def test_secret_rotation_needs_previous_secret_and_rollback_restores_it():
    e, w = _w()
    run(w, R2, _secret="Old-Secret-01")
    rot = {"op": "ap.radius.server", "action": "modify", "name": "NPS-02", "secret_ref": "p3", "timeout": 8}
    with pytest.raises(CC.PolicyViolation, match="previous_secret_ref"):
        CC.validate_operation(rot, family="access_point")
    rot["previous_secret_ref"] = "p2"
    before_key = [x for x in e.text.split("\n") if x.startswith(" key")]
    v, (ok, got) = run(w, rot, _secret="New-Secret-02", _previous_secret="Old-Secret-01")
    assert ok and got["timeout"] == "8"
    assert [x for x in e.text.split("\n") if x.startswith(" key")] != before_key
    w.rollback("cp")
    assert [x for x in e.text.split("\n") if x.startswith(" key")] == before_key     # previous secret restored
    assert "timeout 5" in e.text


def test_preconditions_and_validation():
    e, w = _w()
    w.checkpoint()
    with pytest.raises(WriterError, match="still assigned"):
        w.apply(CC.validate_operation({"op": "ap.radius.server", "action": "delete", "name": "INM-LAB-RADIUS",
                                       "secret_ref": "p"}, family="access_point") | {"_secret": "s-secret-1"})
    with pytest.raises(WriterError, match="enterprise SSID"):
        w.apply(CC.validate_operation({"op": "ap.ssid.auth_servers", "ssid": "Corp-WiFi", "servers": ["INM-LAB-RADIUS"],
                                       "acknowledge_auth_change": True}, family="access_point"))
    with pytest.raises(WriterError, match="does not exist"):
        w.apply(CC.validate_operation({"op": "ap.ssid.auth_servers", "ssid": "INM-LAB-1X", "servers": ["NOPE"],
                                       "acknowledge_auth_change": True}, family="access_point"))
    for bad, msg in ((dict(R2, key="x"), "Secrets are never"), (dict(R2, timeout=99), "timeout"),
                     ({"op": "ap.ssid.auth_servers", "ssid": "X", "servers": ["A", "B", "C"], "acknowledge_auth_change": True}, "one or two"),
                     ({"op": "ap.ssid.auth_servers", "ssid": "X", "servers": ["A"], "load_balancing": True,
                       "acknowledge_auth_change": True}, "two servers")):
        with pytest.raises(CC.PolicyViolation, match=msg):
            CC.validate_operation(bad, family="access_point")


def test_rendered_lines_pass_device_cli_allowlist():
    e, w = _w()
    for op, sec in ((R2, {"_secret": "Grammar-Secret-1"}),
                    ({"op": "ap.ssid.auth_servers", "ssid": "INM-LAB-1X", "servers": ["INM-LAB-RADIUS"],
                      "accounting": "disabled", "acknowledge_auth_change": True}, {})):
        for call in w.render(CC.validate_operation(op, family="access_point") | sec):
            for ln in call["lines"]:
                assert any(p.fullmatch(ln) for p in S._ALLOWED_CONFIG_LINES), ln


def test_ssid_create_without_a_resolved_secret_is_refused_before_anything_is_sent():
    """rc18 sweep regression: a masked/placeholder pre-shared key must never reach a device."""
    import pytest
    from backend.infrastructure.writers import aruba_instant as AI
    from backend.infrastructure.writers import WriterError

    class Probe(AI.ArubaInstantWriter):
        def __init__(self):
            self.sent = []
            self._last_rc18 = None

        def _running(self):
            return "version 8.10\n"

    w = Probe()
    w.client = type("C", (), {"config_post": lambda _s, ep, js: w.sent.append((ep, js))})()
    for op in ({"op": "ap.ssid.create", "ssid": "Guest", "vlan": 30, "security": "wpa2-psk-aes"},
               {"op": "ap.ssid.enterprise", "ssid": "Corp", "vlan": 20, "radius_name": "nps1", "radius_ip": "10.0.0.5"}):
        with pytest.raises(WriterError) as e:
            w.apply(op)
        assert e.value.category == "PRECONDITION" and "nothing was sent" in str(e.value)
    assert w.sent == []
