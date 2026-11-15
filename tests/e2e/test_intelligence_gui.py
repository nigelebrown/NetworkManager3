"""Network Intelligence browser E2E — all eight workspaces in Chrome against tests/e2e/intelligence_stack.py.

Run:  python -I tests/e2e/intelligence_stack.py /tmp/intel-e2e 8830 &
      INM_E2E_URL=http://127.0.0.1:8830 python -m pytest tests/e2e/test_intelligence_gui.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("INM_E2E_URL")
PW = os.environ.get("INM_E2E_ADMIN_PASSWORD", "Intel-E2E-Admin-2026!")
CHROME = os.environ.get("INM_E2E_CHROME", "/usr/bin/google-chrome")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL is required (start tests/e2e/intelligence_stack.py)")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e" / "intelligence"


def _login(ctx, user, pw):
    pg = ctx.new_page()
    faults: list[str] = []
    pg.on("pageerror", lambda e: faults.append(f"browser error: {e}"))
    pg.on("response", lambda r: faults.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
    pg.faults = faults
    pg.goto(URL + "/login")
    pg.get_by_label("Username").fill(user)
    pg.get_by_label("Password").fill(pw)
    pg.get_by_role("button", name="Sign in").click()
    pg.wait_for_url(re.compile(r".*/$"), timeout=15000)
    return pg


@pytest.fixture(scope="module")
def browser():
    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture(scope="module")
def page(browser):
    ctx = browser.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
    pg = _login(ctx, "admin", PW)
    yield pg
    assert not pg.faults, pg.faults


def shot(pg, name):
    pg.screenshot(path=str(EVID / f"{name}.png"), full_page=True)


def api(pg, method, path, data=None):
    tok = pg.evaluate("JSON.parse(sessionStorage.getItem('sims_session')).access_token")
    r = getattr(pg.request, method)(URL + "/api" + path, headers={"Authorization": "Bearer " + tok}, data=data)
    assert r.status < 300, (path, r.status, r.text())
    return r.json()


def test_1_device_insights_analyze(page):
    page.goto(URL + "/phase4-insights")
    page.get_by_role("button", name="Analyze now").first.click()
    expect(page.get_by_text(re.compile(r"Analysed 8 device\(s\): \d+ new"))).to_be_visible(timeout=15000)
    expect(page.get_by_text("Device not monitored").first).to_be_visible()
    expect(page.get_by_text("Sustained high CPU").first).to_be_visible()
    shot(page, "1-device-insights")


def test_2_recommendations_triage(page):
    page.goto(URL + "/recommendations")
    card = page.locator("section[data-rec='PHASE4_CPU_PERCENT_SUSTAINED']").first
    expect(card).to_be_visible()
    card.get_by_role("button", name="Evidence").click()
    expect(card.locator("pre")).to_contain_text("samples")
    card.get_by_label("Triage note").fill("Load test scheduled on this AP")
    card.get_by_role("button", name="acknowledge").click()
    expect(page.get_by_text("acknowledged.")).to_be_visible()
    page.get_by_role("button", name="Analyze devices").click()
    expect(page.get_by_text(re.compile(r"Device insights: 0 new, \d+ refreshed"))).to_be_visible()
    shot(page, "2-recommendations")


def test_3_ai_rules_two_person_change(page, browser):
    api(page, "post", "/v1/users", {"username": "e2e.approver", "role": "approver", "temporary_password": "Temporary-Pass-123", "site_scope": "all"})
    page.goto(URL + "/ai-learning?rule=PHASE4_CPU_PERCENT_SUSTAINED")
    expect(page.get_by_text("Version history")).to_be_visible()
    page.get_by_label("Parameter threshold").fill("85")
    page.get_by_label("Change reason").fill("Lab APs run hot; tighten earlier")
    page.get_by_role("button", name="Propose change").click()
    expect(page.get_by_text("Change proposed; an independent approver must approve it.")).to_be_visible()
    expect(page.get_by_text("Awaiting an independent approver.")).to_be_visible()
    ctx = browser.new_context(viewport={"width": 1500, "height": 950})
    p2 = ctx.new_page()
    p2.goto(URL + "/login")
    p2.get_by_label("Username").fill("e2e.approver")
    p2.get_by_label("Password").fill("Temporary-Pass-123")
    p2.get_by_role("button", name="Sign in").click()
    p2.wait_for_url(re.compile(r".*/change-password"), timeout=15000)
    tok = p2.evaluate("JSON.parse(sessionStorage.getItem('sims_session')).access_token")
    assert p2.request.post(URL + "/api/auth/change-password", headers={"Authorization": "Bearer " + tok},
                           data={"current_password": "Temporary-Pass-123", "new_password": "Approver-Pass-456!"}).status == 200
    p2 = _login(ctx, "e2e.approver", "Approver-Pass-456!")
    p2.goto(URL + "/ai-learning?rule=PHASE4_CPU_PERCENT_SUSTAINED")
    p2.get_by_label("Decision note").fill("Reviewed lab data, agree")
    p2.get_by_role("button", name="Approve").click()
    expect(p2.get_by_text("Approved; new version active.")).to_be_visible()
    expect(p2.get_by_role("cell", name="v2").first).to_be_visible()
    shot(p2, "3-ai-rules-approved")
    assert not p2.faults, p2.faults
    ctx.close()


def test_4_predictive_validated(page):
    page.goto(URL + "/predictive")
    page.get_by_role("button", name="Run predictions").click()
    expect(page.get_by_text(re.compile(r"Run SUCCEEDED: (PARTIALLY_)?VALIDATED"))).to_be_visible(timeout=60000)
    expect(page.get_by_text("Backtest evaluation per rule")).to_be_visible()
    page.get_by_label("Prediction state").select_option("AT_RISK")
    rows = page.locator("table").last.locator("tbody tr")
    expect(rows).to_have_count(3, timeout=15000)                         # filter applied: only the busy APs
    for i in range(3):
        expect(rows.nth(i)).to_contain_text("AT_RISK")
    expect(rows.nth(0)).to_contain_text("lab-ap-busy-")
    expect(page.get_by_text("OPERATIONAL").first).to_be_visible()
    page.get_by_label("Prediction state").select_option("INSUFFICIENT_DATA")
    expect(page.get_by_role("link", name="lab-ap-new").first).to_be_visible()
    shot(page, "4-predictive")


def test_5_operational_analytics_drilldown_export(page):
    page.goto(URL + "/analytics")
    expect(page.get_by_role("heading", name="By site")).to_be_visible(timeout=15000)
    page.get_by_label("Window").select_option("7d")
    row = page.get_by_role("row").filter(has_text="Lab Annex")
    row.get_by_role("button", name="Drill down").click()
    expect(page.get_by_text("Devices at Lab Annex")).to_be_visible()
    expect(page.get_by_role("link", name="lab-sw-silent")).to_be_visible()
    expect(page.get_by_text("NO_DATA").first).to_be_visible()
    with page.expect_download() as d:
        page.get_by_role("button", name="Export CSV").click()
    assert d.value.suggested_filename == "analytics-7d.csv"
    d.value.save_as(EVID / "analytics-7d.csv")
    shot(page, "5-analytics")


def test_6_learning_centre(page):
    page.goto(URL + "/learning")
    expect(page.get_by_text("Model comparison & rollback")).to_be_visible()
    page.get_by_role("button", name="Run simulation").click()
    expect(page.get_by_text(re.compile(r"Simulation complete — device writes: (0|NONE|none)"))).to_be_visible()
    shot(page, "6-learning")


def test_7_assistant_rules_fallback(page):
    page.goto(URL + "/assistant")
    page.get_by_placeholder("e.g. Which APs had the most disconnects today?").fill("Which access points are offline?")
    page.get_by_role("button", name="Ask").click()
    expect(page.get_by_text("Rules fallback — model unavailable")).to_be_visible(timeout=15000)
    expect(page.get_by_text("Sources:").first).to_be_visible()
    shot(page, "7-assistant")


def test_8_rf_optimization_controls(page):
    page.goto(URL + "/optimization")
    expect(page.get_by_text("RF optimization — per-device opt-in")).to_be_visible()
    shot(page, "8-optimization")
    inv = api(page, "get", "/v1/intelligence/inventory")
    assert inv["complete"], inv
