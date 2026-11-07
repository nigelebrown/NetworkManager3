"""Firmware compliance, firmware images + stage-only upgrade, and the WebUI certificate upload."""
import base64
import datetime as dt
import ipaddress
import json
import logging
import socket
import struct
from datetime import timedelta

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from sqlalchemy import select

from backend.certs import bundle, service as cert_service
from backend.certs.tftp import TftpOnce
from backend.config import get_settings
from backend.database.models import Alert, ConfigChange, Site, utcnow
from backend.database.session import session_scope
from backend.firmware import compliance, images
from backend.monitoring.collector import poll_site
from backend.security.crypto import decrypt_secret, encrypt_secret

from .conftest import login
from .test_config import _live_site, vc, writable  # noqa: F401

NAME = "ArubaInstant_Gemini_8.10.0.30_92000"
BLOB = b"\x00ARUBA-IMAGE" * 400


@pytest.fixture
def env(app_env, tmp_path):
    app_env.setenv("SIMS_DATA_DIR", str(tmp_path / "data"))
    app_env.setenv("SIMS_ALLOW_CONFIG_CHANGES", "true")
    app_env.setenv("SIMS_PUBLIC_BASE_URL", "http://192.0.2.10:8000")
    get_settings.cache_clear()
    return app_env


def _upload(client, h, name=NAME, data=BLOB):
    return client.post("/api/firmware/images", files={"file": (name, data)}, headers=h)


# ---- compliance ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("running,target,mode,expected", [
    ("8.10.0.21-8.10.0.21", "8.10.0.21", "minimum", "compliant"),
    ("8.10.0.21-8.10.0.21", "8.10.0.30", "minimum", "outdated"),
    ("8.13.3.1", "8.10.0.21", "minimum", "compliant"),
    ("8.13.3.1", "8.10.0.21", "exact", "newer"),
    ("8.10.0.9", "8.10.0.21", "minimum", "outdated"),      # numeric, not text, comparison
    (None, "8.10.0.21", "minimum", "unknown"),
    ("8.10.0.21", None, "minimum", "unknown"),
    ("garbage", "8.10.0.21", "minimum", "unknown"),
])
def test_evaluate(running, target, mode, expected):
    assert compliance.evaluate(running, target, mode) == expected


def test_policy_validation_and_rbac(env, client, admin_headers):
    ok = client.post("/api/firmware/policy", json={"target": "8.10.0.21", "mode": "exact"}, headers=admin_headers)
    assert ok.status_code == 200
    for bad in ({"target": "latest"}, {"target": "8.10"}, {"sites": {"abc": "8.10.0.21"}}, {"sites": {"999": "8.10.0.21"}}):
        assert client.post("/api/firmware/policy", json=bad, headers=admin_headers).status_code == 422
    with session_scope() as db:
        from backend.database.models import User
        from backend.security.crypto import hash_password
        db.add(User(username="vw", password_hash=hash_password("viewer-pass-123"), role="viewer"))
    vw = login(client, "vw", "viewer-pass-123")
    assert client.get("/api/firmware/compliance", headers=vw).status_code == 200
    assert client.post("/api/firmware/policy", json={"target": "8.10.0.21"}, headers=vw).status_code == 403
    assert client.get("/api/firmware/images", headers=vw).status_code == 403
    assert _upload(client, vw).status_code == 403


@pytest.fixture
def live_mode(env):
    env.setenv("SIMS_MODE", "live")
    get_settings.cache_clear()


def test_noncompliance_alert_raised_and_resolved(env, live_mode, client, admin_headers, vc):
    sid = _live_site()
    client.post("/api/firmware/policy", json={"target": "8.10.0.30"}, headers=admin_headers)
    with session_scope() as db:
        poll_site(db, db.get(Site, sid))
    with session_scope() as db:
        a = db.scalars(select(Alert).where(Alert.kind == "firmware_noncompliant")).all()
        assert len(a) == 1 and "8.10.0.21" in a[0].message and "8.10.0.30" in a[0].message
    rep = [s for s in client.get("/api/firmware/compliance", headers=admin_headers).json()["sites"] if s["site_id"] == sid][0]
    assert rep["status"] == "outdated"
    client.post("/api/firmware/policy", json={"target": "8.10.0.21"}, headers=admin_headers)
    with session_scope() as db:
        poll_site(db, db.get(Site, sid))
        assert db.scalars(select(Alert.status).where(Alert.kind == "firmware_noncompliant")).first() != "open"


