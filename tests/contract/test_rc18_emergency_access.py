"""rc18 (register P4-09): emergency (break-glass) privileged access is a distinct, audited workflow.

It waives only the two-person rule, only for plans the holder explicitly approves under the session; the rest of
change governance (certification, verified backup, readback, rollback) still runs. Opening a session notifies at
critical severity; actions during the window are linked; a different administrator must review it."""
from datetime import timedelta

from backend.infrastructure import clock
from tests.conftest import make_user
from tests.contract.test_production_read_write import certify, env, live, plan  # noqa: F401


def test_break_glass_waives_only_two_person_rule_and_is_reviewed_by_another_admin(env):
    c, adm, appr, sw, _ = env
    certify(c, adm, appr, sw, test_port="1/1/20", lag_ports=["1/1/21", "1/1/22"])
    p = plan(c, adm, sw, [{"op": "switch.vlan.define", "value": 345}], "Outage fix")
    sub = c.post(f"/api/v1/changes/plans/{p['id']}/submit", headers=adm)
    p = sub.json() if sub.status_code == 200 else p
    # without a session: self-approval refused (two-person rule)
    r = c.post(f"/api/v1/changes/plans/{p['id']}/approve", headers=adm, json={"expected_revision": p["revision"], "emergency": True})
    assert r.status_code == 403
    assert c.post("/api/v1/emergency-access", headers=adm, json={"reason": "too short", "ticket": "INC1"}).status_code == 422
    s = c.post("/api/v1/emergency-access", headers=adm, json={
        "reason": "Core switch outage at night, no second approver reachable", "ticket": "INC-4471", "minutes": 30}).json()
    assert s["state"] == "ACTIVE" and s["holder"] == "admin"
    assert c.post("/api/v1/emergency-access", headers=adm, json={
        "reason": "Second session for the same administrator", "ticket": "INC-4472"}).status_code == 409
    # approving WITHOUT the explicit emergency flag is still refused
    assert c.post(f"/api/v1/changes/plans/{p['id']}/approve", headers=adm,
                  json={"expected_revision": p["revision"]}).status_code == 403
    ok = c.post(f"/api/v1/changes/plans/{p['id']}/approve", headers=adm,
                json={"expected_revision": p["revision"], "emergency": True, "note": "restore service"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["state"] == "APPROVED" and ok.json()["decision_note"].startswith("[EMERGENCY ")
    # the governed execution chain still runs (certified op, verified backup, readback)
    ex = c.post(f"/api/v1/changes/plans/{p['id']}/execute", headers=adm).json()
    assert ex["outcome"] == "APPLIED_VERIFIED", ex
    # the review: not by the holder, not while active
    assert c.post(f"/api/v1/emergency-access/{s['id']}/review", headers=adm, json={"note": "reviewed by myself"}).status_code == 403
    admin2 = make_user(c, adm, "second.admin", "admin")
    assert c.post(f"/api/v1/emergency-access/{s['id']}/review", headers=admin2, json={"note": "too early to review"}).status_code == 409
    assert c.post(f"/api/v1/emergency-access/{s['id']}/end", headers=adm).json()["state"] == "ENDED_PENDING_REVIEW"
    done = c.post(f"/api/v1/emergency-access/{s['id']}/review", headers=admin2,
                  json={"note": "Change matched INC-4471; VLAN 345 verified on readback"}).json()
    assert done["state"] == "REVIEWED" and done["reviewed_by"] == "second.admin"
    acts = [a["action"] for a in done["actions"]]
    assert "emergency_access_started" in acts and "change_plan_emergency_self_approved" in acts
    # after the session ended, self-approval is refused again
    p2 = plan(c, adm, sw, [{"op": "switch.vlan.define", "value": 346}], "After")
    assert c.post(f"/api/v1/changes/plans/{p2['id']}/approve", headers=adm,
                  json={"expected_revision": p2["revision"], "emergency": True}).status_code == 403
    # an operator can never open a session; critical notification event was queued for every connection
    op = make_user(c, adm, "ops.person", "operator")
    assert c.post("/api/v1/emergency-access", headers=op, json={
        "reason": "Operators cannot open break-glass sessions", "ticket": "INC-1"}).status_code == 403
    audit = {e["action"] for e in c.get("/api/audit?limit=500", headers=adm).json()}
    assert {"emergency_access_started", "emergency_access_ended", "emergency_access_reviewed",
            "change_plan_emergency_self_approved", "change_plan_self_approval_denied"} <= audit


def test_session_expires_and_unreviewed_sessions_become_overdue(env):
    c, adm, _, _, _ = env
    s = c.post("/api/v1/emergency-access", headers=adm, json={
        "reason": "Firewall rule rollback during an outage window", "ticket": "INC-77", "minutes": 5}).json()
    with clock.frozen(clock.now() + timedelta(minutes=6)):
        st = {x["id"]: x["state"] for x in c.get("/api/v1/emergency-access", headers=adm).json()["sessions"]}
        assert st[s["id"]] == "ENDED_PENDING_REVIEW"
    with clock.frozen(clock.now() + timedelta(hours=25)):
        lst = c.get("/api/v1/emergency-access", headers=adm).json()
        assert {x["id"]: x["state"] for x in lst["sessions"]}[s["id"]] == "REVIEW_OVERDUE" and lst["overdue_reviews"] == 1
