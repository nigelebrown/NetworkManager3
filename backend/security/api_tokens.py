"""REST API / metrics access tokens (rc18 integrations, directive 2.10).

Tokens are for external systems that READ: they authenticate as their owner with the role capped at viewer,
only GET/HEAD requests are accepted, credential/secret/user/token administration paths are refused, and a
"metrics"-only token can read nothing but the Prometheus endpoint. Writes always need an interactive session and
the normal change governance; an API token can never approve, apply or read a device credential.

Format: ``inm_pat_<8 hex id>_<43 char secret>``; only the SHA-256 of the whole token is stored.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_rc18 import ApiToken

PREFIX = "inm_pat_"
SCOPES = ("read", "metrics")
MAX_DAYS = 365
METRICS_PATH = "/api/v1/metrics"
DENY = ("/credentials", "/vault", "/users", "/api-tokens", "/integrations", "/ssh-legacy", "/auth/", "/secrets",
        "/snmp-profiles", "/profiles/", "/export", "/backup")


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(db: Session, *, name: str, scopes: list[str], owner: User, actor: str, days: int) -> tuple[ApiToken, str]:
    if not scopes or set(scopes) - set(SCOPES):
        raise ValueError(f"scopes must be a non-empty subset of {SCOPES}")
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"expiry must be 1-{MAX_DAYS} days")
    ident = secrets.token_hex(4)
    token = f"{PREFIX}{ident}_{secrets.token_urlsafe(32)}"
    row = ApiToken(name=name, prefix=f"{PREFIX}{ident}", token_hash=digest(token), scopes=sorted(set(scopes)),
                   owner=owner.username, created_by=actor, expires_at=datetime.now(timezone.utc) + timedelta(days=days),
                   enabled=True)
    db.add(row)
    db.flush()
    return row, token


def _aware(t):
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t


def resolve(db: Session, token: str, request: Request) -> User:
    """Authenticate an API token for this request or raise 401/403. Returns a transient, viewer-capped user."""
    row = db.scalar(select(ApiToken).where(ApiToken.token_hash == digest(token)))
    now = datetime.now(timezone.utc)
    if row is None or not row.enabled or row.revoked_at is not None or _aware(row.expires_at) <= now:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid, revoked or expired API token")
    owner = db.scalar(select(User).where(User.username == row.owner))
    if owner is None or not owner.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API token owner is disabled")
    path = request.url.path
    if request.method not in ("GET", "HEAD"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API tokens are read-only; changes need an interactive session")
    if path == METRICS_PATH:
        if "metrics" not in row.scopes:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "API token lacks the metrics scope")
    elif "read" not in row.scopes:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API token is limited to the metrics endpoint")
    elif any(d in path for d in DENY):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "API tokens cannot read credential or administration data")
    if row.last_used_at is None or _aware(row.last_used_at) < now - timedelta(minutes=1):
        row.last_used_at = now
        row.last_used_ip = (request.client.host if request.client else None)
        db.commit()
    # Transient object (never added to the session): owner identity and site scope, role capped at viewer.
    return User(id=owner.id, username=owner.username, role="viewer", site_scope=owner.site_scope,
                is_active=True, must_change_password=False, password_hash="!api-token")


def public(row: ApiToken) -> dict:
    iso = (lambda t: _aware(t).isoformat() if t else None)
    now = datetime.now(timezone.utc)
    state = ("REVOKED" if row.revoked_at else "EXPIRED" if _aware(row.expires_at) <= now
             else "DISABLED" if not row.enabled else "ACTIVE")
    return {"id": row.id, "name": row.name, "prefix": row.prefix, "scopes": row.scopes, "owner": row.owner,
            "created_by": row.created_by, "created_at": iso(row.created_at), "expires_at": iso(row.expires_at),
            "last_used_at": iso(row.last_used_at), "last_used_ip": row.last_used_ip, "state": state,
            "revoked_by": row.revoked_by, "revoked_at": iso(row.revoked_at)}
