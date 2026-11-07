"""Write Phase 1 statuses into copies of the two controlled CSVs (docs/). Status vocabulary:

  PASS     the procedure as written was executed and met the expected result (evidence linked)
  PARTIAL  the logic/behaviour was proven (evidence linked) but part of the written procedure needs an environment
           this build did not have (clean CentOS Stream 9 VM, Docker registry access, real devices)
  NOT_RUN  not executed; reason given
Nothing is marked PASS on code presence alone.
"""
import csv
import sys
from pathlib import Path

src_dir, out_dir, date = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
OWNER = "Claude (build agent) — independent re-run required"
CENTOS = "requires clean CentOS Stream 9 VM + Docker registry access (unavailable in build environment)"

A = {
 "P1-01": ("NOT_RUN", CENTOS, ""),
 "P1-02": ("PARTIAL", "Native rig: stopping loki, ai, worker, timescale, postgres and nats each detected DOWN/DEGRADED and recovered; Docker container-level run pending", "evidence/tests/integration_native_stack.log"),
 "P1-03": ("PASS", "Synthetic event published twice (producer retry) + consumer offline: one event_records row, one telemetry row", "evidence/tests/integration_native_stack.log; tests/unit/test_pipeline_units.py"),
 "P1-04": ("PASS", "verify: TimescaleDB row + Loki line found by correlation ID for synthetic event", "evidence/native/verify_native_stack.log"),
 "P1-05": ("PASS", "Browser: dashboard re-queried after WS invalidation without reload; reconnect after API restart re-queried", "evidence/e2e/e2e_pytest.log; evidence/e2e/*.png"),
 "P1-06": ("PASS", "Viewer and granted-scope technician denied (403/404); every denial audited with request ID", "evidence/tests/unit_contract.log"),
 "P1-07": ("PASS", "login/logout/failed login rows carry actor, role, UTC time, outcome, request ID, session hash; chain verifies", "evidence/tests/unit_contract.log"),
 "P1-08": ("PARTIAL", "All demo rows/devices/recommendations labelled DEMO/RULE (tests + screenshots); network trace inside Docker pending", "evidence/e2e/; evidence/tests/unit_contract.log"),
 "P1-09": ("PARTIAL", "Native full stop/start: audit, events, users, devices, telemetry persisted, audit chain intact; Docker volume recreate pending", "evidence/tests/integration_native_stack.log"),
 "P1-10": ("NOT_RUN", "restore.sh --target test implemented; needs Docker images on a VM", ""),
 "P1-11": ("PARTIAL", "Legacy 169 + unit/contract 48 + integration 13 + E2E 5 pass; shellcheck clean; secret grep clean; image scan/SBOM not run", "evidence/tests/; evidence/deployment/"),
 "P1-12": ("PASS", "OpenAPI lists /api/v1; discovery/changes routes return 501 NOT_IMPLEMENTED with phase", "evidence/tests/unit_contract.log"),
 "SEC-01": ("PARTIAL", "Unauthenticated and wrong-password NATS refused; API identity cannot publish device events; DB bad password refused; port exposure verified from compose config only", "evidence/tests/integration_native_stack.log; evidence/deployment/secrets_and_compose_validation.log"),
 "SEC-02": ("PASS", "DEMO/REAL identity keys distinct for same hostname+serial; DB unique (source_type, vendor, serial)", "tests/unit/test_domain_and_architecture.py"),
 "P1-DEP-01": ("NOT_RUN", CENTOS, ""),
 "P1-DEP-02": ("PARTIAL", "Secret generation idempotent (hash unchanged on re-run); full installer re-run pending", "evidence/deployment/secrets_and_compose_validation.log"),
 "P1-DEP-03": ("PARTIAL", "compose config: only web + alloy publish, both 127.0.0.1; data networks internal; ss on VM pending", "evidence/deployment/secrets_and_compose_validation.log"),
 "P1-DEP-04": ("PASS", "Generated env, admin password, backup key, TLS key all 0600, /etc/inm 0700; values never printed; no env in git", "evidence/deployment/secrets_and_compose_validation.log"),
 "P1-DEP-05": ("PARTIAL", "verify CLI exits 1 naming role:management_db with PostgreSQL stopped (native); verify.sh in Docker pending", "evidence/native/verify_mgmt_db_down.log"),
 "P1-DEP-06": ("PASS", "API restarted with browser open; indicator offline -> live; authoritative re-query without reload", "evidence/e2e/04_reconnecting.png; 05_reconnected_live.png"),
 "P1-DEP-07": ("PASS", "Switches/AP fixtures DEMO; AI page and API state RULE engine, trained_model=false", "evidence/e2e/07_switches_demo.png; 09_ai_rules.png"),
 "P1-DEP-08": ("PASS", "Synthetic syslog via Alloy found in Loki; known host MAPPED to device, unknown host UNMAPPED with reason", "evidence/tests/integration_native_stack.log"),
 "P1-DEP-09": ("PASS", "Published with worker stopped; replayed once after restart; JetStream de-duplicated the retry", "evidence/tests/integration_native_stack.log"),
 "P1-DEP-10": ("PASS", "Browser 'Run round-trip': event ID traced to PostgreSQL, TimescaleDB, Loki and WS hub delivery", "evidence/e2e/06_system_health_round_trip.png"),
 "P1-DEP-11": ("PASS", "bash -n + shellcheck (warnings only, reviewed) on all scripts; all test suites pass", "evidence/deployment/shellcheck.log; evidence/tests/"),
 "P1-DEP-12": ("PASS", "Port 8443/1514 held by other processes: installer stopped at PRECHECK, exit 3, /etc/inm untouched (run on Ubuntu with INM_ALLOW_UNSUPPORTED_OS=1)", "evidence/deployment/preflight_port_conflict.log"),
 "P1-DEP-13": ("NOT_RUN", CENTOS, ""),
 "P1-DEP-14": ("PARTIAL", "record-release writes receipt + INSTALLER audit row with named operator; operator logins audited; installer-driven run on VM pending", "evidence/native/record_release.log"),
 "P1-DEP-15": ("PARTIAL", "Write routes refused + audited; Aruba client makes no network call under lock; demo profile refuses live mode; Docker network-trace pending", "evidence/tests/unit_contract.log"),
 "INTEG-P1-01": ("PASS", "SIMS baseline suite run before refactor: 169 passed, 0 failed, 0 skipped (133 definitions incl. parametrised)", "evidence/baseline/baseline_pytest.log"),
 "INTEG-P1-02": ("PASS", "Frozen-clock parity: worker path == original scheduler for summary, APs, clients, alerts, experience, sites. Intentional UI differences listed for approval", "tests/contract/test_demo_parity.py; docs/intentional_changes.md"),
 "INTEG-P1-03": ("PASS", "All 53 baseline routes mounted; full legacy suite passes on integrated code (one assertion changed: Alembic head 0003->0004)", "evidence/tests/legacy_after_integration.log"),
 "INTEG-P1-04": ("PARTIAL", "Attempted writes denied + audited; worker on internal-only network in compose; in-container route test runs in verify.sh (Docker pending)", "evidence/tests/unit_contract.log"),
 "INTEG-P1-05": ("PASS", "Two workers for 3 intervals: 3 site events per interval, no duplicate polls", "evidence/tests/integration_native_stack.log"),
 "INTEG-P1-06": ("PASS", "Hub replay from cursor / resync on gap (unit) + browser reconnect re-query (E2E)", "evidence/tests/unit_contract.log; evidence/e2e/"),
 "INTEG-P1-07": ("PARTIAL", "Gateway forwards only /api/tags and /api/chat; model context contains no MAC/IP/hostname; live Ollama model not run", "evidence/tests/unit_contract.log"),
 "INTEG-P1-08": ("PASS", "Pre-existing backups migrated MIGRATED_REDACTED, restorable=false; never restore candidates", "evidence/tests/unit_contract.log"),
 "INTEG-P1-09": ("NOT_RUN", CENTOS, ""),
 "INTEG-P1-10": ("PARTIAL", "Schema gate refuses newer/unknown schema with exit 3 + manual path; rollback.sh on live DB pending", "evidence/tests/unit_contract.log"),
 "INTEG-P1-11": ("PASS", "NATS stopped: health DOWN, readiness 503, synthetic request 503; restored: round-trip complete with exactly one record", "evidence/tests/integration_native_stack.log"),
 "INTEG-P1-12": ("PASS", "Scoped user: REST filtered; live WS received only own-site or system notices; audit topics withheld", "evidence/tests/integration_native_stack.log"),
}

