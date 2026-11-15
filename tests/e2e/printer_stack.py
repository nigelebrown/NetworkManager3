"""Isolated Printer Intelligence stack for browser E2E: real app + real DB + emulated SNMP printers.

Usage: python -I tests/e2e/printer_stack.py <workdir> <port>
Seeds 130 days of history by running the real poller against emulated agents on a simulated clock,
then serves the API + built frontend. A background thread keeps the emulated counters moving so
scheduled polling shows live changes. Source of every value is labelled EMULATED.
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
work, port = Path(sys.argv[1]), int(sys.argv[2])
work.mkdir(parents=True, exist_ok=True)
from cryptography.fernet import Fernet  # noqa: E402

os.environ.update({
    "SIMS_DATABASE_URL": f"sqlite:///{work / 'mgmt.db'}", "SIMS_MODE": "demo", "SIMS_ROLE": "standalone",
    "SIMS_JWT_SECRET": "e2e-" + "x" * 44, "SIMS_FERNET_KEY": Fernet.generate_key().decode(),
    "SIMS_ADMIN_PASSWORD": os.environ.get("INM_E2E_ADMIN_PASSWORD", "Printer-E2E-Admin-2026!"),
    "SIMS_FORCE_INITIAL_PASSWORD_CHANGE": "false", "INM_PRINTER_EMULATION": "true",
    "INM_READONLY_DEVICE_CIDRS": "10.103.70.0/24",  # approved read-only printer range (as allow-readonly-device-range.sh)
    "INM_SCHEDULED_COLLECTION": "false", "INM_SCHEDULED_VAULT": "false", "INM_SCHEDULED_ANALYSIS": "false",
})

from backend.database import session as dbs  # noqa: E402
from backend.printers import collector as C, mib, service as S  # noqa: E402

LIFE = mib.PRT_MARKER_LIFE + ".1.1"
FLEET = [  # ip, manufacturer, model, serial, pages/day, supplies
    ("10.103.70.11", "Kyocera", "ECOSYS MA4000cix", "KYC-0001", 900, [("TK-5345K Black", 3, 80), ("TK-5345C Cyan", 3, 60),
                                                                        ("TK-5345M Magenta", 3, 55), ("TK-5345Y Yellow", 3, 70)]),
    ("10.103.70.12", "Kyocera", "ECOSYS M2640idw", "KYC-0002", 60, [("TK-1150 Black", 3, 90)]),
    ("10.103.70.13", "Kyocera", "ECOSYS M2640idw", "KYC-0003", 45, [("TK-1150 Black", 3, 40)]),
    ("10.103.70.14", "Kyocera", "ECOSYS MA5500ifx", "KYC-0004", 1500, [("TK-3430 Black", 3, 30), ("Waste toner", 4, -3)]),
    ("10.103.70.15", "Xerox", "VersaLink B62", "XRX-0001", 300, [("006R01735 Black", 3, 50)]),
    ("10.103.70.16", "HP", "Color LaserJet M454dn", "HPC-0001", 200, [("W2030A Black", 3, 75)]),
]
DISCOVERABLE = "10.103.70.20"


def agents():
    for ip, manu, model, serial, ppd, sup in FLEET:
        vals = C.emulated_printer(manufacturer=manu, model=model, serial=serial, impressions=100000,
                                  supplies=[{"descr": d, "type": t, "class": 4 if t == 4 else 3, "level": lvl} for d, t, lvl in sup],
                                  hostname=f"prn-{serial.lower()}")
        S.EMULATORS[ip] = C.EmulatedPrinterAgent(vals)
    S.EMULATORS[DISCOVERABLE] = C.EmulatedPrinterAgent(C.emulated_printer(
        manufacturer="Kyocera", model="TASKalfa 6004i", serial="KYC-NEW-01", impressions=5000,
        supplies=[{"descr": "TK-8725K Black", "level": 88}], hostname="prn-new"))


def seed():
    dbs.init_engine()
    dbs.create_schema()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    clock = {"t": now - timedelta(days=130)}
    S.now_utc = lambda: clock["t"]  # simulated clock only during seeding
    with dbs.session_scope() as db:
        for ip, manu, model, serial, _, _ in FLEET:
            p, _ = S.register_or_update(db, {"manufacturer": manu, "model": model, "serial_number": serial, "management_ip": ip,
                                             "station": "Head Office" if serial != "KYC-0004" else "Records"}, provenance="MANUAL")
            m = S.monitoring(db, p.id)
            m.enabled, m.interval_seconds, m.next_poll_at, m.approved_by = True, 21600, clock["t"], "e2e-seed"
        mdl = S.ensure_model(db, "Kyocera", "ECOSYS M2640idw")
        mdl.recommended_monthly_volume, mdl.max_monthly_duty_cycle, mdl.verification = 2500, 50000, "VERIFIED"
        mdl2 = S.ensure_model(db, "Kyocera", "ECOSYS MA5500ifx")
        mdl2.recommended_monthly_volume, mdl2.max_monthly_duty_cycle, mdl2.verification = 15000, 150000, "VERIFIED"
    step = 0
    while clock["t"] < now:
        for ip, _, _, _, ppd, sup in FLEET:
            a = S.EMULATORS[ip]
            a.values[LIFE] += ppd // 4
            a.values["1.3.6.1.4.1.1347.43.10.1.1.12.1.1"] = a.values[LIFE]
            for i in range(1, len(sup) + 1):
                k = mib.PRT_SUPPLY_LEVEL + f".1.{i}"
                if a.values.get(k, -3) >= 0 and a.values.get(mib.PRT_SUPPLY_CLASS + f".1.{i}") == 3:
                    a.values[k] = 100 if a.values[k] <= 4 else a.values[k] - (1 if step % 2 else 0)
        if step % 40 == 7:
            S.EMULATORS["10.103.70.14"].values[mib.HR_PRINTER_ERROR_STATE + ".1"] = b"\x04\x00"
        elif step % 40 == 8:
            S.EMULATORS["10.103.70.14"].values[mib.HR_PRINTER_ERROR_STATE + ".1"] = b"\x00\x00"
        asyncio.run(S.run_due(dbs.session_scope, "e2e-seed"))
        clock["t"] += timedelta(hours=6)
        step += 1
    with dbs.session_scope() as db:
        for mon in db.scalars(__import__("sqlalchemy").select(S.PrinterMonitoring)):
            mon.interval_seconds, mon.next_poll_at = 300, now
    S.now_utc = lambda: datetime.now(timezone.utc)


def drift():
    while True:
        time.sleep(20)
        for ip, *_rest in FLEET:
            S.EMULATORS[ip].values[LIFE] += 3


agents()
seed()
threading.Thread(target=drift, daemon=True).start()
import uvicorn  # noqa: E402
from backend.main import create_app  # noqa: E402

uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")
