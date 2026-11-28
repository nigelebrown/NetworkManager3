"""rc18 (2.12) Imported Group column: import -> validated assignment -> inheritance -> inventory, with direct
database verification. Covers new, existing, bulk, invalid, duplicate (case-insensitive) and cross-family groups,
partial import with rejected rows, devices listed twice or with conflicting group values, DEMO/REAL separation,
reassignment, dry run and permissions."""
from sqlalchemy import select

from backend.database.models_remediation import DeviceGroup, DeviceGroupMember
from backend.database.session import session_scope
from tests.conftest import make_user


def _import(c, h, rows):
    r = c.post("/api/v1/quick-devices/bulk", headers=h, json={"devices": rows, "mode": "partial"})
    assert r.status_code == 201, r.text
    return {x["serial_number"]: x["id"] for x in r.json()["results"]}, r.json().get("rejected", [])


def _rows():
    rows = [{"family": "switch", "site_name": "HQ", "hostname": f"sw-{i}", "serial_number": f"SWG{i:03d}",
             "model": "CX 6200F 24G", "status": "up"} for i in range(6)]
    rows += [{"family": "access_point", "site_name": "HQ", "hostname": f"ap-{i}", "serial_number": f"APG{i:03d}",
              "model": "AP-515", "status": "up"} for i in range(3)]
    rows.append({"family": "switch", "site_name": "HQ", "hostname": "dup", "serial_number": "SWG000",
                 "model": "CX 6200"})                                       # duplicate serial -> rejected row
    return rows


def _members(db, name):
    g = db.scalar(select(DeviceGroup).where(DeviceGroup.name == name))
    return g, {m.device_id for m in db.scalars(select(DeviceGroupMember).where(DeviceGroupMember.group_id == g.id))}


def test_import_assign_inherit_and_inventory_end_to_end(client, admin):
    c, h = client, admin
    ids, rejected = _import(c, h, _rows())
    assert len(ids) == 9 and len(rejected) == 1 and rejected[0]["serial_number"] == "SWG000"
    existing = c.post("/api/v1/device-groups", headers=h, json={"name": "Clinic Switches", "family": "switch"}).json()
    assert c.post("/api/v1/device-groups", headers=h, json={"name": "clinic switches", "family": "switch"}).status_code == 409
    c.post("/api/v1/device-groups", headers=h, json={"name": "Lobby", "family": "access_point"})
    sw = [ids[f"SWG{i:03d}"] for i in range(6)]
    ap = [ids[f"APG{i:03d}"] for i in range(3)]
    body = {"reassign": False, "assignments": [
        {"group": "  CLINIC   switches ", "family": "switch", "device_ids": sw[:3], "target": "create"},  # matches existing
        {"group": "Core", "family": "switch", "device_ids": sw[3:5] + [sw[0]], "target": "create"},       # sw0 conflicting
        {"group": "Lobby", "family": "switch", "device_ids": [sw[5]], "target": "create"},                # name used by APs
        {"group": "x", "family": "access_point", "device_ids": [ap[0]], "target": "create"},             # invalid name
        {"group": "Wing <A>", "family": "access_point", "device_ids": [ap[1]], "target": "create"},      # invalid chars
        {"group": "Wards", "family": "access_point", "device_ids": [ap[2], ap[2], sw[1]], "target": "create"},
    ]}
    dry = c.post("/api/v1/device-groups/import-assign", headers=h, json=body | {"dry_run": True}).json()
    assert dry["dry_run"] and not dry["created_groups"]
    with session_scope() as db:
        assert not db.scalar(select(DeviceGroup).where(DeviceGroup.name == "Core"))        # dry run wrote nothing
    r = c.post("/api/v1/device-groups/import-assign", headers=h, json=body).json()
    by = {}
    for x in r["results"]:
        by.setdefault(x["device_id"], []).append(x)
    assert {x["status"] for x in by[sw[1]]} == {"ASSIGNED", "REJECTED"}       # clinic OK; Wards = wrong family
    assert [x["status"] for x in by[sw[0]]] == ["REJECTED", "REJECTED"] or "conflicting" in by[sw[0]][0]["reason"]
    assert "already used by a access point group" in by[sw[5]][0]["reason"]
    assert "2-100" in by[ap[0]][0]["reason"] and "not allowed" in by[ap[1]][0]["reason"]
    assert [x["status"] for x in by[ap[2]]] == ["ASSIGNED", "REJECTED"] and "more than once" in by[ap[2]][1]["reason"]
    assert {g["name"] for g in r["created_groups"]} == {"Core", "Wards"}         # existing group was reused
    with session_scope() as db:                                                   # database verification
        g, mem = _members(db, "Clinic Switches")
        assert g.id == existing["id"] and mem == {sw[1], sw[2]}
        _, core = _members(db, "Core")
        assert core == {sw[3], sw[4]}
        _, wards = _members(db, "Wards")
        assert wards == {ap[2]}
        assert db.scalar(select(DeviceGroup).where(DeviceGroup.name == "Lobby")).family == "access_point"
    # inheritance: a group template now applies to its imported members (and only to them)
    t = c.post("/api/v1/config-templates", headers=h, json={"name": "Core VLANs", "family": "switch", "scope": "group",
                                                             "group_id": r["created_groups"][0]["id"] if r["created_groups"][0]["name"] == "Core"
                                                             else r["created_groups"][1]["id"],
                                                             "priority": 50, "config": {"vlans": [77]}})
    assert t.status_code == 201, t.text
    inherits = c.get(f"/api/v1/config-templates/devices/{sw[3]}/preview", headers=h).json()
    assert any(x["name"] == "Core VLANs" and x["scope"] == "group" for x in inherits["templates"])
    other = c.get(f"/api/v1/config-templates/devices/{sw[1]}/preview", headers=h).json()
    assert not any(x["name"] == "Core VLANs" for x in other["templates"])
    # inventory shows the memberships
    inv = {d["id"]: d for d in c.get("/api/v1/devices", headers=h).json()}
    assert [g["name"] for g in inv[sw[3]]["groups"]] == ["Core"] and inv[sw[5]]["groups"] == []
    # reassignment moves a device between groups of the same type
    re = c.post("/api/v1/device-groups/import-assign", headers=h, json={"reassign": True, "assignments": [
        {"group": "Core", "family": "switch", "device_ids": [sw[1]], "target": "create"}]}).json()
    assert re["results"][0]["status"] == "ASSIGNED" and re["results"][0]["moved_from"] == ["Clinic Switches"]
    with session_scope() as db:
        assert _members(db, "Clinic Switches")[1] == {sw[2]} and sw[1] in _members(db, "Core")[1]
    # re-import of the same file is idempotent
    again = c.post("/api/v1/device-groups/import-assign", headers=h, json={"reassign": False, "assignments": [
        {"group": "Core", "family": "switch", "device_ids": [sw[3]], "target": "create"}]}).json()
    assert again["results"][0]["status"] == "ALREADY_MEMBER" and not again["created_groups"]
    acts = {e["action"] for e in c.get("/api/audit?limit=300", headers=h).json()}
    assert "device_group_import_assigned" in acts


