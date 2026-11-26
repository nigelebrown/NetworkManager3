"""rc17 AP cluster reboot orchestration: independent approval, gates, journal-before-send, no replay, verification."""
from types import SimpleNamespace

import pytest

from backend.configmgmt import service as cfg
from backend.firmware import ap_reboot as R


class FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def _change(**kw):
    return SimpleNamespace(id=7, kind="firmware_stage", status="applied", created_by="ops.lead",
                           plan={"target": "8.10.0.30", "expected_members": [{"serial": "AP001"}, {"serial": "AP002"}]} | kw)


@pytest.fixture
def gates_ok(monkeypatch):
    monkeypatch.setattr(R, "audit", lambda *a, **k: None)
    monkeypatch.setattr(R, "_gates", lambda db, change, site, actor: ("SHA256:fp", "admin", "pw"))
    monkeypatch.setenv("INM_IAP_REBOOT_WAIT_SECONDS", "0")
    monkeypatch.setenv("INM_IAP_REBOOT_POLL_SECONDS", "0")


def test_reboot_needs_independent_approval_and_member_inventory(monkeypatch):
    monkeypatch.setattr(R, "audit", lambda *a, **k: None)
    with pytest.raises(cfg.PlanError, match="independent"):
        R.approve(FakeDb(), _change(), "ops.lead")
    with pytest.raises(cfg.PlanError, match="inventory"):
        R.approve(FakeDb(), _change(expected_members=[]), "approver.1")
    staged_not = _change()
    staged_not.status = "planned"
    with pytest.raises(cfg.PlanError, match="Stage"):
        R.approve(FakeDb(), staged_not, "approver.1")
    c = _change()
    a = R.approve(FakeDb(), c, "approver.1")
    assert a["members"] == ["AP001", "AP002"] and c.plan["reboot_approval"]["by"] == "approver.1"


def test_reboot_journals_before_sending_verifies_and_is_never_replayed(gates_ok):
    db, c, sent = FakeDb(), _change(), []
    R.approve(db, c, "approver.1")

    def runner(host, user, pw, fp, command, timeout):
        assert c.plan["reboot"]["state"] == "REBOOT_SENT" and db.commits >= 1   # journalled + committed first
        sent.append(command)
        raise ConnectionResetError("session dropped while reloading")          # expected during a reload
    out = R.reboot(db, c, SimpleNamespace(vc_host="192.0.2.10"), "admin", runner=runner,
                   verifier=lambda: {"state": "VERIFIED_RUNNING_TARGET"}, sleep=lambda s: None)
    assert sent == ["reload all"] and out["state"] == "REBOOT_VERIFIED"
    assert c.plan["reboot"]["send_error"] == "ConnectionResetError"
    with pytest.raises(cfg.PlanError, match="never sent twice"):
        R.reboot(db, c, SimpleNamespace(vc_host="192.0.2.10"), "admin", runner=runner,
                 verifier=lambda: {"state": "VERIFIED_RUNNING_TARGET"}, sleep=lambda s: None)
    assert sent == ["reload all"]


def test_unverified_members_leave_reboot_unverified_with_next_step(gates_ok):
    db, c = FakeDb(), _change()
    R.approve(db, c, "approver.1")
    out = R.reboot(db, c, SimpleNamespace(vc_host="192.0.2.10"), "admin", runner=lambda *a, **k: "",
                   verifier=lambda: {"state": "UNVERIFIED", "missing_serials": ["AP002"]}, sleep=lambda s: None)
    assert out["state"] == "REBOOT_UNVERIFIED" and "recovery" in out["next"]


def test_gate_refusal_sends_nothing(monkeypatch):
    monkeypatch.setattr(R, "audit", lambda *a, **k: None)
    db, c, sent = FakeDb(), _change(), []
    with pytest.raises(cfg.PlanError, match="not been approved"):
        R.reboot(db, c, SimpleNamespace(vc_host="192.0.2.10", simulated=False, enabled=True), "admin",
                 runner=lambda *a, **k: sent.append(a))
    assert sent == [] and "reboot" not in c.plan
