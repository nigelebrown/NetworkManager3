"""Browser E2E (Chrome) for the rc18 high-risk deployment gate (register P6-10) against tests/e2e/ops_stack.py.

  INM_E2E_URL=http://127.0.0.1:8830 python -m pytest tests/e2e/test_deployment_gui.py

Admin deploys a group spanning two demo sites from Group deployment: the Impact panel shows HIGH RISK / multi site
and the release gate; Execute stays disabled. An approver approves the plans; the admin and that approver are not
offered the release; a third person opens the deployment from the list, approves the release of the current
impact, and the admin's Execute button becomes available. DEMO/EMULATED data only; nothing is executed here (the
execution path is covered by tests/contract/test_rc18_high_risk_deployment.py). Browser errors and HTTP 5xx fail.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("INM_E2E_URL")
PW = os.environ.get("INM_E2E_ADMIN_PASSWORD", "Ops-E2E-Admin-2026!")
CHROME = os.environ.get("INM_E2E_CHROME", "/usr/bin/google-chrome")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL is required (start tests/e2e/ops_stack.py)")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e" / "deployment-rc18"
RUN = uuid.uuid4().hex[:5]
GLOBAL = {"vlans": [10, 20], "port_roles": {"user": {"access_vlan": 10, "bpdu_guard": "enabled"}},
          "port_assignments": [{"ports": "1/1/1-1/1/8", "role": "user"}], "system": {"ntp": ["10.10.0.10"]}}


def _api(token=None):
    c = httpx.Client(base_url=URL + "/api", timeout=60)
    if token:
        c.headers["Authorization"] = "Bearer " + token
    return c


def _token(user, pw):
    r = _api().post("/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _user(adm, name, role):
    tmp, final = "Deploy-Temp-2026!x", "Deploy-Final-2026!x"
    assert adm.post("/v1/users", json={"username": name, "role": role, "temporary_password": tmp,
                                       "site_scope": "all"}).status_code == 201
    u = _api(_token(name, tmp))
    assert u.post("/auth/change-password", json={"current_password": tmp, "new_password": final}).status_code == 200
    return final


def _browser_login(b, user, pw):
    ctx = b.new_context(viewport={"width": 1440, "height": 950})
    pg = ctx.new_page()
    pg.faults = []
    pg.on("pageerror", lambda e: pg.faults.append(f"browser error: {e}"))
    pg.on("response", lambda r: pg.faults.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
    pg.goto(URL + "/login")
    pg.get_by_label("Username").fill(user)
    pg.get_by_label("Password").fill(pw)
    pg.get_by_role("button", name="Sign in").click()
    pg.wait_for_url(re.compile(r".*/$"), timeout=15000)
    return ctx, pg


def test_high_risk_deployment_impact_and_independent_release(tmp_path):
    EVID.mkdir(parents=True, exist_ok=True)
    adm = _api(_token("admin", PW))
    sws = [d for d in adm.get("/v1/devices").json() if d["source_type"] == "DEMO" and d["family"] == "switch"]
    two_sites = []
    for d in sws:
        if d["site_id"] not in {x["site_id"] for x in two_sites}:
            two_sites.append(d)
    assert len(two_sites) >= 2, "demo dataset needs switches in two sites"
    gname = f"GUI batch {RUN}"
    g = adm.post("/v1/device-groups", json={"name": gname, "family": "switch"}).json()["id"]
    adm.post(f"/v1/device-groups/{g}/members", json={"device_ids": [d["id"] for d in two_sites[:2]]})
    assert adm.post("/v1/config-templates", json={"name": f"GUI fleet {RUN}", "family": "switch", "scope": "global",
                                                  "config": GLOBAL}).status_code == 201
    for d in two_sites[:2]:
        assert adm.post(f"/v1/vault/devices/{d['id']}/capture-from-device").status_code == 201
    appr_pw = _user(adm, f"appr{RUN}", "approver")
    rev_pw = _user(adm, f"rev{RUN}", "approver")
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx, pg = _browser_login(b, "admin", PW)
        pg.goto(URL + "/deployments")
        pg.locator("tr", has_text=gname).get_by_role("button", name="Deploy group templates").click()
        expect(pg.get_by_text(re.compile(r"Deployment [0-9a-f]{8}: 2 plan\(s\) for approval"))).to_be_visible()
        impact = pg.get_by_test_id("deployment-impact")
        expect(impact).to_contain_text("HIGH RISK")
        expect(impact).to_contain_text("multi site")
        gate = pg.get_by_test_id("release-gate")
        expect(gate).to_contain_text("Release approval required")
        expect(gate.get_by_role("button", name="Approve release of this impact")).to_have_count(0)   # creator
        pg.screenshot(path=str(EVID / "01_impact_release_required.png"), full_page=True)
        # independent plan approvals (approver account, API)
        ap = _api(_token(f"appr{RUN}", appr_pw))
        bid = adm.get("/v1/deployments").json()[0]["batch_id"]           # newest first
        expect(pg.get_by_text(f"Deployment {bid[:8]}").first).to_be_visible()
        for row in adm.get(f"/v1/deployments/{bid}").json()["plans"]:
            plan = next(x for x in adm.get("/v1/changes/plans").json() if x["id"] == row["plan_id"])
            assert ap.post(f"/v1/changes/plans/{plan['id']}/approve",
                           json={"expected_revision": plan["revision"]}).status_code == 200
        # the plan approver is not offered the release
        actx, apg = _browser_login(b, f"appr{RUN}", appr_pw)
        apg.goto(URL + "/deployments")
        apg.get_by_label("Open deployment").select_option(bid)
        expect(apg.get_by_test_id("release-gate")).to_contain_text("Release approval required")
        expect(apg.get_by_role("button", name="Approve release of this impact")).to_have_count(0)
        # a third person opens it from the list and releases the current impact
        rctx, rpg = _browser_login(b, f"rev{RUN}", rev_pw)
        rpg.goto(URL + "/deployments")
        rpg.get_by_label("Open deployment").select_option(bid)
        expect(rpg.get_by_test_id("deployment-impact")).to_contain_text("HIGH RISK")
        rpg.get_by_label("Release review note").fill("Impact, rollback and change window reviewed for both sites")
        rpg.get_by_role("button", name="Approve release of this impact").click()
        expect(rpg.get_by_test_id("release-gate")).to_contain_text("Release approval recorded")
        expect(rpg.get_by_test_id("release-gate")).to_contain_text(f"Approved by rev{RUN}")
        rpg.screenshot(path=str(EVID / "02_release_recorded.png"), full_page=True)
        # the admin now sees the release and can execute
        pg.get_by_role("button", name="Refresh").click()
        expect(pg.get_by_test_id("release-gate")).to_contain_text("Release approval recorded")
        expect(pg.get_by_role("button", name="Execute approved wave")).to_be_enabled()
        pg.screenshot(path=str(EVID / "03_admin_can_execute.png"), full_page=True)
        audit = [a["action"] for a in adm.get("/audit?limit=300").json()]
        assert "deployment_release_approved" in audit
        faults = pg.faults + apg.faults + rpg.faults
        b.close()
    assert not faults, faults
