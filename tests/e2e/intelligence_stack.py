"""Isolated Network Intelligence stack for browser E2E: real app, SQLite management + telemetry stores, seeded
REAL-labelled lab devices with 21 days of recorded telemetry (written exactly as collectors write it).

Usage: python -I tests/e2e/intelligence_stack.py <workdir> <port>
No device is contacted; the AI model is deliberately absent so the assistant must use its rules fallback.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
work, port = Path(sys.argv[1]), int(sys.argv[2])
work.mkdir(parents=True, exist_ok=True)
from cryptography.fernet import Fernet  # noqa: E402

os.environ.update({
    "SIMS_DATABASE_URL": f"sqlite:///{work / 'mgmt.db'}", "INM_TELEMETRY_DATABASE_URL": f"sqlite:///{work / 'ts.db'}",
    "SIMS_MODE": "demo", "SIMS_ROLE": "standalone", "SIMS_JWT_SECRET": "e2e-" + "y" * 44,
    "SIMS_FERNET_KEY": Fernet.generate_key().decode(),
    "SIMS_ADMIN_PASSWORD": os.environ.get("INM_E2E_ADMIN_PASSWORD", "Intel-E2E-Admin-2026!"),
    "INM_SCHEDULED_COLLECTION": "false", "INM_SCHEDULED_VAULT": "false", "INM_SCHEDULED_PREDICTIVE": "false",
    "INM_PRINTER_POLLING": "false",
})

from backend.database import session as dbs  # noqa: E402
from backend.database.models_inm import InmDevice, InmSite  # noqa: E402
from backend.domain.model import DataQuality, SourceType, TelemetryPoint  # noqa: E402
from backend.infrastructure.runtime import telemetry_store  # noqa: E402

dbs.init_engine()
dbs.create_schema()
store = telemetry_store()
store.migrate()
now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
with dbs.session_scope() as db:
    hq, annex = InmSite(name="Lab HQ", source_type="REAL"), InmSite(name="Lab Annex", source_type="REAL")
    db.add_all([hq, annex])
    db.flush()
    devs = []
    for i, (name, fam, site, profile) in enumerate([
            ("lab-ap-busy-1", "access_point", hq.id, "busy"), ("lab-ap-busy-2", "access_point", hq.id, "busy"),
            ("lab-ap-busy-3", "access_point", annex.id, "busy"), ("lab-ap-calm-1", "access_point", hq.id, "calm"),
            ("lab-ap-calm-2", "access_point", annex.id, "calm"), ("lab-ap-calm-3", "access_point", annex.id, "calm"),
            ("lab-ap-new", "access_point", hq.id, "new"), ("lab-sw-silent", "switch", annex.id, "silent")]):
        d = InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family=fam, model="AP-535" if fam == "access_point" else "CX 6200",
                      serial_number=f"LAB{i:04d}", hostname=name, adapter="aruba-instant", site_id=site, last_observed_at=now)
        db.add(d)
        db.flush()
        devs.append((d.id, profile))


def pts(device_id, metric, rows):
    return [TelemetryPoint(device_id=device_id, metric_name=metric, observed_at=t, value=v, unit="percent",
                           collection_method="ARUBA_REST_READ", quality=DataQuality.OBSERVED, source_type=SourceType.REAL,
                           source_event_id=str(uuid.uuid4())) for t, v in rows]


batch = []
for did, profile in devs:
    if profile == "silent":
        continue
    days = 2 if profile == "new" else 21
    t = now - timedelta(days=days)
    rows = []
    while t <= now:
        peak = 13 <= t.hour <= 15
        cpu = (96.0 if peak else 38.0) if profile in ("busy", "new") else 33.0 + (t.hour % 3)
        rows.append((t, cpu))
        t += timedelta(hours=1)
    rows += [(datetime.now(timezone.utc) - timedelta(minutes=m), 97.0 if profile == "busy" else 35.0) for m in (2, 6)]
    batch += pts(did, "cpu_percent", rows)
    batch += pts(did, "memory_percent", [(r[0], 55.0) for r in rows[-48:]])
for i in range(0, len(batch), 2000):
    store.write_points(batch[i:i + 2000])

import uvicorn  # noqa: E402
from backend.main import create_app  # noqa: E402

uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
