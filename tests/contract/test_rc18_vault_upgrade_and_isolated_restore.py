"""rc18 (register P5-DEP-01, P5-06): versioned vault records survive an upgrade, and a vault backup restores onto an
isolated second instance.

P5-DEP-01: the PREVIOUS release (rc17, commit f6f44ce, schema 0015) seals configuration artifacts with its own code;
this release migrates that database to 0016 and verifies every artifact; a record written by a newer format is
refused instead of guessed at.
P5-06: the management database and vault objects are copied to a second, isolated instance; with the same vault key
every artifact verifies there; tampering in the copy is detected there without affecting the original; a different
key cannot open the copy (the key must be escrowed separately from backups)."""
import json
import os
import shutil
import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PREVIOUS = os.environ.get("INM_TEST_PREVIOUS_RELEASE", "420dabf55f3761884ddbf05553748dedb058fe73")

OLD_SEALER = r"""
import os, sys, json
sys.path.insert(0, sys.argv[1])
from backend.database import session as dbs
dbs.init_engine(); dbs.create_schema()
from backend.database.models_inm import InmDevice
from backend.infrastructure.change_executor import seal_artifact
out = []
with dbs.session_scope() as db:
    d = InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family="switch", model="CX 6200F 24G",
                  serial_number="VAULT-UPG-1", hostname="vault-sw", firmware="ML.10.14.1020", adapter="aruba_cx_10_14")
    db.add(d); db.flush()
    for i in range(3):
        a = seal_artifact(db, d, json.dumps({"System": {"hostname": "vault-sw", "rev": i}}).encode(),
                          trigger="MANUAL", actor="rc17-sealer", source="EMULATOR")
        out.append({"id": a.id, "format_version": a.manifest.get("format_version")})
    did = d.id
print(json.dumps({"device": did, "artifacts": out}))
"""


