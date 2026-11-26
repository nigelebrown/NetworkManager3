"""No hardware is contacted to classify the Phase 2 evidence checklist."""
from datetime import datetime, timezone
from types import SimpleNamespace

from backend.domain.phase2_acceptance import build_acceptance, normalized_model


def _d(family, model, adapter="manual_snapshot"):
    return SimpleNamespace(
        id="synthetic-device", source_type="REAL", family=family, model=model,
        firmware="testing", adapter=adapter, last_observed_at=datetime.now(timezone.utc),
    )


def _baseline(data, source="aruba_instant_rest"):
    return SimpleNamespace(id="snapshot-test", source=source, completeness="PARTIAL",
        warning="Not a restorable configuration", config_json=data,
        captured_at=datetime.now(timezone.utc))


def test_supported_models_are_normalized_without_claiming_firmware_certification():
    examples = [
        ("access_point", "Aruba AP-505", "AP-505"),
        ("access_point", "AP 535-RW", "AP-535"),
        ("switch", "Aruba CX 6100", "CX 6100"),
        ("switch", "CX 6200F 48G", "CX 6200"),
    ]
    for family, reported, canonical in examples:
        assert normalized_model(family, reported) == canonical
    for family, reported in [
        ("access_point", "AP-575"), ("access_point", "CX 6200"),
        ("switch", "CX 6300M"), ("switch", "AP-535"),
        ("switch", ""), ("access_point", "AP-5350"),
    ]:
        assert normalized_model(family, reported) is None


def test_instant_snapshot_does_not_infer_radios_or_channel_width():
    report = build_acceptance(_d("access_point", "AP-535", "aruba_readonly_probe"),
        profile=None, binding=None,
        snapshot=_baseline({"ssids": [{"profile": "Admin", "essid": "Admin",
                                       "vlan": 100}], "vlans": [100]}),
        drafts=[], writes_locked=True)
    statuses = {f["field"]: f["status"] for f in report["field_checks"]}
    assert statuses["ssid_profiles"] == "OBSERVED"
    assert statuses["ssid_vlan_mappings"] == "OBSERVED"
    assert statuses["radio_channel_power"] == "NOT_VERIFIED"
    assert statuses["channel_width"] == "NOT_VERIFIED"
    assert report["hardware_certified"] is False
    assert report["stage"] == "BASELINE_CAPTURED"


def test_cx_partial_snapshot_does_not_infer_vlan_assignment():
    report = build_acceptance(_d("switch", "CX 6100", "aruba_readonly_probe"),
        profile=None, binding=None,
        snapshot=_baseline({"vlans": [{"id": 100}],
                            "interfaces": [{"name": "1/1/1", "admin_state": "up"}]}),
        drafts=[], writes_locked=True)
    statuses = {f["field"]: f["status"] for f in report["field_checks"]}
    assert statuses["vlans"] == "OBSERVED"
    assert statuses["interfaces"] == "OBSERVED"
    assert statuses["interface_access_vlan"] == "NOT_VERIFIED"
    assert statuses["lacp_poe_lldp"] == "NOT_VERIFIED"


def test_unsafe_device_write_profile_is_not_pilot_eligible():
    report = build_acceptance(_d("switch", "CX 6200"), None, None, None, [],
                              writes_locked=False)
    assert report["device_writes"] == "UNSAFE"
    assert report["operator_pilot_eligible"] is False
    assert report["hardware_certified"] is False
