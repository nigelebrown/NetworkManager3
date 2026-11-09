"""Browser end-to-end for a LIVE-mode deployment (not demo mode).

Run against a running server:
  INM_E2E_URL=http://127.0.0.1:8800 INM_E2E_ADMIN_PASSWORD=... INM_E2E_CHROME=/usr/bin/google-chrome \
  python -m pytest tests/e2e/test_live_operations.py -q

Walks every navigation entry, the demo load/remove switch and the engineering workflows, failing on
any rendered error, uncaught page exception or 5xx API response. Screenshots go to evidence/e2e-live/.
"""
import os
import re
from pathlib import Path

import pytest

URL = os.environ.get("INM_E2E_URL")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL not set (requires a running server)")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e-live"
PAGES = ["/", "/executive", "/wifi", "/system", "/onboarding", "/onboarding/manual", "/discovery", "/sites", "/aps",
         "/switches", "/clients", "/collection", "/experience", "/phase4-insights", "/alerts", "/logs", "/compliance",
         "/changes", "/config-templates", "/vault", "/config", "/replace", "/accountability", "/audit", "/learning",
         "/optimization", "/certification", "/device-alerts", "/assistant", "/ai-learning", "/firmware", "/printers", "/platform", "/recovery", "/data", "/users"]


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("INM_E2E_CHROME") or None, args=["--no-sandbox"])
        yield b
        b.close()


def session(browser, user, password):
    ctx = browser.new_context(viewport={"width": 1500, "height": 950}, ignore_https_errors=True)
    page = ctx.new_page()
    problems = []
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: problems.append(f"{r.status} {r.url}") if r.status >= 500 else None)
    def login(pw):
        page.goto(URL + "/login")
        page.get_by_label(re.compile("user", re.I)).fill(user)
        page.get_by_label(re.compile("password", re.I)).fill(pw)
        page.get_by_role("button", name=re.compile("sign in|log in", re.I)).click()
        page.wait_for_url(re.compile(r".*/(change-password)?$"), timeout=15000)
    login(password)
    if page.url.endswith("/change-password"):   # new accounts must replace their temporary password
        api(page, "POST", "/auth/change-password", {"current_password": password, "new_password": password + "-Changed"})
        login(password + "-Changed")
        assert not page.url.endswith("/change-password")
    return ctx, page, problems


