"""Pure-function tests for Printer Intelligence SRS analytics (no database, no network)."""
import asyncio
import io
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from backend.printers import collector as C
from backend.printers import export, fleet, mib, mib_parser
from backend.printers import utilization as U

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ---- Printer-MIB decoding -----------------------------------------------------------------------
@pytest.mark.parametrize("raw,state,val", [(-1, "OTHER", None), (-2, "UNKNOWN", None), (-3, "SOME_REMAINING", None),
                                           (-7, "INVALID", None), (None, "MISSING", None), (0, "VALID", 0), (42, "VALID", 42)])
def test_special_values_are_never_zero(raw, state, val):
    assert mib.decode_special(raw) == (state, val)


def test_supply_percent_and_status():
    assert mib.supply_percent(25, 100) == ("VALID", 25.0)
    assert mib.supply_percent(-3, 100) == ("SOME_REMAINING", None)
    assert mib.supply_percent(50, -2) == ("NO_CAPACITY", None)
    assert mib.supply_percent(150, 100) == ("INCONSISTENT", None)
    assert mib.supply_status("SOME_REMAINING", None) == "OK"
    assert mib.supply_status("UNKNOWN", None) == "UNKNOWN"          # unknown is not empty
    assert mib.supply_status("VALID", 0.0) == "EMPTY" and mib.supply_status("VALID", 10.0) == "LOW"
    assert mib.supply_status("VALID", 5.0, receptacle=True) == "NEARLY_FULL"


def test_error_state_bitmask_and_alerts():
    codes = {f["code"] for f in mib.decode_error_state(b"\x84\x02")}
    assert codes == {"LOW_PAPER", "JAMMED", "OVERDUE_PREVENTIVE_MAINTENANCE"}
    assert mib.decode_error_state("0x10 00")[0]["code"] == "NO_TONER"
    assert mib.decode_error_state("garbage") == []
    assert mib.decode_alert(9, 3, "Paper jam in tray 2")["category"] == "PAPER"
    assert mib.decode_alert(424242, 4, None)["code"] == "ALERT_424242"


def test_model_identity_is_extracted_not_guessed():
    assert mib.model_from_description("KYOCERA Document Solutions ECOSYS MA5500ifx")["model"] == "ECOSYS MA5500ifx"
    assert mib.model_from_description("Xerox VersaLink C70 Color MFP")["model"] == "VersaLink C70"
    assert mib.model_from_description("HP LaserJet 400")["confidence"] == "HIGH"
    assert mib.model_from_description("HP LJ 400")["model"] is None            # abbreviation not expanded
    assert mib.model_family("ECOSYS MA4000cix") == {"technology": "laser", "colour": True, "multifunction": True}
    assert mib.model_family("ECOSYS M2640idw")["colour"] is False
    assert mib.model_family("VersaLink B62")["colour"] is False and mib.model_family("WF-6590")["technology"] == "inkjet"
    assert mib.manufacturer_from_object_id("1.3.6.1.4.1.1347.41") == "Kyocera"


def test_profile_selection_prefers_specific_and_falls_back_to_generic():
    profiles = [{"key": k["key"], **k, "enabled": True} for k in mib.BUILTIN_PROFILES]
    assert C.select_profile(profiles, "1.3.6.1.4.1.1347.41", "")["key"] == "kyocera-printer"
    assert C.select_profile(profiles, "1.3.6.1.4.1.99999.1", "")["key"] == "rfc3805-generic"
    profiles.append({"key": "custom", "match_enterprise": None, "match_descr_regex": "ACME", "priority": 10,
                     "metrics": [], "enabled": True})
    assert C.select_profile(profiles, "1.3.6.1.4.1.99999.1", "ACME 9000")["key"] == "custom"


def test_poll_unreachable_records_no_values():
    r = asyncio.run(C.poll(C.EmulatedPrinterAgent({}, reachable=False), mib.BUILTIN_PROFILES))
    assert not r.reachable and not r.snmp_ok and r.meters == {} and r.samples == []


def test_poll_retains_counters_when_later_metrics_exceed_time_budget():
    class SlowAlerts(C.EmulatedPrinterAgent):
        async def walk(self, oid):
            if oid == mib.PRT_ALERT_CODE:
                await asyncio.sleep(1)
            return await super().walk(oid)

    result = asyncio.run(C.poll(SlowAlerts(C.emulated_printer(impressions=500)), mib.BUILTIN_PROFILES,
                                metric_budget_seconds=0.05))
    assert result.snmp_ok and result.meters["impressions_total"]["value"] == 500
    assert result.error == "poll time budget reached; saved available metrics"
    assert not result.faults_observed


