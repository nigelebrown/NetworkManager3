"""Alert notifications and escalation (E2E-20) — real HTTP receiver and UDP syslog socket; SMTP recorded.

Checks: severity floor, demo alerts excluded by default, exactly-once enqueue across repeated sweeps, values in the
delivered payload equal the persisted alert, retry with backoff -> DEAD (never reported as sent), escalation only
for unacknowledged alerts, RESOLVED only to channels that saw OPENED, credential-bearing URLs refused, viewer cannot
manage channels, and the test-send reports the real outcome.
"""
from __future__ import annotations

import json
import socket
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from backend.database.models_phases import InmAlert
from backend.database.models_remediation import NotificationDelivery
from backend.database.session import session_scope
from backend.infrastructure import notifications as N
from tests.conftest import login, make_user
from tests.contract.test_production_read_write import live  # noqa: F401


class Receiver:
    def __init__(self):
        self.posts: list[dict] = []
        self.status = 200
        rec = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                rec.posts.append({"json": json.loads(body), "kind": self.headers.get("X-INM-Notification")})
                self.send_response(rec.status)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}/hook"


@pytest.fixture
def rx(monkeypatch):
    monkeypatch.setenv("INM_NOTIFICATION_ALLOWED_HOSTS", "127.0.0.1")
    r = Receiver()
    yield r
    r.srv.shutdown()


def _alert(sev="critical", source="REAL", rule="DEVICE_OFFLINE", opened=None) -> str:
    with session_scope() as db:
        a = InmAlert(device_id="dev-1", rule_id=rule, severity=sev, message=f"{rule} on core-sw-1", value=1.0,
                     threshold=0.0, state="OPEN", source_type=source, correlation_id="corr-123", entity="1/1/7",
                     opened_at=opened or datetime.now(timezone.utc))
        db.add(a)
        db.flush()
        return a.id


def _sweep(now=None):
    with session_scope() as db:
        return N.sweep(db, now)


def _deliveries(**kw):
    with session_scope() as db:
        q = db.query(NotificationDelivery)
        for k, v in kw.items():
            q = q.filter(getattr(NotificationDelivery, k) == v)
        return [(d.kind, d.state, d.attempts, d.channel_id, d.last_error) for d in q]


def test_webhook_floor_exactly_once_payload_fidelity_and_resolution(live, rx):
    adm = login(live)
    ch = live.post("/api/v1/notifications/channels", headers=adm, json={
        "name": "noc-hook", "kind": "webhook", "target": rx.url, "min_severity": "major"}).json()
    crit, minor, demo = _alert("critical"), _alert("minor", rule="PORT_ERRORS"), _alert("critical", source="DEMO")
    _sweep(); _sweep(); _sweep()                                 # repeated sweeps (several replicas) -> once
    assert [p["json"]["alert_id"] for p in rx.posts] == [crit]   # minor below floor, demo excluded by default
    got = rx.posts[0]["json"]
    with session_scope() as db:
        a = db.get(InmAlert, crit)
        assert (got["severity"], got["message"], got["entity"], got["correlation_id"], got["state"]) == \
               (a.severity, a.message, a.entity, a.correlation_id, a.state)
    assert rx.posts[0]["kind"] == "OPENED"
    # resolution is announced once, only to the channel that saw the opening
    with session_scope() as db:
        a = db.get(InmAlert, crit)
        a.state, a.resolved_at = "RESOLVED", datetime.now(timezone.utc)
        db.get(InmAlert, minor).state = "RESOLVED"
    _sweep(); _sweep()
    assert [(p["kind"], p["json"]["alert_id"]) for p in rx.posts] == [("OPENED", crit), ("RESOLVED", crit)]
    hist = live.get("/api/v1/notifications/deliveries", headers=adm).json()
    assert {(h["kind"], h["state"]) for h in hist} == {("OPENED", "SENT"), ("RESOLVED", "SENT")}
    assert all(h["channel_id"] == ch["id"] for h in hist)
    assert demo not in {h["alert_id"] for h in hist}


def test_failing_receiver_retries_with_backoff_then_dead_and_never_reports_sent(live, rx):
    adm = login(live)
    live.post("/api/v1/notifications/channels", headers=adm, json={"name": "flaky", "kind": "webhook", "target": rx.url})
    rx.status = 500
    aid = _alert("critical")
    t = datetime.now(timezone.utc)
    _sweep(t)
    assert _deliveries(alert_id=aid)[0][:3] == ("OPENED", "PENDING", 1)
    _sweep(t + timedelta(seconds=1))                             # inside backoff: not retried
    assert _deliveries(alert_id=aid)[0][2] == 1
    for i in range(N.MAX_ATTEMPTS):
        _sweep(t + timedelta(hours=i + 1))
    kind, state, attempts, _, err = _deliveries(alert_id=aid)[0]
    assert (state, attempts) == ("DEAD", N.MAX_ATTEMPTS) and "HTTP 500" in err
    dead = live.get("/api/v1/notifications/deliveries?state=DEAD", headers=adm).json()
    assert dead and dead[0]["sent_at"] is None
    # operator retry after the receiver is fixed
    rx.status = 200
    assert live.post(f"/api/v1/notifications/deliveries/{dead[0]['id']}/retry", headers=adm).status_code == 200
    _sweep(t + timedelta(hours=12))
    assert _deliveries(alert_id=aid)[0][1] == "SENT" and len(rx.posts) == N.MAX_ATTEMPTS + 1


