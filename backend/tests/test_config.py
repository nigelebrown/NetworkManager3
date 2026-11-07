"""Configuration management: gates, plan/confirm/apply/verify/rollback, AP move. Mock device only."""
import json

import httpx
import pytest
from sqlalchemy import select

from backend.aruba import authentication as auth_mod
from backend.aruba.client import ArubaClient, ArubaTarget
from backend.aruba.configuration import SsidChange, parse_running_config, redact
from backend.aruba.errors import ArubaUnsupportedError
from backend.config import get_settings
from backend.database.models import AccessPoint, Alert, AuditLog, ConfigBackup, ConfigChange, Site, User
from backend.database.session import session_scope
from backend.monitoring.collector import poll_site
from backend.security.crypto import encrypt_secret, hash_password

from .conftest import VERSION_TEXT, login

PSK = "SuperSecret-Pass-123"


class FakeVC:
    """Stateful stand-in for an Instant Virtual Controller (formats pending verification on real hardware)."""

    def __init__(self, ap_name="34:8a:12:aa:bb:cc", serial="CNTEST0001"):
        self.ssids = {"Corp": {"essid": "Corp", "vlan": 30, "opmode": "wpa2-psk-aes", "rf_band": "all", "enabled": True}}
        self.ap_name, self.serial = ap_name, serial
        self.posts: list[tuple[str, dict]] = []
        self.reject = False      # device answers with an error
        self.ignore_vlan = False  # device accepts but does not apply the VLAN

    def running_config(self):
        out = ["version 8.10.0.21-8.10.0.21"]
        for name, s in self.ssids.items():
            out += [f"wlan ssid-profile {name}", " enable" if s["enabled"] else " disable", f" essid {s['essid']}",
                    " wpa-passphrase 5ecr3tH45h", f" opmode {s['opmode']}", f" vlan {s['vlan']}", f" rf-band {s['rf_band']}", ""]
        return "\n".join(out)

    def aps(self):
        return ("1 Access Points\n---------------\nName  IP Address  Mode  Spectrum  Clients  Type  Serial #\n"
                f"----  ----------  ----  --------  -------  ----  --------\n{self.ap_name}  10.190.2.71*  access  disable  0  505(indoor)  {self.serial}\n")

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/login":
            return httpx.Response(200, json={"Status": "Success", "sid": "sid-x"})
        if path == "/rest/logout":
            return httpx.Response(200, json={"Status": "Success"})
        if path == "/rest/show-cmd":
            cmd = request.url.params["cmd"]
            text = {"show aps": self.aps(), "show clients": "", "show version": VERSION_TEXT,
                    "show running-config": self.running_config()}.get(cmd)
            if text is None:
                return httpx.Response(200, json={"Status": "Failure", "Message": "unsupported"})
            return httpx.Response(200, json={"Status": "Success", "Command output": text})
        if path in ("/rest/ssid", "/rest/hostname"):
            body = json.loads(request.content)
            self.posts.append((path, body))
            if self.reject:
                return httpx.Response(200, json={"Status-code": 6, "message": "CLI0 error: invalid"})
            if path == "/rest/hostname":
                self.ap_name = body["hostname_info"]["hostname"]
            else:
                p = body["ssid-profile"]
                cur = self.ssids.setdefault(p["ssid-profile"], {"essid": "", "vlan": 1, "opmode": "opensystem", "rf_band": "all", "enabled": True})
                if "essid" in p: cur["essid"] = p["essid"]["value"]
                if "vlan" in p and not self.ignore_vlan: cur["vlan"] = int(p["vlan"]["value"])
                if "opmode" in p: cur["opmode"] = p["opmode"]
                if "rf-band" in p: cur["rf_band"] = p["rf-band"]
                if p.get("disable") == "yes": cur["enabled"] = False
                if p.get("enable") == "yes": cur["enabled"] = True
            return httpx.Response(200, json={"Status": 0, "message": "Success"})
        return httpx.Response(404)


@pytest.fixture
def writable(app_env):
    app_env.setenv("SIMS_ALLOW_CONFIG_CHANGES", "true")
    get_settings.cache_clear()


