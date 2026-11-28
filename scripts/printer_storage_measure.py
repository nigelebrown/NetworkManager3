"""Count raw printer samples stored over 24 h of 900 s polls (96 polls) for one emulated printer whose page
counter advances every poll and whose toner falls 1 % every 8 polls. Reports samples/day; bytes are DERIVED from the
rc16 PostgreSQL measurement (22.3 MB / 58,004 samples incl. indexes)."""
import asyncio, json, os, sys, tempfile
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.getcwd())
d = tempfile.mkdtemp()
os.environ.update({"SIMS_DATABASE_URL": f"sqlite:///{d}/m.db", "INM_TELEMETRY_DATABASE_URL": f"sqlite:///{d}/t.db",
                   "SIMS_MODE": "demo", "SIMS_JWT_SECRET": "x" * 48})
from sqlalchemy import func, select
from backend.database import session as dbs
from backend.database.models_printer_srs import PrinterMetricSample
from backend.printers import collector as C, mib, service as S
dbs.init_engine(); dbs.create_schema()
agent = C.EmulatedPrinterAgent(C.emulated_printer(impressions=1000, supplies=[{"descr": "TK-1150 Black", "level": 80}]))
with dbs.session_scope() as db:
    p, _ = S.register_or_update(db, {"manufacturer": "Kyocera", "model": "ECOSYS M2640idw", "serial_number": "M1",
                                     "management_ip": "10.9.9.9"}, provenance="MANUAL"); pid = p.id
t0 = datetime(2026, 11, 20, tzinfo=timezone.utc); counts = []
for i in range(96):
    agent.values[mib.PRT_MARKER_LIFE + ".1.1"] += 37
    if i and i % 8 == 0:
        agent.values[mib.PRT_SUPPLY_LEVEL + ".1.1"] -= 1
    res = asyncio.run(C.poll(agent, mib.BUILTIN_PROFILES, now=t0 + timedelta(seconds=900 * i)))
    with dbs.session_scope() as db:
        S.apply_poll(db, db.get(S.PrinterDevice, pid), res)
        counts.append(db.scalar(select(func.count()).select_from(PrinterMetricSample)))
per_sample = 22.3e6 / 58004
total = counts[-1]
out = {"polls": 96, "samples_first_poll": counts[0], "samples_24h": total,
       "samples_per_poll_after_first": round((total - counts[0]) / 95, 2),
       "rc16_samples_per_poll": round(58004 / 1797, 1),
       "derived_bytes_per_printer_day": round(total * per_sample),
       "derived_rc16_bytes_per_printer_day": round(96 * 58004 / 1797 * per_sample),
       "derived_reduction_percent": round(100 * (1 - total / (96 * 58004 / 1797)), 1)}
print(json.dumps(out, indent=1))
