import httpx
from sqlalchemy import select

from backend.config import get_settings
from backend.database.models import AccessPoint, Alert, AuditLog, ClientRecord, Site, User
from backend.database.session import session_scope
from backend.monitoring.collector import poll_all, poll_site
from backend.security.crypto import encrypt_secret, hash_password

from .conftest import aruba_handler, login


def _poll_demo():
    poll_all()


def test_auth_required(client):
    assert client.get("/api/aps").status_code == 401
    assert client.get("/api/dashboard/summary").status_code == 401


def test_login_bad_password_and_lockout(client):
    for _ in range(5):
        r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "admin", "password": "correct-horse-battery"})
    assert r.status_code == 429  # locked after repeated failures
    with session_scope() as db:
        assert db.scalar(select(AuditLog).where(AuditLog.action == "login_failed")) is not None


def test_demo_dashboard_is_labeled_simulated(client, admin_headers):
    _poll_demo()
    d = client.get("/api/dashboard/summary", headers=admin_headers).json()
    assert d["simulated"] is True and d["mode"] == "demo"
    assert d["sites_total"] == 3
    assert d["aps_total"] == 12 and d["aps_down"] == 1
    assert d["clients_total"] > 0
    ux = d["user_experience"]  # computed from AP-reported SNR/speed only; partial inputs are reported, not hidden
    assert 0 <= d["user_experience_score"] <= 100 and ux["good"] + ux["fair"] + ux["poor"] + ux["unscored"] == d["clients_total"]
    assert 0 <= d["health_score"] <= 100
    assert d["alerts_active"] >= 1  # the demo warehouse AP is offline


def test_offline_ap_alert_ack_resolve_flow(client, admin_headers):
    _poll_demo()
    alerts = client.get("/api/alerts?status=active", headers=admin_headers).json()
    offline = next(a for a in alerts if a["kind"] == "ap_offline")
    assert offline["simulated"] is True
    r = client.post(f"/api/alerts/{offline['id']}/ack", headers=admin_headers).json()
    assert r["status"] == "acknowledged" and r["acknowledged_by"] == "admin"
    r = client.post(f"/api/alerts/{offline['id']}/resolve", headers=admin_headers).json()
    assert r["status"] == "resolved"


def _make_viewer(client):
    with session_scope() as db:
        db.add(User(username="viewer1", password_hash=hash_password("viewer-password-1"), role="viewer"))
    return login(client, "viewer1", "viewer-password-1")


def test_viewer_gets_masked_client_identifiers_and_no_admin_access(client, admin_headers):
    _poll_demo()
    v = _make_viewer(client)
    rows = client.get("/api/clients", headers=v).json()
    assert rows and all(r["mac"].endswith("xx:xx:xx") and r["ip"] is None and r["hostname"] is None for r in rows)
    full = client.get("/api/clients", headers=admin_headers).json()
    assert not full[0]["mac"].endswith("xx:xx:xx") and full[0]["ip"]
    assert client.get("/api/audit", headers=v).status_code == 403
    assert client.post("/api/alerts/1/ack", headers=v).status_code == 403


def test_client_search_and_quality_filter(client, admin_headers):
    _poll_demo()
    all_rows = client.get("/api/clients", headers=admin_headers).json()
    target = all_rows[0]["hostname"]
    hit = client.get("/api/clients", params={"q": target}, headers=admin_headers).json()
    assert hit and all(target.lower() in (h["hostname"] or "").lower() for h in hit)
    good = client.get("/api/clients", params={"quality": "good"}, headers=admin_headers).json()
    assert all(c["quality"] == "good" for c in good)


def test_site_creation_blocked_in_demo_mode(client, admin_headers):
    r = client.post("/api/sites", headers=admin_headers, json={
        "name": "x", "vc_host": "10.0.0.1", "api_username": "a", "api_password": "b"})
    assert r.status_code == 400


def test_site_creation_validates_and_encrypts_in_live_mode(app_env, tmp_path):
    app_env.setenv("SIMS_MODE", "live")
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        h = login(c)
        bad = c.post("/api/sites", headers=h, json={
            "name": "x", "vc_host": "10.0.0.1; rm -rf /", "api_username": "a", "api_password": "b"})
        assert bad.status_code == 422
        ok = c.post("/api/sites", headers=h, json={
            "name": "Lab", "vc_host": "10.190.2.71", "api_username": "admin", "api_password": "PLAINTEXT-PW"})
        assert ok.status_code == 201
        assert "PLAINTEXT-PW" not in ok.text and "api_password" not in ok.json()
        with session_scope() as db:
            site = db.scalar(select(Site).where(Site.name == "Lab"))
            assert site.api_password_enc and "PLAINTEXT-PW" not in site.api_password_enc
        logs = c.get("/api/audit", headers=h).json()
        assert any(l["action"] == "site_create" for l in logs)
        assert "PLAINTEXT-PW" not in str(logs)


