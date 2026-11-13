"""Browser E2E of the guided workflow on the REAL distributed stack (tests/fullstack/stack.sh up — separate API and
worker processes, NATS, TimescaleDB, Loki, strict CX emulators on real sockets). Replaces the retired
pre-1.1 demo-stack suite (test_browser_live.py).

  INM_E2E_URL=http://127.0.0.1:8810 INM_E2E_ADMIN_PASSWORD='FullStack-Admin-2026!' INM_E2E_CHROME=/usr/bin/google-chrome \\
    INM_E2E_RESTART_CMD="bash tests/fullstack/stack.sh restart api" python -m pytest tests/e2e/test_gui_workflow.py

Covers: every wizard state, credentials, discovery, register, import + backup, group/templates, preview, configure
now, independent approval in a second browser session, Apply to device refused for locked/uncertified hardware,
monitoring to ONLINE through the worker pipeline, RBAC for a viewer, live WebSocket reconnect after an API
restart, and no demo devices in live mode.
"""
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.e2e.test_live_operations import api, session, shot  # noqa: F401  (shared helpers)

URL = os.environ.get("INM_E2E_URL")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL not set (requires the running full stack)")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e-gui"


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("INM_E2E_CHROME") or None, args=["--no-sandbox"])
        yield b
        b.close()


def snap(page, name):
    page.screenshot(path=str(EVID / f"{name}.png"), full_page=True)


