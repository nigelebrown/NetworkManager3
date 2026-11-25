"""Printer Intelligence browser E2E (Chrome) against tests/e2e/printer_stack.py (emulated SNMP printers).

Run:  python -I tests/e2e/printer_stack.py /tmp/prn-e2e 8820 &
      INM_E2E_URL=http://127.0.0.1:8820 python -m pytest tests/e2e/test_printer_gui.py
Every workspace and device tab is opened as a logged-in user; browser errors and HTTP 5xx fail the test.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("INM_E2E_URL")
PW = os.environ.get("INM_E2E_ADMIN_PASSWORD", "Printer-E2E-Admin-2026!")
CHROME = os.environ.get("INM_E2E_CHROME", "/usr/bin/google-chrome")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL is required (start tests/e2e/printer_stack.py)")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e" / "printers"
MIB = Path(__file__).resolve().parents[1] / "contract" / "test_printer_srs.py"


@pytest.fixture(scope="module")
def page():
    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1500, "height": 950}, accept_downloads=True)
        pg = ctx.new_page()
        faults: list[str] = []
        pg.on("pageerror", lambda e: faults.append(f"browser error: {e}"))
        pg.on("response", lambda r: faults.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
        pg.faults = faults
        pg.goto(URL + "/login")
        pg.get_by_label("Username").fill("admin")
        pg.get_by_label("Password").fill(PW)
        pg.get_by_role("button", name="Sign in").click()
        pg.wait_for_url(re.compile(r".*/$"), timeout=15000)
        yield pg
        browser.close()
        assert not faults, faults


def token(pg):
    return pg.evaluate("JSON.parse(sessionStorage.getItem('sims_session')).access_token")


def api(pg, method, path, data=None):
    r = getattr(pg.request, method)(URL + "/api" + path, headers={"Authorization": "Bearer " + token(pg)}, data=data)
    assert r.status < 300, (path, r.status, r.text())
    return r.json()


def shot(pg, name):
    pg.screenshot(path=str(EVID / f"{name}.png"), full_page=True)


def test_overview_and_navigation(page):
    page.goto(URL + "/printers")
    expect(page.get_by_role("heading", name="Printer fleet overview")).to_be_visible()
    expect(page.locator(".inm-kpi").first).to_contain_text("6")
    for label in ("Inventory", "Live monitoring", "Utilization", "Consumables", "Maintenance", "Model comparison",
                  "Recommendations", "Reports", "SNMP & MIB profiles", "Overview"):
        page.get_by_role("navigation", name="Printer workspaces").get_by_role("link", name=label).click()
        page.wait_for_timeout(400)
        assert not page.faults, page.faults
    shot(page, "01-overview")


def test_live_monitoring_shows_separate_dimensions(page):
    page.goto(URL + "/printers/live")
    expect(page.get_by_text("prn-kyc-0001")).to_be_visible()
    expect(page.get_by_text("REACHABLE").first).to_be_visible()
    expect(page.get_by_text("AVAILABLE").first).to_be_visible()
    shot(page, "02-live")


def test_discovery_job_and_approval(page):
    cid = api(page, "post", "/printers/credential-profiles",
              {"name": "E2E SNMPv3", "version": "3", "username": "inm-ro", "auth_passphrase": "auth-pass-123", "privacy_passphrase": "priv-pass-123"})["id"]
    page.goto(URL + "/printers/inventory")
    page.get_by_role("checkbox", name="E2E SNMPv3 (SNMPv3)", exact=True).check()
    page.get_by_label("CIDR (/9–/32) or IP list").fill("10.103.70.20, 10.103.70.99")
    page.get_by_role("button", name="Start discovery").click()
    expect(page.get_by_text(re.compile(r"SUCCEEDED: 2/2 checked · 1 printers found"))).to_be_visible(timeout=30000)
    page.get_by_label("Select 10.103.70.20").check()
    page.get_by_role("button", name="Approve selected for monitoring").click()
    expect(page.get_by_text("1 registered")).to_be_visible()
    expect(page.get_by_role("link", name="prn-new").or_(page.get_by_role("link", name="KYC-NEW-01"))).to_be_visible()
    shot(page, "03-discovery")


def test_csv_import_preview_and_commit(page):
    page.goto(URL + "/printers/inventory")
    page.get_by_label("CSV text").fill("manufacturer,model,serial_number,management_ip,station\n"
                                      "Epson,WF-6590,EPS-0001,10.103.70.30,Annex\nEpson,WF-6590,EPS-0001,10.103.70.31,Annex\n")
    page.get_by_role("button", name="Preview & validate").click()
    expect(page.get_by_text("1 valid, 1 with errors of 2.")).to_be_visible()
    page.get_by_role("button", name="Import valid rows").click()
    expect(page.get_by_text("1 imported, 1 skipped.")).to_be_visible()


def _open_printer(page, name="prn-kyc-0004"):
    page.goto(URL + "/printers/inventory")
    page.get_by_role("link", name=name).first.click()
    expect(page.get_by_role("tab", name="Overview")).to_be_visible()


def test_device_tabs(page):
    _open_printer(page)
    expect(page.get_by_text("Capability matrix")).to_be_visible()
    expect(page.get_by_text("SUPPORTED").first).to_be_visible()
    shot(page, "04-device-overview")
    page.get_by_role("tab", name="Usage").click()
    expect(page.get_by_text(re.compile(r"Total \(impressions_total\)"))).to_be_visible()
    page.get_by_label("Granularity").select_option("month")
    expect(page.get_by_text("SUSTAINED_HIGH")).to_be_visible(timeout=10000)        # 1,500/day vs 15,000 verified
    shot(page, "05-device-usage")
    page.get_by_role("tab", name="Consumables").click()
    expect(page.get_by_text("SOME_REMAINING").first).to_be_visible()               # waste toner -3 decoded
    expect(page.get_by_text("REPLACEMENT_CONFIRMED").first).to_be_visible()
    shot(page, "06-device-consumables")
    page.get_by_role("tab", name="Faults & maintenance").click()
    expect(page.get_by_text("JAMMED").first).to_be_visible()
    page.get_by_label("Component").fill("fuser")
    page.get_by_label("Description").fill("Fuser replaced after repeated jams")
    page.get_by_role("button", name="Add record").click()
    expect(page.get_by_text(re.compile(r"Recorded; \d+ device fault period"))).to_be_visible()
    shot(page, "07-device-faults")
    page.get_by_role("tab", name="Raw SNMP data").click()
    expect(page.get_by_text("marker_life_count").first).to_be_visible()
    page.get_by_label("MIB root").select_option("system")
    page.get_by_role("button", name="Run walk").click()
    expect(page.get_by_text(re.compile(r"SUCCEEDED: \d+ rows"))).to_be_visible(timeout=20000)
    shot(page, "08-device-raw")
    page.get_by_role("tab", name="Configuration").click()
    page.get_by_role("button", name="Poll now").click()
    expect(page.get_by_text("Read-only poll completed.")).to_be_visible(timeout=30000)
    shot(page, "09-device-config")


def test_recommendations_review(page):
    page.goto(URL + "/printers/recommendations")
    page.get_by_role("button", name="Run analysis").click()
    expect(page.get_by_text(re.compile(r"\d+ new, \d+ refreshed"))).to_be_visible()
    first = page.locator("section.inm-card").filter(has_text="UNDERSIZED").first
    expect(first).to_be_visible()
    first.get_by_label("Decision note").fill("Replace with higher duty model at next budget")
    first.get_by_role("button", name="approved").click()
    expect(page.get_by_text("Recorded approved.")).to_be_visible()
    shot(page, "10-recommendations")


def test_reports_generate_and_download(page):
    page.goto(URL + "/printers/reports")
    for value, fmt in (("monthly_utilization", "xlsx"), ("executive_fleet", "pdf"), ("fleet_inventory", "csv")):
        page.get_by_label("Report").select_option(value)
        page.get_by_label("Format").select_option(fmt)
        with page.expect_download() as d:
            page.get_by_role("button", name="Generate & download").click()
        assert d.value.suggested_filename.endswith("." + fmt)
        d.value.save_as(EVID / d.value.suggested_filename)
    shot(page, "11-reports")


def test_mib_upload_browse_build_profile(page, tmp_path):
    src = MIB.read_text()
    text = src[src.index("ACME-PRINTER-MIB DEFINITIONS"):src.index("END\n'''") + 4]
    f = tmp_path / "ACME-PRINTER-MIB.mib"
    f.write_text(text)
    page.goto(URL + "/printers/profiles")
    page.locator("input[type=file]").set_input_files(str(f))
    expect(page.get_by_text(re.compile(r"ACME-PRINTER-MIB v\d+: 9 objects, RESOLVED"))).to_be_visible()
    expect(page.get_by_text("acmeColorPrintCount")).to_be_visible()
    page.get_by_label("Select acmeTotalPageCount").check()
    page.get_by_label("Select acmeColorPrintCount").check()
    page.get_by_role("button", name="Build profile from selection").click()
    page.get_by_label("key").fill("acme-e2e")
    page.get_by_label("name", exact=True).fill("ACME (E2E)")
    page.get_by_role("button", name="Create profile").click()
    expect(page.get_by_text("Profile created.")).to_be_visible()
    expect(page.get_by_text("acme-e2e")).to_be_visible()
    shot(page, "12-mib-profiles")


def test_model_comparison_and_maintenance_pages(page):
    page.goto(URL + "/printers/models")
    expect(page.get_by_text("ECOSYS M2640idw").first).to_be_visible()
    expect(page.get_by_text("INSUFFICIENT_EVIDENCE").first).to_be_visible()
    shot(page, "13-models")
    page.goto(URL + "/printers/maintenance")
    expect(page.get_by_text("Reliability by model")).to_be_visible()
    expect(page.get_by_text("Fuser replaced after repeated jams")).to_be_visible()
    shot(page, "14-maintenance")
    page.goto(URL + "/printers/consumables")
    expect(page.get_by_text("Supplies by printer")).to_be_visible()
    shot(page, "15-consumables")