def test_counter_unit_must_be_known_before_labelling():
    vals = C.emulated_printer(impressions=500, counter_unit=11)   # hours: never relabelled as impressions
    for k in [k for k in vals if k.startswith("1.3.6.1.4.1.1347.")]:
        del vals[k]
    r = asyncio.run(C.poll(C.EmulatedPrinterAgent(vals), mib.BUILTIN_PROFILES))
    assert "impressions_total" not in r.meters and r.capabilities["impressions"]["state"] == "UNSUPPORTED"


def test_inconsistent_mono_colour_split_is_invalid():
    r = asyncio.run(C.poll(C.EmulatedPrinterAgent(C.emulated_printer(impressions=1000, bw=900, colour=900)), mib.BUILTIN_PROFILES))
    assert r.capabilities["mono_colour_split"]["state"] == "INVALID"


# ---- intervals ------------------------------------------------------------------------------------
def test_interval_classification():
    t1 = T0 + timedelta(minutes=15)
    assert U.derive_interval(100, T0, 150, t1)["quality"] == "VALID"
    assert U.derive_interval(2**32 - 10, T0, 5, t1) | {} == U.derive_interval(2**32 - 10, T0, 5, t1)
    assert U.derive_interval(2**32 - 10, T0, 5, t1)["delta"] == 15
    assert U.derive_interval(1000, T0, 10, t1)["quality"] == "RESET"
    assert U.derive_interval(100, T0, 100 + 15 * 200, t1)["quality"] == "IMPLAUSIBLE"
    assert U.derive_interval(100, T0, 200, T0 + timedelta(days=3))["quality"] == "GAP"
    assert U.derive_interval(100, T0, 200, t1, serial_changed=True)["quality"] == "REPLACEMENT"
    assert U.derive_interval(100, t1, 200, T0)["quality"] == "INVALID"


def test_aggregate_proportional_attribution_missing_vs_zero():
    ivs = [{"start_at": T0 + timedelta(minutes=30), "end_at": T0 + timedelta(minutes=90), "delta": 60, "quality": "VALID"},
           {"start_at": T0 + timedelta(minutes=90), "end_at": T0 + timedelta(minutes=120), "delta": 0, "quality": "VALID"}]
    b = U.aggregate(ivs, T0, T0 + timedelta(hours=3), "hour")
    assert [x["volume"] for x in b] == [30.0, 30.0, None]
    assert b[0]["coverage"] == 0.5 and b[0]["estimated_full_volume"] == 60.0
    assert b[1]["measured_zero"] is False and b[2]["confidence"] == "INSUFFICIENT"
    z = U.aggregate([{"start_at": T0, "end_at": T0 + timedelta(hours=1), "delta": 0, "quality": "VALID"}],
                    T0, T0 + timedelta(hours=1), "hour")
    assert z[0]["volume"] == 0.0 and z[0]["measured_zero"] is True
    s = U.period_summary(b)
    assert s["total"] == 60.0 and s["missing_buckets"] == 1


def test_reset_intervals_never_contribute_volume():
    ivs = [{"start_at": T0, "end_at": T0 + timedelta(hours=1), "delta": None, "quality": "RESET"}]
    b = U.aggregate(ivs, T0, T0 + timedelta(hours=1), "hour")
    assert b[0]["volume"] is None and b[0]["excluded_intervals"] == 1


@pytest.mark.parametrize("g,start,nxt", [("week", "2026-01-05", "2026-01-12"), ("month", "2026-01-01", "2026-02-01"),
                                         ("year", "2026-01-01", "2027-01-01"), ("day", "2026-01-07", "2026-01-08")])
def test_bucket_calendar(g, start, nxt):
    b = U.bucket_start(datetime(2026, 1, 7, 13, tzinfo=timezone.utc), g)
    assert b.date().isoformat() == start and U.next_bucket(b, g).date().isoformat() == nxt


def test_anomalies_require_history_and_flag_peaks():
    days = [{"bucket_start": f"d{i}", "volume": 100.0 + (i % 3), "coverage": 1.0} for i in range(20)]
    assert U.robust_anomalies(days[:10]) == []
    days[7]["volume"] = 900.0
    a = U.robust_anomalies(days)
    assert len(a) == 1 and a[0]["kind"] == "PEAK"


