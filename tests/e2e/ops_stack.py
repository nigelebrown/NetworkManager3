"""Isolated operations stack for browser E2E of import mapping, port monitoring groups and printer profiles.

Usage: python -I tests/e2e/ops_stack.py <workdir> <port>
Demo mode, SQLite, the labelled demo dataset, and two collection passes for demo switches through the CX protocol
emulator (the same telemetry code path as hardware). Every value is DEMO/EMULATED; nothing contacts a network.
Serves the API and the built frontend (run `npm run build` in frontend/ first).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
work, port = Path(sys.argv[1]), int(sys.argv[2])
work.mkdir(parents=True, exist_ok=True)
from cryptography.fernet import Fernet  # noqa: E402

os.environ.update({
    "SIMS_DATABASE_URL": f"sqlite:///{work / 'mgmt.db'}", "INM_TELEMETRY_DATABASE_URL": f"sqlite:///{work / 'ts.db'}",
    "SIMS_MODE": "demo", "SIMS_INM_PROFILE": "demo", "SIMS_ROLE": "standalone", "SIMS_DEVICE_WRITE_LOCK": "true",
    "SIMS_JWT_SECRET": "e2e-" + "x" * 44, "SIMS_FERNET_KEY": Fernet.generate_key().decode(),
    "SIMS_ADMIN_PASSWORD": os.environ.get("INM_E2E_ADMIN_PASSWORD", "Ops-E2E-Admin-2026!"),
    "SIMS_FORCE_INITIAL_PASSWORD_CHANGE": "false", "INM_VAULT_DIR": str(work / "vault"),
    "INM_SCHEDULED_COLLECTION": "false", "INM_SCHEDULED_VAULT": "false", "INM_SCHEDULED_ANALYSIS": "false",
    "SIMS_DISABLE_SCHEDULER": "true",
})

from sqlalchemy import select  # noqa: E402

from backend.database import session as dbs  # noqa: E402


def seed():
    from fastapi.testclient import TestClient
    from backend.main import create_app
    with TestClient(create_app()):              # runs schema creation + admin bootstrap exactly as the server does
        pass
    from backend.database.models_inm import InmDevice
    from backend.database.models_phases import CollectionPlan
    from backend.infrastructure import demo_dataset
    from backend.infrastructure.collection_runner import demo_collector, run_due
    from backend.infrastructure.runtime import telemetry_store
    dbs.init_engine()
    telemetry_store().migrate()
    with dbs.session_scope() as db:
        demo_dataset.load(db, actor="e2e-seed", store=telemetry_store())
    with dbs.session_scope() as db:
        plans = [p.id for p in db.scalars(select(CollectionPlan).join(InmDevice, InmDevice.id == CollectionPlan.device_id)
                                          .where(InmDevice.family == "switch"))]
    now = datetime.now(timezone.utc)
    for at in (now - timedelta(minutes=6), now - timedelta(minutes=1)):
        with dbs.session_scope() as db:
            run_due(db, now=at, store=telemetry_store(), worker="e2e-seed", plan_ids=plans, collector=demo_collector)


if __name__ == "__main__":
    seed()
    import uvicorn
    from backend.main import create_app
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
