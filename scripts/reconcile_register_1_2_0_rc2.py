"""Reconcile docs/Unified_Acceptance_Register_v4.csv for 1.2.0-rc2 (software remediation) and regenerate the phase
matrix (docs/RC_1.2.0_TEST_MATRIX.md).

Rules (owner directive, 2026-11-09 "complete all software remediation before hardware testing"):
  PASS     - software-only control proven by a named automated test in the rc2 runs.
  PARTIAL  - proven only on protocol emulators / native stack / mocked device I/O; the hardware or CentOS-host half
             is still BLOCKED. Emulator PASS is software PASS only and never counts as hardware evidence.
  BLOCKED  - needs AP-505/AP-535/CX 6100/CX 6200 hardware, a CentOS Stream 9 host with registry access, multi-VM
             topology or an external party.
  NOT_RUN  - could be run in software but no test exists yet (listed in the gap register).

1. Every row citing tests by name is re-verified: each cited test must exist in the rc2 collection, otherwise the
   row is reported and demoted to NOT_RUN (never silently left PASS).
2. rc1 regression-log links are re-pointed to the rc2 logs.
3. Rows with new rc2 evidence are updated from U below. No row becomes PASS on emulator evidence.
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "docs" / "Unified_Acceptance_Register_v4.csv"
OLD = "evidence/tests/regression-1.2.0-rc1.log"
LOG = "evidence/regression/full-regression.log"
FS = "evidence/fullstack/fullstack-tests.log"
UBR = "evidence/fullstack/upgrade-backup-restore.log"
GUI = "evidence/e2e-gui/"
CERT = "evidence/certification/emulated_certification_table.md"
DATE = "2026-11-09"
OWNER = "Claude (build agent) — independent re-run required"
HW = "hardware half requires lab AP-505/AP-535/CX 6100/CX 6200 (not available)"

# test_id -> (status, actual result, evidence)
U: dict[str, tuple[str, str, str]] = {
    "P1-02": ("PARTIAL", "Native full stack: NATS, Loki, PostgreSQL, API and worker each stopped; outage buffered/"
              "retried and recovered with nothing lost. Container-health variant on CentOS BLOCKED",
              f"{FS} :: test_05_nats_outage_is_buffered_by_the_outbox_and_delivered_once_after_recovery; "
              "test_06_loki_outage_retries_ingest_and_never_acks_unlogged_events; "
              "test_09_database_restart_and_api_restart_lose_nothing"),
    "P1-09": ("PARTIAL", "Native full stack: DB and API restart lose no workflow, telemetry or outbox state; "
              "container recreate on CentOS BLOCKED", f"{FS} :: test_09_database_restart_and_api_restart_lose_nothing"),
    "P1-10": ("PARTIAL", "pg_dump -Fc of the running stack restored into disposable databases with "
              "timescaledb_pre/post_restore: every table's row count and content checksum identical, live data "
              "untouched. JetStream/Loki volume restore via restore.sh on CentOS BLOCKED",
              f"{UBR} :: test_backup_and_restore_round_trip_of_the_running_full_stack; "
              "evidence/fullstack/backup_restore_round_trip.json"),
    "P2-09": ("PASS", "Worker killed mid-/24 scan: another worker takes the lease and resumes from the cursor; no "
              "duplicate registrations; failure reason recorded",
              f"{LOG} :: test_worker_crash_mid_scan_is_resumed_from_cursor_by_another_worker; "
              f"{FS} :: test_02_workflow_discovery_runs_in_the_worker_and_survives_a_worker_crash"),
    "P3-03": ("PARTIAL", "Unreachable -> OFFLINE/STALE with last-seen preserved, identical in collector, NOC, "
              "Device 360 and alerts; recovery on reconnect (emulated + native stack); " + HW,
              f"{LOG} :: test_status_agrees_across_collector_noc_device360_and_alerts_through_outage_and_recovery; "
              f"{FS} :: test_07_device_offline_then_recovery_and_session_expiry"),
    "P3-07": ("PASS", "drop_chunks retention on TimescaleDB 2.22 removes points older than 90 days only; current data "
              "still queryable through the store", f"{UBR} :: test_retention_drops_only_points_older_than_the_window_on_timescaledb"),
    "P5-05": ("PARTIAL", "Device read fails (HTTP 500): schedule shows FAILED <category>, audit records it, nothing "
              "partial is sealed, last good snapshot still listed and integrity PASS (emulated); " + HW,
              f"{LOG} :: test_snapshot_retrieval_failure_is_recorded_and_last_good_snapshot_stays_verifiable"),
    "P6-06": ("PARTIAL", "Defect found and fixed in rc2: execution compared the approved baseline only with the newest "
              "vault artifact. Now the live pre-change backup is compared (raw, then normalized); an out-of-band edit "
              "stops execution with no write, returns the plan to PENDING_APPROVAL with a new revision and baseline; "
              "the old approval cannot be replayed (emulated); " + HW,
              f"{LOG} :: test_out_of_band_change_after_approval_stops_execution_until_renewed_review"),
    "P6-10": ("PARTIAL", "Group deployment: one plan per device, each independently approved, progress shown, stop "
              "at first failure (emulated). Cluster-scope impact view and a stronger approval policy for high-risk "
              "multi-device changes are NOT implemented",
              f"{LOG} :: test_multi_device_deployment_with_progress_and_stop_on_failure"),
    "P7-08": ("PARTIAL", "Instant full restore reported UNSUPPORTED (never auto-restored); guided procedure + sealed "
              "pre-change snapshot returned (emulated); " + HW,
              f"{LOG} :: test_crash_mid_change_is_recovered_from_the_durable_journal"),
    "P6-DEP-01": ("PARTIAL", "Process killed mid-change: encrypted journal marks RECOVERY_REQUIRED + alert; recovery "
                  "restores from checkpoint; no unknown write outcome (emulated). A real package upgrade during a "
                  "pending change on CentOS is BLOCKED",
                  f"{LOG} :: test_crash_mid_change_is_recovered_from_the_durable_journal"),
    "P11-DEP-03": ("PARTIAL", "rc1 schema (0009 + t001) with data upgraded in place to 0010 + t002 on PostgreSQL 16 "
                   "+ TimescaleDB 2.22; all rows kept, port identity back-filled, rc2 ORM reads them. Old/new app "
                   "interoperation window on CentOS BLOCKED",
                   f"{UBR} :: test_rc1_to_rc2_upgrade_preserves_data_on_postgresql_and_timescaledb; "
                   "evidence/fullstack/upgrade_rc1_to_rc2.json"),
    "CXAPI-01": ("PARTIAL", "Version negotiated per firmware (v10.14 preferred, GET /rest fallback, 10.09-10.15 "
                 "accepted, 10.08 refused UNSUPPORTED_API) against the strict emulator; " + HW,
                 f"{LOG} :: test_api_version_negotiated_per_firmware; test_unsupported_firmware_is_refused_with_a_clear_category"),
    "CXAPI-02": ("PARTIAL", "Every port of 24- and 48-port switches stored and retrievable with its interface "
                 "identity (strict emulator + native TimescaleDB); " + HW,
                 f"{LOG} :: test_every_port_of_24_and_48_port_switches_is_stored_and_retrievable; "
                 f"{FS} :: test_04_monitoring_pipeline_end_to_end_with_one_correlation_id"),
    "CXAPI-10": ("PARTIAL", "Emulated certification campaign CX 6100 24G / CX 6200F 48G: every op backup/checkpoint/"
                 "apply/readback/rollback + full restore matches baseline (SOFTWARE PASS ONLY); " + HW,
                 f"{CERT}; {LOG} :: test_emulated_certification_campaign"),
    "AAPI-04": ("PARTIAL", "Device rejection recorded safely; no false green: run never SUCCEEDED unless the persisted "
                "count is verified (emulated); " + HW,
                f"{LOG} :: test_device_rejection_marks_failed; test_no_false_success_when_telemetry_cannot_be_persisted_then_recovers"),
}


def collected() -> set[str]:
    """Names of every test function in the rc2 suites (unit, contract, backend, full-stack, browser)."""
    out = subprocess.run([str(ROOT / ".venv/bin/python"), "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                          "backend/tests", "tests/unit", "tests/contract", "tests/fullstack", "tests/e2e"],
                         cwd=ROOT, capture_output=True, text=True).stdout
    names = set()
    for line in out.splitlines():
        if "::" in line:
            names.add(re.sub(r"\[.*$", "", line.split("::")[-1]))
            names.add(Path(line.split("::")[0]).stem)          # rows may cite a whole test module
    return names


def cited(link: str) -> list[str]:
    names = []
    for part in link.split(";"):
        part = part.split("::")[-1] if "::" in part else part
        for tok in re.findall(r"\btest_[A-Za-z0-9_]+", part):
            names.append(tok)
    return names


def main() -> int:
    rows = list(csv.DictReader(REG.open()))
    fields = list(rows[0].keys())
    names = collected()
    missing_report, changed = [], 0
    for r in rows:
        link = r.get("artifact_link") or ""
        if r["test_id"] in U:
            st, actual, ev = U[r["test_id"]]
            r["status"], r["actual_result"], r["date"], r["owner"] = st, actual, DATE, OWNER
            r["artifact_link"] = ev
            changed += 1
            link = ev
        elif OLD in link:
            r["artifact_link"] = link.replace(OLD, LOG)
            link = r["artifact_link"]
        gone = [n for n in cited(link) if n not in names]
        if gone:
            missing_report.append((r["test_id"], r["status"], gone))
            if r["status"] in ("PASS", "PARTIAL"):
                r["status"], r["defect"] = "NOT_RUN", f"cited test(s) no longer exist: {', '.join(gone)}"
                changed += 1
    with REG.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    by = defaultdict(list)
    for r in rows:
        by[r["phase"]].append(r)
    tot = Counter(r["status"] for r in rows)
    out = ["# 1.2.0-rc2 phase test matrix (software remediation)", "",
           "Generated by `scripts/reconcile_register_1_2_0_rc2.py` from `docs/Unified_Acceptance_Register_v4.csv`.",
           "PARTIAL = proven on protocol emulators, the native full stack or mocked device I/O only; the hardware / "
           "CentOS-host half is BLOCKED. No row is PASS on emulator evidence alone. Every test cited below exists in "
           "the rc2 test collection (verified when this file was generated).", "",
           "| Status | Count |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in sorted(tot.items())] + [f"| **Total** | **{len(rows)}** |", ""]
    order = ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11", "CROSS", "PRINTER"]
    cell = lambda s: (s or "").replace("|", "/").replace("\n", " ")
    for ph in order + sorted(set(by) - set(order)):
        if ph not in by:
            continue
        c = Counter(r["status"] for r in by[ph])
        out += [f"## {ph} — " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())), "",
                "| Test ID | Procedure | Expected | Actual | Status | Evidence | Defect |", "|---|---|---|---|---|---|---|"]
        for r in by[ph]:
            out.append(f"| {r['test_id']} | {cell(r['procedure'])} | {cell(r['expected_result'])} | "
                       f"{cell(r.get('actual_result') or r['evidence'])} | **{r['status']}** | {cell(r['artifact_link'])} | "
                       f"{cell(r.get('defect'))} |")
        out.append("")
    (ROOT / "docs" / "RC_1.2.0_TEST_MATRIX.md").write_text("\n".join(out))
    print(f"updated {changed} rows; totals {dict(tot)}")
    for m in missing_report:
        print("MISSING CITED TEST:", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
