"""The legacy SIMS write screens (SSID, AP rename, firmware staging, certificates) cannot bypass the
two-person rule or the emergency stop that govern INM change plans."""
import pytest

from backend.config import get_settings
from backend.database.models import User
from backend.database.session import session_scope
from backend.security.crypto import hash_password

from .conftest import login
from .test_config import _live_site, _plan, vc, writable  # noqa: F401  (fixtures)

PSK = "correct-horse-battery-staple"


@pytest.fixture
def enforced(app_env):
    app_env.setenv("SIMS_REQUIRE_INDEPENDENT_APPROVAL", "true")
    get_settings.cache_clear()


def _user(name, role):
    with session_scope() as db:
        db.add(User(username=name, role=role, password_hash=hash_password("Approver-pass-2026"),
                    must_change_password=False))


def test_legacy_apply_requires_independent_approval(enforced, client, admin_headers, writable, vc):  # noqa: F811
    sid = _live_site()
    plan = _plan(client, admin_headers, sid, profile="Guest", essid="Guest", vlan=300, opmode="wpa2-psk-aes",
                 passphrase=PSK).json()
    r = client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert r.status_code == 422 and "independent approval" in r.json()["detail"] and vc.posts == []
    # requester cannot approve own change
    assert client.post(f"/api/config/changes/{plan['id']}/approve", headers=admin_headers).status_code == 422
    _user("cab.member", "approver")
    appr = login(client, "cab.member", "Approver-pass-2026")
    a = client.post(f"/api/config/changes/{plan['id']}/approve", headers=appr)
    assert a.status_code == 200 and a.json()["plan"]["approval"]["approved_by"] == "cab.member"
    ok = client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert ok.status_code == 200 and ok.json()["status"] == "applied" and vc.posts


def test_legacy_apply_blocked_by_emergency_stop(enforced, client, admin_headers, writable, vc):  # noqa: F811
    sid = _live_site()
    plan = _plan(client, admin_headers, sid, profile="Guest", essid="Guest", vlan=300, opmode="wpa2-psk-aes",
                 passphrase=PSK).json()
    _user("cab.member", "approver")
    client.post(f"/api/config/changes/{plan['id']}/approve", headers=login(client, "cab.member", "Approver-pass-2026"))
    from backend.infrastructure.phase_services import control
    with session_scope() as db:
        control(db).emergency_stop = True
    r = client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert r.status_code == 422 and "Emergency stop" in r.json()["detail"] and vc.posts == []
