"""Independent rc2 safety regressions: no physical device, no network egress.

All tests exercise production modules, not copies of their implementation.
"""
from __future__ import annotations

import concurrent.futures
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base
from backend.database.models_inm import InmDevice, JobLease, SiteAccessGrant
from backend.database.models_remediation import EventOutbox, NotificationChannel, NotificationDelivery, OnboardingWorkflow
from backend.domain.events import new_event
from backend.infrastructure import event_effects, notifications, outbox
from backend.infrastructure.change_executor import _lease, _release
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.aruba_cx import CxSession, redact


def _factory(tmp_path, *tables):
    engine = create_engine(f"sqlite:///{tmp_path / 'safety.db'}", connect_args={"timeout": 6, "check_same_thread": False})
    Base.metadata.create_all(engine, tables=[t.__table__ for t in tables])
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_one_durable_device_writer_even_when_lease_expired(tmp_path):
    Session = _factory(tmp_path, JobLease)
    with Session.begin() as db:
        db.add(JobLease(job="device-write:dev-1", holder="expired", expires_at=datetime.now(timezone.utc) - timedelta(minutes=2)))
    barrier = threading.Barrier(2)
    def claim(holder):
        barrier.wait(timeout=5)
        with Session() as db:
            return holder, _lease(db, "dev-1", holder)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(claim, ("unique-1", "unique-2")))
    assert [ok for _, ok in results].count(True) == 1, results
    winner = next(holder for holder, ok in results if ok)
    loser = next(holder for holder, ok in results if not ok)
    with Session.begin() as db:
        assert db.get(JobLease, "device-write:dev-1").holder == winner
        _release(db, "dev-1", loser)  # no-op, cannot release another worker's claim
    with Session() as db:
        assert not _lease(db, "dev-1", "third-worker")
    with Session.begin() as db:
        _release(db, "dev-1", winner)
    with Session() as db:
        assert _lease(db, "dev-1", "third-worker")


def test_notification_claim_is_exclusive_between_workers(tmp_path, monkeypatch):
    Session = _factory(tmp_path, NotificationChannel, NotificationDelivery)
    at = datetime.now(timezone.utc)
    with Session.begin() as db:
        db.add(NotificationChannel(id="channel-1", name="test", kind="webhook", target="https://public.example.test/x",
                                   min_severity="major", enabled=True, created_by="qa"))
        db.add(NotificationDelivery(id="delivery-1", dedupe_key="event-1", channel_id="channel-1", kind="OPENED",
                                    payload={"notification": "OPENED", "severity": "major", "message": "up"},
                                    state="PENDING", attempts=0, created_at=at, next_attempt_at=at))
    sent = []
    monkeypatch.setattr(notifications, "send", lambda ch, payload: (sent.append(payload), time.sleep(0.15)))
    barrier = threading.Barrier(2)
    def worker():
        barrier.wait(timeout=5)
        with Session() as db:
            return notifications.deliver_due(db, at)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(lambda _: worker(), (1, 2)))
    assert len(sent) == 1, (sent, results)
    with Session() as db:
        d = db.get(NotificationDelivery, "delivery-1")
        assert (d.state, d.attempts, d.claim_owner) == ("SENT", 1, None)


def test_out_of_order_failure_cannot_reverse_new_success(monkeypatch):
    now = datetime.now(timezone.utc)
    device = InmDevice(id="dev", source_type="REAL", environment="lab", vendor="aruba", family="switch",
                       serial_number="dev1", status="up", last_observed_at=now)
    class Db:
        def get(self, cls, key):
            return device if cls is InmDevice and key == "dev" else None

        def execute(self, statement):
            # No collection plans in this isolated unit fixture: the collector
            # correctly falls back to the device's last-observed timestamp.
            return []
    monkeypatch.setattr(event_effects, "apply_alerts", lambda *a, **kw: {"changed": False})
    older = new_event("device.poll.failed", source_service="collector", actor_id="collector", source_type="REAL",
                      device_id="dev", occurred_at=now-timedelta(minutes=20),
                      payload={"collector": "scheduled", "failure_category": "UNREACHABLE", "consecutive_failures": 1})
    assert event_effects.apply_effects(Db(), older) == []
    assert device.status == "up" and device.last_observed_at == now
    newer = new_event("device.poll.failed", source_service="collector", actor_id="collector", source_type="REAL",
                      device_id="dev", occurred_at=now+timedelta(minutes=1),
                      payload={"collector": "scheduled", "failure_category": "UNREACHABLE", "consecutive_failures": 1})
    event_effects.apply_effects(Db(), newer)
    assert device.last_observed_at == now+timedelta(minutes=1)


