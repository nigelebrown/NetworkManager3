# NetworkManager3 1.3.0-rc18 — acceptance matrix

**Legend**

| Status | Meaning |
|---|---|
| **PASS** | Developer-tested in this workspace, in the rc18 evidence run. EMULATED where marked. Never a hardware pass. |
| **PARTIAL** | Software built and tested; the stated remainder needs hardware or external infrastructure (category 5/6). |
| **UNSUPPORTED** | The vendor capability does not exist, or is refused by design, with the reason. |
| **DISABLED PENDING AUTHORISATION** | Built, but kept off and unmounted by owner instruction. |
| **REQUIRES HARDWARE VALIDATION** | Software done; it cannot be accepted without physical equipment (checklist row). |

The machine-readable record is `docs/Unified_Acceptance_Register_v5.csv`. Every v4 ID is preserved, and two
columns were added: `rc18_category` (directive categories 1–8) and `rc18_review`. The row-by-row review is in
`docs/REGISTER_REVIEW_1.3.0_RC18.md`.

**281 original rows (v4 → v5)**

| Status | rc17 | rc18 |
|---|---|---|
| PASS | 142 (62 of them without an actual result) | **160**: the 62 re-proven in the rc18 evidence run, plus 18 completed |
| PARTIAL | 67 | 64 |
| BLOCKED | 53 | 51 |
| NOT_RUN | 19 | 6 |

**What the 121 remaining non-PASS rows need, by category**

None of them is waiting for software:

| Category | Rows |
|---|---|
| 5 physical hardware | 69 |
| 6 external infrastructure | 50 |
| 7 unsupported vendor capability | 1 |
| 8 outside approved scope | 1 |

**New rows:** RC18-T01…T22. 21 PASS, and RC18-T17 (the hardware checklist) NOT_RUN. Total: 303 rows.

## Directive section 2

| § | Capability | Status | Evidence | Remaining (category) |
|---|---|---|---|---|
| 2.1 | ML pipeline (features, temporal split, selection, holdout vs statistical, registry, drift, live performance, fallback, rollback, scheduled retraining, admin controls, dashboard) | PASS (fixture + synthetic data) | `test_rc18_ml_pipeline.py`, `test_integrations_ml_gui.py` | A model trained and validated on ≥ 7 days of **real** fleet telemetry (NI-T12, category 6). Synthetic models are never production-validated |
| 2.2 | CX RADIUS servers, server groups, accounting, 802.1X/MAB port policy, client status | PASS (EMULATED) | `test_rc18_cx_aaa_ospf.py` | HW-CX-22/23 (5) |
| 2.3 | Instant RADIUS servers, SSID failover/load balancing, accounting, secret rotation, rollback | PASS (EMULATED) | `test_rc18_instant_radius.py` | HW-AP-18/19 (5) |
| 2.4 | Printer fleet reports; purchasing and print-job modules isolated | PASS; purchasing / print jobs **DISABLED PENDING AUTHORISATION** | `test_rc18_printer_reports.py` | Print-server agent: needs owner authorisation (8) |
| 2.5 | Printer settings via standard MIBs with per-model support detection | PASS (EMULATED); energy saving **UNSUPPORTED** (no standard object) | `test_rc18_printer_settings.py` | HW-PRN-10…12 (5) |
| 2.6 | Optics (DOM) with truthful missing/unsupported states | PASS (EMULATED, AOS-CX format) | `test_rc18_optics.py` | HW-CX-25 (5); other vendors' DOM formats not built (read-only SNMP adapters) |
| 2.7 | Release signature verification, fail-closed install/update/rollback/offline | PASS (disposable keys) | `test_release_signing.py` | Owner key ceremony + HW-PL-24/25 (6) |
| 2.8 | Paramiko advisory resolved without weakening SSH | PASS | `test_ssh_policy.py` | Confirm 8.10 / 10.14 negotiate rsa-sha2 / ed25519 (5) |
| 2.9 | React Router 7 | PASS (Chrome) | `test_routing_rc18.py`, nav audit | — |
| 2.10 | Integrations module | PASS (local receivers) | `test_rc18_integrations.py`, `test_integrations_ml_gui.py` | HW-INT-01/02 real Teams/SIEM (6) |
| 2.11 | OSPF on CX 6200, refused on 6100; BGP UNSUPPORTED_BY_MODEL | PASS (EMULATED); BGP **UNSUPPORTED** | `test_rc18_cx_aaa_ospf.py` | HW-CX-24 (5) |
| 2.12 | Imported Group column end to end | PASS (Chrome + DB) | `test_rc18_group_import.py`, `test_group_import_gui.py` | — |
| 2.13 | Deterministic predictive test | PASS | `test_rc18_predictive_clock.py` | — |

## Directive section 3 — software completed during the register review

| Row(s) | Status now | Evidence |
|---|---|---|
| P6-10 high-risk / cluster deployment impact and release approval | PARTIAL → software PASS; hardware (5) remains | `test_rc18_high_risk_deployment.py`, `test_deployment_gui.py` |
| RC16-T12 onboarding account-lockout guard | software PASS; lab AAA (6) remains | `test_automatic_onboarding.py` |
| P11-DEP-03 expand/migrate/contract window | software PASS; multi-node rolling upgrade (6) remains | `test_rc18_migration_window.py` |
| P3-01, INTEG-P3-03, GAP-T03 syslog → timeline → attribution | software PASS (real packets through Alloy); real devices (5) remain | `test_rc18_syslog_bridge.py`, fullstack `test_10c` |
| P4-08 logging-health alerts | software PASS; outage drill on real AAA/syslog (6) remains | `test_rc18_syslog_bridge.py` |
| P10-01 / P10-02 / P10-03 Cisco / UniFi read-only | software PASS (EMULATED); real equipment (5/6) remains | `test_rc17_snmp_adapters.py`, `test_rc18_syslog_bridge.py`, `test_rc18_unifi_adapter.py` |
| P3-08, P4-09, CXAPI-07, P5-DEP-01, P5-06, P8-DEP-01, INTEG-P3-01, INTEG-P3-02, INTEG-P11-04, GAP-T15, P1-08, P1-DEP-15, P8-03, P8-04, INTEG-P8-01, INTEG-P8-04, GAP-T10, P8-08, PRN-T17 | see the register (PASS where every referenced test passed in the evidence run) | `docs/REGISTER_REVIEW_1.3.0_RC18.md` |

## Directive section 4 — test infrastructure

| Item | Status | Evidence |
|---|---|---|
| One command: provision, run everything, evidence report | PASS | `scripts/run_full_test_suite.sh` → `evidence/test-run-<UTC>/TEST_EVIDENCE_REPORT.md` |
| CI runner without Docker | PROVIDED (not run on GitHub from here: no remote access) | `.github/workflows/full-test-suite.yml` |
| tests/integration, tests/fullstack, gui_workflow, live_operations, demo GUI, TimescaleDB/NATS/Loki, mixed load | see `docs/TEST_RESULTS_1.3.0_RC18.md` | evidence run |

## Directive section 5 — whole-application review

`docs/WHOLE_APP_REVIEW_1.3.0_RC18.md`: 0 silent exception handlers, 0 unrouted frontend calls, no placeholder responses
in live paths, and no remaining `FIXME`, `NotImplementedError` or `CONCEPT` markers.
