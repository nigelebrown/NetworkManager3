"""Alert notifications and escalation (E2E-20).

sweep() is idempotent and safe to run from every scheduler/worker replica:
  1. enqueue: every OPEN, unsuppressed alert at or above a channel's severity floor gets one OPENED delivery per
     channel; every RESOLVED alert that was notified gets one RESOLVED delivery (unique dedupe_key).
  2. escalate: an alert still OPEN (not ACKNOWLEDGED) escalate_after_minutes after it opened gets one ESCALATED
     delivery on the channel's escalation channel. Acknowledging the alert stops escalation.
  3. deliver: PENDING deliveries are sent; failures retry with exponential backoff and become DEAD after
     MAX_ATTEMPTS — visible in the UI, never silently dropped, never reported as sent.

Channels never hold credentials: webhook URLs with userinfo are refused; email uses the operator-configured SMTP
relay (INM_SMTP_HOST/INM_SMTP_PORT/INM_SMTP_FROM); syslog is RFC 5424 over UDP.
"""
from __future__ import annotations

import json
import ipaddress
import uuid
import os
import smtplib
import socket
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urlparse

import httpx
from sqlalchemy import or_, and_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..database.models_phases import InmAlert
from ..database.models_remediation import NotificationChannel, NotificationDelivery

SEVERITY_RANK = {"info": 0, "minor": 1, "major": 2, "critical": 3}
KINDS = ("webhook", "email", "syslog")
MAX_ATTEMPTS = 6
TIMEOUT_S = 5.0


class ChannelError(ValueError):
    pass


def _aware(t: datetime | None) -> datetime | None:
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t


def validate_channel(kind: str, target: str, min_severity: str) -> None:
    if kind not in KINDS:
        raise ChannelError(f"kind must be one of {KINDS}")
    if min_severity not in SEVERITY_RANK:
        raise ChannelError(f"min_severity must be one of {tuple(SEVERITY_RANK)}")
    target = (target or "").strip()
    if not target:
        raise ChannelError("target is required")
    if kind == "webhook":
        u = urlparse(target)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ChannelError("webhook target must be an http(s) URL")
        if u.username or u.password:
            raise ChannelError("credentials in the webhook URL are not allowed")
        _safe_webhook_destination(u)
    elif kind == "email":
        addrs = [a.strip() for a in target.split(",") if a.strip()]
        if not addrs or not all("@" in a and " " not in a for a in addrs):
            raise ChannelError("email target must be one or more comma-separated addresses")
    else:
        host, _, port = target.rpartition(":")
        if not host or not port.isdigit() or not 0 < int(port) < 65536:
            raise ChannelError("syslog target must be host:port")


def _safe_webhook_destination(u) -> None:
    """Reject local/metadata/private targets unless the operator explicitly authorizes an on-prem receiver.

    Protect the application against accidental SSRF; deployment egress policy must independently constrain
    DNS rebinding and cross-network redirects. No implicit HTTP->HTTPS or redirect following is permitted.
    """
    from urllib.parse import urlsplit
    if isinstance(u, str):
        u = urlsplit(u)
    host = (u.hostname or "").lower().rstrip(".")
    allowed = {x.strip().lower() for x in os.getenv("INM_NOTIFICATION_ALLOWED_HOSTS", "").split(",") if x.strip()}
    if host in allowed:
        return  # explicit operator-approved on-prem integration
    if u.scheme != "https":
        raise ChannelError("webhooks require HTTPS unless the exact host is allowlisted")
    if host in ("localhost", "localhost.localdomain") or host.endswith((".localhost", ".local", ".internal")):
        raise ChannelError("local or internal webhook destinations require an explicit host allowlist")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return  # DNS is also checked immediately before sending
    if not addr.is_global:
        raise ChannelError("non-public webhook addresses require an explicit host allowlist")


def _validate_webhook_dns(target: str) -> None:
    from urllib.parse import urlsplit
    u = urlsplit(target)
    _safe_webhook_destination(u)
    host = (u.hostname or "").lower().rstrip(".")
    if host in {x.strip().lower() for x in os.getenv("INM_NOTIFICATION_ALLOWED_HOSTS", "").split(",") if x.strip()}:
        return
    try:
        hosts = {x[4][0] for x in socket.getaddrinfo(host, u.port or 443, type=socket.SOCK_STREAM)}
    except OSError:
        raise ChannelError("webhook DNS lookup failed") from None
    if not hosts or any(not ipaddress.ip_address(h).is_global for h in hosts):
        raise ChannelError("webhook DNS resolved to a forbidden network address")


