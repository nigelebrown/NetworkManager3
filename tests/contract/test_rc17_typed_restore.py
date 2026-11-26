"""rc17 typed reconciliation for Instant (EMULATED): drift -> typed ops -> applied through the real writer ->
back in sync; 80 MHz / deleted SSIDs reported as unrestorable, never replayed as raw CLI."""
from backend.domain import change_control as CC
from backend.domain import instant_reconcile as IR
from backend.infrastructure.writers.emulator import InstantEmulator


def test_drift_is_reconciled_with_typed_operations_only():
    e = InstantEmulator()
    baseline = e.text
    e._cli(["arm", "no client-match", "band-steering-mode balance-bands", "exit",
            "wlan ssid-profile Corp-WiFi", "dot11k", "vlan 200", "exit"])
    r = IR.plan(baseline, e.text)
    kinds = sorted(o["op"] for o in r["operations"])
    assert kinds == ["ap.band_steering", "ap.client_match", "ap.ssid.roaming", "ap.ssid.vlan"], r
    assert r["unrestorable"] == []
    w = e.writer()
    w.open()
    for op in r["operations"]:
        v = CC.validate_operation(op, family="access_point")
        w.checkpoint()
        w.apply(v)
        assert w.readback(v)[0], op
    assert IR.plan(baseline, e.text)["in_sync"] is True


def test_80mhz_and_deleted_ssids_are_never_restored_blindly():
    e = InstantEmulator()
    baseline = e.text.replace(" no 80mhz-support\n", "")          # baseline that allowed 80 MHz
    cur = e.text
    e._cli(["no wlan ssid-profile Corp-WiFi"])
    r = IR.plan(baseline, e.text)
    reasons = {u["setting"]: u["reason"] for u in r["unrestorable"]}
    assert "arm wide-bands" in reasons and "80 MHz is never configured" in reasons["arm wide-bands"]
    assert "wlan ssid-profile Corp-WiFi" in reasons and "secret_ref" in reasons["wlan ssid-profile Corp-WiFi"]
    assert not any(o["op"] == "ap.radio.width" for o in r["operations"])
    assert IR.plan(cur, cur)["in_sync"] is True