def test_cx_login_rejects_redirect_html_and_no_cookie():
    for response in (
        lambda: httpx.Response(302, headers={"location": "/login"}),
        lambda: httpx.Response(200, headers={"content-type": "text/html"}, text="<form>login</form>"),
        lambda: httpx.Response(200, json={"status": "OK"}),
    ):
        sess = CxSession("192.0.2.1", "user", "test-secret", transport=httpx.MockTransport(lambda _: response()))
        with pytest.raises(WriterError):
            sess.open()
        assert not sess._open
        sess.close()


def test_cx_form_body_and_secret_redaction():
    seen = []
    def transport(request):
        seen.append(request)
        return httpx.Response(200, headers={"x-csrf-token": "csrf-secret", "set-cookie": "session=abc; Path=/"})
    sess = CxSession("192.0.2.1", "user", "password-secret", transport=httpx.MockTransport(transport))
    try:
        sess.open()
        assert sess._open
        assert "password-secret" not in str(seen[0].url)
        assert b"password-secret" in seen[0].content
        assert "application/x-www-form-urlencoded" in seen[0].headers["content-type"]
    finally:
        sess.close()
    for raw in ('{"password":"password-secret", "x-csrf-token":"csrf-secret"}',
                'password=password-secret&token=csrf-secret',
                'Authorization: password-secret'):
        redacted = redact(raw)
        assert "password-secret" not in redacted, redacted
        assert "csrf-secret" not in redacted, redacted


def test_channel_target_masked_and_loopback_webhook_refused():
    from backend.api.notifications import _ch
    target = "https://hooks.example.org/path/sensitive-token?token=query-secret"
    out = _ch(NotificationChannel(id="1", name="t", kind="webhook", target=target,
                                  min_severity="major", created_by="a"))
    assert "sensitive-token" not in out["target"] and "query-secret" not in out["target"]
    with pytest.raises(notifications.ChannelError):
        notifications.validate_channel("webhook", "http://127.0.0.1:8080/internal", "major")


def test_invalidation_only_after_database_commit(tmp_path, monkeypatch):
    Session = _factory(tmp_path, EventOutbox)
    e = new_event("synthetic.probe", source_service="test", actor_id="test", source_type="REAL",
                  payload={"summary": "ok"})
    with Session.begin() as db:
        outbox.enqueue(db, e)
    notifications_at_commit = []
    def listener(topics, correlation_id, event_id):
        with Session() as db:
            notifications_at_commit.append(db.get(EventOutbox, event_id).state)
    monkeypatch.setattr(outbox, "_ingest_inprocess", lambda db, ev: SimpleNamespace(topics=["devices"]))
    outbox.set_ui_notifier(listener)
    try:
        with Session.begin() as db:
            outbox.deliver_row(db, db.get(EventOutbox, e.event_id))
            assert notifications_at_commit == []
        assert notifications_at_commit == ["DELIVERED"]
    finally:
        outbox.set_ui_notifier(None)


def test_scoped_users_cannot_see_other_site_workflows(tmp_path):
    from fastapi import HTTPException
    from backend.api.workflows import get_workflow, list_workflows
    Session = _factory(tmp_path, OnboardingWorkflow, SiteAccessGrant)
    with Session.begin() as db:
        db.add_all([
            OnboardingWorkflow(id="aaaa0000-0000-0000-0000-000000000001", title="Secret B", site_id="site-b",
                               site_name="Site B", created_by="staff", step="SITE", state="ACTIVE"),
            OnboardingWorkflow(id="aaaa0000-0000-0000-0000-000000000002", title="Site A", site_id="site-a",
                               site_name="Site A", created_by="staff", step="SITE", state="ACTIVE"),
        ])
    scoped = SimpleNamespace(id=42, username="operator", role="operator", site_scope="granted")
    # No grant -> no physical sites: a workflow list cannot leak names or device counts.
    with Session() as db:
        assert list_workflows(db=db, user=scoped) == []
        with pytest.raises(HTTPException) as e:
            get_workflow("aaaa0000-0000-0000-0000-000000000001", db=db, user=scoped)
        assert e.value.status_code == 404