def test_site_override_beats_global_target(env, live_mode, client, admin_headers, vc):
    sid = _live_site()
    client.post("/api/firmware/policy", json={"target": "8.10.0.99", "sites": {str(sid): "8.10.0.21"}}, headers=admin_headers)
    with session_scope() as db:
        poll_site(db, db.get(Site, sid))
    rep = [s for s in client.get("/api/firmware/compliance", headers=admin_headers).json()["sites"] if s["site_id"] == sid][0]
    assert rep["target"] == "8.10.0.21" and rep["status"] == "compliant"


# ---- images --------------------------------------------------------------------------------------------------------
def test_parse_name():
    assert images.parse_name("ArubaInstant_Gemini_8.10.0.21_91450") == ("Gemini", "8.10.0.21")
    assert images.parse_name("ArubaInstant_Scorpio_8.13.3.1_97206.ari") == ("Scorpio", "8.13.3.1")
    for bad in ("firmware.bin", "ArubaInstant_Nope_8.10.0.21_1", "../ArubaInstant_Gemini_8.10.0.21_1", "ArubaInstant_Gemini_8.10_1", ""):
        with pytest.raises(images.ImageError):
            images.parse_name(bad)


def test_upload_list_delete_and_limits(env, client, admin_headers):
    r = _upload(client, admin_headers)
    assert r.status_code == 201 and r.json()["ap_class"] == "Gemini" and r.json()["version"] == "8.10.0.30"
    assert (tmp := images.image_path(NAME)) is not None and tmp.read_bytes() == BLOB
    assert _upload(client, admin_headers).status_code == 422                       # duplicate
    assert _upload(client, admin_headers, name="evil.bin").status_code == 422     # unknown name
    assert _upload(client, admin_headers, name="ArubaInstant_Scorpio_8.10.0.30_1", data=b"x").status_code == 422   # too small
    env.setenv("SIMS_MAX_FIRMWARE_MB", "1")
    get_settings.cache_clear()
    big = _upload(client, admin_headers, name="ArubaInstant_Scorpio_8.10.0.30_2", data=b"a" * (1024 * 1024 + 5))
    assert big.status_code == 422 and "limit" in big.json()["detail"]
    assert images.image_path("ArubaInstant_Scorpio_8.10.0.30_2") is None and not list(images.image_dir().glob("*.part"))
    assert [i["filename"] for i in client.get("/api/firmware/images", headers=admin_headers).json()] == [NAME]
    iid = client.get("/api/firmware/images", headers=admin_headers).json()[0]["id"]
    assert client.delete(f"/api/firmware/images/{iid}", headers=admin_headers).status_code == 200
    assert images.image_path(NAME) is None


@pytest.mark.parametrize("name", ["../x", "a/b", ".hidden", "..\\x"])
def test_image_path_refuses_traversal(env, name):
    assert images.image_path(name) is None


# ---- stage-only upgrade --------------------------------------------------------------------------------------------
class UpgradeVC:
    """Wraps FakeVC and records /rest/os-upgrade."""
    def __init__(self, inner):
        self.inner, self.upgrades = inner, []

    def __call__(self, request):
        if request.url.path == "/rest/os-upgrade":
            self.upgrades.append(json.loads(request.content))
            return httpx.Response(200, json={"Status-code": 0, "message": "Success"})
        return self.inner(request)


@pytest.fixture
def upvc(vc, monkeypatch):
    import backend.aruba.authentication as auth_mod
    up = UpgradeVC(vc)
    real = auth_mod.client_for_site
    wrapped = lambda site, transport=None: real(site, httpx.MockTransport(up))  # noqa: E731
    monkeypatch.setattr("backend.firmware.upgrade.client_for_site", wrapped)
    monkeypatch.setattr("backend.configmgmt.service.client_for_site", wrapped)
    monkeypatch.setattr("backend.monitoring.collector.client_for_site", wrapped)
    return up


def _plan_upgrade(client, h, sid, ids, **kw):
    return client.post(f"/api/firmware/sites/{sid}/upgrade/plan", json={"image_ids": ids, **kw}, headers=h)