def test_escalation_only_for_unacknowledged_alerts_over_email_and_syslog(live, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_message(self, m):
            sent.append({"to": m["To"], "subject": m["Subject"], "body": json.loads(m.get_content())})

    monkeypatch.setattr(N.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setenv("INM_SMTP_HOST", "smtp.example.test")
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.bind(("127.0.0.1", 0))
    udp.settimeout(2)
    adm = login(live)
    oncall = live.post("/api/v1/notifications/channels", headers=adm, json={
        "name": "oncall-mail", "kind": "email", "target": "oncall@example.test", "min_severity": "critical"}).json()
    sl = live.post("/api/v1/notifications/channels", headers=adm, json={
        "name": "siem", "kind": "syslog", "target": f"127.0.0.1:{udp.getsockname()[1]}", "min_severity": "major",
        "escalate_after_minutes": 15, "escalation_channel_id": oncall["id"]}).json()
    assert sl["escalate_after_minutes"] == 15
    t0 = datetime.now(timezone.utc)
    acked, open_ = _alert("major", opened=t0), _alert("major", rule="PORT_FLAPPING", opened=t0)
    _sweep(t0)
    lines = [udp.recv(9000).decode(), udp.recv(9000).decode()]
    assert all(line.startswith("<131>1 ") and "OPENED" in line for line in lines)   # local0.err, RFC 5424
    with session_scope() as db:
        a = db.get(InmAlert, acked)
        a.state, a.acked_by = "ACKNOWLEDGED", "noc.operator"
    _sweep(t0 + timedelta(minutes=10))
    assert sent == []                                            # not yet due
    _sweep(t0 + timedelta(minutes=16))
    _sweep(t0 + timedelta(minutes=30))
    assert [s["body"]["alert_id"] for s in sent] == [open_]      # acknowledged alert is not escalated; once only
    assert sent[0]["to"] == "oncall@example.test" and sent[0]["subject"].startswith("[INM MAJOR] ESCALATED")
    udp.close()


def test_channel_validation_permissions_and_truthful_test_send(live, rx):
    adm = login(live)
    bad = live.post("/api/v1/notifications/channels", headers=adm, json={
        "name": "leaky", "kind": "webhook", "target": "https://user:secret@hooks.example.test/x"})
    assert bad.status_code == 422 and "credentials" in bad.text
    assert live.post("/api/v1/notifications/channels", headers=adm, json={
        "name": "x", "kind": "pager", "target": "y"}).status_code == 422
    assert live.post("/api/v1/notifications/channels", headers=adm, json={
        "name": "esc", "kind": "webhook", "target": rx.url, "escalate_after_minutes": 5}).status_code == 422
    v = make_user(live, adm, "noc.viewer2", "viewer")
    assert live.post("/api/v1/notifications/channels", headers=v, json={
        "name": "v", "kind": "webhook", "target": rx.url}).status_code == 403
    ok = live.post("/api/v1/notifications/channels", headers=adm, json={"name": "good", "kind": "webhook", "target": rx.url}).json()
    r = live.post(f"/api/v1/notifications/channels/{ok['id']}/test", headers=adm).json()
    assert r["delivered"] is True and rx.posts[-1]["kind"] == "TEST"
    rx.status = 503
    r = live.post(f"/api/v1/notifications/channels/{ok['id']}/test", headers=adm).json()
    assert r["delivered"] is False and "503" in r["error"]
    dead_port = live.post("/api/v1/notifications/channels", headers=adm, json={
        "name": "nowhere", "kind": "webhook", "target": "http://127.0.0.1:9/x"}).json()
    r = live.post(f"/api/v1/notifications/channels/{dead_port['id']}/test", headers=adm).json()
    assert r["delivered"] is False and r["error"]
    assert live.get("/api/v1/notifications/channels", headers=v).status_code == 200


def test_feature_list_declares_notifications_live_and_unbuilt_features_not_implemented(live):
    f = {m["key"]: m["status"] for m in live.get("/api/v1/system/features", headers=login(live)).json()["modules"]}
    assert f["notifications"] == "LIVE"
    for k in ("switch_port_access_control", "switch_stacking", "switch_acl_routing", "client_roaming_rf_interference",
              "remote_diagnostics"):
        assert f[k] == "NOT_IMPLEMENTED", k
