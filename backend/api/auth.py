from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import User
from ..database.models_inm import RevokedToken
from ..database.session import get_db
from ..security.context import Actor, session_hash, set_actor
from ..security.crypto import create_access_token, decode_access_token, hash_password, verify_password
from ..security.deps import audit, client_ip, get_current_user, login_limiter, user_site_scope

router = APIRouter(prefix="/api/auth", tags=["auth"])

MAX_FAILED = 5
LOCK_MINUTES = 15
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    if not login_limiter.check(client_ip(request)):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Wait a minute.")
    user = db.scalar(select(User).where(User.username == body.username))
    now = datetime.now(timezone.utc)

    if user and user.locked_until and user.locked_until.replace(tzinfo=timezone.utc) > now:
        audit(db, "login_locked", username=body.username, request=request, success=False)
        db.commit()
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Account temporarily locked.")

    ok = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not (user and user.is_active and ok):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILED:
                user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                user.failed_logins = 0
        audit(db, "login_failed", username=body.username, request=request, success=False)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    user.failed_logins, user.locked_until = 0, None
    token, expires = create_access_token(user.username, user.role)
    set_actor(Actor(user.username, user.role, session_hash(decode_access_token(token).get("jti"))))
    audit(db, "login", username=user.username, request=request)
    return {"access_token": token, "token_type": "bearer", "expires_at": expires.isoformat(),
            "username": user.username, "role": user.role, "must_change_password": bool(user.must_change_password)}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    scope = user_site_scope(db, user)
    return {"username": user.username, "role": user.role, "must_change_password": bool(user.must_change_password),
            "site_scope": "all" if scope is None else sorted(scope)}


@router.post("/logout")
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Server-side sign-out: the token's id is revoked until it would have expired anyway."""
    payload = request.state.token_payload
    if payload.get("jti") and not db.get(RevokedToken, payload["jti"]):
        db.add(RevokedToken(jti=payload["jti"], expires_at=datetime.fromtimestamp(payload["exp"], timezone.utc)))
    audit(db, "logout", username=user.username, request=request)
    return {"ok": True}


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        audit(db, "password_change_failed", username=user.username, request=request, success=False)
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    if body.new_password == body.current_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "The new password must differ from the current one")
    user.password_hash, user.must_change_password = hash_password(body.new_password), False
    audit(db, "password_changed", username=user.username, request=request)
    return {"ok": True}
