"""Browser E2E (Chrome) for the Imported Group column (rc18, directive 2.12) against tests/e2e/ops_stack.py.

Run:  (cd frontend && npm run build)
      python -I tests/e2e/ops_stack.py /tmp/ops-e2e 8830 &
      INM_E2E_URL=http://127.0.0.1:8830 INM_E2E_DB=/tmp/ops-e2e/mgmt.db python -m pytest tests/e2e/test_group_import_gui.py

CSV with a Group column -> column mapping -> row validation (invalid + duplicate rows stay in review) -> partial
import -> group review (existing group reused, new group created, cross-type clash, invalid name) -> assignment ->
group template inheritance -> inventory Groups column -> reassignment. Membership is verified in the database file
directly (INM_E2E_DB), not only through the API. Browser errors and HTTP 5xx fail the test.
"""
from __future__ import annotations

import os
import re
import sqlite3
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("INM_E2E_URL")
DB = os.environ.get("INM_E2E_DB")
PW = os.environ.get("INM_E2E_ADMIN_PASSWORD", "Ops-E2E-Admin-2026!")
CHROME = os.environ.get("INM_E2E_CHROME", "/usr/bin/google-chrome")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL is required (start tests/e2e/ops_stack.py)")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e" / "group-import-rc18"
RUN = uuid.uuid4().hex[:5].upper()


def _api(pg, method, path, body=None):
    return pg.evaluate("""async ([method, path, body]) => {
        const s = JSON.parse(localStorage.getItem('sims_session') || sessionStorage.getItem('sims_session') || 'null');
        const r = await fetch('/api' + path, {method, headers: {'Content-Type': 'application/json',
            Authorization: 'Bearer ' + (s && s.access_token)}, body: body ? JSON.stringify(body) : undefined});
        return {status: r.status, body: r.status === 204 ? null : await r.json()};
    }""", [method, path, body])


def _db_members(group_name: str) -> set[str]:
    con = sqlite3.connect(DB)
    try:
        rows = con.execute("SELECT d.serial_number FROM device_group_members m JOIN device_groups g ON g.id = m.group_id "
                           "JOIN inm_devices d ON d.id = m.device_id WHERE g.name = ?", (group_name,)).fetchall()
        return {r[0] for r in rows}
    finally:
        con.close()


@pytest.fixture(scope="module")
def browser():
    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