@pytest.fixture
def vc(app_env, monkeypatch):
    fake = FakeVC()
    real = auth_mod.client_for_site
    wrapped = lambda site, transport=None: real(site, httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr("backend.configmgmt.service.client_for_site", wrapped)
    monkeypatch.setattr("backend.monitoring.collector.client_for_site", wrapped)
    return fake


def _live_site(name="Lab", host="10.190.2.71"):
    with session_scope() as db:
        s = Site(name=name, vc_host=host, vc_port=4343, api_username="u", api_password_enc=encrypt_secret("p"), simulated=False)
        db.add(s)
        db.flush()
        return s.id


def _poll(site_id):
    with session_scope() as db:
        poll_site(db, db.get(Site, site_id))


def _plan(client, h, site_id, **body):
    return client.post(f"/api/config/sites/{site_id}/ssid/plan", json=body, headers=h)


# ---- gates -------------------------------------------------------------------------------
def test_client_refuses_config_post_when_disabled_or_endpoint_unknown(app_env, monkeypatch):
    c = ArubaClient(ArubaTarget("10.0.0.1", 4343, "u", "p"), transport=httpx.MockTransport(FakeVC()))
    with pytest.raises(ArubaUnsupportedError, match="disabled"):
        c.config_post("ssid", {})
    monkeypatch.setenv("SIMS_ALLOW_CONFIG_CHANGES", "true")
    get_settings.cache_clear()
    with pytest.raises(ArubaUnsupportedError, match="not permitted"):
        c.config_post("reload", {})
    get_settings.cache_clear()


def test_planning_blocked_by_default_and_for_demo_sites(client, admin_headers, vc):
    sid = _live_site()
    r = _plan(client, admin_headers, sid, profile="T1", essid="T1", vlan=40, opmode="opensystem")
    assert r.status_code == 422 and "disabled" in r.json()["detail"]
    assert vc.posts == []


def test_demo_site_config_is_readable_but_not_writable(client, admin_headers, writable):
    demo = client.get("/api/sites", headers=admin_headers).json()[0]
    r = client.post(f"/api/config/sites/{demo['id']}/pull", headers=admin_headers).json()
    assert r["simulated"] is True and {s["profile"] for s in r["ssids"]} == {"Corp-WiFi", "Guest-WiFi", "IoT-Devices"}
    assert _plan(client, admin_headers, demo["id"], profile="X1", essid="x", vlan=5, opmode="opensystem").status_code == 422


def test_rbac(client, admin_headers, writable):
    with session_scope() as db:
        db.add(User(username="op", password_hash=hash_password("operator-pass-12"), role="operator"))
        db.add(User(username="vw", password_hash=hash_password("viewer-pass-123"), role="viewer"))
    op, vw = login(client, "op", "operator-pass-12"), login(client, "vw", "viewer-pass-123")
    sid = client.get("/api/sites", headers=admin_headers).json()[0]["id"]
    assert client.get(f"/api/config/sites/{sid}/ssids", headers=op).status_code == 200
    assert client.get(f"/api/config/sites/{sid}/ssids", headers=vw).status_code == 403
    assert client.get(f"/api/config/sites/{sid}/backups", headers=op).status_code == 403
    assert _plan(client, op, sid, profile="X1", essid="x", vlan=5, opmode="opensystem").status_code == 403


# ---- validation ---------------------------------------------------------------------------
@pytest.mark.parametrize("body", [
    {"profile": "bad name!", "essid": "x", "vlan": 5, "opmode": "opensystem"},
    {"profile": "ok", "essid": 'q"uote', "vlan": 5, "opmode": "opensystem"},
    {"profile": "ok", "essid": "x", "vlan": 5000, "opmode": "opensystem"},
    {"profile": "ok", "essid": "x", "vlan": 5, "opmode": "wep"},
    {"profile": "ok", "essid": "x", "vlan": 5, "opmode": "opensystem", "passphrase": "12345678"},
    {"profile": "ok", "essid": "x" * 33, "vlan": 5, "opmode": "opensystem"},
])
def test_ssid_input_validation(client, admin_headers, writable, vc, body):
    sid = _live_site()
    assert _plan(client, admin_headers, sid, **body).status_code == 422
    assert vc.posts == []


def test_new_ssid_requires_passphrase_for_psk(client, admin_headers, writable, vc):
    sid = _live_site()
    r = _plan(client, admin_headers, sid, profile="T1", essid="T1", vlan=40, opmode="wpa2-psk-aes")
    assert r.status_code == 422 and "passphrase" in r.json()["detail"]


# ---- full workflow --------------------------------------------------------------------------
def test_create_ssid_plan_confirm_apply_verify_rollback(client, admin_headers, writable, vc):
    sid = _live_site()
    r = _plan(client, admin_headers, sid, profile="Test1", essid="Test-SSID", vlan=40, opmode="wpa2-psk-aes", passphrase=PSK)
    assert r.status_code == 201
    plan = r.json()
    assert plan["status"] == "planned" and plan["plan"]["creating"] is True
    assert plan["plan"]["request"]["body"]["ssid-profile"]["wpa-passphrase"] == "********"
    assert PSK not in json.dumps(plan)
    assert vc.posts == []  # planning never writes to the device

    bad = client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "yes"}, headers=admin_headers)
    assert bad.status_code == 422 and vc.posts == []

    ok = client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers).json()
    assert ok["ok"] and ok["status"] == "applied", ok
    path, body = vc.posts[0]
    assert path == "/rest/ssid" and body["ssid-profile"]["wpa-passphrase"] == PSK  # real value reaches the device only
    assert body["ssid-profile"]["vlan"] == {"action": "create", "value": "40"}
    assert vc.ssids["Test1"]["vlan"] == 40

    # secrets are nowhere at rest in plain text
    with session_scope() as db:
        for ch in db.scalars(select(ConfigChange)):
            assert PSK not in ch.payload_enc and PSK not in json.dumps(ch.plan)
        assert all(PSK not in b.text and "5ecr3tH45h" not in b.text for b in db.scalars(select(ConfigBackup)))
        kinds = {b.kind for b in db.scalars(select(ConfigBackup))}
        assert {"pre-change", "post-change"} <= kinds
        audits = [a.detail or "" for a in db.scalars(select(AuditLog))]
        assert any(a.startswith("#") for a in audits) and not any(PSK in a for a in audits)

    again = client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert again.status_code == 422  # a plan can be applied only once

    rb = client.post(f"/api/config/changes/{plan['id']}/rollback-plan", headers=admin_headers).json()
    assert rb["rollback_of"] == plan["id"] and rb["status"] == "planned"
    done = client.post(f"/api/config/changes/{rb['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers).json()
    assert done["status"] == "applied" and vc.ssids["Test1"]["enabled"] is False


def test_modify_existing_ssid_vlan_and_rollback_restores_previous(client, admin_headers, writable, vc):
    sid = _live_site()
    plan = _plan(client, admin_headers, sid, profile="Corp", vlan=55).json()
    assert plan["plan"]["creating"] is False and plan["plan"]["before"]["vlan"] == 30
    assert any("VLAN" in w for w in plan["plan"]["warnings"])
    client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert vc.ssids["Corp"]["vlan"] == 55
    rb = client.post(f"/api/config/changes/{plan['id']}/rollback-plan", headers=admin_headers).json()
    client.post(f"/api/config/changes/{rb['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert vc.ssids["Corp"]["vlan"] == 30


def test_device_rejection_marks_failed(client, admin_headers, writable, vc):
    sid = _live_site()
    plan = _plan(client, admin_headers, sid, profile="Corp", vlan=55).json()
    vc.reject = True
    r = client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers).json()
    assert r["ok"] is False and r["status"] == "failed" and "rejected" in r["message"]
    assert vc.ssids["Corp"]["vlan"] == 30


def test_readback_mismatch_is_reported_not_hidden(client, admin_headers, writable, vc):
    sid = _live_site()
    plan = _plan(client, admin_headers, sid, profile="Corp", vlan=55).json()
    vc.ignore_vlan = True
    r = client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers).json()
    assert r["status"] == "applied_unverified" and any("VLAN" in p for p in r["problems"])


