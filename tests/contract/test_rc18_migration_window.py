"""P11-DEP-03 (rc18): expand / migrate / contract sequence and the old/new interoperation window.

1. Expand: migration 0016 is additive. The PREVIOUS release's code (rc17, f6f44ce, schema 0015) reads and writes a
   database this release migrated to 0016 - devices, change plans, ML models (new NOT NULL column has a server
   default), notification channels - and this release reads those rows back.
2. Window: an expand migration records `schema:compat` {head, phase: expand, compatible_readers}. This release
   starts against a newer schema only when that marker lists its own head; it then reads and writes normally.
3. Contract: once the marker no longer lists this release (or is absent, or says contract), it refuses to start.
rc17 itself predates the marker and refuses 0016 at startup (fail-safe) - its row-level compatibility in (1) is what
the marker for 0016 records for future readers.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from tests.contract.test_rc18_vault_upgrade_and_isolated_restore import _client, _env, _previous_release

OLD_WRITER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from backend.database import session as dbs
dbs.init_engine()                      # no migration: the database is already at the NEWER schema 0016
from sqlalchemy import select
from backend.database.models_inm import InmDevice
from backend.database.models_phases import ChangePlan, MlModel
from backend.database.models_remediation import NotificationChannel
out = {}
with dbs.session_scope() as db:
    seen = db.scalar(select(InmDevice).where(InmDevice.serial_number == "WIN-NEW-1"))
    out["old_reads_new_device"] = bool(seen and seen.hostname == "written-by-rc18")
    d = InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family="switch", model="CX 6100 24G",
                  serial_number="WIN-OLD-1", hostname="written-by-rc17", adapter="aruba_cx_10_14")
    db.add(d); db.flush()
    db.add(ChangePlan(device_id=d.id, title="rc17 plan", operations=[{"op": "noop"}], capability_report=[],
                      plan_hash="b" * 64, rollback_plan={}, state="DRAFT", requester="rc17", source="OPERATOR"))
    db.add(MlModel(name="rc17-model", kind="breach", algorithm="logistic", status="CANDIDATE",
                   dataset_sha256="c" * 64, split={}, metrics={}, parameters={}, trained_by="rc17"))
    db.add(NotificationChannel(name="rc17-hook", kind="webhook", target="https://hooks.example.invalid/x",
                               enabled=False, created_by="rc17"))
    try:
        dbs.assert_schema_current()
        out["old_startup_guard"] = "ACCEPTED"
    except RuntimeError as exc:
        out["old_startup_guard"] = "REFUSED"
print(json.dumps(out))
"""


def _sql(db: Path, *stmts):
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        rows = [c.execute(text(s)).fetchall() if s.lstrip().upper().startswith("SELECT") else c.execute(text(s))
                for s in stmts]
    eng.dispose()
    return rows


def _marker(db: Path) -> dict:
    v = _sql(db, "SELECT value FROM app_settings WHERE key = 'schema:compat'")[0][0][0]
    return json.loads(v) if isinstance(v, str) else v


def test_expand_migration_keeps_previous_release_rows_compatible(app_env, tmp_path):
    from tests.conftest import login
    db, vault = tmp_path / "mgmt.db", tmp_path / "vault"
    with _client(app_env, db, vault) as c:                         # this release migrates a fresh DB to 0016
        h = login(c)
        assert c.get("/api/health", headers=h).status_code == 200
    _sql(db, "INSERT INTO inm_devices (id, source_type, environment, vendor, family, serial_number, hostname, "
             "adapter, lifecycle, status, created_at) VALUES ('11111111-1111-1111-1111-111111111111', 'REAL', 'lab', "
             "'Aruba', 'switch', 'WIN-NEW-1', 'written-by-rc18', 'aruba_cx_10_14', 'monitored', 'unknown', "
             "'2026-11-25 00:00:00')")
    assert _marker(db) == {"head": "0016", "phase": "expand", "compatible_readers": ["0015"]}
    old = _previous_release(tmp_path)
    r = subprocess.run([sys.executable, "-I", "-c", OLD_WRITER, str(old)], capture_output=True, text=True,
                       env=_env(db, vault), cwd=old, timeout=300)
    assert r.returncode == 0, r.stderr[-3000:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out == {"old_reads_new_device": True, "old_startup_guard": "REFUSED"}
    # rows written by the previous release are valid for this release (server defaults fill the new columns)
    with _client(app_env, db, vault) as c:
        h = login(c)
        devs = {d["hostname"] for d in c.get("/api/v1/devices", headers=h).json()}
        assert {"written-by-rc17", "written-by-rc18"} <= devs
        ml = _sql(db, "SELECT production_validated FROM ml_models WHERE name = 'rc17-model'")[0]
        assert ml and ml[0][0] in (0, False)


def _future_expand(db: Path, readers: list[str], phase: str = "expand", head: str = "0017"):
    _sql(db, "ALTER TABLE inm_devices ADD COLUMN future_note VARCHAR(40)",
         f"UPDATE alembic_version SET version_num = '{head}'",
         "DELETE FROM app_settings WHERE key = 'schema:compat'",
         "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES ('schema:compat', '"
         + json.dumps({"head": head, "phase": phase, "compatible_readers": readers})
         + "', '2026-11-25 00:00:00', 'migration " + head + "')")


@pytest.mark.parametrize("readers,phase,starts", [
    (["0016"], "expand", True),          # inside the window: this release keeps working
    (["0015"], "expand", False),         # window does not include this release
    (["0016"], "contract", False),       # contract step: old readers retired
    (None, None, False),                 # no marker: never guess
])
def test_this_release_runs_inside_a_declared_window_only(app_env, tmp_path, readers, phase, starts):
    from tests.conftest import login
    db, vault = tmp_path / "mgmt.db", tmp_path / "vault"
    with _client(app_env, db, vault) as c:
        login(c)
    if readers is None:
        _sql(db, "ALTER TABLE inm_devices ADD COLUMN future_note VARCHAR(40)",
             "UPDATE alembic_version SET version_num = '0017'", "DELETE FROM app_settings WHERE key = 'schema:compat'")
    else:
        _future_expand(db, readers, phase)
    if not starts:
        with pytest.raises(RuntimeError, match="accepts"):
            with _client(app_env, db, vault, SIMS_AUTO_MIGRATE="false"):
                pass
        return
    with _client(app_env, db, vault, SIMS_AUTO_MIGRATE="false") as c:
        h = login(c)
        g = c.post("/api/v1/device-groups", headers=h, json={"name": "Window group", "family": "switch"})
        assert g.status_code in (200, 201), g.text
        assert any(x["name"] == "Window group" for x in c.get("/api/v1/device-groups", headers=h).json())
        assert c.get("/api/v1/devices", headers=h).status_code == 200
    assert _sql(db, "SELECT version_num FROM alembic_version")[0][0][0] == "0017"    # nothing was migrated
    assert os.path.exists(db)
