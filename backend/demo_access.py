"""Reset the demo-only admin credential without rebuilding or erasing database volumes.

Intentional demo convenience is gated by the runtime profile, localhost web bind
and global device-write lock. NEVER run or enable this for lab/live/production.
"""
from __future__ import annotations

from sqlalchemy import select

from .config import get_settings
from .database.models import User
from .database.session import init_engine, session_scope
from .security.crypto import hash_password
from .security.deps import audit


def main() -> None:
    s = get_settings()
    failures = s.validate_secrets() + s.validate_profile()
    if failures or not s.demo_simple_login or s.admin_password != "demo123!":
        raise SystemExit("Refused: demo credentials are only valid in an explicitly locked, localhost-bound demo.")
    init_engine()
    with session_scope() as db:
        user = db.scalar(select(User).where(User.username == "admin"))
        if not user or user.role != "admin":
            raise SystemExit("Refused: no existing named admin account to reset.")
        user.password_hash = hash_password(s.admin_password)
        user.failed_logins = 0
        user.locked_until = None
        user.must_change_password = False
        audit(db, "demo_admin_password_reset", username="installer", target="admin",
              detail="Demo-only, localhost-bound credential reset; remove for production",
              channel="INSTALLER", actor_kind="installer")
    print("[DEMO ONLY] admin credentials: admin / demo123! (localhost/SSH tunnel access only)")
    print("[DEMO ONLY] Device writes remain locked. Replace the credential before any wider exposure.")


if __name__ == "__main__":
    main()