def test_expired_plan_cannot_be_applied(client, admin_headers, writable, vc):
    from datetime import timedelta
    from backend.database.models import utcnow
    sid = _live_site()
    plan = _plan(client, admin_headers, sid, profile="Corp", vlan=55).json()
    with session_scope() as db:
        db.get(ConfigChange, plan["id"]).expires_at = utcnow() - timedelta(minutes=1)
    r = client.post(f"/api/config/changes/{plan['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert r.status_code == 422 and vc.posts == []


def test_rename_ap_keeps_identity(client, admin_headers, writable, vc):
    sid = _live_site()
    _poll(sid)
    with session_scope() as db:
        ap = db.scalar(select(AccessPoint))
        ap_id, old = ap.id, ap.name
        assert ap.mac == "34:8a:12:aa:bb:cc" and ap.is_master is True and ap.model == "AP-505"
    r = client.post(f"/api/config/sites/{sid}/ap-rename/plan", json={"ap_id": ap_id, "new_name": "Lobby-AP"}, headers=admin_headers)
    assert r.status_code == 201
    out = client.post(f"/api/config/changes/{r.json()['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers).json()
    assert out["status"] == "applied", out
    assert vc.posts[0][1] == {"iap_ip_addr": "10.190.2.71", "hostname_info": {"hostname": "Lobby-AP"}}
    with session_scope() as db:
        aps = db.scalars(select(AccessPoint)).all()
        assert len(aps) == 1 and aps[0].id == ap_id and aps[0].name == "Lobby-AP"  # no duplicate after the rename
    bad = client.post(f"/api/config/sites/{sid}/ap-rename/plan", json={"ap_id": ap_id, "new_name": "bad name;rm"}, headers=admin_headers)
    assert bad.status_code == 422


# ---- AP move ---------------------------------------------------------------------------------
def test_move_ap_between_sites(client, admin_headers, vc):
    a, b = _live_site("A", "10.0.0.1"), _live_site("B", "10.0.0.2")
    _poll(a)
    with session_scope() as db:
        ap = db.scalar(select(AccessPoint))
        ap_id = ap.id
        db.add(Alert(site_id=a, ap_id=ap_id, kind="ap_offline", severity="critical", message="x"))
    r = client.post(f"/api/aps/{ap_id}/move", json={"target_site_id": b}, headers=admin_headers)
    assert r.status_code == 422  # explicit confirmation required
    out = client.post(f"/api/aps/{ap_id}/move", json={"target_site_id": b, "confirm": True}, headers=admin_headers).json()
    assert out["moved"] and out["from"] == "A" and out["to"] == "B"
    assert out["config_pulled"] is True and out["seen_in_destination"] is True  # fake VC for B reports it
    with session_scope() as db:
        assert db.get(AccessPoint, ap_id).site_id == b
        assert db.scalar(select(Alert).where(Alert.ap_id == ap_id, Alert.site_id == a)) is None
        assert db.scalar(select(ConfigBackup).where(ConfigBackup.site_id == b, ConfigBackup.kind == "move")) is not None
        assert db.scalar(select(AuditLog).where(AuditLog.action == "ap_move")) is not None
    same = client.post(f"/api/aps/{ap_id}/move", json={"target_site_id": b, "confirm": True}, headers=admin_headers)
    assert same.status_code == 422


def test_move_warns_when_ap_not_in_destination_cluster(client, admin_headers, vc, monkeypatch):
    a, b = _live_site("A", "10.0.0.1"), _live_site("B", "10.0.0.2")
    _poll(a)
    other = FakeVC(ap_name="aa:aa:aa:00:00:01", serial="CNOTHER")  # destination VC does not list our AP
    real = auth_mod.client_for_site
    def wrapped(site, transport=None):
        return real(site, httpx.MockTransport(other if site.name == "B" else vc))
    monkeypatch.setattr("backend.configmgmt.service.client_for_site", wrapped)
    monkeypatch.setattr("backend.monitoring.collector.client_for_site", wrapped)
    with session_scope() as db:
        ap_id = db.scalar(select(AccessPoint)).id
    out = client.post(f"/api/aps/{ap_id}/move", json={"target_site_id": b, "confirm": True}, headers=admin_headers).json()
    assert out["seen_in_destination"] is False and any("not reporting" in w for w in out["warnings"])


# ---- helpers ----------------------------------------------------------------------------------
def test_redact_and_parse():
    txt = "wlan ssid-profile X\n wpa-passphrase abc123\n essid X\n mgmt-user admin 5ecret\n"
    r = redact(txt)
    assert "abc123" not in r and "5ecret" not in r and "essid X" in r
    assert parse_running_config(txt)["ssids"][0]["essid"] == "X"
    assert SsidChange(profile="a", vlan=10).vlan == 10