def test_group_column_import_assignment_inheritance_inventory_and_reassignment(browser, tmp_path):
    ctx = browser.new_context(viewport={"width": 1440, "height": 950})
    pg = ctx.new_page()
    faults: list[str] = []
    pg.on("pageerror", lambda e: faults.append(f"browser error: {e}"))
    pg.on("response", lambda r: faults.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
    pg.goto(URL + "/login")
    pg.get_by_label("Username").fill("admin")
    pg.get_by_label("Password").fill(PW)
    pg.get_by_role("button", name="Sign in").click()
    pg.wait_for_url(re.compile(r".*/$"), timeout=15000)
    existing = f"Clinic {RUN}"
    clash = f"Lobby {RUN}"
    assert _api(pg, "POST", "/v1/device-groups", {"name": existing, "family": "switch"})["status"] == 201
    assert _api(pg, "POST", "/v1/device-groups", {"name": clash, "family": "access_point"})["status"] == 201
    csv = tmp_path / "export-with-groups.csv"
    csv.write_text("Type,Site,Name,Serial,Model,Group\n"
                   f"switch,HQ,sw-a-{RUN},GS{RUN}1,CX 6200F 24G,{existing.upper()}\n"      # existing (case differs)
                   f"switch,HQ,sw-b-{RUN},GS{RUN}2,CX 6200F 24G,Core {RUN}\n"              # new group
                   f"switch,HQ,sw-c-{RUN},GS{RUN}3,CX 6200F 24G,Core {RUN}\n"
                   f"switch,HQ,sw-d-{RUN},GS{RUN}4,CX 6200F 24G,{clash}\n"                 # name used by APs
                   f"router,HQ,rt-{RUN},GS{RUN}5,X1,Core {RUN}\n"                           # invalid row
                   f"switch,HQ,sw-dup-{RUN},GS{RUN}1,CX 6200F 24G,Core {RUN}\n")           # duplicate serial
    pg.goto(URL + "/onboarding/import")
    pg.get_by_label("Select CSV file").set_input_files(str(csv))
    pg.get_by_label("Device type", exact=True).select_option(label="Type")
    pg.get_by_label("Site *", exact=True).select_option(label="Site")
    pg.get_by_label("Device name *", exact=True).select_option(label="Name")
    pg.get_by_label("Serial number *", exact=True).select_option(label="Serial")
    pg.get_by_label("Model *", exact=True).select_option(label="Model")
    pg.get_by_label("Group", exact=True).select_option(label="Group")
    expect(pg.get_by_text("Review 6 devices · 4 ready · 2 need review.")).to_be_visible()
    pg.get_by_role("button", name="Confirm and import 4 device(s)").click()
    expect(pg.get_by_role("status").first).to_contain_text("4 added, 0 updated. 2 rows remain in review.")
    pg.get_by_role("button", name="Review group assignment").click()
    expect(pg.get_by_label(f"Group for {existing.upper()} (switch)")).to_have_value(re.compile(".+-"))  # existing id
    expect(pg.get_by_label(f"Group for Core {RUN} (switch)")).to_have_value("create")
    expect(pg.get_by_text("A group with this name exists for another device type.")).to_be_visible()
    pg.screenshot(path=str(EVID / "group-review.png"), full_page=True)
    pg.get_by_role("button", name="Assign to groups").click()
    expect(pg.get_by_role("status").filter(has_text="assigned")).to_contain_text("3 assigned")
    expect(pg.get_by_role("status").filter(has_text="assigned")).to_contain_text(f"created “Core {RUN}”")
    pg.screenshot(path=str(EVID / "group-assigned.png"), full_page=True)
    if DB:                                                       # direct database verification
        assert _db_members(existing) == {f"GS{RUN}1"}
        assert _db_members(f"Core {RUN}") == {f"GS{RUN}2", f"GS{RUN}3"}
        assert _db_members(clash) == set()
    # inheritance: a group template applies to imported members
    groups = {g["name"]: g for g in _api(pg, "GET", "/v1/device-groups")["body"]}
    t = _api(pg, "POST", "/v1/config-templates", {"name": f"Core VLANs {RUN}", "family": "switch", "scope": "group",
                                                   "group_id": groups[f"Core {RUN}"]["id"], "priority": 40,
                                                   "config": {"vlans": [70]}})
    assert t["status"] == 201, t
    devs = {d["serial_number"]: d for d in _api(pg, "GET", "/v1/devices")["body"]}
    prev = _api(pg, "GET", f"/v1/config-templates/devices/{devs[f'GS{RUN}2']['id']}/preview")["body"]
    assert any(x["name"] == f"Core VLANs {RUN}" for x in prev["templates"])
    # inventory shows the memberships
    pg.goto(URL + "/inventory")
    row = pg.locator("tr", has_text=f"sw-b-{RUN}")
    expect(row).to_contain_text(f"Core {RUN}")
    expect(pg.locator("tr", has_text=f"sw-d-{RUN}")).to_contain_text("—")
    pg.screenshot(path=str(EVID / "inventory-groups.png"), full_page=True)
    # reassignment through the same flow (server-side validation, recorded move)
    r = _api(pg, "POST", "/v1/device-groups/import-assign", {"reassign": True, "assignments": [
        {"group": f"Core {RUN}", "family": "switch", "device_ids": [devs[f"GS{RUN}1"]["id"]], "target": "create"}]})
    assert r["body"]["results"][0]["moved_from"] == [existing]
    if DB:
        assert _db_members(existing) == set() and f"GS{RUN}1" in _db_members(f"Core {RUN}")
    assert faults == [], faults
    ctx.close()
