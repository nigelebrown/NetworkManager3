"""FastAPI dependencies: authentication, RBAC, rate limiting, audit helper."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import timezone
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from starlette.requests import HTTPConnection
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AuditLog, User
from ..database.models_inm import AuditChainHead, RevokedToken, SiteAccessGrant
from ..database.session import get_db
from .context import Actor, get_actor, get_request_id, session_hash, set_actor, set_scope
from .crypto import decode_access_token

_bearer = HTTPBearer(auto_error=False)

# auditor / approver have viewer-level data reads plus their own endpoints (audit explorer; approvals in Phase 4).
ROLE_RANK = {"viewer": 1, "auditor": 1, "approver": 1, "operator": 2, "admin": 3}
ROLES = tuple(ROLE_RANK)

# Paths a user who must change their password may still call.
_PASSWORD_CHANGE_ALLOWED = {"/api/auth/me", "/api/auth/change-password", "/api/auth/logout"}


def resolve_user(db: Session, token: str) -> tuple[User, dict]:
    """Validate a bearer token (signature, expiry, revocation, active user). Used by REST and WebSocket."""
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")
    jti = payload.get("jti")
    if jti and db.get(RevokedToken, jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session has been signed out")
    user = db.scalar(select(User).where(User.username == payload.get("sub")))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired session")
    return user, payload


def user_site_scope(db: Session, user: User) -> frozenset[int] | None:
    if user.role == "admin" or (user.site_scope or "all") == "all":
        return None
    return frozenset(db.scalars(select(SiteAccessGrant.site_id).where(SiteAccessGrant.user_id == user.id)))


def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user, payload = resolve_user(db, creds.credentials)
    set_actor(Actor(user.username, user.role, session_hash(payload.get("jti"))))
    set_scope(user_site_scope(db, user))
    request.state.token_payload = payload
    if user.must_change_password and request.url.path not in _PASSWORD_CHANGE_ALLOWED:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "password_change_required")
    return user


def require_role(minimum: str):
    def checker(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[minimum]:
            _deny(db, request, user, f"requires role {minimum}")
        return user
    return checker


def require_any_role(*roles: str):
    """Exact-role check for non-hierarchical duties (e.g. audit explorer = admin or auditor)."""
    def checker(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        if user.role not in roles:
            _deny(db, request, user, f"requires one of {','.join(roles)}")
        return user
    return checker


def _deny(db: Session, request: Request, user: User, why: str) -> None:
    """403 and an audited denial (P1-06). Committed immediately because the request is about to fail."""
    audit(db, "access_denied", username=user.username, target=f"{request.method} {request.url.path}",
          detail=why, request=request, success=False)
    db.commit()
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")


# ---- rate limiting (in-memory sliding window; fine for a single-process deployment) ----
class RateLimiter:
    def __init__(self, limit: int, window_seconds: int):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


login_limiter = RateLimiter(limit=8, window_seconds=60)
api_limiter = RateLimiter(limit=300, window_seconds=60)


def client_ip(request: HTTPConnection) -> str:
    return request.client.host if request.client else "unknown"


def rate_limit_api(conn: HTTPConnection) -> None:
    # HTTPConnection (not Request) so the app-wide dependency also resolves on WebSocket routes.
    if not api_limiter.check(client_ip(conn)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests")


# ---- audit ------------------------------------------------------------------
GENESIS_HASH = "0" * 64
_chain_lock = Lock()


def _utc_iso(dt) -> str | None:
    if dt is None:
        return None
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite returns naive UTC
    return dt.astimezone(timezone.utc).isoformat()


def _canonical(row: AuditLog) -> str:
    import json
    return json.dumps({
        "ts": _utc_iso(row.ts),
        "username": row.username, "action": row.action, "target": row.target, "detail": row.detail,
        "source_ip": row.source_ip, "success": bool(row.success), "request_id": row.request_id,
        "correlation_id": row.correlation_id, "actor_role": row.actor_role, "actor_kind": row.actor_kind,
        "session_id_hash": row.session_id_hash, "channel": row.channel, "prev_hash": row.prev_hash,
    }, sort_keys=True, separators=(",", ":"))


def compute_event_hash(row: AuditLog) -> str:
    import hashlib
    return hashlib.sha256(_canonical(row).encode()).hexdigest()


def audit(
    db: Session,
    action: str,
    *,
    username: str | None = None,
    target: str | None = None,
    detail: str | None = None,
    request: Request | None = None,
    success: bool = True,
    channel: str = "APP",
    actor_kind: str | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    """Append a hash-chained audit entry (AuditEvent v1). Callers must never pass secrets in `detail`.

    The chain head row is locked (SELECT ... FOR UPDATE on PostgreSQL; a process lock elsewhere) so concurrent
    writers cannot fork the chain. Each row stores prev_hash and event_hash = sha256(canonical fields).
    """
    from datetime import datetime
    actor = get_actor()
    row = AuditLog(
        ts=datetime.now(timezone.utc),
        username=username if username is not None else (actor.username if actor else None),
        action=action, target=target, detail=detail,
        source_ip=client_ip(request) if request else None,
        success=success,
        request_id=get_request_id(),
        correlation_id=correlation_id,
        actor_role=actor.role if actor and (username is None or username == actor.username) else None,
        actor_kind=actor_kind or ("human" if (actor or username) else "service"),
        session_id_hash=actor.session_id_hash if actor and (username is None or username == actor.username) else None,
        channel=channel,
    )
    with _chain_lock:
        head = db.scalar(select(AuditChainHead).where(AuditChainHead.id == 1).with_for_update())
        if head is None:
            head = AuditChainHead(id=1, last_hash=GENESIS_HASH)
            db.add(head)
        row.prev_hash = head.last_hash
        row.event_hash = compute_event_hash(row)
        db.add(row)
        db.flush()
        head.last_hash, head.last_audit_id = row.event_hash, row.id
    return row


def verify_audit_chain(db: Session) -> dict:
    """Recompute every chained row in id order. Legacy (pre-INM) rows are reported, not chained."""
    prev = GENESIS_HASH
    checked = legacy = 0
    for row in db.scalars(select(AuditLog).order_by(AuditLog.id)):
        if row.event_hash is None:
            legacy += 1
            continue
        if row.prev_hash != prev:
            return {"ok": False, "checked": checked, "legacy_unchained": legacy, "broken_at_id": row.id,
                    "reason": "prev_hash does not match the previous entry (row removed or reordered)"}
        if compute_event_hash(row) != row.event_hash:
            return {"ok": False, "checked": checked, "legacy_unchained": legacy, "broken_at_id": row.id,
                    "reason": "content does not match its hash (row modified)"}
        prev = row.event_hash
        checked += 1
    head = db.get(AuditChainHead, 1)
    if head and head.last_hash != prev:
        return {"ok": False, "checked": checked, "legacy_unchained": legacy, "broken_at_id": None,
                "reason": "chain head does not match the last entry (tail truncated)"}
    return {"ok": True, "checked": checked, "legacy_unchained": legacy, "head": prev}
