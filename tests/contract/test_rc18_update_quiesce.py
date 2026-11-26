"""rc18 (register P6-DEP-01): an update or rollback is refused while a device change is executing or an interrupted
change awaits recovery; approved-but-not-started plans do not block (they are re-validated at execution)."""
import subprocess
from pathlib import Path

from backend.cli import change_quiesce_state
from backend.database.models_phases import ChangePlan
from backend.database.models_remediation import ChangeRecoveryJournal
from backend.database.session import session_scope

ROOT = Path(__file__).resolve().parents[2]


def _plan(db, state):
    p = ChangePlan(device_id="d", title="t", operations=[], capability_report=[], plan_hash="h" * 64,
                   expected_config_hash=None, firmware_at_request=None, rollback_plan={}, state=state,
                   requester="a", source="OPERATOR", revision=1)
    db.add(p); db.flush()
    return p


def test_quiesce_state_blocks_in_flight_and_unrecovered_changes(client):
    with session_scope() as db:
        assert change_quiesce_state(db)["quiescent"] is True
        _plan(db, "APPROVED")
        st = change_quiesce_state(db)
        assert st["quiescent"] is True and st["approved_not_started"] == 1
        ex = _plan(db, "EXECUTING")
        assert change_quiesce_state(db)["quiescent"] is False
        ex.state = "APPLIED_VERIFIED"
        j = ChangeRecoveryJournal(execution_id="e", plan_id=ex.id, device_id="d", environment="EMULATED",
                                  state="RECOVERY_REQUIRED", strategy="DEVICE_CHECKPOINT", payload_enc="x")
        db.add(j); db.flush()
        st = change_quiesce_state(db)
        assert st["quiescent"] is False and "RECOVERY_REQUIRED" in st["blockers"][0]
        j.state = "RECOVERED"
        assert change_quiesce_state(db)["quiescent"] is True


def test_update_and_rollback_scripts_call_the_gate_before_any_change():
    upd = (ROOT / "deployment/centos9/update.sh").read_text()
    assert upd.index("require_change_quiesce") < upd.index('log "BACKUP before any change"')
    rb = (ROOT / "deployment/centos9/rollback.sh").read_text()
    assert rb.index("require_change_quiesce") < rb.index('git -C "$ROOT" checkout -q "$REF"')
    common = (ROOT / "deployment/centos9/common.sh").read_text()
    body = common[common.index("require_change_quiesce() {"):]
    assert "change-quiesce-check" in body and "UPDATE REFUSED" in body and '"quiescent": true' in body
    # the shell function refuses when the CLI says "not quiescent" or cannot answer
    lines = body.split("\n")
    func = "\n".join(lines[: lines.index("}") + 1])
    harness = "\n".join(["set -Eeuo pipefail", 'die() { echo "DIE:$2:$1"; exit "$2"; }', "log() { :; }",
                          'compose() { echo "$FAKE"; return "$FAKE_RC"; }', func, "require_change_quiesce && echo OK"])
    for fake, rc, expect in (('{"quiescent": true, "blockers": []}', 0, "OK"),
                             ('{"quiescent": false, "blockers": ["plan x is executing"]}', 4, "DIE:5"),
                             ("", 1, "DIE:5")):
        r = subprocess.run(["bash", "-c", harness], capture_output=True, text=True,
                           env={"FAKE": fake, "FAKE_RC": str(rc), "PATH": "/usr/bin:/bin"})
        assert r.stdout.strip().startswith(expect), (fake, r.stdout, r.stderr)