def test_duty_cycle_needs_verified_figures():
    m = [{"bucket_start": f"2026-0{i}-01", "volume": 5000.0, "coverage": 1.0, "full_bucket_coverage": 1.0,
          "estimated_full_volume": 5000.0} for i in range(1, 5)]
    assert U.duty_cycle_assessment(m, None, None)["status"] == "INSUFFICIENT_EVIDENCE"
    assert U.duty_cycle_assessment(m, 3000, 50000)["status"] == "SUSTAINED_HIGH"
    assert U.duty_cycle_assessment(m, 50000, None)["status"] == "SUSTAINED_LOW"
    assert U.duty_cycle_assessment(m[:2], 3000, None)["status"] == "INSUFFICIENT_EVIDENCE"


# ---- supplies / reliability / health / benchmark --------------------------------------------------
def test_replacement_detection_and_forecast_interval():
    assert fleet.detect_replacement(10, 98)["status"] == "CONFIRMED"
    assert fleet.detect_replacement(45, 90)["confirmation"] == "OPERATOR_REQUIRED"
    assert fleet.detect_replacement(10, 30) is None and fleet.detect_replacement(None, 90) is None
    pts = [(T0 + timedelta(days=d), 100 - 5 * d + (0.5 if d % 2 else -0.5)) for d in range(10)]
    f = fleet.forecast_with_uncertainty(pts, now=T0 + timedelta(days=9))
    assert f["status"] == "FORECAST" and 9 <= f["days_to_empty"] <= 12
    assert f["days_to_empty_low"] <= f["days_to_empty"] <= f["days_to_empty_high"]
    assert fleet.forecast_with_uncertainty(pts[:3])["status"] == "INSUFFICIENT_EVIDENCE"


def test_measured_yields_need_two_confirmed_replacements():
    ev = [{"supply_index": "1.1", "colour": "black", "supply_type": "toner", "status": "CONFIRMED", "meter_value": 1000,
           "observed_at": T0, "cost_amount": 9000},
          {"supply_index": "1.1", "colour": "black", "supply_type": "toner", "status": "OPEN", "meter_value": 4000,
           "observed_at": T0 + timedelta(days=5)},
          {"supply_index": "1.1", "colour": "black", "supply_type": "toner", "status": "CONFIRMED", "meter_value": 7000,
           "observed_at": T0 + timedelta(days=9)}]
    y = fleet.measured_yields(ev)
    assert len(y) == 1 and y[0]["measured_yield"] == 6000 and y[0]["cost_per_1000"] == 1500.0


def test_reliability_excludes_wan_power_and_uses_operating_window():
    recs = [{"observed_at": T0 + timedelta(days=1), "reported_at": T0 + timedelta(days=1),
             "restored_at": T0 + timedelta(days=1, hours=3), "qualifying_failure": True, "cause_category": "MECHANICAL"},
            {"observed_at": T0 + timedelta(days=2), "qualifying_failure": True, "cause_category": "WAN"},
            {"observed_at": T0 + timedelta(days=3), "qualifying_failure": False, "cause_category": "POWER"}]
    r = fleet.reliability(recs, period_start=T0, period_end=T0 + timedelta(days=60), impressions=200000)
    assert r["qualifying_failures"] == 1 and r["excluded_non_mechanical"] == 2
    assert r["mtbf_hours"] == 1440.0 and r["mttr_hours"] == 3.0 and r["incidents_per_100k_impressions"] == 0.5
    w = {"days": [0, 1, 2, 3, 4], "start": "08:00", "end": "17:00"}
    assert fleet.operating_hours(datetime(2026, 1, 5, tzinfo=timezone.utc), datetime(2026, 1, 12, tzinfo=timezone.utc), w) == 45.0


def test_health_score_is_unknown_without_evidence():
    assert fleet.health_score(availability=None, snmp_success=None, active_faults=[], supplies=[], repeated_faults=0,
                              last_success_at=None)["status"] == "INSUFFICIENT_DATA"
    h = fleet.health_score(availability=1.0, snmp_success=1.0, active_faults=[{"severity": "critical"}],
                           supplies=[{"status": "EMPTY"}], repeated_faults=0, last_success_at=T0, now=T0)
    assert h["status"] in ("DEGRADED", "CRITICAL") and h["score"] < 80
    stale = fleet.health_score(availability=1.0, snmp_success=1.0, active_faults=[], supplies=[], repeated_faults=0,
                               last_success_at=T0, now=T0 + timedelta(days=1))
    assert stale["status"] == "STALE"


