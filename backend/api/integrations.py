"""Integrations module (rc18, directive 2.10): one place to configure every connection to an external system.

  connections   SMTP e-mail, webhook (none / bearer / HMAC), Microsoft Teams (Workflows webhook, Adaptive Card),
                syslog (UDP / TCP), external-monitoring heartbeat push. Credentials are write-only and Fernet-
                encrypted; connection tests report the real outcome; enable/disable; delivery status, retries
                and DEAD letters per connection; every change is audited.
  REST API      read-only, expiring, revocable API tokens (shown once, stored as SHA-256)
  monitoring    Prometheus text endpoint /api/v1/metrics (token with the metrics scope, or an admin session),
                off until an administrator enables it; no cloud dependency.
Alert routing (severity floor, escalation) stays on the Notifications screen.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database.models import AppSetting, AuditLog, User
from ..database.models_rc18 import ApiToken
from ..database.models_remediation import NotificationChannel, NotificationDelivery
from ..database.session import get_db
from ..infrastructure import notifications as N
from ..security import api_tokens as AT
from ..security.crypto import encrypt_secret
from ..security.deps import audit, get_current_user, require_any_role, require_role
from ._phase_common import iso

router = APIRouter(prefix="/api/v1/integrations", tags=["integrations"])
metrics_router = APIRouter(tags=["integrations"])
MONITORING_KEY = "integrations:monitoring"
admin = Depends(require_role("admin"))
KIND_LABEL = {"email": "SMTP e-mail", "webhook": "Webhook", "teams": "Microsoft Teams", "syslog": "Syslog",
              "heartbeat": "External monitoring (heartbeat)"}


class ConnectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str
    target: str = Field(min_length=1, max_length=300)
    config: dict = Field(default_factory=dict)
    secret: str | None = Field(default=None, max_length=4096)      # write-only
    clear_secret: bool = False
    min_severity: str = "major"
    include_demo: bool = False
    enabled: bool = True


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["read"])
    days: int = Field(default=90, ge=1, le=AT.MAX_DAYS)


class MonitoringIn(BaseModel):
    metrics_enabled: bool


def _stats(db: Session, cid: str) -> dict:
    rows = dict(db.execute(select(NotificationDelivery.state, func.count()).where(NotificationDelivery.channel_id == cid)
                           .group_by(NotificationDelivery.state)).all())
    last = db.scalar(select(NotificationDelivery).where(NotificationDelivery.channel_id == cid)
                     .order_by(NotificationDelivery.created_at.desc()).limit(1))
    return {"by_state": rows, "last_delivery_at": iso(last.created_at) if last else None,
            "last_error": last.last_error if last and last.state in ("DEAD", "PENDING") else None}


def _conn(db: Session, c: NotificationChannel) -> dict:
    from .notifications import _ch
    out = _ch(c)
    if c.kind in ("teams", "heartbeat"):                          # mask path/query like webhooks
        from urllib.parse import urlsplit
        u = urlsplit(c.target)
        out["target"] = f"{u.scheme}://{u.hostname}" + ("/[redacted]" if u.path not in ("", "/") or u.query else "")
    cfg = dict(c.config or {})
    health = ("DISABLED" if not c.enabled else "UNTESTED" if c.last_test_ok is None
              else "OK" if c.last_test_ok else "FAILING")
    return out | {"kind_label": KIND_LABEL.get(c.kind, c.kind), "config": cfg, "secret_configured": bool(c.secret_enc),
                  "last_test_at": iso(c.last_test_at), "last_test_ok": c.last_test_ok, "health": health,
                  "deliveries": _stats(db, c.id)}


def _get(db: Session, cid: str) -> NotificationChannel:
    c = db.get(NotificationChannel, cid)
    if c is None:
        raise HTTPException(404, "Connection not found.")
    return c


def _apply(db: Session, c: NotificationChannel, body: ConnectionIn, creating: bool) -> None:
    if body.target.endswith("/[redacted]"):
        raise HTTPException(422, "Re-enter the complete destination URL.")
    has_secret = bool(body.secret) or (bool(c.secret_enc) and not body.clear_secret and not creating)
    try:
        N.validate_channel(body.kind, body.target, body.min_severity)
        cfg = N.validate_config(body.kind, body.config, has_secret)
    except (N.ChannelError, ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None
    if not creating and c.kind != body.kind:
        raise HTTPException(422, "The connection type cannot change; create a new connection.")
    c.name, c.kind, c.target, c.config = body.name, body.kind, body.target.strip(), cfg
    c.min_severity, c.include_demo, c.enabled = body.min_severity, body.include_demo, body.enabled
    if body.secret:
        c.secret_enc = encrypt_secret(body.secret)
        c.last_test_at = c.last_test_ok = None                    # a new credential must be re-tested
    elif body.clear_secret:
        c.secret_enc = None


@router.get("")
def overview(db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor"))):
    conns = [_conn(db, c) for c in db.scalars(select(NotificationChannel).order_by(NotificationChannel.kind,
                                                                                    NotificationChannel.name))]
    mon = db.get(AppSetting, MONITORING_KEY)
    return {"connections": conns, "kinds": KIND_LABEL,
            "api_tokens": [AT.public(t) for t in db.scalars(select(ApiToken).order_by(ApiToken.created_at.desc()))],
            "monitoring": {"metrics_enabled": bool(mon and mon.value.get("metrics_enabled")), "path": AT.METRICS_PATH,
                           "auth": "Bearer API token with the 'metrics' scope (or an administrator session)"},
            "policy": "Credentials are write-only and encrypted at rest. API tokens are read-only and cannot reach "
                      "credentials, users, tokens or integrations. No cloud service is required."}


@router.post("/connections", status_code=201)
def create_connection(body: ConnectionIn, request: Request, db: Session = Depends(get_db), user: User = admin):
    if db.scalar(select(NotificationChannel.id).where(NotificationChannel.name == body.name)):
        raise HTTPException(409, "A connection with this name exists.")
    c = NotificationChannel(created_by=user.username, name=body.name, kind=body.kind, target=body.target)
    _apply(db, c, body, creating=True)
    db.add(c)
    db.flush()
    audit(db, "integration_created", username=user.username, target=c.id,
          detail=f"{c.kind} {c.name} secret={'set' if c.secret_enc else 'none'}", request=request)
    return _conn(db, c)


@router.put("/connections/{cid}")
def update_connection(cid: str, body: ConnectionIn, request: Request, db: Session = Depends(get_db), user: User = admin):
    c = _get(db, cid)
    rotated = bool(body.secret)
    _apply(db, c, body, creating=False)
    audit(db, "integration_updated", username=user.username, target=c.id,
          detail=f"{c.kind} {c.name}" + (" secret rotated" if rotated else " secret cleared" if body.clear_secret else ""),
          request=request)
    return _conn(db, c)


@router.post("/connections/{cid}/{action}")
def connection_action(cid: str, action: str, request: Request, db: Session = Depends(get_db), user: User = admin):
    c = _get(db, cid)
    if action in ("enable", "disable"):
        c.enabled = action == "enable"
        audit(db, f"integration_{action}d", username=user.username, target=c.id, detail=c.name, request=request)
        return _conn(db, c)
    if action == "test":
        out = N.test_send(db, c, user.username) if c.kind != "heartbeat" else _heartbeat_test(db, c)
        audit(db, "integration_tested", username=user.username, target=c.id, success=bool(out["delivered"]),
              detail="delivered" if out["delivered"] else f"failed: {out['error']}", request=request)
        return out | {"connection": _conn(db, c)}
    raise HTTPException(404, "Unknown action.")


def _heartbeat_test(db: Session, c: NotificationChannel) -> dict:
    now = datetime.now(timezone.utc)
    try:
        N.send(c, N.heartbeat_summary(db))
        c.last_test_at, c.last_test_ok = now, True
        return {"delivered": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        c.last_test_at, c.last_test_ok = now, False
        return {"delivered": False, "error": N._public_send_error(c, exc)}


@router.delete("/connections/{cid}", status_code=204)
def delete_connection(cid: str, request: Request, db: Session = Depends(get_db), user: User = admin):
    c = _get(db, cid)
    for other in db.scalars(select(NotificationChannel).where(NotificationChannel.escalation_channel_id == cid)):
        other.escalation_channel_id, other.escalate_after_minutes = None, None
    db.delete(c)
    audit(db, "integration_deleted", username=user.username, target=cid, detail=c.name, request=request)


@router.get("/connections/{cid}/deliveries")
def connection_deliveries(cid: str, state: str | None = None, db: Session = Depends(get_db),
                          user: User = Depends(require_any_role("admin", "auditor"))):
    _get(db, cid)
    from .notifications import _dl
    q = select(NotificationDelivery).where(NotificationDelivery.channel_id == cid) \
        .order_by(NotificationDelivery.created_at.desc()).limit(200)
    if state:
        q = q.where(NotificationDelivery.state == state)
    return [_dl(d) for d in db.scalars(q)]


@router.post("/deliveries/{did}/retry")
def retry(did: str, request: Request, db: Session = Depends(get_db), user: User = admin):
    """Requeue a DEAD delivery (alert or business event) for another bounded set of attempts."""
    d = db.get(NotificationDelivery, did)
    if d is None or d.state != "DEAD" or d.kind in ("TEST", "HEARTBEAT"):
        raise HTTPException(409, "Only a DEAD alert or event notification can be retried.")
    d.state, d.attempts, d.next_attempt_at = "PENDING", 0, None
    audit(db, "integration_delivery_retry", username=user.username, target=d.id, request=request)
    from .notifications import _dl
    return _dl(d)


@router.get("/audit")
def integration_audit(db: Session = Depends(get_db), user: User = Depends(require_any_role("admin", "auditor"))):
    prefixes = ("integration_", "notification_channel_", "api_token_", "notification_retry", "metrics_endpoint_")
    from sqlalchemy import or_
    q = select(AuditLog).where(or_(*[AuditLog.action.like(p + "%") for p in prefixes])) \
        .order_by(AuditLog.id.desc()).limit(300)
    rows = list(db.scalars(q))
    return [{"at": iso(r.ts), "action": r.action,
             "by": r.username, "target": r.target, "detail": r.detail, "success": r.success} for r in rows]


# --------------------------------------------------------------------------------------------- API tokens
@router.post("/api-tokens", status_code=201)
def create_token(body: TokenIn, request: Request, db: Session = Depends(get_db), user: User = admin):
    if db.scalar(select(ApiToken.id).where(ApiToken.name == body.name)):
        raise HTTPException(409, "A token with this name exists.")
    try:
        row, token = AT.issue(db, name=body.name, scopes=body.scopes, owner=user, actor=user.username, days=body.days)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    audit(db, "api_token_created", username=user.username, target=row.id,
          detail=f"{row.name} {row.prefix} scopes={row.scopes} expires={iso(row.expires_at)}", request=request)
    return AT.public(row) | {"token": token, "note": "Copy the token now; it is not shown again."}


@router.delete("/api-tokens/{tid}")
def revoke_token(tid: str, request: Request, db: Session = Depends(get_db), user: User = admin):
    row = db.get(ApiToken, tid)
    if row is None:
        raise HTTPException(404, "Token not found.")
    if row.revoked_at is None:
        row.revoked_at, row.revoked_by, row.enabled = datetime.now(timezone.utc), user.username, False
        audit(db, "api_token_revoked", username=user.username, target=row.id, detail=row.name, request=request)
    return AT.public(row)


# --------------------------------------------------------------------------------------------- monitoring
@router.put("/monitoring")
def set_monitoring(body: MonitoringIn, request: Request, db: Session = Depends(get_db), user: User = admin):
    row = db.get(AppSetting, MONITORING_KEY)
    if row is None:
        db.add(AppSetting(key=MONITORING_KEY, value={"metrics_enabled": body.metrics_enabled}, updated_by=user.username))
    else:
        row.value, row.updated_by = {"metrics_enabled": body.metrics_enabled}, user.username
    audit(db, "metrics_endpoint_" + ("enabled" if body.metrics_enabled else "disabled"), username=user.username,
          request=request)
    return {"metrics_enabled": body.metrics_enabled}


def _esc(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_metrics(db: Session) -> str:
    from ..database.models_inm import InmDevice
    from ..database.models_phases import InmAlert, MlModel
    from ..ml.service import mode_of
    import json as _json
    from pathlib import Path as _Path
    try:
        version = _json.loads((_Path(__file__).resolve().parents[2] / "release.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        version = "unknown"
    lines: list[str] = []

    def metric(name: str, help_: str, kind: str, samples: list[tuple[dict, float]]):
        lines.append(f"# HELP {name} {help_}")
        lines.append(f"# TYPE {name} {kind}")
        for labels, value in samples:
            lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels.items())
            lines.append(f"{name}{{{lab}}} {value}" if lab else f"{name} {value}")
    metric("inm_build_info", "Build information", "gauge", [({"version": version}, 1)])
    metric("inm_devices", "Managed devices by source, family and status", "gauge",
           [({"source": s, "family": f, "status": st}, n) for s, f, st, n in db.execute(
               select(InmDevice.source_type, InmDevice.family, InmDevice.status, func.count())
               .group_by(InmDevice.source_type, InmDevice.family, InmDevice.status)).all()])
    metric("inm_alerts_open", "Open alerts by severity", "gauge",
           [({"severity": s}, n) for s, n in db.execute(select(InmAlert.severity, func.count())
                                                       .where(InmAlert.state == "OPEN").group_by(InmAlert.severity)).all()])
    metric("inm_notification_deliveries", "Notification deliveries by state", "gauge",
           [({"state": s}, n) for s, n in db.execute(select(NotificationDelivery.state, func.count())
                                                    .group_by(NotificationDelivery.state)).all()])
    metric("inm_integration_connection_up", "Last connection test or heartbeat succeeded (1) or failed (0)", "gauge",
           [({"name": c.name, "kind": c.kind}, 1 if c.last_test_ok else 0) for c in
            db.scalars(select(NotificationChannel).where(NotificationChannel.enabled.is_(True),
                                                         NotificationChannel.last_test_ok.is_not(None)))])
    metric("inm_ml_model_primary", "Promoted trained model drives predictions (1) or not (0)", "gauge",
           [({"target": m.target, "model": m.name, "mode": mode_of(db, m)[0]}, 1 if mode_of(db, m)[0] == "PRIMARY" else 0)
            for m in db.scalars(select(MlModel).where(MlModel.status == "PROMOTED", MlModel.target.is_not(None)))])
    return "\n".join(lines) + "\n"


@metrics_router.get(AT.METRICS_PATH, response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db), user: User = Depends(get_current_user), request: Request = None):
    row = db.get(AppSetting, MONITORING_KEY)
    if not (row and row.value.get("metrics_enabled")):
        raise HTTPException(404, "The metrics endpoint is disabled (Integrations → External monitoring).")
    via_token = isinstance(getattr(request.state, "token_payload", None), dict) and "api_token" in request.state.token_payload
    if not via_token and user.role != "admin":
        raise HTTPException(403, "Use an API token with the metrics scope, or an administrator session.")
    return PlainTextResponse(render_metrics(db), media_type="text/plain; version=0.0.4")