def _one_image(client, h, name=NAME):
    return _upload(client, h, name=name).json()["id"]


def test_plan_needs_a_reachable_public_url(env, client, admin_headers, upvc):
    sid, iid = _live_site(), _one_image(client, admin_headers)
    for bad in ("", "http://localhost:8000", "http://127.0.0.1:8000", "ftp://x"):
        env.setenv("SIMS_PUBLIC_BASE_URL", bad)
        get_settings.cache_clear()
        r = _plan_upgrade(client, admin_headers, sid, [iid])
        assert r.status_code == 422 and "SIMS_PUBLIC_BASE_URL" in r.json()["detail"]


def test_plan_hides_token_and_stage_only(env, client, admin_headers, upvc):
    sid = _live_site()
    with session_scope() as db:
        poll_site(db, db.get(Site, sid))
    iid = _one_image(client, admin_headers)
    r = _plan_upgrade(client, admin_headers, sid, [iid])
    assert r.status_code == 201, r.text
    plan = r.json()["plan"]
    token = images.token_for(r.json()["id"], NAME)
    assert plan["running"] == "8.10.0.21" and plan["target"] == "8.10.0.30" and plan["auto_reboot"] == "no"
    assert token not in json.dumps(r.json()) and "<secret-token>" in plan["images"][0]["url"]
    assert any("Stage only" in w for w in plan["warnings"])
    assert upvc.upgrades == []                                                    # planning never touches the AP


def test_downgrade_same_version_and_duplicate_classes_refused(env, client, admin_headers, upvc):
    sid = _live_site()
    with session_scope() as db:
        poll_site(db, db.get(Site, sid))
    old = _one_image(client, admin_headers, "ArubaInstant_Gemini_8.10.0.10_1")
    same = _one_image(client, admin_headers, "ArubaInstant_Gemini_8.10.0.21_2")
    new = _one_image(client, admin_headers, NAME)
    assert "older" in _plan_upgrade(client, admin_headers, sid, [old]).json()["detail"]
    assert _plan_upgrade(client, admin_headers, sid, [old], allow_downgrade=True).status_code == 201
    assert "already runs" in _plan_upgrade(client, admin_headers, sid, [same]).json()["detail"]
    assert "one image per AP class" in _plan_upgrade(client, admin_headers, sid, [old, new]).json()["detail"]


