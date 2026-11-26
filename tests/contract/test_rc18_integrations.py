"""rc18 (2.10) Integrations module against real local receivers: HTTP webhook (bearer + HMAC), Microsoft Teams
Adaptive Card envelope, SMTP relay with AUTH, syslog UDP and TCP (RFC 6587), heartbeat push; write-only encrypted
credentials, connection tests, enable/disable, delivery status and retry, audit history; read-only expiring API
tokens and the Prometheus metrics endpoint."""
import base64
import hashlib
import hmac
import json
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.database.models_remediation import NotificationChannel
from backend.database.session import session_scope
from tests.conftest import make_user


class Http:
    def __init__(self, status=200):
        self.got, self.status = [], status
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                outer.got.append((dict(self.headers), body))
                self.send_response(outer.status)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/hook/secret-path"


class Smtp(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.mail, self.auth = [], []
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                w = lambda s: self.wfile.write((s + "\r\n").encode())  # noqa: E731
                w("220 test ESMTP")
                data = None
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode().rstrip("\r\n")
                    if data is not None:
                        if line == ".":
                            outer.mail.append("\n".join(data)); data = None; w("250 queued")
                        else:
                            data.append(line)
                        continue
                    cmd = line.split(" ")[0].upper()
                    if cmd == "EHLO":
                        self.wfile.write(b"250-test\r\n250 AUTH PLAIN LOGIN\r\n")
                    elif cmd == "AUTH":
                        outer.auth.append(base64.b64decode(line.split(" ")[2]).split(b"\0")[1:]); w("235 ok")
                    elif cmd in ("MAIL", "RCPT", "RSET", "NOOP"):
                        w("250 ok")
                    elif cmd == "DATA":
                        data = []; w("354 go")
                    elif cmd == "QUIT":
                        w("221 bye"); return
                    else:
                        w("502 no")
        super().__init__(("127.0.0.1", 0), H)
        threading.Thread(target=self.serve_forever, daemon=True).start()


@pytest.fixture
def integ(client, admin, monkeypatch):
    monkeypatch.setenv("INM_NOTIFICATION_ALLOWED_HOSTS", "127.0.0.1")
    return client, admin


def _create(c, h, **body):
    r = c.post("/api/v1/integrations/connections", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_webhook_bearer_and_hmac_credentials_are_encrypted_write_only_and_used(integ):
    c, h = integ
    rx = Http()
    bearer = _create(c, h, name="siem", kind="webhook", target=rx.url, config={"auth": "bearer"}, secret="tok-123")
    assert bearer["secret_configured"] is True and "secret" not in bearer and bearer["target"].endswith("/[redacted]")
    with session_scope() as db:
        enc = db.get(NotificationChannel, bearer["id"]).secret_enc
        assert enc and "tok-123" not in enc                       # encrypted at rest
    t = c.post(f"/api/v1/integrations/connections/{bearer['id']}/test", headers=h).json()
    assert t["delivered"] is True and t["connection"]["health"] == "OK"
    assert rx.got[-1][0]["Authorization"] == "Bearer tok-123"
    signed = _create(c, h, name="soar", kind="webhook", target=rx.url, config={"auth": "hmac"}, secret="k3y")
    assert c.post(f"/api/v1/integrations/connections/{signed['id']}/test", headers=h).json()["delivered"]
    hdrs, body = rx.got[-1]
    assert hdrs["X-INM-Signature"] == "sha256=" + hmac.new(b"k3y", body, hashlib.sha256).hexdigest()
    r = c.post("/api/v1/integrations/connections", headers=h,
               json={"name": "nosecret", "kind": "webhook", "target": rx.url, "config": {"auth": "hmac"}})
    assert r.status_code == 422 and "needs a secret" in r.text
    r = c.post("/api/v1/integrations/connections", headers=h,
               json={"name": "leak", "kind": "webhook", "target": rx.url, "config": {"password": "x"}})
    assert r.status_code == 422 and "unsupported settings" in r.text
    overview = c.get("/api/v1/integrations", headers=h).text
    assert "tok-123" not in overview and "k3y" not in overview and "secret-path" not in overview


def test_teams_adaptive_card_syslog_udp_tcp_and_smtp_auth(integ):
    c, h = integ
    rx = Http()
    teams = _create(c, h, name="noc-teams", kind="teams", target=rx.url.replace("http://", "https://"))
    assert teams["kind_label"] == "Microsoft Teams"
    with session_scope() as db:                                   # local receiver is plain HTTP in the test
        db.get(NotificationChannel, teams["id"]).target = rx.url
    assert c.post(f"/api/v1/integrations/connections/{teams['id']}/test", headers=h).json()["delivered"]
    card = json.loads(rx.got[-1][1])
    assert card["type"] == "message"
    att = card["attachments"][0]
    assert att["contentType"] == "application/vnd.microsoft.card.adaptive" and att["content"]["type"] == "AdaptiveCard"
    assert c.post("/api/v1/integrations/connections", headers=h, json={
        "name": "t2", "kind": "teams", "target": "http://example.org/x"}).status_code == 422
    # syslog over UDP and TCP
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.bind(("127.0.0.1", 0)); u.settimeout(3)
    s_udp = _create(c, h, name="syslog-udp", kind="syslog", target=f"127.0.0.1:{u.getsockname()[1]}")
    assert c.post(f"/api/v1/integrations/connections/{s_udp['id']}/test", headers=h).json()["delivered"]
    assert b"networkmanager3" in u.recv(9000)
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); srv.settimeout(3)
    got = []
    threading.Thread(target=lambda: got.append(srv.accept()[0].recv(9000)), daemon=True).start()
    s_tcp = _create(c, h, name="syslog-tcp", kind="syslog", target=f"127.0.0.1:{srv.getsockname()[1]}",
                    config={"protocol": "tcp"})
    assert c.post(f"/api/v1/integrations/connections/{s_tcp['id']}/test", headers=h).json()["delivered"]
    import time; time.sleep(0.2)
    n, _, rest = got[0].partition(b" ")
    assert int(n) == len(rest) and rest.startswith(b"<")          # RFC 6587 octet counting
    # SMTP relay with authentication (credential encrypted, used for AUTH)
    smtp = Smtp()
    mail = _create(c, h, name="ops-mail", kind="email", target="noc@example.org", secret="mail-pass",
                   config={"smtp_host": "127.0.0.1", "smtp_port": smtp.server_address[1], "smtp_security": "none",
                           "smtp_username": "inm", "from_address": "inm@example.org"})
    t = c.post(f"/api/v1/integrations/connections/{mail['id']}/test", headers=h).json()
    assert t["delivered"] is True, t
    assert smtp.auth[-1] == [b"inm", b"mail-pass"] and "Subject: [INM INFO] TEST" in smtp.mail[-1]
    assert c.post("/api/v1/integrations/connections", headers=h, json={
        "name": "m2", "kind": "email", "target": "a@b.c", "config": {"smtp_host": "x", "smtp_username": "u"}}).status_code == 422


def test_failures_are_visible_retryable_disable_stops_delivery_and_everything_is_audited(integ):
    from datetime import datetime, timezone
    from backend.infrastructure import notifications as N
    c, h = integ
    rx = Http(status=500)
    ch = _create(c, h, name="flaky", kind="webhook", target=rx.url, min_severity="info")
    t = c.post(f"/api/v1/integrations/connections/{ch['id']}/test", headers=h).json()
    assert t["delivered"] is False and "HTTP 500" in t["error"] and t["connection"]["health"] == "FAILING"
    with session_scope() as db:
        N.enqueue_event(db, kind="test_event", severity="major", message="disk", ref="r1", event="OPEN")
        for _ in range(N.MAX_ATTEMPTS):
            for d in db.query(N.NotificationDelivery).filter_by(channel_id=ch["id"], kind="EVENT"):
                d.next_attempt_at = None
            db.commit()
            N.deliver_due(db, datetime.now(timezone.utc))
    dl = c.get(f"/api/v1/integrations/connections/{ch['id']}/deliveries", headers=h).json()
    assert any(d["kind"] == "EVENT" and d["state"] == "DEAD" for d in dl), [(d["kind"], d["state"], d["attempts"], d["last_error"]) for d in dl]
    ev = [d for d in dl if d["kind"] == "EVENT"][0]
    assert ev["attempts"] == N.MAX_ATTEMPTS and "HTTP 500" in ev["last_error"]
    rx.status = 200
    assert c.post(f"/api/v1/integrations/deliveries/{ev['id']}/retry", headers=h).json()["state"] == "PENDING"
    with session_scope() as db:
        assert N.deliver_due(db, datetime.now(timezone.utc))["sent"] == 1
    assert c.post(f"/api/v1/integrations/connections/{ch['id']}/disable", headers=h).json()["health"] == "DISABLED"
    with session_scope() as db:
        assert N.enqueue_event(db, kind="test_event", severity="major", message="x", ref="r2", event="OPEN") == 0
    viewer = make_user(c, h, "int.viewer", "viewer")
    assert c.get("/api/v1/integrations", headers=viewer).status_code == 403
    assert c.post(f"/api/v1/integrations/connections/{ch['id']}/enable", headers=viewer).status_code == 403
    acts = [a["action"] for a in c.get("/api/v1/integrations/audit", headers=h).json()]
    assert {"integration_created", "integration_tested", "integration_delivery_retry", "integration_disabled"} <= set(acts)


def test_heartbeat_push_is_scheduled_and_failures_are_recorded(integ):
    from datetime import datetime, timedelta, timezone
    from backend.infrastructure import notifications as N
    c, h = integ
    rx = Http()
    hb = _create(c, h, name="uptime", kind="heartbeat", target=rx.url, config={"interval_minutes": 5})
    now = datetime.now(timezone.utc)
    with session_scope() as db:
        assert N.heartbeats(db, now)["sent"] == 1
        assert N.heartbeats(db, now + timedelta(minutes=1))["sent"] == 0          # interval respected
    assert json.loads(rx.got[-1][1])["schema"] == "inm.heartbeat/v1"
    rx.status = 503
    with session_scope() as db:
        assert N.heartbeats(db, now + timedelta(minutes=6))["failed"] == 1
    dead = c.get(f"/api/v1/integrations/connections/{hb['id']}/deliveries", headers=h).json()
    assert dead[0]["kind"] == "HEARTBEAT" and dead[0]["state"] == "DEAD"


def test_api_tokens_are_read_only_scoped_revocable_and_metrics_needs_enablement(integ):
    c, h = integ
    tok = c.post("/api/v1/integrations/api-tokens", headers=h, json={"name": "grafana", "scopes": ["read"], "days": 30}).json()
    raw = tok["token"]
    assert raw.startswith("inm_pat_") and tok["state"] == "ACTIVE"
    assert raw not in c.get("/api/v1/integrations", headers=h).text            # shown once only
    th = {"Authorization": f"Bearer {raw}"}
    assert c.get("/api/v1/system/features", headers=th).status_code == 200
    assert c.post("/api/v1/integrations/api-tokens", headers=th, json={"name": "x"}).status_code == 403  # read-only
    assert c.get("/api/v1/integrations", headers=th).status_code == 403
    assert c.get("/api/v1/users", headers=th).status_code == 403
    assert c.get("/api/v1/metrics", headers=th).status_code in (403, 404)
    m = c.post("/api/v1/integrations/api-tokens", headers=h, json={"name": "prom", "scopes": ["metrics"]}).json()
    mh = {"Authorization": f"Bearer {m['token']}"}
    assert c.get("/api/v1/metrics", headers=mh).status_code == 404               # disabled by default
    assert c.put("/api/v1/integrations/monitoring", headers=h, json={"metrics_enabled": True}).status_code == 200
    r = c.get("/api/v1/metrics", headers=mh)
    assert r.status_code == 200 and "# TYPE inm_devices gauge" in r.text and 'inm_build_info{version="' in r.text
    assert c.get("/api/v1/system/features", headers=mh).status_code == 403      # metrics-only token
    assert c.get("/api/v1/metrics", headers=th).status_code == 403               # read token lacks metrics scope
    c.delete(f"/api/v1/integrations/api-tokens/{tok['id']}", headers=h)
    assert c.get("/api/v1/system/features", headers=th).status_code == 401
    assert c.post("/api/v1/integrations/api-tokens", headers=h, json={"name": "bad", "scopes": ["write"]}).status_code == 422
    toks = {t["name"]: t for t in c.get("/api/v1/integrations", headers=h).json()["api_tokens"]}
    assert toks["grafana"]["state"] == "REVOKED" and toks["prom"]["last_used_at"]


def test_sweep_in_one_session_records_failed_attempt_instead_of_leaving_it_claimed(integ):
    """Regression (rc18): enqueue + deliver in the same session must not leave a failed delivery in SENDING."""
    from datetime import datetime, timezone
    from backend.infrastructure import notifications as N
    c, h = integ
    rx = Http(status=502)
    ch = _create(c, h, name="same-session", kind="webhook", target=rx.url, min_severity="info")
    with session_scope() as db:
        N.enqueue_event(db, kind="t", severity="major", message="m", ref="s1", event="OPEN")
        out = N.deliver_due(db, datetime.now(timezone.utc))
        assert out["retry"] == 1
        d = db.query(N.NotificationDelivery).filter_by(channel_id=ch["id"], kind="EVENT").one()
        db.refresh(d)
        assert d.state == "PENDING" and d.attempts == 1 and d.claim_owner is None and "HTTP 502" in d.last_error