def test_existing_target_permissions_and_demo_real_separation(client, admin):
    c, h = client, admin
    ids, _ = _import(c, h, _rows()[:2])
    sw = list(ids.values())
    ap_group = c.post("/api/v1/device-groups", headers=h, json={"name": "AP only", "family": "access_point"}).json()
    r = c.post("/api/v1/device-groups/import-assign", headers=h, json={"assignments": [
        {"group": "AP only", "family": "switch", "device_ids": sw, "target": "existing", "group_id": ap_group["id"]}]}).json()
    assert r["counts"] == {"REJECTED": 2} and "not found for this device type" in r["results"][0]["reason"]
    c.post("/api/v1/demo-data/load", headers=h)
    demo_sw = [d["id"] for d in c.get("/api/v1/devices", headers=h).json() if d["source_type"] == "DEMO" and d["family"] == "switch"][:1]
    if demo_sw:
        r = c.post("/api/v1/device-groups/import-assign", headers=h, json={"assignments": [
            {"group": "Mixed", "family": "switch", "device_ids": sw[:1] + demo_sw, "target": "create"}]}).json()
        assert [x["status"] for x in r["results"]] == ["ASSIGNED", "REJECTED"]
        assert "DEMO and REAL" in r["results"][1]["reason"]
    op = make_user(c, h, "grp.operator", "operator")
    assert c.post("/api/v1/device-groups/import-assign", headers=op, json={"assignments": [
        {"group": "Nope", "family": "switch", "device_ids": sw, "target": "create"}]}).status_code == 403
    r = c.post("/api/v1/device-groups/import-assign", headers=h, json={"assignments": [
        {"group": "Ghost", "family": "switch", "device_ids": ["00000000-0000-0000-0000-000000000000"], "target": "create"}]}).json()
    assert r["results"][0]["status"] == "REJECTED" and "not found" in r["results"][0]["reason"]
