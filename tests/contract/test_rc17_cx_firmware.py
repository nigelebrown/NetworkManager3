"""rc17 AOS-CX firmware lifecycle on the CX emulator (EMULATED; no hardware). Vendor-hash gate, train/downgrade
checks, independent approval, stage to the non-booted slot, boot verification, recovery, no blind re-send."""
import hashlib
import io

from tests.conftest import login, make_user


def _img(name="ML_10_15_0005.swi", size=4096):
    data = (name.encode() * (size // len(name) + 1))[:size]
    return name, data, hashlib.sha256(data).hexdigest()


def _switch(c, h, serial="FW-CX-1", fw="ML.10.14.1020"):
    from backend.infrastructure.writers.emulator import CxEmulator
    r = c.post("/api/v1/quick-devices", headers=h, json={"site_name": "HQ", "family": "switch", "hostname": serial.lower(),
               "serial_number": serial, "model": "CX 6200F 24G", "firmware": fw, "status": "up", "metrics": {}})
    assert r.status_code == 201, r.text
    did = r.json()["id"]
    from backend.database.models_inm import InmDevice
    from backend.database.session import session_scope
    with session_scope() as db:
        db.get(InmDevice, did).source_type = "DEMO"          # labelled emulator path, never a network call
    from backend.infrastructure import change_executor as X
    emu = CxEmulator(model="6200F 24G", firmware=fw, ports=24)
    emu.state["system"]["serial_number"] = serial
    X._EMULATORS[did] = emu
    return did, emu


def test_cx_firmware_lifecycle_and_safety_gates(client, admin, monkeypatch, tmp_path):
    monkeypatch.setenv("SIMS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("INM_CX_REBOOT_WAIT_SECONDS", "1")
    monkeypatch.setenv("INM_CX_REBOOT_POLL_SECONDS", "0.05")
    from backend.config import get_settings
    get_settings.cache_clear()
    appr = make_user(client, admin, "fw.appr", "approver")
    did, emu = _switch(client, admin)
    name, data, sha = _img()
    bad = client.post("/api/v1/switch-firmware/images", headers=admin, data={"vendor_sha256": "0" * 64},
                      files={"file": (name, io.BytesIO(data))})
    assert bad.status_code == 422 and "discarded" in bad.json()["detail"]
    img = client.post("/api/v1/switch-firmware/images", headers=admin, data={"vendor_sha256": sha},
                      files={"file": (name, io.BytesIO(data))}).json()
    assert img["authenticity"] == "VENDOR_HASH_MATCH" and img["version"] == "ML.10.15.0005"
    # wrong train and downgrade refused at planning
    pl_name, pl_data, pl_sha = _img("PL_10_15_0005.swi")
    pl = client.post("/api/v1/switch-firmware/images", headers=admin, data={"vendor_sha256": pl_sha},
                     files={"file": (pl_name, io.BytesIO(pl_data))}).json()
    assert client.post("/api/v1/switch-firmware/plans", headers=admin,
                       json={"device_id": did, "image_id": pl["id"]}).status_code == 422
    plan = client.post("/api/v1/switch-firmware/plans", headers=admin, json={"device_id": did, "image_id": img["id"]}).json()
    assert plan["status"] == "PENDING_APPROVAL"
    assert client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/stage", headers=admin).status_code == 409   # unapproved
    assert client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/approve", headers=admin).status_code == 403  # self
    assert client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/approve", headers=appr).json()["status"] == "APPROVED"
    staged = client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/stage", headers=admin).json()
    assert "status" in staged, staged
    assert staged["status"] == "STAGED" and staged["staged_slot"] == "secondary"
    assert emu.fw["secondary"] == "ML.10.15.0005" and emu.fw["booted"] == "primary"       # no reboot during stage
    assert client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/boot", headers=admin, json={}).status_code == 422
    booted = client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/boot", headers=admin,
                         json={"acknowledge_reboot": True}).json()
    assert booted["status"] == "BOOT_VERIFIED" and emu.state["system"]["software_version"] == "ML.10.15.0005"
    assert client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/boot", headers=admin,
                       json={"acknowledge_reboot": True}).status_code == 409          # never re-sent
    rec = client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/recover", headers=admin).json()
    assert rec["status"] == "RECOVERED" and emu.state["system"]["software_version"] == "ML.10.14.1020"
    steps = [h["step"] for h in rec["history"]]
    assert steps == ["PLANNED", "APPROVED", "STAGE", "BOOT_SENT", "BOOT", "RECOVER"]


def test_boot_that_comes_back_on_old_image_requires_recovery(client, admin, monkeypatch, tmp_path):
    monkeypatch.setenv("SIMS_DATA_DIR", str(tmp_path))
    from backend.config import get_settings
    get_settings.cache_clear()
    appr = make_user(client, admin, "fw.appr2", "approver")
    did, emu = _switch(client, admin, "FW-CX-2")
    name, data, sha = _img()
    img = client.post("/api/v1/switch-firmware/images", headers=admin, data={"vendor_sha256": sha},
                      files={"file": (name, io.BytesIO(data))}).json()
    plan = client.post("/api/v1/switch-firmware/plans", headers=admin, json={"device_id": did, "image_id": img["id"]}).json()
    client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/approve", headers=appr)
    client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/stage", headers=admin)
    emu.fail_boot = True
    out = client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/boot", headers=admin, json={"acknowledge_reboot": True}).json()
    assert out["status"] == "RECOVERY_REQUIRED"
    viewer = make_user(client, admin, "fw.viewer", "viewer")
    assert client.post(f"/api/v1/switch-firmware/plans/{plan['id']}/recover", headers=viewer).status_code == 403
