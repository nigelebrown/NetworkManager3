"""Password hashing, credential encryption, and session tokens."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings

JWT_ALG = "HS256"


# ---- passwords -------------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode())
    except ValueError:
        return False


# ---- device credential encryption -----------------------------------------
def _fernet() -> Fernet:
    key = get_settings().fernet_key
    try:
        return Fernet(key.encode())
    except Exception as exc:  # bad key format
        raise RuntimeError(
            "SIMS_FERNET_KEY is not a valid Fernet key. See .env.example for how to generate one."
        ) from exc


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Cannot decrypt stored credential: SIMS_FERNET_KEY changed since it was saved."
        ) from exc


# ---- session tokens -------------------------------------------------------
def create_access_token(username: str, role: str) -> tuple[str, datetime]:
    s = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=s.session_minutes)
    token = jwt.encode(
        {"sub": username, "role": role, "exp": expires, "iat": datetime.now(timezone.utc),
         "jti": uuid.uuid4().hex},
        s.jwt_secret,
        algorithm=JWT_ALG,
    )
    return token, expires


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError:
        return None
