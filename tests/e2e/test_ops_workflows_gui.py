"""Browser E2E (Chrome) for 1.3.0-rc16 workflows against tests/e2e/ops_stack.py (DEMO/EMULATED data only).

Run:  (cd frontend && npm run build)
      python -I tests/e2e/ops_stack.py /tmp/ops-e2e 8830 &
      INM_E2E_URL=http://127.0.0.1:8830 python -m pytest tests/e2e/test_ops_workflows_gui.py

Covers: CSV import with custom column names → mapping → row-level rejections → partial import → rejected-row export;
port monitoring group creation with multi-port checkbox selection, location roll-up, history and delete; SNMPv3
printer profile with explicit protocols; viewer read-only access. Browser errors and HTTP 5xx fail the test.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("INM_E2E_URL")
PW = os.environ.get("INM_E2E_ADMIN_PASSWORD", "Ops-E2E-Admin-2026!")
CHROME = os.environ.get("INM_E2E_CHROME", "/usr/bin/google-chrome")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL is required (start tests/e2e/ops_stack.py)")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e" / "ops-rc16"


def _login(browser, user, password, width=1440):
    ctx = browser.new_context(viewport={"width": width, "height": 950}, accept_downloads=True)
    pg = ctx.new_page()
    faults: list[str] = []
    pg.on("pageerror", lambda e: faults.append(f"browser error: {e}"))
    pg.on("response", lambda r: faults.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
    pg.faults = faults
    pg.goto(URL + "/login")
    pg.get_by_label("Username").fill(user)
    pg.get_by_label("Password").fill(password)
    pg.get_by_role("button", name="Sign in").click()
    pg.wait_for_url(re.compile(r".*/(change-password)?$"), timeout=15000)
    return ctx, pg


def _api(pg, method, path, body=None):
    return pg.evaluate("""async ([method, path, body]) => {
        const s = JSON.parse(localStorage.getItem('sims_session') || sessionStorage.getItem('sims_session') || 'null');
        const r = await fetch('/api' + path, {method, headers: {'Content-Type': 'application/json',
            Authorization: 'Bearer ' + (s && s.access_token)}, body: body ? JSON.stringify(body) : undefined});
        return {status: r.status, body: r.status === 204 ? null : await r.json()};
    }""", [method, path, body])


@pytest.fixture(scope="module")
def browser():
    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


RUN = __import__("uuid").uuid4().hex[:6].upper()


def test_csv_import_column_mapping_partial_and_rejected_export(browser, tmp_path):
    ctx, pg = _login(browser, "admin", PW)
    csv = tmp_path / "custom-export.csv"
    csv.write_text(f"Kind,Location,Device Name,SN,Device Model,Mgmt Address\n"
                   f"AP,Branch A,BR-AP-01,E2E-AP-{RUN}1,AP-505,10.10.1.11\n"
                   f"AP,Branch A,BR-AP-02,E2E-AP-{RUN}2,AP-505,10.10.1.12\n"
                   f"printer,Branch A,BR-PRN-01,E2E-PR-{RUN},M404,10.10.1.13\n"
                   f"switch,,BR-SW-01,E2E-SW-{RUN},CX 6100,10.10.1.2\n"
                   f"AP,Branch A,BR-AP-03,e2e-ap-{RUN}1,AP-505,10.10.1.14\n")
    pg.goto(URL + "/onboarding/import")
    pg.get_by_label("Select CSV file").set_input_files(str(csv))
    # unknown headers are not guessed: map them explicitly
    pg.get_by_label("Device type", exact=True).select_option(label="Kind")
    pg.get_by_label("Site *", exact=True).select_option(label="Location")
    pg.get_by_label("Device name *", exact=True).select_option(label="Device Name")
    pg.get_by_label("Serial number *", exact=True).select_option(label="SN")
    pg.get_by_label("Model *", exact=True).select_option(label="Device Model")
    pg.get_by_label("Management IP", exact=True).select_option(label="Mgmt Address")
    expect(pg.get_by_text("Review 5 devices · 2 ready · 3 need review.")).to_be_visible()
    expect(pg.get_by_text('Unknown device type "printer"')).to_be_visible()
    expect(pg.get_by_text("Duplicate serial in this file")).to_be_visible()
    expect(pg.get_by_text("Missing site")).to_be_visible()
    pg.get_by_label("Site for row 5").fill("Branch A")         # fix one row in place
    expect(pg.get_by_text("Review 5 devices · 3 ready · 2 need review.")).to_be_visible()
    pg.screenshot(path=str(EVID / "import-mapping-review.png"), full_page=True)
    with pg.expect_download() as dl:
        pg.get_by_role("button", name="Export 2 rows to fix").click()
    exported = Path(dl.value.path()).read_text()
    assert "source_row" in exported and "Duplicate serial in this file" in exported and "printer" in exported
    pg.get_by_role("button", name="Confirm and import 3 device(s)").click()
    expect(pg.get_by_role("status")).to_contain_text("3 added, 0 updated. 2 rows remain in review.")
    expect(pg.get_by_text("Assign credentials to 2 imported access points")).to_be_visible()
    devices = _api(pg, "GET", "/v1/devices")["body"]
    serials = {d["serial_number"] for d in devices if d["source_type"] == "REAL"}
    assert {f"E2E-AP-{RUN}1", f"E2E-AP-{RUN}2", f"E2E-SW-{RUN}"} <= serials and f"e2e-ap-{RUN}1" not in serials
    pg.screenshot(path=str(EVID / "import-result.png"), full_page=True)
    assert pg.faults == []
    ctx.close()


def test_port_monitoring_group_multi_select_dashboard_history_delete(browser):
    ctx, pg = _login(browser, "admin", PW)
    switches = [d for d in _api(pg, "GET", "/v1/devices?family=switch")["body"] if d["family"] == "switch"]
    sw = next(d for d in switches if _api(pg, "GET", f"/v1/devices/{d['id']}/ports")["body"].get("ports"))
    pg.goto(URL + "/port-groups")
    pg.get_by_label("New group name").fill(f"AP Uplink {RUN}")
    pg.get_by_role("button", name="Create group").click()
    expect(pg.get_by_label("Group", exact=True)).to_have_value(re.compile(".+"))
    pg.get_by_label("Switch", exact=True).select_option(value=sw["id"])
    pg.get_by_label("Port label").fill("AP uplink")
    pg.get_by_label("Utilization % ≥").fill("80")
    boxes = pg.locator("fieldset input[type=checkbox]")
    expect(boxes.first).to_be_visible()
    boxes.nth(0).check()
    boxes.nth(1).check()
    pg.get_by_role("button", name="Add 2 ports").click()
    expect(pg.locator("table tbody tr")).to_have_count(2)
    expect(pg.get_by_text(re.compile(r"anomalies / 2 ports"))).to_be_visible()
    states = pg.locator("table tbody tr td:nth-child(3) span").all_inner_texts()
    assert set(states) <= {"Critical", "Warning", "Unsupported", "Stale", "No data", "Healthy"} and len(states) == 2
    pg.screenshot(path=str(EVID / "port-group-dashboard.png"), full_page=True)
    pg.get_by_role("button", name="History").first.click()
    expect(pg.get_by_text("Points are measured samples only")).to_be_visible()
    expect(pg.get_by_role("heading", name="Utilization (%)")).to_be_visible()
    pg.screenshot(path=str(EVID / "port-group-history.png"), full_page=True)
    with pg.expect_download() as dl:
        pg.get_by_role("button", name="Export CSV").click()
    assert "location,switch,port" in Path(dl.value.path()).read_text().replace('"', "")

    # viewer: read-only view with no management controls
    _api(pg, "POST", "/v1/users", {"username": f"e2e.viewer{RUN.lower()}", "role": "viewer",
                                   "temporary_password": "Viewer-Temp-2026!x", "site_scope": "all"})
    pg.evaluate("""async (username) => {
        const login = await fetch('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({username, password: 'Viewer-Temp-2026!x'})});
        const token = (await login.json()).access_token;
        const r = await fetch('/api/auth/change-password', {method: 'POST', headers: {'Content-Type': 'application/json',
            Authorization: 'Bearer ' + token}, body: JSON.stringify({current_password: 'Viewer-Temp-2026!x',
            new_password: 'Viewer-Perm-2026!xy'})});
        if (r.status !== 200) throw new Error('password change failed ' + r.status);
    }""", f"e2e.viewer{RUN.lower()}")
    vctx, vpg = _login(browser, f"e2e.viewer{RUN.lower()}", "Viewer-Perm-2026!xy")
    vpg.goto(URL + "/port-groups")
    vpg.get_by_label("Group", exact=True).select_option(label=f"AP Uplink {RUN} (2 ports)")
    expect(vpg.locator("table tbody tr")).to_have_count(2)
    expect(vpg.get_by_role("button", name="Create group")).to_have_count(0)
    expect(vpg.get_by_role("button", name="Delete group")).to_have_count(0)
    expect(vpg.get_by_role("button", name="Remove from group")).to_have_count(0)
    vpg.screenshot(path=str(EVID / "port-group-viewer.png"), full_page=True)
    assert vpg.faults == []
    vctx.close()

    pg.once("dialog", lambda d: d.accept())
    pg.get_by_role("button", name="Delete group").click()
    expect(pg.get_by_label("Group", exact=True)).to_have_value("")
    assert all(g["name"] != f"AP Uplink {RUN}" for g in _api(pg, "GET", "/v1/port-groups")["body"])
    assert pg.faults == []
    ctx.close()


def test_printer_snmpv3_profile_protocol_selection(browser):
    ctx, pg = _login(browser, "admin", PW)
    pg.goto(URL + "/printers/inventory")
    pg.get_by_text("Manual registration, credential profiles and one-off SNMP checks", exact=True).click()
    pg.get_by_role("button", name="Discover printers").click()
    pg.wait_for_load_state("networkidle")
    summary = pg.get_by_text("Add a credential profile", exact=True)
    if not pg.locator("details:has(> summary:text-is('Add a credential profile'))").evaluate("d => d.open"):
        summary.click()
    pg.get_by_label("Profile name").fill(f"Kyocera v3 {RUN}")
    pg.get_by_label("SNMP version").select_option("3")
    pg.get_by_label("Username", exact=True).fill("monitor")
    pg.get_by_label("Authentication protocol").select_option("SHA")
    pg.get_by_label("Authentication passphrase").fill("auth-pass-e2e")
    pg.get_by_label("Privacy protocol").select_option("AES256")
    pg.get_by_label("Privacy passphrase").fill("priv-pass-e2e")
    pg.get_by_role("button", name="Save profile").click()
    expect(pg.get_by_text("Printer credential profile saved.")).to_be_visible()
    profiles = _api(pg, "GET", "/printers/credential-profiles")["body"]
    saved = next(p for p in profiles if p["name"] == f"Kyocera v3 {RUN}")
    assert (saved["auth_protocol"], saved["priv_protocol"], saved["security_level"]) == ("SHA", "AES256", "authPriv")
    assert "auth-pass-e2e" not in str(profiles) and "priv-pass-e2e" not in str(profiles)
    # authNoPriv hides the privacy inputs (the form collapses once a profile exists; reopen it)
    pg.wait_for_load_state("networkidle")
    if not pg.locator("details:has(> summary:text-is('Add a credential profile'))").evaluate("d => d.open"):
        summary.click()
    pg.get_by_label("SNMP version").select_option("3")
    pg.get_by_label("Security level").select_option("authNoPriv")
    expect(pg.get_by_label("Privacy passphrase")).to_have_count(0)
    pg.screenshot(path=str(EVID / "printer-snmpv3-profile.png"), full_page=True)
    assert pg.faults == []
    ctx.close()