def test_workflow_not_done_until_every_device_accounted_for():
    from backend.api.workflows import _plans_fully_applied
    from backend.database.models_phases import ChangePlan
    plan = SimpleNamespace(id="p1", device_id="a", state="APPLIED_VERIFIED")
    class Store:
        def get(self, cls, key):
            return plan if cls is ChangePlan and key == "p1" else None
    workflow = SimpleNamespace(mode="NOW", device_ids=["a", "b"], plan_ids=["p1"], outcome={})
    assert not _plans_fully_applied(Store(), workflow)
    workflow.outcome = {"already_compliant": ["b"]}
    assert _plans_fully_applied(Store(), workflow)
    plan.state = "FAILED_ROLLED_BACK"
    assert not _plans_fully_applied(Store(), workflow)
    plan.state = "APPLIED_VERIFIED"
    workflow.plan_ids = ["p1", "p1"]
    assert not _plans_fully_applied(Store(), workflow)


def test_webhook_error_cannot_expose_secret_url_token():
    from backend.infrastructure.notifications import _public_send_error
    ch = NotificationChannel(kind="webhook", target="https://hooks.example.org/SECRET123?token=QUERY456")
    exc = httpx.ConnectError("Error connecting to https://hooks.example.org/SECRET123?token=QUERY456")
    out = _public_send_error(ch, exc)
    assert "SECRET123" not in out and "QUERY456" not in out
    assert "webhook delivery failed" in out


def test_cx_strict_emulator_accepts_form_body_with_csrf_and_reauthentication():
    from backend.infrastructure.writers.emulator import CxEmulator
    emu = CxEmulator(model="CX 6200F 24G", firmware="ML.10.14.1020", ports=24)
    w = emu.writer()
    w.open()
    assert w.session.csrf
    assert w.identity()["csrf"] is True
    assert emu.violations == []
    emu.expire_sessions()
    assert w.identity()["csrf"] is True
    assert emu.logins == 2
    w.close()
    assert emu.violations == []


def test_deferred_configuration_resumes_at_preview_without_implicit_writes(monkeypatch):
    from backend.api import workflows
    from fastapi import HTTPException
    w = SimpleNamespace(id="w1", mode="LATER", step="MONITOR", state="WAITING", plan_ids=[], history=[],
                        updated_at=datetime.now(timezone.utc))
    monkeypatch.setattr(workflows, "_wf", lambda *_args: w)
    monkeypatch.setattr(workflows, "_view", lambda db, item: {"state": item.state, "step": item.step, "mode": item.mode})
    admin = SimpleNamespace(username="admin")
    out = workflows.wf_resume("w1", db=object(), user=admin)
    assert out == {"state": "ACTIVE", "step": "PREVIEW", "mode": None}
    assert w.history[-1]["step"] == "PREVIEW"
    with pytest.raises(HTTPException) as e:
        workflows.wf_resume("w1", db=object(), user=admin)
    assert e.value.status_code == 409


def test_notification_retry_checks_site_scope_first(monkeypatch):
    from backend.api import notifications as api
    from backend.database.models_phases import InmAlert
    from fastapi import HTTPException
    delivery = NotificationDelivery(id="del-1", kind="OPENED", state="DEAD", alert_id="a1")
    alert = InmAlert(id="a1", site_id="other-site")
    class Db:
        def get(self, cls, key):
            return delivery if cls is NotificationDelivery else alert if cls is InmAlert else None
    called = []
    def deny(db, user, site):
        called.append(site)
        raise HTTPException(404, "No access")
    from backend.api import workflows
    monkeypatch.setattr(workflows, "_require_site", deny)
    with pytest.raises(HTTPException) as e:
        api.retry_delivery("del-1", request=None, db=Db(), user=SimpleNamespace(username="scoped", role="operator"))
    assert e.value.status_code == 404 and called == ["other-site"]
    assert delivery.state == "DEAD"
