"""P6-10 (rc18): impact and cluster scope of multi-device / cluster changes, and a stronger approval policy.

* the impact view lists devices, sites, every cluster reached (with members outside the batch) and the operations;
* a HIGH-risk deployment (cluster-scoped, multi-site or large) cannot execute until a separate reviewer - neither the
  deployment creator nor any plan approver - approves the current impact digest;
* the approval is invalidated when a plan or the cluster scope changes; refusals are audited;
* a STANDARD deployment keeps the per-plan approval only.
Emulated (demo) devices; this says nothing about hardware.
"""
import uuid

from backend.database.models_inm import InmCluster, InmDevice
from backend.database.models_phases import ChangePlan
from backend.database.session import session_scope
from backend.infrastructure import change_executor as X
from tests.conftest import make_user
from tests.contract.test_remediation_workflow import _certify, _templates, demo  # noqa: F401  (fixture)


def _batch(c, adm, appr, n, name="Batch"):
    sws = [d["id"] for d in c.get("/api/v1/devices", headers=adm).json()
           if d["source_type"] == "DEMO" and d["family"] == "switch"][:n]
    g = c.post("/api/v1/device-groups", headers=adm, json={"name": name, "family": "switch"}).json()["id"]
    c.post(f"/api/v1/device-groups/{g}/members", headers=adm, json={"device_ids": sws})
    _templates(c, adm, "switch", g)
    for d in sws:
        _certify(c, adm, appr, d)
        assert c.post(f"/api/v1/vault/devices/{d}/capture-from-device", headers=adm).status_code == 201
    b = c.post("/api/v1/deployments", headers=adm, json={"group_id": g}).json()
    return b["batch_id"], sws


def _approve_all(c, adm, appr, batch):
    st = c.get(f"/api/v1/deployments/{batch}", headers=adm).json()
    for p in st["plans"]:
        plan = next(x for x in c.get("/api/v1/changes/plans", headers=adm).json() if x["id"] == p["plan_id"])
        assert c.post(f"/api/v1/changes/plans/{plan['id']}/approve", headers=appr,
                      json={"expected_revision": plan["revision"]}).status_code == 200
    return c.get(f"/api/v1/deployments/{batch}", headers=adm).json()


def _audit(c, adm, action):
    return [a for a in c.get("/api/audit?limit=500", headers=adm).json() if a["action"] == action]


def test_large_batch_needs_independent_release_of_the_current_impact(demo, app_env):
    app_env.setenv("INM_DEPLOYMENT_HIGH_RISK_DEVICES", "2")
    c, adm, appr = demo
    batch, sws = _batch(c, adm, appr, 2)
    imp = c.get(f"/api/v1/deployments/{batch}/impact", headers=adm).json()
    assert imp["risk"] == "HIGH" and "LARGE_BATCH" in [r["code"] for r in imp["reasons"]]
    assert imp["devices_in_batch"] == 2 and imp["operations"] and len(imp["digest"]) == 64
    assert imp["release"] == {"required": True, "approved": False, "approval": None, "stale": False}

    # release before every plan is approved -> refused
    rev = make_user(c, adm, "release.rev", "approver")
    assert c.post(f"/api/v1/deployments/{batch}/release-approval", headers=rev,
                  json={"digest": imp["digest"], "note": "Reviewed impact and rollback for both switches"}).status_code == 409
    st = _approve_all(c, adm, appr, batch)
    assert st["impact"]["digest"] != imp["digest"]           # approvals bump plan revisions -> new digest
    # execution is refused without a release and the refusal is audited; nothing is written
    before = {d: X._EMULATORS[d].writes for d in sws if d in X._EMULATORS}
    r = c.post(f"/api/v1/deployments/{batch}/execute", headers=adm, json={"stop_on_failure": True})
    assert r.status_code == 409 and "separate reviewer" in r.json()["detail"]
    assert _audit(c, adm, "deployment_execute_refused")
    assert before and {d: X._EMULATORS[d].writes for d in before} == before
    note = {"note": "Reviewed impact, rollback and maintenance window for both switches"}
    # the creator and the plan approver cannot release; the stale digest is refused
    assert c.post(f"/api/v1/deployments/{batch}/release-approval", headers=adm,
                  json={"digest": st["impact"]["digest"], **note}).status_code == 403
    assert c.post(f"/api/v1/deployments/{batch}/release-approval", headers=appr,
                  json={"digest": st["impact"]["digest"], **note}).status_code == 403
    assert _audit(c, adm, "deployment_release_refused")
    assert c.post(f"/api/v1/deployments/{batch}/release-approval", headers=rev,
                  json={"digest": imp["digest"], **note}).status_code == 409
    listed = {b["batch_id"]: b for b in c.get("/api/v1/deployments", headers=rev).json()}   # reviewer can find it
    assert listed[batch]["risk"] == "HIGH" and listed[batch]["release"]["approved"] is False
    assert listed[batch]["created_by"] == "admin" and listed[batch]["progress"]["approved"] == 2
    ok = c.post(f"/api/v1/deployments/{batch}/release-approval", headers=rev,
                json={"digest": st["impact"]["digest"], **note})
    assert ok.status_code == 200 and ok.json()["release"]["approved"] is True
    assert ok.json()["release"]["approval"]["approved_by"] == "release.rev"
    assert _audit(c, adm, "deployment_release_approved")
    r = c.post(f"/api/v1/deployments/{batch}/execute", headers=adm, json={"stop_on_failure": True}).json()
    assert [e["outcome"] for e in r["executed"]] == ["APPLIED_VERIFIED", "APPLIED_VERIFIED"]
    # the released digest stays valid while the rollout progresses (plan state is not part of it)
    assert c.get(f"/api/v1/deployments/{batch}", headers=adm).json()["release"]["approved"] is True