def _previous_release(tmp: Path) -> Path:
    try:
        data = subprocess.run(["git", "-C", str(ROOT), "archive", PREVIOUS], check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip(f"previous release {PREVIOUS} is not available in this checkout (shallow clone)")
    dest = tmp / "rc17"
    with tarfile.open(fileobj=BytesIO(data)) as t:
        t.extractall(dest, filter="data")
    return dest


def _env(db: Path, vault: Path, **extra) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SIMS_DATABASE", "INM_VAULT", "INM_TELEMETRY"))}
    return env | {"SIMS_DATABASE_URL": f"sqlite:///{db}", "INM_VAULT_DIR": str(vault), "SIMS_MODE": "demo",
                  "SIMS_ROLE": "api"} | extra


def _client(monkeypatch, db: Path, vault: Path, **extra):
    for k, v in _env(db, vault, **extra).items():
        monkeypatch.setenv(k, v)
    from backend.config import get_settings
    from backend.database import session as dbs
    from backend.infrastructure import runtime
    get_settings.cache_clear(); runtime.telemetry_store.cache_clear()
    dbs._engine = dbs._SessionLocal = None
    from fastapi.testclient import TestClient
    from backend.main import create_app
    return TestClient(create_app())


def test_previous_release_artifacts_verify_after_upgrade_and_restore_on_isolated_instance(app_env, tmp_path):
    from tests.conftest import login
    old = _previous_release(tmp_path)
    a_db, a_vault = tmp_path / "a" / "mgmt.db", tmp_path / "a" / "vault"
    a_db.parent.mkdir()
    r = subprocess.run([sys.executable, "-I", "-c", OLD_SEALER, str(old)], capture_output=True, text=True,
                       env=_env(a_db, a_vault), cwd=old, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    sealed = json.loads(r.stdout.strip().splitlines()[-1])
    assert [x["format_version"] for x in sealed["artifacts"]] == [1, 1, 1]
    # --- upgrade: this release migrates the rc17 database (0015 -> 0016) and opens every old record
    with _client(app_env, a_db, a_vault) as c:
        h = login(c)
        for x in sealed["artifacts"]:
            v = c.post(f"/api/v1/vault/artifacts/{x['id']}/verify", headers=h).json()
            assert v["integrity"] == "PASS", v
        versions = c.get(f"/api/v1/devices/{sealed['device']}/config-versions", headers=h)
        assert versions.status_code == 200 and len(versions.json().get("versions", versions.json())) >= 3
    # --- backup (DB file + vault objects) restored onto an isolated second instance
    b_db, b_vault = tmp_path / "b" / "mgmt.db", tmp_path / "b" / "vault"
    b_db.parent.mkdir()
    shutil.copy2(a_db, b_db)
    shutil.copytree(a_vault, b_vault)
    with _client(app_env, b_db, b_vault) as c:
        h = login(c)
        for x in sealed["artifacts"]:
            assert c.post(f"/api/v1/vault/artifacts/{x['id']}/verify", headers=h).json()["integrity"] == "PASS"
        objs = sorted(p for p in b_vault.rglob("*") if p.is_file())
        objs[0].write_bytes(objs[0].read_bytes()[:-3] + b"xyz")     # tamper one object in the COPY only
        results = [c.post(f"/api/v1/vault/artifacts/{x['id']}/verify", headers=h).json()["integrity"]
                   for x in sealed["artifacts"]]
        assert results.count("FAIL") == 1 and results.count("PASS") == 2
    with _client(app_env, a_db, a_vault) as c:                       # the original is unaffected
        h = login(c)
        assert all(c.post(f"/api/v1/vault/artifacts/{x['id']}/verify", headers=h).json()["integrity"] == "PASS"
                   for x in sealed["artifacts"])
    from cryptography.fernet import Fernet
    shutil.copy2(a_db, b_db)
    shutil.rmtree(b_vault)
    shutil.copytree(a_vault, b_vault)
    with _client(app_env, b_db, b_vault, INM_VAULT_MASTER_KEY=Fernet.generate_key().decode()) as c:
        h = login(c)
        v = c.post(f"/api/v1/vault/artifacts/{sealed['artifacts'][0]['id']}/verify", headers=h).json()
        assert v["integrity"] == "FAIL" and "key" in v["reason"].lower()


def test_newer_record_format_is_refused():
    from cryptography.fernet import Fernet
    from backend.domain import vault as V
    key = Fernet.generate_key()
    s = V.seal(b"x", master_key=key, identity={"fidelity": V.FIDELITY_FULL})
    manifest = dict(s.manifest, format_version=V.FORMAT_VERSION + 1)
    with pytest.raises(V.VaultIntegrityError, match="newer than this release"):
        V.open_sealed(s.ciphertext, wrapped_key=s.wrapped_key, manifest=manifest, signature=V._sign(key, manifest),
                      master_key=key)


OLD_SCOPED_SEED = r"""
import os, sys, json
sys.path.insert(0, sys.argv[1])
from backend.database import session as dbs
dbs.init_engine(); dbs.create_schema()
from backend.database.models import Site, User
from backend.database.models_inm import InmDevice, InmSite, SiteAccessGrant
from backend.security.crypto import hash_password
with dbs.session_scope() as db:
    out = {}
    for name in ("North", "South"):
        legacy = Site(name=name, vc_host=None, simulated=False)
        phys = InmSite(name=name, source_type="REAL")
        db.add_all([legacy, phys]); db.flush()
        devs = []
        for i in range(3):
            d = InmDevice(source_type="REAL", environment="live", vendor="Aruba", family="switch", model="CX 6100 12G",
                          serial_number=f"{name[:2].upper()}-{i}", hostname=f"{name.lower()}-sw-{i}", adapter="aruba_cx_10_14",
                          site_id=phys.id, legacy_site_id=legacy.id)
            db.add(d); db.flush(); devs.append(d.id)
        out[name] = {"legacy": legacy.id, "site": phys.id, "devices": devs}
    db.add(User(username="admin", password_hash=hash_password(os.environ["SIMS_ADMIN_PASSWORD"]), role="admin"))
    u = User(username="north.auditor", password_hash=hash_password("North-Auditor-2026!"), role="auditor",
             site_scope="granted", must_change_password=False)
    db.add(u); db.flush()
    db.add(SiteAccessGrant(user_id=u.id, site_id=out["North"]["legacy"], granted_by="rc17-seed"))
print(json.dumps(out))
"""


def test_scoped_inventory_and_audit_reports_after_upgrade(app_env, tmp_path):
    """INTEG-P11-04: data written by the previous release, upgraded, then reported by a site-scoped auditor."""
    from datetime import datetime, timedelta, timezone
    from tests.conftest import login
    old = _previous_release(tmp_path)
    db, vault = tmp_path / "s" / "mgmt.db", tmp_path / "s" / "vault"
    db.parent.mkdir()
    r = subprocess.run([sys.executable, "-I", "-c", OLD_SCOPED_SEED, str(old)], capture_output=True, text=True,
                       env=_env(db, vault), cwd=old, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    seed = json.loads(r.stdout.strip().splitlines()[-1])
    with _client(app_env, db, vault) as c:
        adm = login(c)
        aud = login(c, "north.auditor", "North-Auditor-2026!")
        rng = {"start": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
               "end": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
        inv = c.post("/api/v1/reports/exports", headers=aud, json={"kind": "inventory"} | rng).json()
        assert inv["meta"]["scope"].startswith("sites:1:") and inv["row_count"] == 3
        import csv
        import io
        assert {row["serial_number"] for row in csv.DictReader(io.StringIO(inv["body"]))} == {"NO-0", "NO-1", "NO-2"}
        full = c.post("/api/v1/reports/exports", headers=adm, json={"kind": "inventory"} | rng).json()
        assert full["meta"]["scope"] == "all-sites" and full["row_count"] == 6
        c.get(f"/api/v1/devices/{seed['South']['devices'][0]}/capabilities", headers=adm)
        aud_rows = c.post("/api/v1/reports/exports", headers=aud, json={"kind": "audit"} | rng).json()
        south = set(seed["South"]["devices"])
        assert not [x for x in csv.DictReader(io.StringIO(aud_rows["body"])) if x["target"] in south]
        assert aud_rows["signature"] and aud_rows["sha256"]
