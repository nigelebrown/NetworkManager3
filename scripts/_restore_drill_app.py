"""Helper for validate_pg_migration_restore.py: seed a head-schema DB through the real API, or verify a restored copy.

Prints one JSON line with record counts and the audit-chain state. Uses only the environment passed by the caller.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402

PW = "Restore-Drill-2026!"


def summary(client, headers):
    from backend.database.session import session_scope
    from backend.security.deps import verify_audit_chain
    devices = sorted(d["serial_number"] for d in client.get("/api/v1/devices", headers=headers).json()
                     if d["source_type"] == "REAL")
    users = sorted(u["username"] for u in client.get("/api/v1/users", headers=headers).json())
    groups = sorted(g["name"] for g in client.get("/api/v1/port-groups", headers=headers).json())
    profiles = sorted(p["name"] for p in client.get("/api/printers/credential-profiles", headers=headers).json())
    printers = sorted(p["serial_number"] for p in client.get("/api/printers", headers=headers).json())
    with session_scope() as db:
        chain = verify_audit_chain(db)
    return {"devices": devices, "users": users, "port_groups": groups, "printer_profiles": profiles,
            "printers": printers, "audit_chain_ok": chain["ok"]}


def main(mode):
    with TestClient(create_app()) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": PW})
        assert r.status_code == 200, r.text
        h = {"Authorization": "Bearer " + r.json()["access_token"]}
        if mode == "seed":
            batch = [{"family": "access_point" if i % 2 else "switch", "site_name": "Restore HQ",
                      "hostname": f"rst-{i}", "serial_number": f"RST-{i:04d}", "model": "AP-505" if i % 2 else "CX 6200F",
                      "status": "unknown", "metrics": {}} for i in range(40)]
            assert c.post("/api/v1/quick-devices/bulk", headers=h, json={"devices": batch}).status_code == 201
            assert c.post("/api/v1/users", headers=h, json={"username": "restore.viewer", "role": "viewer",
                                                            "temporary_password": "Restore-Temp-2026!",
                                                            "site_scope": "all"}).status_code == 201
            assert c.post("/api/v1/port-groups", headers=h, json={"name": "BWC Dock", "members": []}).status_code == 201
            assert c.post("/api/printers/credential-profiles", headers=h, json={
                "name": "Restore v3", "version": "3", "username": "u", "auth_passphrase": "auth-pass-1",
                "privacy_passphrase": "priv-pass-1", "auth_protocol": "SHA", "priv_protocol": "AES256"}).status_code == 201
            assert c.post("/api/printers", headers=h, json={"manufacturer": "HP", "model": "Color LaserJet M454dn",
                                                            "serial_number": "RST-PRN-1"}).status_code == 201
        print(json.dumps(summary(c, h), sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1])