def test_release_is_invalidated_when_a_plan_changes(demo, app_env):
    app_env.setenv("INM_DEPLOYMENT_HIGH_RISK_DEVICES", "2")
    c, adm, appr = demo
    batch, _ = _batch(c, adm, appr, 2)
    st = _approve_all(c, adm, appr, batch)
    rev = make_user(c, adm, "release.rev2", "approver")
    assert c.post(f"/api/v1/deployments/{batch}/release-approval", headers=rev,
                  json={"digest": st["impact"]["digest"], "note": "Reviewed impact and rollback plan"}).status_code == 200
    with session_scope() as db:                       # the approved content changes after release
        p = db.get(ChangePlan, st["plans"][0]["plan_id"])
        p.revision += 1
        p.plan_hash = "0" * 64
    now = c.get(f"/api/v1/deployments/{batch}", headers=adm).json()["release"]
    assert now["approved"] is False and now["stale"] is True
    r = c.post(f"/api/v1/deployments/{batch}/execute", headers=adm, json={"stop_on_failure": True})
    assert r.status_code == 409 and "no longer matches" in r.json()["detail"]


def test_cluster_scope_lists_every_member_and_is_high_risk(demo):
    c, adm, appr = demo
    with session_scope() as db:
        cl = InmCluster(name="Clinic VC", kind="aruba_instant_cluster", source_type="DEMO")
        db.add(cl)
        db.flush()
        aps = [d for d in db.query(InmDevice).filter(InmDevice.source_type == "DEMO",
                                                     InmDevice.family == "access_point").all()][:3]
        assert len(aps) == 3
        for d in aps:
            d.cluster_id = cl.id
        batch = str(uuid.uuid4())
        plan = ChangePlan(device_id=aps[0].id, title="SSID template", operations=[{"op": "ssid.update", "ssid": "Staff"}],
                          capability_report=[], plan_hash="a" * 64, rollback_plan={"batch_id": batch},
                          state="PENDING_APPROVAL", requester="admin", source="TEMPLATE")
        db.add(plan)
        names = sorted(d.hostname for d in aps)
    imp = c.get(f"/api/v1/deployments/{batch}/impact", headers=adm).json()
    assert imp["risk"] == "HIGH" and [r["code"] for r in imp["reasons"]] == ["CLUSTER_SCOPE"]
    assert imp["devices_in_batch"] == 1 and imp["devices_reached"] == 3
    cl = imp["clusters"][0]
    assert cl["cluster_wide"] and cl["member_count"] == 3 and len(cl["also_affected"]) == 2
    assert sorted(m["hostname"] for m in cl["members"]) == names
    assert imp["operations"] == {"ssid.update": 1}


def test_standard_deployment_keeps_per_plan_approval(demo, app_env):
    app_env.setenv("INM_DEPLOYMENT_HIGH_RISK_DEVICES", "50")
    c, adm, appr = demo
    sws = [d for d in c.get("/api/v1/devices", headers=adm).json()
           if d["source_type"] == "DEMO" and d["family"] == "switch"]
    batch, ids = _batch(c, adm, appr, 1, name="Single")
    st = _approve_all(c, adm, appr, batch)
    assert st["impact"]["risk"] == "STANDARD" and st["release"]["required"] is False
    r = c.post(f"/api/v1/deployments/{batch}/execute", headers=adm, json={"stop_on_failure": True}).json()
    assert [e["outcome"] for e in r["executed"]] == ["APPLIED_VERIFIED"]
    rev = make_user(c, adm, "release.rev3", "approver")
    assert c.post(f"/api/v1/deployments/{batch}/release-approval", headers=rev,
                  json={"digest": st["impact"]["digest"], "note": "Not needed for one device"}).status_code == 409
    assert sws and ids
