"""rc18 printer fleet reports: annual mono/colour/duplex utilization and paper-size utilization from vendor counters,
with NOT_SUPPORTED_BY_PROFILE for models without per-size counters (never estimated). Counter resets skipped."""
from datetime import datetime, timezone


def _printer(db, serial):
    from backend.database.models_printer import PrinterDevice
    p = PrinterDevice(manufacturer="Kyocera", model="ECOSYS MA4000cix", serial_number=serial, hostname=serial.lower())
    db.add(p); db.flush()
    return p


def test_annual_and_paper_size_reports(client, admin):
    from backend.database.models_printer_srs import PrinterMetricSample
    from backend.database.session import session_scope
    from backend.printers import service as S
    with session_scope() as db:
        a, b = _printer(db, "RPT-A"), _printer(db, "RPT-B")
        pid = a.id
        def put(key, when, v):
            db.add(PrinterMetricSample(printer_id=pid, metric_key=key, normalized_value=v, observed_at=when, source="VENDOR_MIB_PROFILE"))
        for key, series in {"pages_a4": [100, 400, 50, 150], "pages_letter": [0, 0, 0, 10],
                            "bw_impressions": [1000, 1600, 1600, 1900], "colour_impressions": [10, 210, 210, 310],
                            "duplex_sheets": [5, 305, 305, 405]}.items():
            for i, (when, v) in enumerate(zip((datetime(2025, 3, 1, tzinfo=timezone.utc), datetime(2025, 9, 1, tzinfo=timezone.utc),
                                               datetime(2026, 2, 1, tzinfo=timezone.utc), datetime(2026, 8, 1, tzinfo=timezone.utc)), series)):
                put(key, when, float(v))
    with session_scope() as db:
        cols, rows, meta = S.build_report(db, "paper_size_utilization", {"date_from": "2025-01-01", "date_to": "2026-11-01"})
        by = {r["printer"]: r for r in rows}
        # a4: +300, then a reset (400 -> 50) skipped, then +100 = 400; letter +10
        assert by["rpt-a"]["status"] == "MEASURED" and by["rpt-a"]["pages_a4"] == 400 and by["rpt-a"]["pages_letter"] == 10
        assert by["rpt-a"]["a4_share_percent"] == round(100 * 400 / 410, 1)
        assert by["rpt-b"]["status"] == "NOT_SUPPORTED_BY_PROFILE" and by["rpt-b"]["pages_a4"] is None
        cols, rows, meta = S.build_report(db, "annual_utilization", {"date_from": "2025-01-01", "date_to": "2026-11-01"})
        assert {"year", "mono", "colour", "duplex_sheets", "months_covered", "months_missing"} <= set(cols)
    r = client.get("/api/printers/reports", headers=admin).json()
    keys = {x["key"] for x in r["reports"]}
    assert {"annual_utilization", "paper_size_utilization"} <= keys


def test_paper_size_metric_keys_are_mappable_in_mib_profiles():
    from backend.api.printers_srs import METRIC_KEYS
    assert {"pages_a4", "pages_letter", "pages_legal", "pages_a3", "pages_other_size"} <= set(METRIC_KEYS)


def test_purchasing_and_print_jobs_are_not_mounted_unless_authorised(app_env, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from tests.conftest import login
    monkeypatch.setenv("INM_FEATURE_PURCHASING", "false")
    monkeypatch.setenv("INM_FEATURE_PRINT_JOBS", "false")
    with TestClient(create_app()) as c:
        h = login(c)
        assert c.get("/api/printer-purchasing/requests", headers=h).status_code in (404, 405)
        assert c.post("/api/print-jobs/ingest", json={"jobs": []}).status_code in (404, 405)
        mods = {m["key"]: m["status"] for m in c.get("/api/v1/system/features", headers=h).json()["modules"]}
        assert mods["print_jobs"] == mods["printer_purchasing"] == "DISABLED_PENDING_AUTHORIZATION"
