"""Simulates the collector's alert evaluation for two switches (same code path as monitoring)."""
import os, sys
os.environ["SIMS_DATABASE_URL"] = "postgresql+psycopg://inm@/inm?host=/tmp/pg&port=5433"
os.environ["SIMS_FERNET_KEY"] = open("/tmp/e2e/fernet").read().strip()
os.environ["SIMS_JWT_SECRET"] = "t" * 48
sys.path.insert(0, "/home/claude/src/NetworkManager3-main")
from sqlalchemy import select
from backend.database.session import session_scope
from backend.database.models_inm import InmDevice
from backend.infrastructure import alert_engine
with session_scope() as db:
    for host in ("AllmanTownPS-SW-1", "AdmiralTown-SW-1"):
        d = db.scalar(select(InmDevice).where(InmDevice.hostname == host))
        if sys.argv[1] == "open":
            print(host, alert_engine.apply(db, d, {"consecutive_failures": 3.0}))
        elif host == "AdmiralTown-SW-1":
            print(host, alert_engine.apply(db, d, {"consecutive_failures": 0.0}))