F = {
 "F001": ("IMPLEMENTED_TESTED", "E2E + parity"), "F002": ("IMPLEMENTED_TESTED", "53 legacy routes + /api/v1"),
 "F003": ("IMPLEMENTED_TESTED", "named users, logout, forced change, auditor, site grants"), "F004": ("IMPLEMENTED_TESTED", "AP-505/515/535 + CX 6100/6200 DEMO"),
 "F005": ("IMPLEMENTED_TESTED", "measured seven-role health"), "F006": ("IMPLEMENTED_NOT_VERIFIED", "install.sh + compose; CentOS run pending"),
 "F007": ("IMPLEMENTED_NOT_VERIFIED", "update/verify/backup/restore/rollback scripts; schema gate tested"), "F008": ("IMPLEMENTED_TESTED", "architecture test"),
 "F009": ("IMPLEMENTED_TESTED", "JetStream, durable ingest, lease, DLQ"), "F010": ("IMPLEMENTED_TESTED", "WS + fallback polling"),
 "F011": ("IMPLEMENTED_TESTED", "PostgreSQL + Alembic 0004"), "F012": ("IMPLEMENTED_TESTED", "TimescaleDB hypertable"),
 "F013": ("IMPLEMENTED_TESTED", "Alloy syslog -> Loki -> Logs UI (synthetic)"), "F014": ("IMPLEMENTED_PARTIAL", "isolated gateway; live Ollama not run"),
 "F015": ("IMPLEMENTED_TESTED", "RULE engine + Recommendation v1"), "F016": ("IMPLEMENTED_TESTED", "hash chain, write lock"),
 "F046": ("IMPLEMENTED_PARTIAL", "receipts + audit; installer run on VM pending"),
 "F021": ("STARTED", "Phase 1 groundwork: generic Site/Cluster/Device/Capability tables + projection"),
 "F029": ("STARTED", "Phase 1 groundwork: per-user site grants; approver role reserved"),
 "F030": ("STARTED", "sanitized snapshots retained and labelled NOT RESTORABLE"),
}

out_dir.mkdir(parents=True, exist_ok=True)
with open(src_dir / "Unified_Acceptance_Register_v4.csv", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f)); fields = list(rows[0].keys())
counts = {}
for r in rows:
    if r["test_id"] in A:
        st, ev, link = A[r["test_id"]]
        r.update(status=st, evidence=f"{r['evidence']} || {ev}", owner=OWNER, date=date, artifact_link=link)
    counts[r["status"]] = counts.get(r["status"], 0) + (1 if r["phase"] == "P1" else 0)
missing = {r["test_id"] for r in rows if r["phase"] == "P1"} - set(A)
assert not missing, missing
with open(out_dir / "Unified_Acceptance_Register_v4.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

with open(src_dir / "Feature_Integration_Traceability_v4.csv", encoding="utf-8-sig") as f:
    frows = list(csv.DictReader(f)); ffields = list(frows[0].keys())
for r in frows:
    if r["feature_id"] in F:
        st, note = F[r["feature_id"]]
        r.update(implementation_status=st, developer="Claude (build agent)", evidence_link=note)
with open(out_dir / "Feature_Integration_Traceability_v4.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=ffields); w.writeheader(); w.writerows(frows)
print({k: v for k, v in counts.items() if v})
