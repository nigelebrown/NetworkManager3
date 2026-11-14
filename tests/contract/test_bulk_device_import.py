"""Usable manual AP/CX bulk intake, preview-compatible payload and safety."""
from tests.conftest import make_user


def _device(family, serial, site="Central Office", name=None):
    return {
        "family": family, "site_name": site, "hostname": name or serial,
        "serial_number": serial, "model": "AP-535" if family == "access_point" else "CX 6200F",
        "status": "unknown", "metrics": {},
    }


def test_bulk_import_adds_real_aps_and_switches_to_inventory(client, admin):
    payload = {"devices": [
        _device("access_point", "AP-CN-12345"),
        _device("switch", "SW-CN-67890"),
    ]}
    r = client.post("/api/v1/quick-devices/bulk", headers=admin, json=payload)
    assert r.status_code == 201, r.text
    out = r.json()
    assert (out["count"], out["added"], out["updated"]) == (2, 2, 0)
    assert out["device_writes"] == "LOCKED" and out["source_type"] == "REAL"
    devices = client.get("/api/v1/devices", headers=admin).json()
    serials = {d["serial_number"] for d in devices if d["source_type"] == "REAL"}
    assert serials == {"AP-CN-12345", "SW-CN-67890"}


def test_repeat_import_updates_existing_identity_without_duplicates(client, admin):
    row = _device("access_point", "AP-EXIST-02")
    first = client.post("/api/v1/quick-devices/bulk", headers=admin, json={"devices": [row]})
    assert first.status_code == 201, first.text
    second = client.post("/api/v1/quick-devices/bulk", headers=admin, json={
        "devices": [{**row, "hostname": "NEW-NAME", "serial_number": "ap-exist-02"}],
    })
    assert second.status_code == 201, second.text
    assert second.json()["updated"] == 1
    assert first.json()["results"][0]["id"] == second.json()["results"][0]["id"]
    devices = client.get("/api/v1/devices", headers=admin).json()
    matching = [d for d in devices if d["source_type"] == "REAL"]
    assert len(matching) == 1 and matching[0]["hostname"] == "NEW-NAME"


def test_duplicate_rows_refused_without_persisting_first_row(client, admin):
    payload = {"devices": [
        _device("access_point", "DUPE-123"),
        _device("access_point", "dupe-123"),
    ]}
    response = client.post("/api/v1/quick-devices/bulk", headers=admin, json=payload)
    assert response.status_code == 422
    assert not any(x["source_type"] == "REAL" for x in client.get("/api/v1/devices", headers=admin).json())


def test_cross_family_conflict_refused_before_any_new_record_created(client, admin):
    created = client.post("/api/v1/quick-devices", headers=admin,
                          json=_device("access_point", "AP-REAL-ONE"))
    assert created.status_code == 201
    payload = {"devices": [
        _device("switch", "SW-NEW-NOT-TO-CREATE"),
        _device("switch", "AP-REAL-ONE"),
    ]}
    response = client.post("/api/v1/quick-devices/bulk", headers=admin, json=payload)
    assert response.status_code == 409
    serials = {x["serial_number"] for x in client.get("/api/v1/devices", headers=admin).json()
               if x["source_type"] == "REAL"}
    assert "SW-NEW-NOT-TO-CREATE" not in serials and "AP-REAL-ONE" in serials


def test_bulk_inventory_only_not_telemetry_or_live_probe(client, admin):
    payload = {"devices": [_device("access_point", "METRIC-DO-NOT-IMPORT")]}
    payload["devices"][0]["metrics"] = {"clients": 10}
    response = client.post("/api/v1/quick-devices/bulk", headers=admin, json=payload)
    assert response.status_code == 422
    assert not any(x["source_type"] == "REAL" for x in client.get("/api/v1/devices", headers=admin).json())


def test_bulk_limit_and_role_enforced(client, admin):
    operator = make_user(client, admin, "operator.bulk", "operator")
    payload = {"devices": [_device("switch", "TEST-RBAC-1")]}
    assert client.post("/api/v1/quick-devices/bulk", headers=operator, json=payload).status_code == 403
    payload = {"devices": [_device("switch", f"SERIAL-{i:03d}") for i in range(101)]}
    assert client.post("/api/v1/quick-devices/bulk", headers=admin, json=payload).status_code == 422


def test_assign_imported_device_to_new_or_existing_site(client, admin):
    imported = client.post("/api/v1/quick-devices/bulk", headers=admin, json={"devices": [
        _device("switch", "SITE-MOVE-SW"), _device("access_point", "SITE-MOVE-AP"),
    ]})
    assert imported.status_code == 201
    switch, ap = [row["id"] for row in imported.json()["results"]]
    site_ids = []
    for device in (switch, ap):
        path = f"/api/v1/quick-devices/{device}/site"
        response = client.patch(path, headers=admin, json={"site_name": "Office Center Building"})
        assert response.status_code == 200, response.text
        site_id = response.json()["site_id"]
        assert client.get(f"/api/v1/devices/{device}", headers=admin).json()["site_id"] == site_id
        site_ids.append(site_id)
    assert site_ids[0] == site_ids[1]