def test_live_poll_with_mocked_aruba(app_env):
    app_env.setenv("SIMS_MODE", "live")
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()) as c:
        h = login(c)
        with session_scope() as db:
            site = Site(name="Lab", vc_host="10.190.2.71", api_username="admin",
                        api_password_enc=encrypt_secret("pw"), simulated=False)
            db.add(site)
            db.flush()
            handler = aruba_handler()
            assert poll_site(db, site, httpx.MockTransport(handler)) is True
        d = c.get("/api/dashboard/summary", headers=h).json()
        assert d["simulated"] is False
        assert d["aps_total"] == 2 and d["clients_total"] == 2 and d["aps_down"] == 0
        assert d["firmware_versions"] == {"8.10.0.21-8.10.0.21": 2}
        assert d["avg_channel_utilization_percent"] is None  # not reported by Phase 1 commands
        aps = c.get("/api/aps", headers=h).json()
        assert all(a["cpu_percent"] is None for a in aps)  # unavailable, not estimated


def test_live_poll_failure_raises_alert_and_marks_site(app_env):
    app_env.setenv("SIMS_MODE", "live")
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()):
        with session_scope() as db:
            site = Site(name="Lab", vc_host="10.190.2.71", api_username="admin",
                        api_password_enc=encrypt_secret("pw"), simulated=False)
            db.add(site)
            db.flush()
            assert poll_site(db, site, httpx.MockTransport(aruba_handler(fail_login=True))) is False
            assert site.last_poll_ok is False
            alert = db.scalar(select(Alert).where(Alert.kind == "poll_failed"))
            assert alert and alert.simulated is False


def test_ap_missing_from_poll_goes_down(app_env):
    app_env.setenv("SIMS_MODE", "live")
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from .conftest import AP_TABLE
    one_ap = "\n".join(AP_TABLE.splitlines()[:4]) + "\n"
    with TestClient(create_app()):
        with session_scope() as db:
            site = Site(name="Lab", vc_host="10.0.0.5", api_username="a",
                        api_password_enc=encrypt_secret("pw"), simulated=False)
            db.add(site)
            db.flush()
            poll_site(db, site, httpx.MockTransport(aruba_handler()))
            poll_site(db, site, httpx.MockTransport(aruba_handler(ap_table=one_ap)))
            aps = {a.name: a.status for a in db.scalars(select(AccessPoint))}
            assert aps == {"lab-ap-1": "up", "lab-ap-2": "down"}
            assert db.scalar(select(Alert).where(Alert.kind == "ap_offline", Alert.status == "active"))


def test_delete_ap_and_site_cleanup(client, admin_headers):
    _poll_demo()
    # simulated APs/sites are protected
    sim_ap = client.get("/api/aps", headers=admin_headers).json()[0]
    assert client.delete(f"/api/aps/{sim_ap['id']}", headers=admin_headers).status_code == 404

    with session_scope() as db:
        site = Site(name="Live1", vc_host="10.0.0.9", vc_port=4343, api_username="u",
                    api_password_enc=encrypt_secret("p"), simulated=False)
        db.add(site); db.flush()
        ap = AccessPoint(site_id=site.id, name="gone-ap", mac="aa:bb:cc:00:00:01", simulated=False, status="down")
        db.add(ap); db.flush()
        db.add(Alert(site_id=site.id, ap_id=ap.id, kind="ap_offline", severity="critical", message="x"))
        ap_id, site_id = ap.id, site.id

    viewer = _make_viewer(client)
    assert client.delete(f"/api/aps/{ap_id}", headers=viewer).status_code == 403

    assert client.delete(f"/api/aps/{ap_id}", headers=admin_headers).json() == {"deleted": True}
    with session_scope() as db:
        assert db.get(AccessPoint, ap_id) is None
        assert db.scalar(select(Alert).where(Alert.ap_id == ap_id)) is None
        assert db.scalar(select(AuditLog).where(AuditLog.action == "ap_delete")) is not None

    assert client.delete(f"/api/sites/{site_id}", headers=admin_headers).json() == {"deleted": True}
    with session_scope() as db:
        assert db.get(Site, site_id) is None