def payload_for(a: InmAlert, kind: str) -> dict:
    return {"schema": "inm.alert.notification/v1", "notification": kind, "alert_id": a.id, "rule_id": a.rule_id,
            "severity": a.severity, "state": a.state, "message": a.message, "device_id": a.device_id,
            "site_id": a.site_id, "entity": a.entity, "value": a.value, "threshold": a.threshold,
            "opened_at": _aware(a.opened_at).isoformat() if a.opened_at else None,
            "resolved_at": _aware(a.resolved_at).isoformat() if a.resolved_at else None,
            "acked_by": a.acked_by, "source_type": a.source_type, "correlation_id": a.correlation_id}


def _enqueue(db: Session, ch: NotificationChannel, a: InmAlert | None, kind: str, payload: dict,
             now: datetime, key: str) -> bool:
    if db.scalar(select(NotificationDelivery.id).where(NotificationDelivery.dedupe_key == key)):
        return False
    try:
        with db.begin_nested():
            db.add(NotificationDelivery(dedupe_key=key, channel_id=ch.id, alert_id=a.id if a else None, kind=kind,
                                        payload=payload, state="PENDING", attempts=0, created_at=now,
                                        next_attempt_at=now, correlation_id=a.correlation_id if a else None))
    except IntegrityError:            # another replica enqueued it first
        return False
    return True


def _eligible(ch: NotificationChannel, a: InmAlert) -> bool:
    if a.suppressed or (a.source_type == "DEMO" and not ch.include_demo):
        return False
    return SEVERITY_RANK.get(a.severity, 0) >= SEVERITY_RANK[ch.min_severity]


def enqueue_and_escalate(db: Session, now: datetime) -> dict:
    out = {"enqueued": 0, "escalated": 0}
    channels = list(db.scalars(select(NotificationChannel).where(NotificationChannel.enabled.is_(True))))
    if not channels:
        return out
    by_id = {c.id: c for c in channels}
    horizon = now - timedelta(days=2)
    alerts = list(db.scalars(select(InmAlert).where(
        (InmAlert.state.in_(("OPEN", "ACKNOWLEDGED"))) | (InmAlert.resolved_at >= horizon))))
    for a in alerts:
        for ch in channels:
            if not _eligible(ch, a):
                continue
            opened_key = f"{a.id}:{ch.id}:OPENED"
            if a.state in ("OPEN", "ACKNOWLEDGED"):
                out["enqueued"] += _enqueue(db, ch, a, "OPENED", payload_for(a, "OPENED"), now, opened_key)
                esc = by_id.get(ch.escalation_channel_id or "")
                if (a.state == "OPEN" and ch.escalate_after_minutes and esc is not None
                        and _aware(a.opened_at) <= now - timedelta(minutes=ch.escalate_after_minutes)):
                    out["escalated"] += _enqueue(db, esc, a, "ESCALATED", payload_for(a, "ESCALATED"), now,
                                                 f"{a.id}:{ch.id}->{esc.id}:ESCALATED")
            elif a.state == "RESOLVED" and db.scalar(select(NotificationDelivery.id).where(
                    NotificationDelivery.dedupe_key == opened_key)):
                # only tell a channel about a resolution it was told about in the first place
                out["enqueued"] += _enqueue(db, ch, a, "RESOLVED", payload_for(a, "RESOLVED"), now,
                                            f"{a.id}:{ch.id}:RESOLVED")
    db.flush()
    return out


def _subject(p: dict) -> str:
    return f"[INM {p['severity'].upper()}] {p['notification']}: {p['message']}"[:200]


def _public_send_error(ch: NotificationChannel, exc: Exception) -> str:
    """Never persist a webhook exception's URL, which can contain an API token in its path or query."""
    if ch.kind == "webhook":
        # The numerical HTTP status is safe and operationally useful; URLs, paths,
        # query strings and exception bodies may hold secrets and stay redacted.
        import re
        status = re.fullmatch(r"webhook answered HTTP ([3-5][0-9]{2})", str(exc)) \
            if isinstance(exc, RuntimeError) else None
        if status:
            return f"HTTP {status.group(1)}: webhook delivery failed; check receiver configuration and reachability"
        return f"{type(exc).__name__}: webhook delivery failed; check receiver configuration and reachability"
    return f"{type(exc).__name__}: {str(exc)[:240]}"[:300]


