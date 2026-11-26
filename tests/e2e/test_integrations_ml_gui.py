"""Browser E2E (Chrome) for rc18 Integrations and trained-model screens against tests/e2e/ops_stack.py.

  INM_E2E_URL=http://127.0.0.1:8830 python -m pytest tests/e2e/test_integrations_ml_gui.py

Integrations: add an SMTP connection with an encrypted credential (never shown again), see it on Alert routing and
change its severity there, disable/enable it, create a read-only API token (shown once) and use it, enable the
metrics endpoint, audit history. Learning centre: train a synthetic model, see it labelled NOT VALIDATED, and see the
engine of record on Predictive health. Browser errors and HTTP 5xx fail the test.
"""
from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

URL = os.environ.get("INM_E2E_URL")
PW = os.environ.get("INM_E2E_ADMIN_PASSWORD", "Ops-E2E-Admin-2026!")
CHROME = os.environ.get("INM_E2E_CHROME", "/usr/bin/google-chrome")
pytestmark = pytest.mark.skipif(not URL, reason="INM_E2E_URL is required (start tests/e2e/ops_stack.py)")
EVID = Path(__file__).resolve().parents[2] / "evidence" / "e2e" / "integrations-ml-rc18"
RUN = uuid.uuid4().hex[:5]


@pytest.fixture(scope="module")
def page():
    EVID.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROME, args=["--no-sandbox"])
        ctx = b.new_context(viewport={"width": 1440, "height": 950})
        pg = ctx.new_page()
        pg.faults = []
        pg.on("pageerror", lambda e: pg.faults.append(f"browser error: {e}"))
        pg.on("response", lambda r: pg.faults.append(f"HTTP {r.status} {r.url}") if r.status >= 500 else None)
        pg.goto(URL + "/login")
        pg.get_by_label("Username").fill("admin")
        pg.get_by_label("Password").fill(PW)
        pg.get_by_role("button", name="Sign in").click()
        pg.wait_for_url(re.compile(r".*/$"), timeout=15000)
        yield pg
        b.close()


def test_integrations_connection_routing_token_and_metrics(page):
    pg = page
    pg.goto(URL + "/integrations")
    expect(pg.get_by_role("heading", name="Integrations")).to_be_visible()
    expect(pg.get_by_text("DESIGN CONCEPT")).to_have_count(0)
    form = pg.get_by_role("form", name="New connection")
    form.get_by_label("Connection name").fill(f"Ops mail {RUN}")
    form.get_by_label("Connection type").select_option(label="SMTP e-mail")
    form.get_by_label("Destination").fill("noc@example.org")
    form.get_by_label("SMTP relay host").fill("smtp.example.org")
    form.get_by_label("SMTP username").fill("inm")
    form.get_by_label("SMTP password").fill(f"secret-{RUN}")
    form.get_by_role("button", name="Add connection").click()
    row = pg.get_by_role("region", name="Connections").locator("tbody tr", has_text=f"Ops mail {RUN}")
    expect(row).to_contain_text("encrypted")
    expect(row).to_contain_text("UNTESTED")
    assert f"secret-{RUN}" not in pg.content()
    row.get_by_role("button", name="Disable").click()
    expect(row).to_contain_text("DISABLED")
    row.get_by_role("button", name="Enable").click()
    expect(row).to_contain_text("UNTESTED")
    pg.screenshot(path=str(EVID / "integrations.png"), full_page=True)
    # alert routing lives on its own screen
    pg.goto(URL + "/notifications")
    pg.get_by_label(f"Minimum severity for Ops mail {RUN}").select_option("critical")
    pg.locator("tbody tr").filter(has=pg.get_by_label(f"Minimum severity for Ops mail {RUN}")).get_by_role("button", name="Save").click()
    expect(pg.get_by_text("Routing saved.")).to_be_visible()
    expect(pg.get_by_role("button", name="Add channel")).to_have_count(0)
    # read-only API token, shown once
    pg.goto(URL + "/integrations")
    tf = pg.get_by_role("form", name="New API token")
    tf.get_by_label("Token name").fill(f"grafana-{RUN}")
    tf.get_by_label("metrics").check()
    tf.get_by_role("button", name="Create token").click()
    token = pg.locator("[role=status] code").inner_text()
    assert token.startswith("inm_pat_")
    status = pg.evaluate("""async (t) => (await fetch('/api/v1/system/features', {headers: {Authorization: 'Bearer ' + t}})).status""", token)
    denied = pg.evaluate("""async (t) => (await fetch('/api/v1/integrations/api-tokens', {method: 'POST',
        headers: {Authorization: 'Bearer ' + t, 'Content-Type': 'application/json'}, body: '{"name":"x"}'})).status""", token)
    assert status == 200 and denied == 403
    pg.get_by_role("button", name="Enable metrics endpoint").click()
    expect(pg.get_by_text("Metrics endpoint enabled.")).to_be_visible()
    metrics = pg.evaluate("""async (t) => await (await fetch('/api/v1/metrics', {headers: {Authorization: 'Bearer ' + t}})).text()""", token)
    assert "inm_devices" in metrics
    pg.reload()
    expect(pg.get_by_role("region", name="Integration audit history")).to_contain_text("api_token_created")
    assert token not in pg.content()
    assert pg.faults == [], pg.faults


def test_learning_centre_synthetic_model_is_labelled_and_engine_of_record_shown(page):
    pg = page
    pg.goto(URL + "/learning")
    expect(pg.get_by_role("heading", name="Trained breach-risk models")).to_be_visible()
    pg.get_by_role("button", name="Train on synthetic data").click()
    expect(pg.get_by_text(re.compile(r"Synthetic pipeline run SUCCEEDED"))).to_be_visible(timeout=60000)
    row = pg.locator("tr", has_text="pred_cpu_breach-v")
    expect(row.first).to_contain_text("SYNTHETIC")
    expect(row.first).to_contain_text("NOT VALIDATED")
    pg.screenshot(path=str(EVID / "learning-models.png"), full_page=True)
    pg.goto(URL + "/predictive")
    region = pg.get_by_role("region", name="Prediction engine of record")
    expect(region).to_contain_text("PRED_CPU_BREACH")
    expect(region).to_contain_text("STATISTICAL FALLBACK")
    assert pg.faults == [], pg.faults