def api(page, method, path, body=None):
    token = page.evaluate("JSON.parse(sessionStorage.getItem('sims_session')).access_token")
    r = page.request.fetch(URL + "/api" + path, method=method, data=body,
                           headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    assert r.status < 400, f"{method} {path}: {r.status} {r.text()}"
    return r.json()


def shot(page, name):
    page.screenshot(path=str(EVID / f"{name}.png"), full_page=True)


def test_live_platform_end_to_end(browser):
    pw = os.environ["INM_E2E_ADMIN_PASSWORD"]
    ctx, page, problems = session(browser, "admin", pw)
    # Live mode: no demo banner, real empty-state guidance
    page.get_by_text("Network Operations Centre").wait_for()
    assert page.get_by_text("DEMO MODE").count() == 0
    shot(page, "01_noc_live_empty")

    # A real device registered through the existing manual intake API (same path as the UI form)
    real = api(page, "POST", "/v1/quick-devices", {"family": "switch", "site_name": "Head Office",
               "hostname": "ho-core-01", "serial_number": "SG-LIVE-0001", "model": "CX 6200F 48G",
               "firmware": "10.14.1020", "management_ip": "10.10.0.5", "status": "up", "metrics": {}})["id"]

    # Load demo data with the button
    page.goto(URL + "/data")
    page.get_by_role("button", name="Load demo data").click()
    page.get_by_text(re.compile("Demo data loaded:")).wait_for(timeout=60000)
    page.get_by_text("DEMO DATA LOADED alongside live data").wait_for()
    shot(page, "02_demo_loaded")

    # Every page renders without an error
    for path in PAGES:
        page.goto(URL + path)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(300)
        alerts = page.locator("[role=alert]")
        for i in range(alerts.count()):
            text = alerts.nth(i).inner_text()
            assert not re.search(r"error|failed|exception|not found", text, re.I), f"{path}: {text}"
        shot(page, "page" + (path.replace("/", "_") or "_root"))

    # NOC wall shows both live and demo devices; drill into Device 360
    page.goto(URL + "/")
    page.get_by_text("Device wall").wait_for()
    assert page.locator("a[href$='/360']").count() >= 10
    page.locator(f"a[href='/devices/{real}/360']").first.click()
    page.get_by_text("Experience", exact=True).first.wait_for()
    for tab in ["Ports", "Best practice", "Changes", "Configuration", "Capabilities", "Operations", "Performance"]:
        page.get_by_role("button", name=tab, exact=True).click()
        page.wait_for_timeout(300)
    shot(page, "03_device360_real")

    # Compliance drill-down on a demo device with poor practice
    page.goto(URL + "/compliance")
    page.get_by_text("Devices — click a row").wait_for()
    page.locator("tbody tr").first.click()
    page.get_by_text(re.compile("^Findings —")).wait_for()
    page.get_by_text("Intended vs installed").wait_for()
    shot(page, "04_compliance_findings")

    # Vault: capture the real device's config, verify integrity
    page.goto(URL + "/vault")
    page.get_by_label("Device").select_option(real)
    page.get_by_label("Configuration text").fill("hostname ho-core-01\nssh server vrf mgmt\ntelnet server\nspanning-tree\nvlan 100\n")
    page.get_by_role("button", name="Encrypt & store").click()
    page.get_by_text(re.compile("Sealed version")).wait_for()
    page.get_by_role("button", name="Verify").first.click()
    page.get_by_text(re.compile("Integrity verified")).wait_for()
    shot(page, "05_vault")

    # Collection: plan for the real device, poll now -> honest failure (no allowlist / no credential)
    page.goto(URL + "/collection")
    page.get_by_label("Device").select_option(real)
    page.get_by_role("button", name="Preview").click()
    page.get_by_text(re.compile("No saved credential profile")).wait_for()
    page.get_by_role("button", name="Approve plan").click()
    page.get_by_text(re.compile("Collection plan approved")).wait_for()
    row = page.locator("tr", has=page.get_by_role("link", name="ho-core-01"))
    row.get_by_role("button", name="Poll now").click()
    page.get_by_text(re.compile("Poll result")).wait_for()
    # The background scheduler may already have polled; either way the failure is shown honestly.
    row.get_by_text(re.compile("AUTH_REJECTED|TARGET_NOT_ALLOWED")).wait_for(timeout=20000)
    shot(page, "06_collection_live_failure_honest")

    # Change workflow: engineer submits, independent approver approves, admin runs the gate -> DENY (locked)
    api(page, "POST", "/v1/users", {"username": "e2e.engineer", "role": "operator", "temporary_password": "Engineer-Temp-2026!", "site_scope": "all"})
    api(page, "POST", "/v1/users", {"username": "e2e.approver", "role": "approver", "temporary_password": "Approver-Temp-2026!", "site_scope": "all"})
    ectx, epage, eprob = session(browser, "e2e.engineer", "Engineer-Temp-2026!")
    epage.goto(URL + "/changes")
    epage.get_by_label("Device").select_option(real)
    epage.get_by_label("Change title").fill("Move 1/1/4 to Voice VLAN 200")
    epage.get_by_label("Operation").select_option("switch.port.access_vlan")
    epage.get_by_label("Port (e.g. 1/1/4)").fill("1/1/4")
    epage.get_by_label("VLAN ID").fill("200")
    epage.get_by_role("button", name="Add to plan").click()
    epage.get_by_role("button", name="Validate & submit for approval").click()
    epage.get_by_text(re.compile("submitted for approval")).wait_for()
    shot(epage, "07_change_submitted")
    actx, apage, aprob = session(browser, "e2e.approver", "Approver-Temp-2026!")
    apage.goto(URL + "/changes")
    card = apage.locator("div.rounded-lg", has=apage.get_by_text("Move 1/1/4 to Voice VLAN 200")).first
    card.get_by_role("button", name="Approve").click()
    apage.get_by_text("Approved.").wait_for()
    page.goto(URL + "/changes")
    card = page.locator("div.rounded-lg", has=page.get_by_text("Move 1/1/4 to Voice VLAN 200")).first
    card.get_by_role("button", name="Show device requests").click()
    card.get_by_text(re.compile("Exact device requests \\(HARDWARE\\)")).wait_for()
    card.get_by_role("button", name="Apply to device").click()
    page.get_by_text(re.compile("Execution gate: DENY")).wait_for()
    shot(page, "08_change_gate_denied")

    # Full write path on an EMULATED demo switch (protocol emulator — never hardware evidence):
    # lab certification -> independent certification approval -> plan -> approval -> apply -> verify
    demo_sw = next(d for d in api(page, "GET", "/v1/devices") if d["source_type"] == "DEMO" and d["family"] == "switch")
    page.goto(URL + "/certification")
    page.get_by_label("Device", exact=True).select_option(demo_sw["id"])
    page.get_by_label("Unused test port").fill("1/1/20")
    page.get_by_label(re.compile("LAG test ports")).fill("1/1/21, 1/1/22")
    page.get_by_label(re.compile("authorised LAB device")).check()
    page.get_by_role("button", name="Run certification").click()
    page.get_by_text(re.compile("Certification run complete: .*EMULATED")).wait_for(timeout=60000)
    shot(page, "08b_certification_run")
    apage.goto(URL + "/certification")
    apage.get_by_role("button", name=re.compile("Approve all pending")).first.click()
    apage.get_by_text(re.compile("CERTIFIED_WRITE")).first.wait_for()
    shot(apage, "08c_certification_approved")
    epage.goto(URL + "/changes")
    epage.get_by_label("Device", exact=True).select_option(demo_sw["id"])
    epage.get_by_label("Change title").fill("E2E emulated VLAN 310")
    epage.get_by_label("Operation").select_option("switch.vlan.define")
    epage.get_by_label("VLAN ID").fill("310")
    epage.get_by_role("button", name="Add to plan").click()
    epage.get_by_role("button", name="Validate & submit for approval").click()
    epage.get_by_text(re.compile("submitted for approval")).wait_for()
    apage.goto(URL + "/changes")
    acard = apage.locator("div.rounded-lg", has=apage.get_by_text("E2E emulated VLAN 310")).first
    acard.get_by_role("button", name="Approve").click()
    apage.get_by_text("Approved.").wait_for()
    page.goto(URL + "/changes")
    card = page.locator("div.rounded-lg", has=page.get_by_text("E2E emulated VLAN 310")).first
    card.get_by_role("button", name="Apply to device").click()
    page.get_by_text(re.compile("Execution APPLIED_VERIFIED")).wait_for(timeout=60000)
    page.get_by_label("Execution timeline").get_by_text("EMULATED").wait_for()
    shot(page, "08d_emulated_apply_verified")

    # Optimization: feature off by default -> DEVICE_OPT_IN refused, shadow only
    page.goto(URL + "/optimization")
    page.get_by_text("OFF (INM_AUTOMATIC_OPTIMIZATION=false)").wait_for()

    # Device alerts page renders rules
    page.goto(URL + "/device-alerts")
    page.wait_for_load_state("networkidle")
    shot(page, "08e_device_alerts")

    # Executive export
    page.goto(URL + "/executive")
    with page.expect_download() as dl:
        page.get_by_role("button", name="Export inventory").click()
    assert dl.value.suggested_filename.startswith("inm-inventory")
    page.get_by_text(re.compile("Signed inventory export")).wait_for()
    shot(page, "09_executive")

    # Remove demo data with the button -> only live device remains
    page.goto(URL + "/data")
    page.get_by_role("button", name="Remove demo data").click()
    page.get_by_role("button", name="Yes, remove").click()
    page.get_by_text(re.compile("Demo data removed")).wait_for(timeout=60000)
    noc = api(page, "GET", "/v1/noc/overview")
    assert [d["device_id"] for d in noc["devices"]] == [real]
    page.goto(URL + "/")
    page.wait_for_load_state("networkidle")
    assert page.get_by_text("DEMO DATA LOADED").count() == 0
    shot(page, "10_after_demo_removed")

    all_problems = problems + eprob + aprob
    assert not all_problems, all_problems
    for c in (ctx, ectx, actx):
        c.close()
