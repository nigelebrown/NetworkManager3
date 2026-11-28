# NetworkManager3 1.3.0-rc18 — developer test results

These results come from this workspace only:

- emulated devices (CX protocol emulator, SNMP printer emulator, Instant emulator)
- fixtures
- a native distributed stack: PostgreSQL 16.15 + TimescaleDB 2.22.1, NATS 2.11.9, Loki 3.5.5, Alloy 1.11.0, API,
  worker and HTTPS device emulators
- Google Chrome 153

**This is software evidence, not hardware certification.**

## 1. Run of record

`scripts/run_full_test_suite.sh`, run `test-run-20261125T185616Z`, commit `9759118`. The report is
`evidence/test-run-20261125T185616Z/TEST_EVIDENCE_REPORT.md`.

| Step | Result |
|---|---|
| Unit + contract on SQLite | **PASS** 829 |
| Legacy SIMS regression | **PASS** 181 |
| Unit + contract on PostgreSQL 16 (template-database mode) | **PASS** 829 |
| Frontend `tsc -b` + `vite build`; API route audit; `npm audit --omit=dev` | **PASS**; 352 frontend API paths, 0 without a backend route; 0 vulnerabilities |
| PostgreSQL migration chain 0004→0016 + dump/restore drill | **PASS** 5 checks |
| Full distributed stack contracts (incl. `test_10c` syslog → attribution through real Alloy) | **PASS** 13 |
| Browser: ops workflows, routing (React Router 7), deployment impact/release, printer GUI, intelligence GUI | **PASS** 3 + 2 + 1 + 9 + 8 |
| Mixed load: 25 users × 120 s against PostgreSQL/TimescaleDB with 1,000 emulated printers seeded and a separate polling worker | **PASS**: 1,432 requests, 0 errors, p50 38 ms, p95 352 ms, p99 539 ms; peak RSS 231 MB; 1,283 printer polls in the sampled worker cycles |
| Upgrade/backup/restore on the stack; native seven-role integration; gui_workflow; live_operations; demo GUI; integrations/ML GUI; group-import GUI; navigation audit | **FAIL** in this run (8 steps). Causes in §2 |

## 2. Failures in the run of record, their causes, and the re-runs

Every failure was investigated. Assertions were changed only where they encoded behaviour that the product
deliberately changed since the test was written; each such change is listed. Product defects found this way were
fixed. Re-runs: `evidence/test-run-20261125T185616Z/rerun-after-fixes/` (junit + logs).

| Step | Cause | Fix | Re-run |
|---|---|---|---|
| fullstack_upgrade_backup_restore | Test hard-coded schema head `0010` (written for rc2) | Reads the head from `release.json` | **PASS** 3, after `fullstack_contracts` **PASS** 13 as in the runner |
| integration_native | Runner did not export the interpreter the tests use to restart roles | Runner exports `PYTHON` | **PASS** 13 |
| e2e_gui_workflow | Stale selectors (rc17 breadcrumbs, Device 360 ARIA tabs, renamed monitoring button). **Product defects:** read-only monitoring state never shown in the workflow; a failed load showed the start page and "No workflows yet". Poll loop counted before data rendered | UI fixed (monitoring section, load error + Retry); selectors updated; loop waits for render | **PASS** |
| e2e_live_operations | Demo data in live mode needs the audited opt-in (rc17); live dashboards deliberately exclude DEMO devices; the demo dataset's similar change plan was clicked; the 39-screen sweep exceeded the per-user API limit (300/min) and throttled later steps. **Product defect:** signed exports had no screen | Opt-in for this suite only (asserted as audited); DEMO exclusion asserted; innermost plan card; sweep as its own paced admin, and a request-limit notice now fails it; signed exports added to Executive overview | **PASS** |
| e2e_demo_gui | The 42-route sweep exceeded the per-user API limit, so the printer workflow got HTTP 429. A Loki-less stack answers Logs with 503 | Printer workflow runs as a second administrator; that 503 is accepted only after re-checking it says "Logging service not configured" | **PASS** |
| e2e_integrations_ml_gui | **Product defect:** concurrent first requests both seeded the AI rule catalogue, and the loser answered HTTP 500 | Per-rule savepoint seeding; regression test `test_rc18_rule_seed_race.py` | **PASS** 2 |
| e2e_group_import_gui | Runner started the next isolated stack while the previous one was still exiting (connection refused) | Runner waits for the old process to exit and checks the new process answers | **PASS** |
| e2e_navigation_audit | Same runner race (connection refused) | Same runner fix | **NOT RE-RUN.** Skipped at the owner's request. It passed in the rc17 and earlier rc18 sessions (74 views, 0 defects), and the only UI changes since then are inside existing screens |

**Not repeated:** the complete one-command run end to end after these fixes. The SQLite and PostgreSQL contract
suites in the run of record ran on `9759118`. The later changes are:

- the rule-seed race fix and its test
- the onboarding and executive UI additions
- e2e, runner and fullstack test corrections
- docs

The touched areas were re-run on the final code, all passing (473): `tests/unit`, `test_rc18_rule_seed_race.py`, `test_network_intelligence.py`, `test_security_and_contracts.py` and `test_rc18_ml_pipeline.py`. The browser re-runs above cover the UI additions.

## 3. New rc18 tests (all in the run of record unless noted)

| Directive item | Tests |
|---|---|
| 2.1 ML | `test_rc18_ml_pipeline.py` (9), `test_integrations_ml_gui.py` (2, re-run) |
| 2.2/2.11 CX RADIUS/AAA/OSPF/BGP | `test_rc18_cx_aaa_ospf.py` |
| 2.3 Instant RADIUS | `test_rc18_instant_radius.py` (5) |
| 2.4/2.5 printers | `test_rc18_printer_reports.py` (3), `test_rc18_printer_settings.py` (7) |
| 2.6 optics | `test_rc18_optics.py` (5) |
| 2.7/2.8 signing, SSH | `tests/unit/test_release_signing.py` (4), `tests/unit/test_ssh_policy.py` (3) |
| 2.9 router | `test_routing_rc18.py` (2) |
| 2.10 integrations | `test_rc18_integrations.py` (6) |
| 2.12 group column | `test_rc18_group_import.py`, `test_group_import_gui.py` (re-run) |
| 2.13 predictive clock | `test_rc18_predictive_clock.py` |
| §3 register completions | `test_rc18_emergency_access.py`, `test_rc18_config_change_distinction.py`, `test_rc18_ingest_saturation.py`, `test_rc18_experience_frozen_fixtures.py`, `test_rc18_legacy_sample_migration.py`, `test_rc18_vault_upgrade_and_isolated_restore.py`, `test_rc18_demo_network_trace.py`, `test_rc18_ai_isolation.py`, `test_rc18_update_quiesce.py`, `test_rc18_high_risk_deployment.py` (4), `test_deployment_gui.py`, `tests/unit/test_automatic_onboarding.py` (+3), `test_rc18_migration_window.py` (5), `test_rc18_syslog_bridge.py` (6), `test_rc18_unifi_adapter.py`, fullstack `test_10b`, `test_10c` |
| defects | `test_rc18_rule_seed_race.py` (written after the run; passing on the final code) |

## 4. What these results do not show

- Any behaviour of physical Aruba, Cisco, UniFi or printer equipment.
- Installation, upgrade or restore on CentOS Stream 9 with Docker.
- Multi-VM resilience.
- 24-hour capacity.
- Model skill on real telemetry.

See the hardware checklist and `docs/REGISTER_REVIEW_1.3.0_RC18.md`.