def test_apply_posts_os_upgrade_without_reboot_and_opens_download(env, client, admin_headers, upvc):
    sid = _live_site()
    with session_scope() as db:
        poll_site(db, db.get(Site, sid))
    iid = _one_image(client, admin_headers)
    ch = _plan_upgrade(client, admin_headers, sid, [iid]).json()
    token = images.token_for(ch["id"], NAME)
    dl = f"/fw/{ch['id']}/{token}/{NAME}"
    assert client.get(dl).status_code == 404                                       # not applied yet: link is dead
    assert client.post(f"/api/config/changes/{ch['id']}/apply", json={"confirm_text": "nope"}, headers=admin_headers).status_code == 422
    r = client.post(f"/api/config/changes/{ch['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "applied" and "Nothing was rebooted" in r.json()["message"]
    body = upvc.upgrades[0]["upgrade-info"]
    assert body["auto-reboot"] == "no" and body["Gemini-url"] == f"http://192.0.2.10:8000{dl}"
    got = client.get(dl)                                                            # no login needed: the link is the credential
    assert got.status_code == 200 and got.content == BLOB
    assert client.get(f"/fw/{ch['id']}/{'0' * 64}/{NAME}").status_code == 404      # wrong token
    assert client.get(f"/fw/{ch['id'] + 1}/{token}/{NAME}").status_code == 404     # token bound to the change
    other = _upload(client, admin_headers, name="ArubaInstant_Scorpio_8.10.0.30_9").json()["filename"]
    assert client.get(f"/fw/{ch['id']}/{images.token_for(ch['id'], other)}/{other}").status_code == 404   # file not in this change
    with session_scope() as db:                                                      # link window closes
        db.get(ConfigChange, ch["id"]).applied_at = utcnow() - timedelta(hours=7)
    assert client.get(dl).status_code == 404


def test_failed_device_response_leaves_link_closed(env, client, admin_headers, upvc, monkeypatch):
    sid = _live_site()
    with session_scope() as db:
        poll_site(db, db.get(Site, sid))
    ch = _plan_upgrade(client, admin_headers, sid, [_one_image(client, admin_headers)]).json()
    upvc.__class__.__call__, orig = (lambda self, req: httpx.Response(200, json={"Status-code": 6, "message": "CLI error"})
                                     if req.url.path == "/rest/os-upgrade" else self.inner(req)), upvc.__class__.__call__
    try:
        r = client.post(f"/api/config/changes/{ch['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    finally:
        upvc.__class__.__call__ = orig
    assert r.json()["status"] == "failed"
    assert client.get(f"/fw/{ch['id']}/{images.token_for(ch['id'], NAME)}/{NAME}").status_code == 404


def test_upgrade_blocked_when_changes_disabled(env, client, admin_headers, upvc):
    sid, iid = _live_site(), _one_image(client, admin_headers)
    env.setenv("SIMS_ALLOW_CONFIG_CHANGES", "false")
    get_settings.cache_clear()
    assert "disabled" in _plan_upgrade(client, admin_headers, sid, [iid]).json()["detail"]


def test_firmware_changes_cannot_be_rolled_back(env, client, admin_headers, upvc):
    sid = _live_site()
    with session_scope() as db:
        poll_site(db, db.get(Site, sid))
    ch = _plan_upgrade(client, admin_headers, sid, [_one_image(client, admin_headers)]).json()
    client.post(f"/api/config/changes/{ch['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert client.post(f"/api/config/changes/{ch['id']}/rollback-plan", headers=admin_headers).status_code == 422


# ---- certificate bundle --------------------------------------------------------------------------------------------
PASS = "Str0ng-Pass.phrase"


def make_bundle(passphrase=PASS, *, encrypt=True, ip="10.190.2.71", days=365, start_offset=-1, key=None, other_cert_key=None,
                ec_key=False):
    key = key or (ec.generate_private_key(ec.SECP256R1()) if ec_key else rsa.generate_private_key(65537, 2048))
    cert_key = other_cert_key or key
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "vc.example.net")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(cert_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now + timedelta(days=start_offset))
            .not_valid_after(now + timedelta(days=days if days > 0 else start_offset + 0.5))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("vc.example.net"), x509.IPAddress(ipaddress.ip_address(ip))]), False)
            .sign(cert_key, hashes.SHA256()))
    enc = (serialization.BestAvailableEncryption(passphrase.encode()) if encrypt else serialization.NoEncryption())
    pem = cert.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)
    return pem, cert


def test_bundle_ok_rsa_and_ec():
    for ec_key in (False, True):
        pem, cert = make_bundle(ec_key=ec_key)
        info = bundle.inspect_bundle(pem, PASS, host="10.190.2.71")
        assert info["subject_cn"] == "vc.example.net" and info["warnings"] == [] and info["key"].split()[0] == ("EC" if ec_key else "RSA")
        assert info["sha256"] == __import__("hashlib").sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def test_bundle_rejections():
    pem, _ = make_bundle()
    with pytest.raises(bundle.BundleError, match="does not decrypt"):
        bundle.inspect_bundle(pem, "Wrong-passphrase")
    with pytest.raises(bundle.BundleError, match="not passphrase-protected"):
        bundle.inspect_bundle(make_bundle(encrypt=False)[0], PASS)
    with pytest.raises(bundle.BundleError, match="does not belong"):
        bundle.inspect_bundle(make_bundle(other_cert_key=rsa.generate_private_key(65537, 2048))[0], PASS)
    with pytest.raises(bundle.BundleError, match="expired"):
        bundle.inspect_bundle(make_bundle(days=0, start_offset=-3)[0], PASS)
    with pytest.raises(bundle.BundleError, match="not valid until"):
        bundle.inspect_bundle(make_bundle(start_offset=5)[0], PASS)
    cert_only = pem[:pem.index(b"-----BEGIN ENCRYPTED")]
    with pytest.raises(bundle.BundleError, match="no private key"):
        bundle.inspect_bundle(cert_only, PASS)
    with pytest.raises(bundle.BundleError, match="No readable PEM"):
        bundle.inspect_bundle(b"hello", PASS)
    for bad in ("", "has space", "semi;colon", "quo\"te", "a" * 65, "new\nline"):
        with pytest.raises(bundle.BundleError, match="passphrase must be"):
            bundle.inspect_bundle(pem, bad)


