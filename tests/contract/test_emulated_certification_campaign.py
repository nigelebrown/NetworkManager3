"""Emulated write-lifecycle certification campaign for the four target models (Section 6).

AP-505, AP-535 (Aruba Instant 8.10 emulator) and CX 6100 24G, CX 6200F 48G (strict AOS-CX REST emulator).
For every model: every typed operation goes through backup -> checkpoint -> apply -> readback -> rollback ->
full-config comparison; independent approval; a real change plan executed with APPLIED_VERIFIED; injected
device failure -> ROLLED_BACK; and (CX) session expiry, CSRF and unsupported-firmware negotiation.

EMULATED results are SOFTWARE PASS ONLY. They are recorded with environment=EMULATED, never count as hardware
certification, and hardware writes stay locked throughout. Set INM_WRITE_EVIDENCE=1 to (re)write
evidence/certification/emulated_certification_runs.json and emulated_certification_table.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from backend.database.session import session_scope
from backend.infrastructure import change_executor as X
from backend.infrastructure.writers import WriterError
from backend.infrastructure.writers.emulator import CxEmulator
from tests.contract.test_production_read_write import approve, certify, env, live, plan  # noqa: F401

MODELS = [
    ("switch", "CX 6100 24G", "PL.10.14.1000"),
    ("switch", "CX 6200F 48G", "ML.10.14.1020"),
    ("access_point", "AP-505", "8.10.0.12"),
    ("access_point", "AP-535", "8.10.0.12"),
]
EVIDENCE = Path(__file__).resolve().parents[2] / "evidence" / "certification"


def _retarget(device_id: str, model: str, firmware: str) -> None:
    from backend.database.models_inm import InmDevice
    with session_scope() as db:
        d = db.get(InmDevice, device_id)
        d.model, d.firmware = model, firmware
    X._EMULATORS.pop(device_id, None)


def _lifecycle(c, adm, appr, dev, family) -> dict:
    """Plan -> approve -> execute (verified) -> injected failure -> automatic rollback."""
    if family == "switch":
        good = [{"op": "switch.vlan.define", "value": 301},
                {"op": "switch.port.access_vlan", "port": "1/1/5", "value": 301},
                {"op": "switch.port.description", "port": "1/1/5", "value": "campaign"}]
        bad = [{"op": "switch.vlan.define", "value": 302},
               {"op": "switch.port.access_vlan", "port": "1/1/6", "value": 302}]
    else:
        good = [{"op": "ap.radio.width", "value": 20}, {"op": "ap.band_steering", "value": "prefer-5ghz"}]
        bad = [{"op": "ap.radio.width", "value": 40}]
    ex = c.post(f"/api/v1/changes/plans/{approve(c, appr, plan(c, adm, dev, good))['id']}/execute", headers=adm).json()
    emu = X._EMULATORS[dev["id"]]
    if family == "switch":
        emu.fail_path = "/system/interfaces/1/1/6"
    else:
        emu.lie = True                          # device accepts the change but readback disagrees
    ex2 = c.post(f"/api/v1/changes/plans/{approve(c, appr, plan(c, adm, dev, bad))['id']}/execute", headers=adm).json()
    if family == "switch":
        emu.fail_path = None
    else:
        emu.lie = False
    return {"apply": ex["outcome"], "apply_steps": [s["step"] for s in ex["steps"]],
            "failure_injected": ex2["outcome"],
            "rollback_verified": any(s["step"] == "rollback_readback" and s["ok"] for s in ex2["steps"])}


def _cx_protocol(model: str, firmware: str) -> dict:
    """Strict-emulator protocol checks: CSRF on every request, no write replay after expiry, safe read re-login, no
    credentials in URLs, version negotiation, and refusal of firmware older than the supported range."""
    emu = CxEmulator(model=model, firmware=firmware, ports=24 if "24G" in model else 48)
    w = emu.writer()
    w.open()
    w.apply({"op": "switch.vlan.define", "value": 401})
    emu.expire_sessions()
    # Never automatically replay a mutating REST call after auth expiration:
    # production code must reconcile an uncertain write before any retry.
    try:
        w.apply({"op": "switch.vlan.define", "value": 402})
        blocked = False
    except WriterError as exc:
        blocked = exc.category == "AUTH_REJECTED" and emu.writes == 1 and "402" not in emu.state["vlans"]
    # A read-only observation may reauthenticate once. The test then starts a
    # fresh manual emulator operation with a known-good session.
    w.identity()
    w.apply({"op": "switch.vlan.define", "value": 402})
    ok, _ = w.readback({"op": "switch.vlan.define", "value": 402})
    w.close()
    old = CxEmulator(model=model, firmware=firmware.split(".")[0] + ".10.08.1010")
    try:
        old.writer().open()
        refused = None
    except WriterError as exc:
        refused = exc.category
    return {"relogin_after_expiry": blocked and emu.logins == 2 and ok and getattr(emu, "expired_hits", 0) == 1, "protocol_violations": list(emu.violations),
            "negotiated_api": emu.writer().protocol().get("api_version") if hasattr(emu.writer(), "protocol") else None,
            "firmware_10_08": refused}


@pytest.mark.parametrize("family,model,firmware", MODELS)
def test_emulated_certification_campaign(env, family, model, firmware):
    c, adm, appr, sw, ap = env
    dev = sw if family == "switch" else ap
    _retarget(dev["id"], model, firmware)
    test = {"test_port": "1/1/20", "lag_ports": ["1/1/21", "1/1/22"]} if family == "switch" else {"test_ssid": "Demo-Corp"}
    res, failed = certify(c, adm, appr, dev, **test)
    # Instant full-config restore is deliberately UNSUPPORTED (guided Replace-device procedure), not a failure
    hard_fail = [x for x in failed if not x.get("unsupported")]
    assert res["environment"] == "EMULATED" and not hard_fail, hard_fail
    if family == "access_point":
        assert [x["operation"] for x in failed] == ["config.restore"]
    assert res["model_family"].replace("CX ", "") in model
    # emulated certification never unlocks hardware and never reports hardware certification
    caps = c.get(f"/api/v1/devices/{dev['id']}/capabilities", headers=adm).json()
    assert caps["environment"] == "EMULATED" and caps["hardware_certified"] is False
    life = _lifecycle(c, adm, appr, dev, family)
    assert life["apply"] == "APPLIED_VERIFIED", life
    assert life["failure_injected"] == "ROLLED_BACK" and life["rollback_verified"], life
    proto = _cx_protocol(model, firmware) if family == "switch" else None
    if proto:
        assert proto["relogin_after_expiry"] and not proto["protocol_violations"], proto
        assert proto["firmware_10_08"] == "UNSUPPORTED_API", proto
    if os.environ.get("INM_WRITE_EVIDENCE"):
        _record(model, firmware, res, life, proto)


def _record(model, firmware, res, life, proto) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    jp = EVIDENCE / "emulated_certification_runs.json"
    runs = json.loads(jp.read_text()) if jp.exists() and os.environ.get("INM_EVIDENCE_APPEND") else {}
    runs[model] = {"environment": "EMULATED", "evidence_class": "SOFTWARE PASS ONLY - not hardware certification",
                   "firmware": firmware, "adapter": res["adapter"], "model_family": res["model_family"],
                   "firmware_train": res["firmware_train"], "run_id": res["run_id"], "results": res["results"],
                   "lifecycle": life, "protocol": proto}
    jp.write_text(json.dumps(runs, indent=1, default=str))
    os.environ["INM_EVIDENCE_APPEND"] = "1"
    rows = ["# Emulated certification campaign — SOFTWARE PASS ONLY (protocol emulators, NOT hardware evidence)",
            "", "Generated by `INM_WRITE_EVIDENCE=1 pytest tests/contract/test_emulated_certification_campaign.py`.",
            "Hardware writes remained locked. Every row below must be repeated on the physical model before any",
            "operation can be certified for hardware.", "",
            "| Model | Firmware | Adapter | Operation | Result | Readback | Rollback restored |", "|---|---|---|---|---|---|---|"]
    for m, r in runs.items():
        for x in r["results"]:
            ev = x["evidence"]
            rows.append(f"| {m} | {r['firmware']} | {r['adapter']} | {x['operation']} | "
                        f"{'PASS (emulated)' if x['passed'] else ('UNSUPPORTED' if x.get('unsupported') else 'FAIL')} | "
                        f"{'yes' if 'readback' in ev else '-'} | {ev.get('rollback_restored', ev.get('matches_baseline', '-'))} |")
    rows += ["", "## Lifecycle and protocol checks", "",
             "| Model | Plan executed | Injected failure | Rollback verified | Re-login after session expiry | CSRF/URL violations | Firmware 10.08 |",
             "|---|---|---|---|---|---|---|"]
    for m, r in runs.items():
        p = r["protocol"] or {}
        rows.append(f"| {m} | {r['lifecycle']['apply']} | {r['lifecycle']['failure_injected']} | "
                    f"{r['lifecycle']['rollback_verified']} | {p.get('relogin_after_expiry', 'n/a (SSH/REST Instant)')} | "
                    f"{len(p['protocol_violations']) if p else 'n/a'} | {p.get('firmware_10_08', 'n/a')} |")
    rows += ["", "Not modelled by the emulators (hardware-only): per-model radio chains and RF limits (AP-505 2x2 vs",
             "AP-535 4x4), PoE budget, real reboot/boot-time behaviour, firmware-specific REST quirks beyond the",
             "version catalogue, Instant cluster conductor failover."]
    (EVIDENCE / "emulated_certification_table.md").write_text("\n".join(rows) + "\n")