def send(ch: NotificationChannel, payload: dict) -> None:
    """Send one notification. Raises on any failure — callers never treat an exception as delivered."""
    if ch.kind == "webhook":
        _validate_webhook_dns(ch.target)
        r = httpx.post(ch.target, json={k: v for k, v in payload.items() if k != "delivery_key"},
                       timeout=TIMEOUT_S, follow_redirects=False,
                       headers={"User-Agent": "NetworkManager3-notifier", "X-INM-Notification": payload["notification"],
                                "X-INM-Idempotency-Key": str(payload.get("delivery_key") or payload.get("alert_id") or "test")})
        if r.status_code >= 300:
            raise RuntimeError(f"webhook answered HTTP {r.status_code}")
    elif ch.kind == "email":
        host = os.environ.get("INM_SMTP_HOST")
        if not host:
            raise RuntimeError("INM_SMTP_HOST is not configured")
        msg = EmailMessage()
        msg["From"] = os.environ.get("INM_SMTP_FROM", "inm@localhost")
        msg["To"] = ch.target
        msg["Subject"] = _subject(payload)
        msg.set_content(json.dumps(payload, indent=1))
        with smtplib.SMTP(host, int(os.environ.get("INM_SMTP_PORT", "25")), timeout=TIMEOUT_S) as s:
            if os.environ.get("INM_SMTP_STARTTLS", "false").lower() == "true":
                s.starttls()
            s.send_message(msg)
    else:
        host, _, port = ch.target.rpartition(":")
        sev = {"critical": 2, "major": 3, "minor": 4, "info": 6}.get(payload.get("severity", "info"), 6)
        line = (f"<{8 * 16 + sev}>1 {datetime.now(timezone.utc).isoformat()} inm networkmanager3 - "
                f"{payload['notification']} - {_subject(payload)} {json.dumps(payload, separators=(',', ':'))}")
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(line.encode()[:8000], (host, int(port)))


def deliver_due(db: Session, now: datetime, limit: int = 200) -> dict:
    """Claim before network I/O, so two replicas cannot send the same *pending attempt* concurrently.

    Receivers should deduplicate X-INM-Idempotency-Key: network acknowledgements can be lost after a
    successful external send; outbound delivery is at-least-once, not magically exactly-once.
    """
    from sqlalchemy.exc import OperationalError
    out = {"sent": 0, "retry": 0, "dead": 0}
    due = or_(
        and_(NotificationDelivery.state == "PENDING",
             or_(NotificationDelivery.next_attempt_at.is_(None), NotificationDelivery.next_attempt_at <= now)),
        and_(NotificationDelivery.state == "SENDING", NotificationDelivery.claim_expires_at <= now),
    )
    ids = list(db.scalars(select(NotificationDelivery.id).where(due)
                          .order_by(NotificationDelivery.created_at).limit(limit)))
    for did in ids:
        holder = uuid.uuid4().hex
        # UPDATE is the atomic claim. Commit before sending so other workers see SENDING.
        try:
            claimed = db.execute(update(NotificationDelivery).where(NotificationDelivery.id == did, due).values(
                state="SENDING", claim_owner=holder, claim_expires_at=now + timedelta(seconds=60),
                attempts=NotificationDelivery.attempts + 1, next_attempt_at=None,
            ).execution_options(synchronize_session=False)).rowcount
            if claimed != 1:
                db.rollback()
                continue
            db.commit()
        except OperationalError:
            db.rollback()
            continue
        d = db.get(NotificationDelivery, did)
        ch = db.get(NotificationChannel, d.channel_id)
        try:
            if ch is None or not ch.enabled:
                raise RuntimeError("channel removed or disabled")
            send(ch, {**d.payload, "delivery_key": d.dedupe_key})
            d.state, d.sent_at, d.last_error = "SENT", datetime.now(timezone.utc), None
            out["sent"] += 1
        except Exception as exc:  # failed attempts remain visible and retry with bounded backoff
            d.last_error = _public_send_error(ch, exc) if ch is not None else "CHANNEL_UNAVAILABLE"
            if d.attempts >= MAX_ATTEMPTS:
                d.state = "DEAD"
                out["dead"] += 1
            else:
                d.state = "PENDING"
                d.next_attempt_at = now + timedelta(seconds=min(3600, 15 * 2 ** (d.attempts - 1)))
                out["retry"] += 1
        d.claim_owner, d.claim_expires_at = None, None
        db.commit()
    return out


def sweep(db: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {**enqueue_and_escalate(db, now), **deliver_due(db, now)}


def test_send(db: Session, ch: NotificationChannel, actor: str) -> dict:
    """Send a test notification synchronously and report what actually happened (no optimistic success)."""
    now = datetime.now(timezone.utc)
    payload = {"schema": "inm.alert.notification/v1", "notification": "TEST", "severity": "info",
               "message": f"Test notification requested by {actor}", "alert_id": None, "sent_at": now.isoformat()}
    d = NotificationDelivery(dedupe_key=f"test:{ch.id}:{now.timestamp()}", channel_id=ch.id, kind="TEST",
                             payload=payload, state="PENDING", attempts=1, created_at=now)
    db.add(d)
    try:
        send(ch, payload)
        d.state, d.sent_at = "SENT", now
    except Exception as exc:  # noqa: BLE001
        d.state, d.last_error = "DEAD", _public_send_error(ch, exc)
    db.flush()
    return {"delivered": d.state == "SENT", "error": d.last_error, "delivery_id": d.id}