def test_bundle_warns_when_name_does_not_match_or_expiring_soon():
    pem, _ = make_bundle(days=10)
    info = bundle.inspect_bundle(pem, PASS, host="10.9.9.9")
    assert any("does not name" in w for w in info["warnings"]) and any("expires in" in w for w in info["warnings"])


# ---- TFTP ----------------------------------------------------------------------------------------------------------
def tftp_get(port, name, expect_error=False):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    s.sendto(struct.pack("!H", 1) + name.encode() + b"\0octet\0", ("127.0.0.1", port))
    data, block = b"", 1
    try:
        while True:
            pkt, addr = s.recvfrom(1024)
            op, num = struct.unpack("!HH", pkt[:4])
            if op == 5:
                return ("error", num, pkt[4:-1].decode())
            assert (op, num) == (3, block)
            data += pkt[4:]
            s.sendto(struct.pack("!HH", 4, num), addr)
            if len(pkt) - 4 < 512:
                return ("ok", data)
            block += 1
    except socket.timeout:
        return ("timeout",)
    finally:
        s.close()


@pytest.mark.parametrize("size", [0, 1, 511, 512, 513, 1300])
def test_tftp_serves_exact_bytes_once(size):
    data = bytes(range(256)) * 6
    data = data[:size]
    srv = TftpOnce("c.pem", data, "127.0.0.1", "127.0.0.1", 0, lifetime=10).start()
    try:
        assert tftp_get(srv.port, "c.pem") == ("ok", data)
        assert srv.wait(2)
        assert tftp_get(srv.port, "c.pem")[0] in ("timeout", "error")      # one transfer only
    finally:
        srv.stop()


def test_tftp_refuses_other_files_and_other_clients():
    srv = TftpOnce("c.pem", b"secret", "127.0.0.1", "127.0.0.1", 0, lifetime=10).start()
    try:
        assert tftp_get(srv.port, "other.pem")[:2] == ("error", 1)
        assert not srv.completed.is_set()
        srv.allowed_ip = "192.0.2.99"                                      # a different client address
        assert tftp_get(srv.port, "c.pem")[:2] == ("error", 2)
    finally:
        srv.stop()


def test_tftp_reports_port_in_use():
    a = TftpOnce("c", b"x", "127.0.0.1", "127.0.0.1", 0).start()
    try:
        with pytest.raises(OSError, match="cannot listen"):
            TftpOnce("c", b"x", "127.0.0.1", "127.0.0.1", a.port)
    finally:
        a.stop()


# ---- certificate plan / apply --------------------------------------------------------------------------------------
def _free_udp_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def certenv(env, monkeypatch):
    port = _free_udp_port()
    env.setenv("SIMS_TFTP_PORT", str(port))
    env.setenv("SIMS_TFTP_BIND", "127.0.0.1")
    get_settings.cache_clear()
    monkeypatch.setattr("backend.certs.service.onboarding.scan_ssh", lambda site: {"fingerprint": "SHA256:abc", "key_type": "ssh-rsa"})
    monkeypatch.setattr(cert_service, "FETCH_WAIT_SECONDS", 0.5)
    monkeypatch.setattr(cert_service, "VERIFY_SECONDS", 0.2)
    monkeypatch.setattr(cert_service.time, "sleep", lambda s: None)
    return port


def _plan_cert(client, h, sid, pem, passphrase=PASS):
    return client.post(f"/api/firmware/sites/{sid}/certificate/plan", data={"passphrase": passphrase},
                       files={"file": ("vc.pem", pem)}, headers=h)


