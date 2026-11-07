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
        b = p.chromium.launch(executable_path=os.environ.get("INM_E2E_CHROME", "/usr/bin/google-chrome"),
                              args=["--no-sandbox", "--disable-dev-shm-usage"])
        yield b
        b.close()


@pytest.fixture(scope="module")
def page(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 950})
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
    page.get_by_text("all data shown is SIMULATED").wait_for(timeout=10000)
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
