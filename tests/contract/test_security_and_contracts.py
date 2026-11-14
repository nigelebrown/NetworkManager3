"""Phase 1 security, safety and API-contract tests (P1-06, P1-07, P1-08, P1-12, SEC-*, INTEG-P1-03/04/07/08)."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from tests.conftest import ADMIN_PW, login, make_user

ROOT = Path(__file__).resolve().parents[2]


def _audit(client, admin, action):
    return client.get(f"/api/v1/audit/events?action={action}", headers=admin).json()


# ------------------------------------------------------------------ device write lock
def test_device_write_routes_refused_and_audited(client, admin):
    """INTEG-P1-04 / P1-DEP-15: apply and REST-enable never reach a device while the lock is on."""
    for path in ("/api/config/changes/1/apply", "/api/sites/1/rest-api/enable"):
        r = client.post(path, headers=admin, json={"confirm_text": "APPLY x", "host_key_sha256": "x"})
        assert r.status_code == 403 and r.json()["code"] == "device_write_locked"
    denied = _audit(client, admin, "device_write_denied")
    assert len(denied) == 2 and all(a["outcome"] != "success" and a["actor"] == "admin" for a in denied)


def test_write_lock_cannot_be_combined_with_writes(app_env):
    app_env.setenv("SIMS_ALLOW_CONFIG_CHANGES", "true")
    from backend.config import get_settings
    get_settings.cache_clear()
    s = get_settings()
    assert s.device_writes_permitted is False
    assert any("not permitted" in p for p in s.validate_profile())
    from backend.main import create_app
    with pytest.raises(RuntimeError, match="SIMS_DEVICE_WRITE_LOCK"):
        with TestClient(create_app()):
            pass


def test_demo_profile_refuses_live_mode(app_env):
    app_env.setenv("SIMS_MODE", "live")
    from backend.config import get_settings
    get_settings.cache_clear()
    from backend.main import create_app
    with pytest.raises(RuntimeError, match="requires SIMS_MODE=demo"):
        with TestClient(create_app()):
            pass


def test_aruba_client_refuses_writes_under_lock(app_env):
    import httpx
    from backend.aruba.client import ArubaClient
    from backend.aruba.errors import ArubaUnsupportedError
    calls = []
    from backend.aruba.client import ArubaTarget
    c = ArubaClient(ArubaTarget(host="10.0.0.1", port=4343, username="u", password="p"),
                    transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={})))
    with pytest.raises(ArubaUnsupportedError):
        c.config_post("ssid", {"x": 1})
    assert calls == []  # no network call at all


# ------------------------------------------------------------------ RBAC and identity
def test_viewer_denied_privileged_actions_and_denial_audited(client, admin):
    """P1-06."""
    v = make_user(client, admin, "val.viewer", "viewer")
    for method, path in (("post", "/api/alerts/1/ack"), ("get", "/api/v1/audit/events"), ("get", "/api/v1/system/services"),
                         ("post", "/api/v1/users"), ("post", "/api/v1/ai/analyze"), ("get", "/api/v1/recovery/snapshots")):
        r = client.request(method.upper(), path, headers=v, json={})
        assert r.status_code == 403, (path, r.status_code)
    denied = [a for a in _audit(client, admin, "access_denied") if a["actor"] == "val.viewer"]
    assert len(denied) == 6 and all(a["request_id"] for a in denied)


def test_auditor_reads_audit_but_cannot_operate(client, admin):
    a = make_user(client, admin, "ann.auditor", "auditor")
    assert client.get("/api/v1/audit/events", headers=a).status_code == 200
    assert client.get("/api/v1/audit/verify", headers=a).json()["ok"] is True
    assert client.post("/api/alerts/1/ack", headers=a).status_code == 403
    o = make_user(client, admin, "otto.operator", "operator")
    assert client.get("/api/v1/audit/events", headers=o).status_code == 403


def test_site_scoped_user_sees_only_granted_site(client, admin):
    """'Unscoped technician' case: legacy and v1 reads are both filtered."""
    from backend.monitoring.collector import poll_all
    poll_all()
    sites = client.get("/api/sites", headers=admin).json()
    assert len(sites) == 3
    keep = sites[0]["id"]
    t = make_user(client, admin, "tess.tech", "operator", scope="granted", grants=[keep])
    assert {s["id"] for s in client.get("/api/sites", headers=t).json()} == {keep}
    assert {a["site_id"] for a in client.get("/api/aps", headers=t).json()} == {keep}
    other = next(s["id"] for s in sites if s["id"] != keep)
    assert client.get(f"/api/aps?site_id={other}", headers=t).status_code == 404
    from backend.infrastructure.persistence.inventory import sync_inventory
    from backend.database.session import session_scope
    with session_scope() as db:
        sync_inventory(db)
    assert {d["legacy_site_id"] for d in client.get("/api/v1/devices", headers=t).json()} == {keep}
    none = make_user(client, admin, "nina.nogrants", "viewer", scope="granted", grants=[])
    assert client.get("/api/aps", headers=none).json() == []


def test_forced_password_change_blocks_everything_else(client, admin):
    client.post("/api/v1/users", headers=admin, json={"username": "pat.new", "role": "operator",
                                                       "temporary_password": "temporary-pass-123", "site_scope": "all"})
    h = login(client, "pat.new", "temporary-pass-123")
    r = client.get("/api/aps", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "password_change_required"
    assert client.get("/api/auth/me", headers=h).json()["must_change_password"] is True
    assert client.post("/api/auth/change-password", headers=h, json={"current_password": "temporary-pass-123",
                                                                      "new_password": "temporary-pass-123"}).status_code == 422
    changed = client.post("/api/auth/change-password", headers=h, json={"current_password": "temporary-pass-123",
                                                                          "new_password": "a-better-password-1"})
    assert changed.status_code == 200
    rotated = {"Authorization": f"Bearer {changed.json()['access_token']}"}
    assert changed.json()["must_change_password"] is False
    assert client.get("/api/aps", headers=h).status_code == 401  # previous session revoked
    assert client.get("/api/aps", headers=rotated).status_code == 200



def test_password_reset_revokes_all_existing_sessions_including_other_tokens(client, admin):
    """Admin-initiated reset revokes independently issued bearer tokens immediately."""
    r = client.post("/api/v1/users", headers=admin, json={
        "username": "rotate.target", "role": "operator",
        "temporary_password": "initial-credential-123", "site_scope": "all"})
    assert r.status_code == 201, r.text
    user_id = r.json()["id"]
    first = login(client, "rotate.target", "initial-credential-123")
    second = login(client, "rotate.target", "initial-credential-123")
    assert client.get("/api/auth/me", headers=first).status_code == 200
    assert client.get("/api/auth/me", headers=second).status_code == 200
    reset = client.post(f"/api/v1/users/{user_id}/reset-password", headers=admin,
                        json={"temporary_password": "reset-credential-456"})
    assert reset.status_code == 200, reset.text
    assert client.get("/api/auth/me", headers=first).status_code == 401
    assert client.get("/api/auth/me", headers=second).status_code == 401
    current = login(client, "rotate.target", "reset-credential-456")
    assert client.get("/api/auth/me", headers=current).json()["must_change_password"] is True


def test_tokens_do_not_expose_password_hash_or_accept_missing_credential_version(client, admin):
    """The JWT includes only an HMAC credential stamp; older unbound tokens cannot authenticate."""
    import jwt
    from backend.config import get_settings
    from backend.database.models import User
    from backend.database.session import session_scope
    headers = login(client)
    token = headers["Authorization"].removeprefix("Bearer ")
    payload = jwt.decode(token, options={"verify_signature": False})
    with session_scope() as db:
        stored_hash = db.query(User).filter_by(username="admin").one().password_hash
    assert "credential_version" in payload and stored_hash not in token
    legacy = dict(payload)
    legacy.pop("credential_version")
    unbound = jwt.encode(legacy, get_settings().jwt_secret, algorithm="HS256")
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {unbound}"}).status_code == 401

def test_logout_revokes_token_and_session_fields_are_audited(client, admin):
    """P1-07: login/logout recorded with actor, UTC time, target, outcome, request ID, session hash."""
    h = login(client)
    rid = "test-request-0001"
    assert client.post("/api/auth/logout", headers={**h, "X-Request-ID": rid}).status_code == 200
    assert client.get("/api/aps", headers=h).status_code == 401
    rows = client.get("/api/v1/audit/events", headers=admin).json()
    out = next(a for a in rows if a["action"] == "logout")
    assert out["request_id"] == rid and out["actor"] == "admin" and out["session_id_hash"] and out["ts"].endswith("+00:00")
    li = next(a for a in rows if a["action"] == "login")
    assert li["session_id_hash"] and li["actor_role"] == "admin"
    fails = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert fails.status_code == 401
    assert _audit(client, admin, "login_failed")[0]["outcome"] != "success"


def test_request_id_echoed(client):
    r = client.get("/api/health", headers={"X-Request-ID": "abcdef0123456789"})
    assert r.headers["x-request-id"] == "abcdef0123456789"
    assert len(client.get("/api/health", headers={"X-Request-ID": "bad id!"}).headers["x-request-id"]) == 32


# ------------------------------------------------------------------ audit integrity
def test_audit_hash_chain_detects_modification_and_deletion(client, admin):
    """SEC-03 groundwork (Phase 1 audit core): tampering is detected."""
    login(client); login(client)
    assert client.get("/api/v1/audit/verify", headers=admin).json()["ok"] is True
    from backend.database.session import get_engine
    with get_engine().begin() as c:
        c.execute(text("UPDATE audit_log SET detail='edited' WHERE id = (SELECT MIN(id) FROM audit_log)"))
    r = client.get("/api/v1/audit/verify", headers=admin).json()
    assert r["ok"] is False and "modified" in r["reason"]
    with get_engine().begin() as c:
        c.execute(text("DELETE FROM audit_log WHERE id = (SELECT MIN(id) FROM audit_log)"))
    r = client.get("/api/v1/audit/verify", headers=admin).json()
    assert r["ok"] is False


def test_audit_export_is_csv_with_hashes(client, admin):
    r = client.get("/api/v1/audit/export", headers=admin)
    assert r.status_code == 200 and r.text.splitlines()[0].endswith("prev_hash,event_hash")


# ------------------------------------------------------------------ contracts
def test_later_phase_routes_return_explicit_501(client, admin):
    """P1-12: no fake success for unbuilt capabilities."""
    for method, path, phase in (("post", "/api/v1/discovery/jobs", 2), ("get", "/api/v1/discovery/jobs/x", 2),
                                ("post", "/api/v1/changes", 6), ("post", "/api/v1/changes/1/approve", 6),
                                ("post", "/api/v1/changes/1/execute", 6)):
        r = getattr(client, method)(path, headers=admin)
        assert r.status_code == 501 and r.json()["status"] == "NOT_IMPLEMENTED" and r.json()["phase"] == phase


def test_every_legacy_route_still_mounted(client):
    """INTEG-P1-03: the compatibility facade keeps all 53 baseline routes."""
    table = (ROOT / "docs" / "legacy_feature_parity.md").read_text()
    legacy = set(re.findall(r"^\| (GET|POST|PUT|DELETE|PATCH) \| `([^`]+)` \|", table, re.M))
    assert len(legacy) == 53
    mounted = set()

    def walk(routes, prefix=""):
        for r in routes:
            if hasattr(r, "original_router"):  # FastAPI >=0.16x wraps included routers lazily
                walk(r.original_router.routes, prefix + (r.include_context.prefix or ""))
            else:
                mounted.update((m, prefix + r.path) for m in (getattr(r, "methods", None) or ()))
    walk(client.app.routes)
    missing = legacy - mounted
    assert not missing, missing


def test_openapi_documents_v1(client):
    paths = client.get("/openapi.json").json()["paths"]
    for p in ("/api/v1/system/services", "/api/v1/devices", "/api/v1/audit/events", "/api/v1/ai/analyze", "/api/v1/users"):
        assert p in paths


def test_legacy_backups_never_restorable(client, admin):
    """INTEG-P1-08."""
    from backend.monitoring.collector import poll_all
    poll_all()
    sid = client.get("/api/sites", headers=admin).json()[0]["id"]
    assert client.post(f"/api/config/sites/{sid}/pull", headers=admin).status_code == 200
    rec = client.get("/api/v1/recovery/snapshots", headers=admin).json()
    assert rec["restore_candidates"] == [] and rec["sanitized_snapshots"]
    assert all(s["restorable"] is False for s in rec["sanitized_snapshots"])
    from backend.database.models import ConfigBackup
    from backend.database.session import session_scope
    with session_scope() as db:
        assert all(b.restorable is False for b in db.scalars(select(ConfigBackup)))


def test_migration_marks_preexisting_backups_not_restorable(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config(); cfg.set_main_option("script_location", str(ROOT / "backend/database/migrations"))
    eng = create_engine(url)
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0003")
        conn.execute(text("INSERT INTO sites (name, vc_port, simulated, enabled, created_at) VALUES ('s', 4343, 0, 1, '2026-01-01')"))
        conn.execute(text("INSERT INTO config_backups (site_id, kind, taken_at, sha256, text, simulated) VALUES (1,'pull','2026-01-01','x','redacted',0)"))
        conn.execute(text("INSERT INTO audit_log (ts, action, success) VALUES ('2026-01-01','login',1)"))
        command.upgrade(cfg, "head")
        row = conn.execute(text("SELECT restorable, classification FROM config_backups")).one()
        assert row == (0, "MIGRATED_REDACTED")
        assert conn.execute(text("SELECT channel FROM audit_log")).scalar() == "LEGACY"


def test_demo_artifacts_are_labelled(client, admin):
    """P1-08 / P1-DEP-07: every simulated row and every rule recommendation says DEMO/RULE."""
    from backend.monitoring.collector import poll_all
    from backend.infrastructure.persistence.inventory import sync_inventory
    from backend.database.session import session_scope
    poll_all()
    with session_scope() as db:
        sync_inventory(db)
    assert client.get("/api/system/status", headers=admin).json()["simulated_data"] is True
    assert all(a["simulated"] for a in client.get("/api/aps", headers=admin).json())
    devs = client.get("/api/v1/devices", headers=admin).json()
    assert devs and all(d["source_type"] == "DEMO" for d in devs)
    assert {d["model"][:7] for d in devs if d["family"] == "switch"} == {"CX 6200", "CX 6100"}
    assert {"AP-505", "AP-535"} <= {d["model"] for d in devs if d["family"] == "access_point"}
    caps = [c for d in devs for c in d["capabilities"] if c["operation"].startswith(("config.", "firmware."))]
    assert caps and all(c["status"] == "UNSUPPORTED" for c in caps)


# ------------------------------------------------------------------ AI isolation
@pytest.fixture
def ai_client(monkeypatch):
    import ai_service.app as appmod
    monkeypatch.setattr(appmod, "TOKEN", "svc-token-123")
    return TestClient(appmod.app)


def test_ai_service_requires_token_and_labels_rules(ai_client):
    assert ai_client.post("/v1/analyze", json={"context": {"sites": []}}).status_code == 401
    r = ai_client.post("/v1/analyze", headers={"Authorization": "Bearer svc-token-123"}, json={"context": {
        "source_type": "DEMO", "sites": [{"site_id": 1, "label": "site-1", "aps_total": 3, "aps_down": 1,
                                          "max_util_percent": 82, "exp_score": 55, "clients_total": 8, "clients_poor": 3}]}})
    body = r.json()
    assert body["engine"] == "RULE" and body["trained_model"] is False
    assert {x["rule_id"] for x in body["recommendations"]} >= {"ap_offline", "high_channel_utilization", "poor_experience"}
    assert all(x["engine"] == "RULE" and x["approval_state"] == "ADVISORY" for x in body["recommendations"])
    assert "80 MHz is blocked" in next(x for x in body["recommendations"] if x["rule_id"] == "high_channel_utilization")["explanation"]


def test_ai_gateway_forwards_only_two_ollama_calls(ai_client):
    """INTEG-P1-07: only the fixed read-only model endpoints are forwarded."""
    for method, path in (("POST", "/ollama/api/pull"), ("POST", "/ollama/api/generate"), ("GET", "/ollama/api/ps"), ("POST", "/ollama/api/delete")):
        assert ai_client.request(method, path, json={}).status_code == 403
    assert ai_client.get("/ollama/api/tags").status_code == 503  # allowed, but no Ollama configured here


def test_ai_service_refuses_platform_secrets(monkeypatch):
    import ai_service.app as appmod
    monkeypatch.setenv("SIMS_FERNET_KEY", "x")
    with pytest.raises(RuntimeError, match="must not receive platform secrets"):
        with TestClient(appmod.app):
            pass


def test_ai_context_has_no_client_identities(client, admin):
    from backend.monitoring.collector import poll_all
    from backend.presentation.api_v1 import _analysis_context
    from backend.database.session import session_scope
    poll_all()
    with session_scope() as db:
        ctx = _analysis_context(db)
        blob = str(ctx)
        from backend.database.models import ClientRecord, AccessPoint
        for c in db.scalars(select(ClientRecord)):
            assert c.mac not in blob and (c.ip or "~") not in blob and (c.hostname or "~") not in blob
        for a in db.scalars(select(AccessPoint)):
            assert a.mac not in blob and (a.ip or "~") not in blob


# ------------------------------------------------------------------ WebSocket auth
def test_websocket_requires_valid_token(client, admin):
    from starlette.websockets import WebSocketDisconnect
    with client.websocket_connect("/ws/v1/events") as ws:
        ws.send_json({"type": "auth", "token": "not-a-token"})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401
    with client.websocket_connect("/ws/v1/events") as ws:
        ws.send_json({"type": "auth", "token": admin["Authorization"][7:], "since": None})
        hello = ws.receive_json()
        assert hello["type"] == "hello"
