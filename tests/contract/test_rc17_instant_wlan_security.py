"""rc17 Instant WLAN security objects through the real writer against the labelled Instant emulator (EMULATED):
role + ACL rules, external captive portal (no fail-through), MAC + 802.1X multi-auth; no overwrite, readback,
inverse rollback, allowlisted CLI grammar, domain validation."""
import pytest

from backend.aruba import ssh as S
from backend.domain import change_control as CC
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.aruba_instant import ArubaInstantWriter, role_lines, portal_lines
from backend.infrastructure.writers.emulator import InstantEmulator

ROLE = {"op": "ap.role.define", "role": "STAFF-LIMITED", "rules": [
    {"dest": {"ip": "10.0.0.0", "mask": "255.0.0.0"}, "protocol": "tcp", "ports": [443, 443], "action": "permit"},
    {"dest": {"ip": "10.0.0.0", "mask": "255.0.0.0"}, "protocol": "any", "action": "deny"},
    {"dest": "any", "protocol": "any", "action": "permit"}]}
PORTAL = {"op": "ap.captive_portal.external", "profile": "VISITOR-PORTAL", "server": "portal.jcf.example",
          "port": 443, "url": "/guest/login", "https": True, "redirect_url": "https://www.example.org/welcome"}
MULTI = {"op": "ap.ssid.multi_auth", "ssid": "INM-LAB-1X", "value": "enabled", "mac_role": "LAB-MAC",
         "acknowledge_auth_change": True}


def _w():
    e = InstantEmulator()
    w = e.writer()
    w.open()
    w.checkpoint()
    return e, w


@pytest.mark.parametrize("op", [ROLE, PORTAL, MULTI])
def test_apply_readback_and_inverse_rollback(op):
    e, w = _w()
    before = e.text
    w.apply(op)
    ok, got = w.readback(op)
    assert ok, got
    w.rollback("cp")
    assert w.readback(op)[0] is False
    norm = lambda t: sorted(x for x in t.split("\n") if x.strip())   # noqa: E731
    assert norm(e.text) == norm(before)


def test_rendered_lines_pass_the_device_cli_allowlist():
    for op in (ROLE, PORTAL, MULTI, MULTI | {"value": "disabled"}):
        e, w = _w()
        for call in w.render(op):
            for ln in call["lines"]:
                assert any(p.fullmatch(ln) for p in S._ALLOWED_CONFIG_LINES), ln
    v = CC.validate_operation(ROLE, family="access_point")
    assert role_lines(v)[0] == "rule 10.0.0.0 255.0.0.0 match tcp 443 443 permit"
    assert role_lines(v)[2] == "rule any any match any any any permit"
    assert portal_lines(CC.validate_operation(PORTAL, family="access_point"))[:3] == [
        "server portal.jcf.example", "port 443", 'url "/guest/login"']


def test_existing_objects_are_never_overwritten_and_preconditions_hold():
    e, w = _w()
    with pytest.raises(WriterError, match="already exists"):
        w.apply(ROLE | {"role": "LAB-PRE"})
    with pytest.raises(WriterError, match="already exists"):
        w.apply(PORTAL | {"profile": "LAB-PORTAL-A"})
    with pytest.raises(WriterError, match="enterprise SSID"):
        w.apply(MULTI | {"ssid": "Corp-WiFi"})                  # PSK SSID: no RADIUS server
    with pytest.raises(WriterError, match="role must already exist"):
        w.apply(MULTI | {"mac_role": "NO-SUCH-ROLE"})


@pytest.mark.parametrize("bad,msg", [
    (ROLE | {"rules": []}, "1-64"),
    (ROLE | {"rules": [{"dest": "any", "protocol": "tcp", "action": "permit"}]}, "port range"),
    (ROLE | {"rules": [{"dest": "any", "protocol": "icmp", "action": "permit"}]}, "protocol"),
    (PORTAL | {"fail_through": True}, "fail-through"),
    (PORTAL | {"https": False}, "Plain-HTTP"),
    (PORTAL | {"url": "/a b"}, "URL path"),
    (PORTAL | {"redirect_url": "http://x"}, "https"),
    (MULTI | {"acknowledge_auth_change": False}, "acknowledge"),
    (MULTI | {"mac_role": None}, "role"),
])
def test_domain_validation_refuses_unsafe_wlan_objects(bad, msg):
    with pytest.raises(CC.PolicyViolation, match=msg):
        CC.validate_operation(bad, family="access_point")