def test_cert_plan_is_display_safe_and_encrypted_at_rest(certenv, client, admin_headers):
    sid = _live_site(host="127.0.0.1")
    pem, cert = make_bundle(ip="127.0.0.1")
    r = _plan_cert(client, admin_headers, sid, pem)
    assert r.status_code == 201, r.text
    text = json.dumps(r.json())
    assert PASS not in text and "PRIVATE KEY" not in text and "BEGIN" not in text
    assert r.json()["plan"]["certificate"]["subject_cn"] == "vc.example.net" and r.json()["plan"]["ssh_host_key"] == "SHA256:abc"
    with session_scope() as db:
        raw = db.get(ConfigChange, r.json()["id"]).payload_enc
        assert PASS not in raw and "PRIVATE" not in raw
        assert json.loads(decrypt_secret(raw))["passphrase"] == PASS


def test_cert_plan_rejects_bad_input_and_needs_ipv4_public_url(certenv, client, admin_headers):
    sid = _live_site(host="127.0.0.1")
    pem, _ = make_bundle(ip="127.0.0.1")
    assert "does not decrypt" in _plan_cert(client, admin_headers, sid, pem, "Other-pass").json()["detail"]
    assert "not passphrase-protected" in _plan_cert(client, admin_headers, sid, make_bundle(encrypt=False)[0]).json()["detail"]
    certenv_url = "http://tftp.example.net:8000"
    get_settings.cache_clear()
    import os
    os.environ["SIMS_PUBLIC_BASE_URL"] = certenv_url
    get_settings.cache_clear()
    assert "LAN IPv4" in _plan_cert(client, admin_headers, sid, pem).json()["detail"]


def _fake_copy(port, fetch=True, error=None):
    seen = {}

    def fake(host, user, pw, fp, *, tftp_ip, filename, passphrase, on_sent=None, **kw):
        seen.update(host=host, fp=fp, tftp_ip=tftp_ip, filename=filename, passphrase=passphrase)
        if error:
            raise cert_service.ArubaError(error)
        if fetch:
            assert tftp_get(port, filename)[0] == "ok"
        return "ok"
    fake.seen = seen
    return fake