def test_benchmark_sample_size_states():
    dev = lambda m, v: {"manufacturer": "K", "model": m, "segment": {"colour": False}, "monthly_volumes": [v] * 4,
                        "recommended_monthly": 3000, "yields": [7000], "cost_per_1000": [1200.0], "incidents": 0,
                        "impressions": v * 4, "cost_complete": True, "consumable_cost": 9000, "maintenance_cost": 0}
    out = {c["model"]: c for c in fleet.benchmark([dev("A", 2000)] * 3 + [dev("B", 2000)])}
    assert out["A"]["evidence"] == "SUFFICIENT" and out["B"]["evidence"] == "INSUFFICIENT_EVIDENCE"
    assert out["A"]["median_recommended_utilization_percent"] == 66.67 and out["A"]["workload_band"] == "MEDIUM"
    recs = fleet.recommend([], list(out.values()), period=(T0, T0))
    assert any(r["code"] == "STANDARDIZATION_RISK" and r["model"] == "K B" for r in recs)


# ---- MIB parser ------------------------------------------------------------------------------------
def test_mib_parser_rejects_non_mib_and_handles_named_numbers():
    with pytest.raises(ValueError):
        mib_parser.parse("print('hello')")
    p = mib_parser.parse("X-MIB DEFINITIONS ::= BEGIN\nfoo OBJECT IDENTIFIER ::= { iso(1) org(3) dod(6) 1 4 1 777 }\n"
                         "fooCount OBJECT-TYPE SYNTAX Counter32 MAX-ACCESS read-only STATUS current\n"
                         " DESCRIPTION \"a \"\"quoted\"\" text -- not a comment\" ::= { foo 1 }\nEND")
    r = mib_parser.resolve(p, {})
    assert r["status"] == "RESOLVED" and r["resolved"]["fooCount"] == "1.3.6.1.4.1.777.1"
    assert mib_parser.metric_definition_for({"oid": "1.2.3", "kind": "scalar", "syntax": "Counter32"}) == \
        {"oid": "1.2.3.0", "access": "scalar", "value_type": "counter"}


def test_mib_size_limit():
    with pytest.raises(ValueError):
        mib_parser.parse("A DEFINITIONS ::= BEGIN\n" + "-" * (mib_parser.MAX_BYTES + 1))


# ---- exports ----------------------------------------------------------------------------------------
def test_exports_csv_injection_xlsx_and_pdf():
    rows = [{"a": "=HYPERLINK(\"x\")", "b": 3.5}, {"a": "-12", "b": None}]
    csv_ = export.to_csv(["a", "b"], rows, {"period": "x"}).decode("utf-8-sig")
    assert "'=HYPERLINK" in csv_ and ",-12," not in csv_ and "-12" in csv_
    x = zipfile.ZipFile(io.BytesIO(export.to_xlsx(["a", "b"], rows)))
    sheet = x.read("xl/worksheets/sheet1.xml").decode()
    assert "&apos;=HYPERLINK" in sheet or "'=HYPERLINK" in sheet
    assert "<v>3.5</v>" in sheet
    pdf = export.to_pdf("T", ["a", "b"], rows * 60)
    assert pdf.startswith(b"%PDF-1.4") and pdf.count(b"/Type /Page ") >= 3
    with pytest.raises(ValueError):
        export.render("doc", "t", [], [])


def test_anomalies_bounded_for_steady_workload_and_ignore_partial_days():
    days = [{"bucket_start": f"d{i}", "volume": 1500.0, "coverage": 1.0, "full_bucket_coverage": 1.0} for i in range(30)]
    days.append({"bucket_start": "today", "volume": 300.0, "coverage": 1.0, "full_bucket_coverage": 0.2})
    assert U.robust_anomalies(days) == []                       # partial day is not a dip
    days[3]["volume"] = 1413.0
    assert U.robust_anomalies(days) == []                       # 6% below a flat median is not anomalous
    days[5]["volume"] = 6000.0
    a = U.robust_anomalies(days)
    assert [x["day"] for x in a] == ["d5"] and abs(a[0]["robust_z"]) < 100


# ---- 1.3.0-rc16: partial readings, truncated tables and SNMPv3 protocol selection ---------------------
def test_one_metric_timeout_keeps_other_valid_readings():
    agent = C.EmulatedPrinterAgent(C.emulated_printer(impressions=900, supplies=[{"descr": "Black Toner", "level": 40}]))
    agent.timeout_oids.add(mib.PRT_SUPPLY_LEVEL)            # supply levels time out, everything else answers
    r = asyncio.run(C.poll(agent, mib.BUILTIN_PROFILES))
    assert r.snmp_ok and r.meters["impressions_total"]["value"] == 900
    assert any(s.metric_key == "supply_level" and s.value_state == "MISSING" for s in r.samples)
    assert all(s["percent_remaining"] is None for s in r.supplies)   # never fabricated from a missing level
    assert r.faults_observed                                        # fault tables themselves were complete


