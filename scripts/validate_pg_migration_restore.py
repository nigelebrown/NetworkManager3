"""Validate management-schema migrations and a dump/restore round trip on a disposable PostgreSQL server.

Usage (never point this at a production database; it creates and drops databases):
    python scripts/validate_pg_migration_restore.py --admin-url postgresql+psycopg://postgres@127.0.0.1:55432/postgres \
        --pg-bin /usr/lib/postgresql/16/bin

Checks, all against freshly created databases:
  1. 0004 -> head: existing site row survives (same check as CI) and head == release.json management_schema_head.
  2. 0013 -> 0014: existing certification evidence rows survive the constraint change; the new scope permits a
     HARDWARE and an EMULATED record for the same operation; downgrade is refused.
  3. head database seeded through the application (users, sites, REAL devices, printer profile, port group, audit
     chain) -> pg_dump -Fc -> pg_restore into a new database -> the application starts against the restored copy,
     the schema check passes, login works, records and the audit hash chain verify.
Telemetry (TimescaleDB) is NOT covered: this environment has no TimescaleDB extension.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sh(*args, env=None):
    subprocess.run(args, check=True, env=env)


def url_for(admin_url: str, db: str) -> str:
    return admin_url.rsplit("/", 1)[0] + "/" + db


def fresh_db(admin_url: str, name: str):
    from sqlalchemy import create_engine, text
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        c.execute(text(f'CREATE DATABASE "{name}"'))
    eng.dispose()


def alembic_upgrade(url: str, target: str):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    eng = create_engine(url)
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "backend/database/migrations"))
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, target)
    eng.dispose()


def scalar(url: str, sql: str, **params):
    from sqlalchemy import create_engine, text
    eng = create_engine(url)
    with eng.connect() as c:
        v = c.execute(text(sql), params).scalar()
    eng.dispose()
    return v


def execute(url: str, sql: str, **params):
    from sqlalchemy import create_engine, text
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(text(sql), params)
    eng.dispose()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-url", required=True)
    ap.add_argument("--pg-bin", default="/usr/lib/postgresql/16/bin")
    a = ap.parse_args()
    head = json.loads((ROOT / "release.json").read_text())["management_schema_head"]
    results = []
    run = uuid.uuid4().hex[:6]

    # 1. 0004 -> head with existing data
    db1 = f"inm_mig_{run}_a"
    fresh_db(a.admin_url, db1)
    u1 = url_for(a.admin_url, db1)
    alembic_upgrade(u1, "0004")
    execute(u1, "INSERT INTO sites (name, vc_port, simulated, enabled, created_at) "
                "VALUES ('Persist-through-release-head', 4343, FALSE, TRUE, '2026-01-01')")
    alembic_upgrade(u1, "head")
    assert scalar(u1, "SELECT version_num FROM alembic_version") == head
    assert scalar(u1, "SELECT count(*) FROM sites WHERE name='Persist-through-release-head'") == 1
    results.append(f"PASS 0004->{head}: existing site preserved, head matches release.json")

    # 2. 0013 -> 0014 certification scope change preserves evidence
    db2 = f"inm_mig_{run}_b"
    fresh_db(a.admin_url, db2)
    u2 = url_for(a.admin_url, db2)
    alembic_upgrade(u2, "0013")
    execute(u2, "INSERT INTO device_certifications (id, adapter_key, model_family, firmware_train, operation, status, "
                "environment, run_id, device_id, evidence, tested_by, tested_at) VALUES "
                "(:id, 'aruba_cx', 'CX6200', '10.14', 'switch.vlan', 'CERTIFIED_WRITE', 'EMULATED', :r, :d, "
                "'{\"note\": \"legacy evidence\"}', 'lab', now())", id=str(uuid.uuid4()), r=str(uuid.uuid4()),
            d=str(uuid.uuid4()))
    alembic_upgrade(u2, "0014")
    assert scalar(u2, "SELECT count(*) FROM device_certifications WHERE evidence::text LIKE '%legacy evidence%'") == 1
    execute(u2, "INSERT INTO device_certifications (id, adapter_key, model_family, firmware_train, operation, status, "
                "environment, run_id, device_id, evidence, tested_by, tested_at) VALUES "
                "(:id, 'aruba_cx', 'CX6200', '10.14', 'switch.vlan', 'NOT_RUN', 'HARDWARE', :r, :d, '{}', 'lab', now())",
            id=str(uuid.uuid4()), r=str(uuid.uuid4()), d=str(uuid.uuid4()))
    try:
        execute(u2, "INSERT INTO device_certifications (id, adapter_key, model_family, firmware_train, operation, "
                    "status, environment, run_id, device_id, evidence, tested_by, tested_at) VALUES (:id, 'aruba_cx', "
                    "'CX6200', '10.14', 'switch.vlan', 'X', 'HARDWARE', :r, :d, '{}', 'lab', now())",
                id=str(uuid.uuid4()), r=str(uuid.uuid4()), d=str(uuid.uuid4()))
        raise AssertionError("duplicate HARDWARE certification scope accepted")
    except Exception as exc:  # noqa: BLE001
        if "uq_cert_scope_env_op" not in str(exc):
            raise
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    eng = create_engine(u2)
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "backend/database/migrations"))
    try:
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0013")
        raise AssertionError("downgrade from 0014 was not refused")
    except RuntimeError as exc:
        assert "Restore the pre-upgrade backup" in str(exc)
    eng.dispose()
    results.append("PASS 0013->0014: legacy certification evidence preserved; HARDWARE/EMULATED scopes separate; "
                   "downgrade refused (restore backup instead)")

    # 2b. 0014 -> 0015 (rc17): purchase requests / print jobs / findings tables added; earlier evidence intact
    alembic_upgrade(u2, "0015")
    for t in ("printer_purchase_requests", "print_jobs", "print_job_findings"):
        assert scalar(u2, f"SELECT count(*) FROM information_schema.tables WHERE table_name='{t}'") == 1, t
    eng = create_engine(u2)
    try:
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0014")
        raise AssertionError("downgrade from 0015 was not refused")
    except RuntimeError as exc:
        assert "Restore the pre-upgrade backup" in str(exc)
    eng.dispose()
    results.append("PASS 0014->0015: purchasing and print-job tables created (expand-only); downgrade refused")

    # 2c. 0015 -> 0016 (rc18): ML lifecycle + API tokens tables, model-registry and channel columns; data intact
    before_models = scalar(u2, "SELECT count(*) FROM ml_models")
    before_channels = scalar(u2, "SELECT count(*) FROM notification_channels")
    alembic_upgrade(u2, "0016")
    for t in ("ml_training_runs", "ml_drift_reports", "ml_inferences", "api_tokens"):
        assert scalar(u2, f"SELECT count(*) FROM information_schema.tables WHERE table_name='{t}'") == 1, t
    for tbl, col in (("ml_models", "production_validated"), ("ml_models", "target"),
                     ("notification_channels", "secret_enc"), ("notification_channels", "config")):
        assert scalar(u2, "SELECT count(*) FROM information_schema.columns WHERE table_name=%r AND column_name=%r"
                      % (tbl, col)) == 1, (tbl, col)
    assert scalar(u2, "SELECT count(*) FROM ml_models") == before_models
    assert scalar(u2, "SELECT count(*) FROM notification_channels") == before_channels
    assert scalar(u2, "SELECT count(*) FROM ml_models WHERE production_validated") == 0   # never true by migration
    assert scalar(u2, "SELECT character_maximum_length FROM information_schema.columns "
                      "WHERE table_name='recommendations' AND column_name='rule_version'") == 64
    assert scalar(u2, "SELECT character_maximum_length FROM information_schema.columns "
                      "WHERE table_name='notification_deliveries' AND column_name='kind'") == 24
    assert scalar(u2, "SELECT character_maximum_length FROM information_schema.columns "
                      "WHERE table_name='change_plans' AND column_name='source'") == 32
    eng = create_engine(u2)
    try:
        with eng.begin() as conn:
            cfg.attributes["connection"] = conn
            command.downgrade(cfg, "0015")
        raise AssertionError("downgrade from 0016 was not refused")
    except RuntimeError as exc:
        assert "Restore the pre-upgrade backup" in str(exc)
    eng.dispose()
    results.append("PASS 0015->0016: ML lifecycle, API token tables and channel credential columns added (expand-only); "
                   "existing models/channels intact, none marked production-validated; downgrade refused")

    # 3. application-seeded head DB -> pg_dump -> pg_restore -> application boots on the copy
    db3, db4 = f"inm_mig_{run}_c", f"inm_mig_{run}_restored"
    fresh_db(a.admin_url, db3)
    u3 = url_for(a.admin_url, db3)
    work = Path(tempfile.mkdtemp(prefix="inm-restore-"))
    env = {**os.environ, "SIMS_DATABASE_URL": u3, "INM_TELEMETRY_DATABASE_URL": f"sqlite:///{work / 'ts.db'}",
           "SIMS_MODE": "live", "SIMS_INM_PROFILE": "lab-readonly", "SIMS_ROLE": "api", "SIMS_DEVICE_WRITE_LOCK": "true",
           "SIMS_JWT_SECRET": "r" * 48, "SIMS_FERNET_KEY": "ZmFrZS1mZXJuZXQta2V5LWZvci10ZXN0cy0zMi1ieXRlcz0=",
           "SIMS_ADMIN_PASSWORD": "Restore-Drill-2026!", "SIMS_FORCE_INITIAL_PASSWORD_CHANGE": "false",
           "SIMS_DISABLE_SCHEDULER": "true", "INM_VAULT_DIR": str(work / "vault")}
    from cryptography.fernet import Fernet
    env["SIMS_FERNET_KEY"] = Fernet.generate_key().decode()
    seed = ROOT / "scripts" / "_restore_drill_app.py"
    out = subprocess.run([sys.executable, str(seed), "seed"], env=env, capture_output=True, text=True)
    if out.returncode:
        print(out.stdout, out.stderr)
        raise SystemExit("seed failed")
    seeded = json.loads(out.stdout.strip().splitlines()[-1])
    dump = work / "mgmt.dump"
    pg = a.admin_url.replace("postgresql+psycopg://", "postgresql://")
    sh(f"{a.pg_bin}/pg_dump", "-Fc", "-f", str(dump), url_for(pg, db3))
    fresh_db(a.admin_url, db4)
    sh(f"{a.pg_bin}/pg_restore", "--no-owner", "-d", url_for(pg, db4), str(dump))
    env["SIMS_DATABASE_URL"] = url_for(a.admin_url, db4)
    out = subprocess.run([sys.executable, str(seed), "verify"], env=env, capture_output=True, text=True)
    if out.returncode:
        print(out.stdout, out.stderr)
        raise SystemExit("verify on restored copy failed")
    restored = json.loads(out.stdout.strip().splitlines()[-1])
    assert restored == seeded, (seeded, restored)
    results.append(f"PASS dump/restore: {dump.stat().st_size} byte custom-format dump restored; application started on "
                   f"the copy, login OK, records identical {restored}")
    for r in results:
        print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