def test_cert_apply_end_to_end_verified_and_secrets_wiped(certenv, client, admin_headers, monkeypatch, caplog):
    sid = _live_site(host="127.0.0.1")
    pem, cert = make_bundle(ip="127.0.0.1")
    ch = _plan_cert(client, admin_headers, sid, pem).json()
    fake = _fake_copy(certenv)
    monkeypatch.setattr(cert_service.ssh, "copy_uiserver_cert", fake)
    monkeypatch.setattr(cert_service, "_peer_sha256", lambda h, p: ch["plan"]["certificate"]["sha256"])
    caplog.set_level(logging.DEBUG)
    r = client.post(f"/api/config/changes/{ch['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers)
    assert r.status_code == 200 and r.json()["status"] == "applied", r.text
    assert fake.seen["tftp_ip"] == "192.0.2.10" and fake.seen["passphrase"] == PASS and fake.seen["fp"] == "SHA256:abc"
    assert PASS not in r.text and "PRIVATE" not in r.text and PASS not in caplog.text
    with session_scope() as db:
        row = db.get(ConfigChange, ch["id"])
        assert json.loads(decrypt_secret(row.payload_enc)) == {}               # key + passphrase wiped
        from backend.database.models import AuditLog
        assert not any(PASS in (a.detail or "") for a in db.scalars(select(AuditLog)))
    # the file the AP downloaded was the uploaded one, byte for byte (checked in tftp tests); the server is gone
    assert tftp_get(certenv, fake.seen["filename"])[0] in ("timeout", "error")


def test_cert_apply_unverified_when_ap_keeps_old_certificate(certenv, client, admin_headers, monkeypatch):
    sid = _live_site(host="127.0.0.1")
    ch = _plan_cert(client, admin_headers, sid, make_bundle(ip="127.0.0.1")[0]).json()
    monkeypatch.setattr(cert_service.ssh, "copy_uiserver_cert", _fake_copy(certenv))
    monkeypatch.setattr(cert_service, "_peer_sha256", lambda h, p: "deadbeef")
    r = client.post(f"/api/config/changes/{ch['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers).json()
    assert r["status"] == "applied_unverified" and "not presenting it yet" in r["message"]


def test_cert_apply_fails_when_ap_never_downloads(certenv, client, admin_headers, monkeypatch):
    sid = _live_site(host="127.0.0.1")
    ch = _plan_cert(client, admin_headers, sid, make_bundle(ip="127.0.0.1")[0]).json()
    monkeypatch.setattr(cert_service.ssh, "copy_uiserver_cert", _fake_copy(certenv, fetch=False))
    r = client.post(f"/api/config/changes/{ch['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers).json()
    assert r["status"] == "failed" and "never downloaded" in r["message"]
    with session_scope() as db:
        assert json.loads(decrypt_secret(db.get(ConfigChange, ch["id"]).payload_enc)) == {}


def test_cert_apply_reports_ssh_failure_and_wipes(certenv, client, admin_headers, monkeypatch):
    sid = _live_site(host="127.0.0.1")
    ch = _plan_cert(client, admin_headers, sid, make_bundle(ip="127.0.0.1")[0]).json()
    monkeypatch.setattr(cert_service.ssh, "copy_uiserver_cert", _fake_copy(certenv, error="SSH login rejected"))
    r = client.post(f"/api/config/changes/{ch['id']}/apply", json={"confirm_text": "APPLY Lab"}, headers=admin_headers).json()
    assert r["status"] == "failed" and "SSH login rejected" in r["message"]
    with session_scope() as db:
        assert json.loads(decrypt_secret(db.get(ConfigChange, ch["id"]).payload_enc)) == {}


def test_cancelled_cert_plan_is_wiped(certenv, client, admin_headers):
    sid = _live_site(host="127.0.0.1")
    ch = _plan_cert(client, admin_headers, sid, make_bundle(ip="127.0.0.1")[0]).json()
    assert client.post(f"/api/config/changes/{ch['id']}/cancel", headers=admin_headers).status_code == 200
    with session_scope() as db:
        assert json.loads(decrypt_secret(db.get(ConfigChange, ch["id"]).payload_enc)) == {}


def test_copy_command_inputs_are_validated(env):
    from backend.aruba import ssh
    from backend.aruba.errors import ArubaUnsupportedError
    for kw in ({"tftp_ip": "a;b"}, {"filename": "x y"}, {"passphrase": "pa ss"}, {"passphrase": "pa;ss"}, {"filename": "../x"}):
        args = {"tftp_ip": "10.0.0.1", "filename": "c.pem", "passphrase": "good-pass", **kw}
        with pytest.raises(ArubaUnsupportedError):
            ssh.copy_uiserver_cert("10.0.0.2", "u", "p", "SHA256:x", **args)


def test_copy_command_is_scrubbed_and_pinned(env, monkeypatch):
    """The passphrase must not survive in anything the SSH helper returns or raises."""
    from backend.aruba import ssh
    from backend.aruba.errors import ArubaSshError

    class Chan:
        def __init__(self): self.sent, self.buf = [], ""
        def get_pty(self): pass
        def invoke_shell(self): self.buf = "AP# "
        def send(self, s):
            self.sent.append(s)
            self.buf += f"{s}% Error: copy failed for {s.split()[-3]}\nAP# "   # echoes the passphrase back on purpose
        def recv_ready(self): return bool(self.buf)
        def recv(self, n):
            b, self.buf = self.buf, ""
            return b.encode()

    class Key:
        def asbytes(self): return b"k"

    class T:
        chan = Chan()
        def get_remote_server_key(self): return Key()
        def auth_password(self, u, p): pass
        def open_session(self): return self.chan
        def close(self): pass

    t = T()
    monkeypatch.setattr(ssh, "_open_transport", lambda h, p, to: t)
    monkeypatch.setattr(ssh, "fingerprint", lambda k: "SHA256:ok")
    with pytest.raises(ArubaSshError) as e:
        ssh.copy_uiserver_cert("10.0.0.2", "u", "p", "SHA256:ok", tftp_ip="10.0.0.1", filename="c.pem", passphrase="Secret.Pass1")
    assert "Secret.Pass1" not in str(e.value)
    assert t.chan.sent == ["copy tftp 10.0.0.1 c.pem uiserver cert Secret.Pass1 format pem\n"]
    t.chan.sent.clear()
    with pytest.raises(ArubaSshError, match="does not match"):
        ssh.copy_uiserver_cert("10.0.0.2", "u", "p", "SHA256:other", tftp_ip="10.0.0.1", filename="c.pem", passphrase="Secret.Pass1")
    assert t.chan.sent == []                                                  # nothing typed on a mismatched host key