def test_truncated_alert_table_keeps_rows_but_never_clears_faults():
    alerts = [{"code": 8, "severity": 3, "descr": "Jam"}, {"code": 1101, "severity": 3, "descr": "Toner empty"}]
    agent = C.EmulatedPrinterAgent(C.emulated_printer(alerts=alerts))
    agent.truncate_tables[mib.PRT_ALERT_CODE] = 1
    r = asyncio.run(C.poll(agent, mib.BUILTIN_PROFILES))
    assert "alert_code" in r.incomplete_metrics and r.error == "table walk interrupted; partial rows kept"
    assert r.faults and not r.faults_observed


def test_snmpv3_protocols_default_to_legacy_and_map_to_pysnmp():
    from backend.printers import snmp as S
    assert S.usm_options({}) == {"auth_protocol": "SHA256", "priv_protocol": "AES128"}   # pre-rc16 profiles
    assert S.usm_options({"auth_protocol": "sha", "priv_protocol": "aes256"}) == {"auth_protocol": "SHA",
                                                                                 "priv_protocol": "AES256"}
    with pytest.raises(ValueError):
        S.usm_options({"auth_protocol": "ROT13"})
    import pysnmp.hlapi.v3arch.asyncio as hl
    u = S.build_auth_data("3", "monitor", "authpass123", "privpass123", auth_protocol="SHA", priv_protocol="AES256")
    assert u.authentication_protocol == hl.USM_AUTH_HMAC96_SHA and u.privacy_protocol == hl.USM_PRIV_CFB256_AES
    u = S.build_auth_data("3", "monitor", "authpass123", auth_protocol="MD5", priv_protocol="NONE")
    assert u.privacy_protocol == hl.USM_PRIV_NONE
    assert S.build_auth_data("2c", "public").message_processing_model == 1
    assert S.build_auth_data("1", "public").message_processing_model == 0
    with pytest.raises(ValueError):
        S.build_auth_data("4", "x")


def test_raw_samples_are_stored_on_change_with_heartbeat(app_env):
    """rc17 storage fix: unchanged values are not re-stored every poll; changes and heartbeats are."""
    from datetime import timedelta as TD
    from sqlalchemy import func, select
    from backend.database import session as dbs
    from backend.database.models_printer_srs import PrinterMetricSample
    from backend.printers import service as S
    dbs.init_engine(); dbs.create_schema()
    agent = C.EmulatedPrinterAgent(C.emulated_printer(impressions=1000, supplies=[{"descr": "TK-1150 Black", "level": 80}]))
    with dbs.session_scope() as db:
        p, _ = S.register_or_update(db, {"manufacturer": "Kyocera", "model": "ECOSYS M2640idw", "serial_number": "EMU0001",
                                         "management_ip": "10.9.9.9"}, provenance="MANUAL")
        pid = p.id
    counts = []
    for i in range(6):
        at = T0 + TD(minutes=15 * i)
        agent.values[mib.PRT_MARKER_LIFE + ".1.1"] += 20            # only the page counter changes
        res = asyncio.run(C.poll(agent, mib.BUILTIN_PROFILES, now=at))
        with dbs.session_scope() as db:
            S.apply_poll(db, db.get(S.PrinterDevice, pid), res)
            counts.append(db.scalar(select(func.count()).select_from(PrinterMetricSample)))
    first, per_poll = counts[0], [b - a for a, b in zip(counts, counts[1:])]
    assert first > 10 and all(0 < d <= 3 for d in per_poll), (first, per_poll)
    agent.values[mib.PRT_SUPPLY_LEVEL + ".1.1"] = 40                # a supply change is stored
    at = T0 + TD(minutes=15 * 6)
    with dbs.session_scope() as db:
        S.apply_poll(db, db.get(S.PrinterDevice, pid), asyncio.run(C.poll(agent, mib.BUILTIN_PROFILES, now=at)))
        assert db.scalar(select(func.count()).select_from(PrinterMetricSample).where(
            PrinterMetricSample.metric_key == "supply_level", PrinterMetricSample.observed_at == at)) == 1
    at = T0 + TD(hours=7)                                            # past the 6 h heartbeat: everything re-stored
    with dbs.session_scope() as db:
        before = db.scalar(select(func.count()).select_from(PrinterMetricSample))
        S.apply_poll(db, db.get(S.PrinterDevice, pid), asyncio.run(C.poll(agent, mib.BUILTIN_PROFILES, now=at)))
        assert db.scalar(select(func.count()).select_from(PrinterMetricSample)) - before >= first - 2
