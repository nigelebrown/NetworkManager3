"""Browser E2E against a running INM stack (P1-05, P1-DEP-06, P1-DEP-07, P1-DEP-10, INTEG-P1-02, INTEG-P1-06).

Requires: INM_E2E_URL (e.g. http://127.0.0.1:58000), INM_E2E_ADMIN_PASSWORD (bootstrap or current),
INM_E2E_RESTART_CMD (shell command that restarts the API role), optional INM_E2E_CHROME (Chrome binary).
Screenshots go to evidence/e2e/.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

URL = os.environ.get("INM_E2E_URL")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL not set (needs a running stack)")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e"
NEW_PW = os.environ.get("INM_E2E_NEW_PASSWORD", "e2e-Admin-Password-2026!")


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        chrome = os.environ.get("INM_E2E_CHROME")
        if not chrome and Path("/usr/bin/google-chrome").exists():
            chrome = "/usr/bin/google-chrome"
        kwargs = {"executable_path": chrome} if chrome else {}
        b = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"], **kwargs)
        yield b
        b.close()


@pytest.fixture(scope="module")
def page(browser):
    # Local CI uses an ephemeral self-signed certificate. Never ignore TLS errors
    # for a non-loopback test target or without an explicit test-only opt-in.
    allow_local_test_cert = (os.environ.get("INM_E2E_ALLOW_SELF_SIGNED") == "1" and
                             URL.startswith(("https://127.0.0.1:", "https://localhost:")))
    ctx = browser.new_context(viewport={"width": 1440, "height": 950},
                              ignore_https_errors=allow_local_test_cert)
    pg = ctx.new_page()
    frames: list[dict] = []

    def on_ws(ws):
        ws.on("framereceived", lambda payload: frames.append({"t": time.time(), **_j(payload)}))
    pg.on("websocket", on_ws)
    pg.frames_log = frames  # type: ignore[attr-defined]
    _login(pg)
    yield pg
    ctx.close()


def _j(payload):
    try:
        return json.loads(payload)
    except Exception:
        return {"raw": str(payload)[:80]}


def _login(pg):
    pg.goto(URL + "/login")
    pg.fill("input[autocomplete=username]", "admin")
    pw = os.environ["INM_E2E_ADMIN_PASSWORD"]
    pg.fill("input[autocomplete=current-password]", pw)
    pg.click("button:has-text('Sign in')")
    pg.get_by_text("Network overview").or_(pg.get_by_text("Set your password")).first.wait_for(timeout=15000)
    if pg.is_visible("text=Set your password"):
        pg.screenshot(path=str(EVID / "01_forced_password_change.png"))
        pg.fill("input[autocomplete=current-password]", pw)
        pg.locator("input[autocomplete=new-password]").nth(0).fill(NEW_PW)
        pg.locator("input[autocomplete=new-password]").nth(1).fill(NEW_PW)
        pg.click("button:has-text('Save password')")
        os.environ["INM_E2E_ADMIN_PASSWORD"] = NEW_PW
    pg.wait_for_selector("text=Network overview", timeout=15000)


def test_dashboard_parity_and_demo_labels(page):
    page.wait_for_selector("[data-testid=live-indicator][data-state=live]", timeout=15000)
    page.get_by_text("simulated fixtures are marked DEMO").wait_for(timeout=10000)
    page.get_by_text("Device writes are LOCKED").wait_for(timeout=10000)
    page.screenshot(path=str(EVID / "02_dashboard_live.png"), full_page=True)
    for path, marker in [("/aps", "Access points"), ("/clients", "Clients"), ("/sites", "Sites"), ("/alerts", "Alerts"),
                         ("/experience", "Experience"), ("/config", "Configuration"), ("/firmware", "Firmware"),
                         ("/assistant", "Assistant")]:
        page.click(f"nav >> a[href='{path}']")
        page.wait_for_timeout(1200)
        assert "/login" not in page.url, path
        page.screenshot(path=str(EVID / f"03_legacy{path.replace('/', '_')}.png"), full_page=True)


def test_live_update_without_reload(page):
    page.click("nav >> a[href='/']")
    page.evaluate("window.__noReloadMarker = 'kept'")
    summary_calls: list[float] = []
    page.on("request", lambda r: summary_calls.append(time.time()) if "/api/dashboard/summary" in r.url else None)
    start = time.time()
    deadline = start + 60
    while time.time() < deadline:
        inval = [f for f in page.frames_log if f.get("type") == "invalidate" and "dashboard" in f.get("topics", []) and f["t"] > start]
        if inval and any(t > inval[0]["t"] for t in summary_calls):
            break
        page.wait_for_timeout(500)
    assert inval, "no dashboard invalidation received over the WebSocket within 60 s"
    assert any(t > inval[0]["t"] for t in summary_calls), "dashboard did not re-query after the invalidation"
    assert page.evaluate("window.__noReloadMarker") == "kept", "page was reloaded"


def test_reconnect_after_api_restart_resyncs(page):
    cmd = os.environ.get("INM_E2E_RESTART_CMD")
    if not cmd:
        pytest.skip("INM_E2E_RESTART_CMD not set")
    page.click("nav >> a[href='/']")
    page.evaluate("window.__noReloadMarker = 'kept2'")
    calls: list[float] = []
    page.on("request", lambda r: calls.append(time.time()) if "/api/dashboard/summary" in r.url else None)
    subprocess.run(cmd, shell=True, check=True, timeout=120)
    page.wait_for_selector("[data-testid=live-indicator][data-state=offline], [data-testid=live-indicator][data-state=connecting]", timeout=20000)
    page.screenshot(path=str(EVID / "04_reconnecting.png"))
    page.wait_for_selector("[data-testid=live-indicator][data-state=live]", timeout=60000)
    back = time.time()
    page.wait_for_timeout(2000)
    assert any(t >= back - 1 for t in calls), "no authoritative re-query after reconnect"
    assert page.evaluate("window.__noReloadMarker") == "kept2", "page was reloaded"
    page.screenshot(path=str(EVID / "05_reconnected_live.png"))


def test_system_health_and_round_trip(page):
    page.click("nav >> a[href='/system']")
    page.wait_for_selector("[data-testid=role-network_worker]", timeout=15000)
    page.wait_for_timeout(1500)
    rows = page.locator("tr[data-testid^=role-]")
    assert rows.count() == 7
    page.click("button:has-text('Run round-trip')")
    page.wait_for_selector("[data-testid=trace] >> text=Complete", timeout=40000)
    page.screenshot(path=str(EVID / "06_system_health_round_trip.png"), full_page=True)


def test_new_modules_are_truthful(page):
    page.click("nav >> a[href='/switches']")
    page.wait_for_selector("text=demo-hq-cx6200-01", timeout=15000)
    assert page.locator("text=DEMO").count() >= 4
    page.screenshot(path=str(EVID / "07_switches_demo.png"), full_page=True)
    page.click("nav >> a[href='/recovery']")
    page.wait_for_selector("text=PLANNED · Phase 5", timeout=10000)
    page.screenshot(path=str(EVID / "08_recovery_planned.png"), full_page=True)
    page.click("nav >> a[href='/ai-learning']")
    page.click("button:has-text('Analyze now')")
    page.wait_for_selector("text=recommendation(s) from RULE", timeout=20000)
    assert page.is_visible("text=Deterministic rules, not machine learning.")
    page.screenshot(path=str(EVID / "09_ai_rules.png"), full_page=True)
    page.click("nav >> a[href='/audit']")
    page.click("button:has-text('Verify hash chain')")
    page.wait_for_selector("[data-testid=chain-result] >> text=Chain intact", timeout=10000)
    page.screenshot(path=str(EVID / "10_audit_chain.png"), full_page=True)
    page.click("nav >> a[href='/logs']")
    page.wait_for_timeout(2500)
    page.screenshot(path=str(EVID / "11_logs.png"), full_page=True)


def test_import_actual_ap_and_switch_observations(page):
    """An admin can import real (manually observed) AP and switch data without device writes."""
    page.goto("https://127.0.0.1:8443/onboarding/manual")
    page.get_by_text("Add a network device").wait_for(timeout=15000)
    page.get_by_label("Site / physical location").fill("CI physical site")
    page.get_by_label("Device name").fill("CI-AP-535")
    page.get_by_label("Device serial number").fill("CI-REAL-AP-535")
    page.get_by_label("Model").fill("AP-535")
    page.get_by_label("Management IPv4 address (optional)").fill("10.101.7.15")
    page.get_by_label("Connected clients").fill("12")
    page.get_by_label("5 GHz utilization (%)").fill("28")
    page.get_by_label("CPU (%)").fill("17")
    page.click("button:has-text('Save real device and readings')")
    page.get_by_text("Real device snapshot saved: CI-AP-535").wait_for(timeout=15000)
    page.get_by_text("REAL · MANUAL").first.wait_for(timeout=15000)
    page.get_by_role("link", name="View details").first.click()
    page.get_by_text("Latest recorded readings").wait_for(timeout=15000)
    # The metric appears in both the summary card and history table.
    page.get_by_text("Connected clients").first.wait_for(timeout=15000)
    page.get_by_text("CI-REAL-AP-535").wait_for(timeout=15000)
    page.get_by_text("10.101.7.15").wait_for(timeout=15000)
    page.screenshot(path=str(EVID / "12_real_ap_detail.png"), full_page=True)
    page.get_by_role("link", name="Back to device inventory").click()
    page.screenshot(path=str(EVID / "12_real_ap_intake.png"), full_page=True)

    page.click("button:has-text('Aruba CX switch')")
    page.get_by_label("Site / physical location").fill("CI physical site")
    page.get_by_label("Device name").fill("CI-CX6200F")
    page.get_by_label("Device serial number").fill("CI-REAL-CX6200")
    page.get_by_label("Model").fill("CX 6200F")
    page.get_by_label("Management IPv4 address (optional)").fill("10.103.7.21")
    page.get_by_label("Total interfaces").fill("24")
    page.get_by_label("Interfaces up").fill("19")
    page.get_by_label("PoE used (W)").fill("88")
    page.click("button:has-text('Save real device and readings')")
    page.get_by_text("Real device snapshot saved: CI-CX6200F").wait_for(timeout=15000)
    page.click("nav >> a[href='/switches']")
    page.get_by_text("CI-CX6200F").wait_for(timeout=15000)
    page.get_by_text("19/24").first.wait_for(timeout=15000)
    page.screenshot(path=str(EVID / "13_real_switch_metrics.png"), full_page=True)


def test_printer_intelligence_workflow(page):
    """Register printer, record real meters/supplies, close toner cycle and see calculated cost."""
    page.click("nav >> a[href='/printers']")
    page.get_by_text("Printer Intelligence").first.wait_for(timeout=15000)
    page.get_by_role("button", name="Add a printer").click()
    page.get_by_label("Model").fill("LaserJet CI Test")
    page.get_by_label("Serial number").fill("CI-PRINTER-001")
    page.get_by_label("Hostname").fill("CI-HQ-PRN-01")
    page.get_by_label("Management IPv4").fill("10.103.44.21")
    page.get_by_label("Area").fill("Area One")
    page.get_by_label("Division").fill("Kingston")
    page.get_by_role("button", name="Register printer").click()
    page.get_by_text("Printer registered.").wait_for(timeout=15000)
    page.get_by_role("link", name="Open").last.click()
    page.get_by_text("LaserJet CI Test").wait_for(timeout=15000)
    page.get_by_label("Cumulative reading").fill("1000")
    page.get_by_role("button", name="Save meter reading").click()
    page.get_by_text("Meter reading saved.").wait_for(timeout=15000)
    page.get_by_label("Cumulative reading").fill("1800")
    page.get_by_role("button", name="Save meter reading").click()
    page.get_by_text("Meter reading saved.").wait_for(timeout=15000)
    page.get_by_role("button", name="Record toner / ink").click()
    page.get_by_label("Remaining (%) — if available").fill("12")
    page.get_by_role("button", name="Save supply reading").click()
    page.get_by_text("Consumable reading saved.").wait_for(timeout=15000)
    page.get_by_text("12%").first.wait_for(timeout=15000)
    page.get_by_role("button", name="Cartridge / refill cycle").click()
    page.get_by_label("Start meter (impressions)").fill("1000")
    page.get_by_label("Consumable purchase cost").fill("8000")
    page.get_by_role("button", name="Record new cartridge or refill").click()
    page.get_by_text("Consumable installation/refill recorded.").wait_for(timeout=15000)
    page.get_by_label("Meter at replacement / next refill").fill("1800")
    page.get_by_role("button", name="Close cycle and calculate").click()
    page.get_by_text("Lifecycle closed.").wait_for(timeout=15000)
    page.get_by_text("JMD 10000.00").wait_for(timeout=15000)
    page.get_by_text("CALCULATED_PROXY").wait_for(timeout=15000)
    page.screenshot(path=str(EVID / "14_printer_intelligence_yield.png"), full_page=True)
    page.click("nav >> a[href='/printers']")
    page.get_by_role("button", name="Analyse now").click()
    page.get_by_text("Rule-based recommendations").wait_for(timeout=15000)
    page.screenshot(path=str(EVID / "15_printer_rule_analysis.png"), full_page=True)


def test_admin_can_bulk_import_and_edit_existing_real_network_devices(page):
    """Use the actual CSV file input, then edit a device on the new onboarding screen."""
    page.goto("https://127.0.0.1:8443/onboarding/manual")
    page.get_by_text("Add a network device").wait_for(timeout=15000)
    csv = (
        "family,site_name,hostname,serial_number,model,management_ip,firmware,status\n"
        "access_point,CI Bulk Site,BULK-AP-535,CI-BULK-AP-001,AP-535,10.101.10.15,8.10.0.21,unknown\n"
        "switch,CI Bulk Site,BULK-CX-6200,CI-BULK-CX-002,CX 6200F,10.103.10.21,10.14,unknown\n"
    )
    page.locator("input[type=file]").set_input_files({
        "name": "ci-bulk-devices.csv", "mimeType": "text/csv", "buffer": csv.encode("utf-8")
    })
    page.get_by_text("Review 2 device(s) before import").wait_for(timeout=15000)
    page.get_by_role("button", name="Confirm and import 2 device(s)").click()
    page.get_by_text("2 new device(s), 0 updated").wait_for(timeout=15000)
    page.get_by_text("BULK-AP-535").last.wait_for(timeout=15000)
    # Editing an existing serial updates the inventory row, not an AP's configuration.
    row = page.locator("tr", has=page.get_by_text("CI-BULK-AP-001")).last
    row.get_by_role("button", name="Edit details").click()
    page.get_by_label("Device name").fill("BULK-AP-535-RENAMED")
    page.get_by_role("button", name="Save real device and readings").click()
    page.get_by_text("Real device snapshot saved: BULK-AP-535-RENAMED").wait_for(timeout=15000)
    page.screenshot(path=str(EVID / "16_bulk_import_edit_network_devices.png"), full_page=True)


def test_guided_onboarding_starts_with_site_and_reusable_credential_choice(page):
    page.click("nav >> a[href='/onboarding']")
    page.get_by_text("Onboard network equipment").wait_for(timeout=15000)
    page.get_by_label("Physical site / location").fill("CI Site")
    page.get_by_role("button", name="Next").click()
    page.get_by_role("button", name="Select existing credentials").wait_for(timeout=15000)
    page.get_by_role("button", name="Create credential profile").wait_for(timeout=15000)
    page.screenshot(path=str(EVID / "17_guided_onboarding_credentials.png"), full_page=True)