def test_guided_workflow_live_mode_end_to_end(browser):
    pw = os.environ["INM_E2E_ADMIN_PASSWORD"]
    ctx, page, problems = session(browser, "admin", pw)
    page.get_by_text("Network command center").wait_for()
    assert page.get_by_text("DEMO MODE").count() == 0 and page.get_by_text("DEMO DATA LOADED").count() == 0
    devs = api(page, "GET", "/v1/devices")
    assert not [d for d in devs if d["source_type"] == "DEMO"]            # live mode: no demo records
    # templates the wizard will apply (created through the same API the Templates page uses)
    api(page, "POST", "/v1/config-templates", {"name": "GUI base", "family": "switch", "scope": "global", "config": {
        "vlans": [10, 20], "port_roles": {"user": {"access_vlan": 10, "bpdu_guard": "enabled"}},
        "port_assignments": [{"ports": "1/1/1-1/1/48", "role": "user"}]}})
    api(page, "POST", "/v1/users", {"username": "gui.approver", "role": "approver", "temporary_password": "Approver-Temp-2026!", "site_scope": "all"})
    api(page, "POST", "/v1/users", {"username": "gui.viewer", "role": "viewer", "temporary_password": "Viewer-Temp-2026!x", "site_scope": "all"})

    # 1 start
    page.goto(URL + "/onboarding")
    page.get_by_label("Task name").fill("GUI clinic switches")
    page.get_by_label("Device type").select_option("switch")
    page.get_by_label("Site").fill("GUI Site")
    page.get_by_role("button", name="Start workflow").click()
    page.get_by_text("2 · Credentials").wait_for()
    snap(page, "01_started")
    # 2 credentials
    page.get_by_label("Profile name").fill("gui-cx")
    page.get_by_label("Username").fill("admin")
    page.get_by_label("Password", exact=True).fill("emulator-password")
    page.get_by_role("button", name="Create & use").click()
    page.get_by_label("Discovery scope").wait_for()
    # 3 discover (/31 = both emulated switches, synchronous)
    page.get_by_label("Discovery scope").fill("10.250.0.24/31")
    page.get_by_role("button", name="Discover", exact=True).click()
    page.get_by_text("EMU-FS-24").wait_for(timeout=30000)
    snap(page, "02_discovered")
    page.get_by_label("Select 10.250.0.24").check()
    page.get_by_label("Select 10.250.0.25").check()
    page.get_by_role("button", name=re.compile("Register selected")).click()
    # 5 import + backup
    page.get_by_role("button", name="Import & back up configuration").click()
    page.get_by_text(re.compile("Configuration imported for 2/2 device")).wait_for(timeout=30000)
    page.get_by_text(re.compile("6 · Configuration scope")).wait_for()
    snap(page, "03_imported")
    # 6 templates/group
    page.get_by_label("New group name").fill("GUI access group")
    page.get_by_role("button", name="Continue to preview").click()
    # 7-8 preview + decide
    page.get_by_text(re.compile("switch.port.access_vlan")).first.wait_for(timeout=30000)
    page.get_by_text(re.compile("Port roles: user ×48")).wait_for()
    snap(page, "04_preview")
    page.get_by_role("button", name="Configure now (submit for approval)").click()
    page.get_by_text("awaiting a second person").first.wait_for()
    wid = re.search(r"id=([0-9a-f-]{36})", page.url).group(1)
    snap(page, "05_awaiting_approval")
    # 9 independent approval in a separate browser session
    actx, apage, aprob = session(browser, "gui.approver", "Approver-Temp-2026!")
    apage.goto(f"{URL}/onboarding?id={wid}")
    for _ in range(2):
        apage.get_by_role("button", name="Approve", exact=True).first.click()
        apage.wait_for_timeout(800)
    assert apage.get_by_role("button", name="Approve", exact=True).count() == 0
    snap(apage, "06_approved_by_second_person")
    # 10 apply: hardware writes are locked and the models are uncertified -> refused, nothing sent
    page.reload()
    page.get_by_role("button", name="Apply to device").click()
    page.get_by_text(re.compile("DENY — .*global_write_lock_off")).first.wait_for(timeout=30000)
    snap(page, "07_apply_refused_locked_uncertified")
    # 11 monitoring through the worker pipeline
    page.get_by_role("button", name="Skip to monitoring").click()
    page.get_by_role("button", name="Start monitoring & poll now").click()
    for _ in range(30):
        page.wait_for_timeout(2000)
        page.reload()
        if page.get_by_text("ONLINE", exact=True).count() >= 2:
            break
    assert page.get_by_text("ONLINE", exact=True).count() >= 2
    page.reload()
    page.get_by_text("Monitored — 2 change plan(s) not applied (approve, certify or unlock, then apply)").wait_for(timeout=20000)
    snap(page, "08_monitoring_online_done")
    # Device 360 for the 48-port switch: faceplate + one port's own history + capability report
    dev48 = next(d for d in api(page, "GET", "/v1/devices") if d["serial_number"] == "EMU-FS-48")
    page.goto(f"{URL}/devices/{dev48['id']}/360")
    page.get_by_role("button", name="Ports", exact=True).click()
    page.get_by_label("Port 1/1/7").click()
    page.get_by_label("Port 1/1/7 history").wait_for()
    page.get_by_role("button", name="Capabilities", exact=True).click()
    page.get_by_text(re.compile("Not certified for")).first.wait_for()
    snap(page, "09_device360_capabilities")
    page.goto(URL + "/topology")
    page.wait_for_load_state("networkidle")
    snap(page, "10_topology")

    # RBAC: a viewer sees no onboarding entry and the API refuses workflows
    vctx, vpage, vprob = session(browser, "gui.viewer", "Viewer-Temp-2026!x")
    assert vpage.get_by_role("link", name="Add & configure devices").count() == 0
    tok = vpage.evaluate("JSON.parse(sessionStorage.getItem('sims_session')).access_token")
    assert vpage.request.post(URL + "/api/v1/workflows", data={"title": "x", "device_family": "switch", "site_name": "y"},
                              headers={"Authorization": f"Bearer {tok}"}).status == 403

    # live channel survives an API restart and reconnects
    page.goto(URL + "/")
    page.get_by_text("Live", exact=True).wait_for(timeout=20000)
    cmd = os.environ.get("INM_E2E_RESTART_CMD")
    if cmd:
        subprocess.run(cmd, shell=True, check=True, timeout=180)
        page.get_by_text("Live", exact=True).wait_for(timeout=60000)
        snap(page, "11_reconnected_after_api_restart")
    restart_noise = [p for p in problems if "502" in p or "503" in p or "Failed to fetch" in p]
    assert not [p for p in problems + aprob + vprob if p not in restart_noise], problems + aprob + vprob
    for c in (ctx, actx, vctx):
        c.close()
