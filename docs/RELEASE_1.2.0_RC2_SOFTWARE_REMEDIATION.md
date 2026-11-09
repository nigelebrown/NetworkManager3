# NetworkManager3 1.2.0-rc2 — Software remediation before hardware testing

Branch `release/software-remediation` (from `release/production-readiness` @ `821a10c`, tag `v1.2.0-rc1`).
Tag `v1.2.0-rc2`. Not merged to `main`; the owner arranges upload and merge.
Hardware access and hardware writes stayed **locked** for the whole campaign. "Emulated PASS" below means **software
PASS only**; it is never hardware certification.

## 1. Verdict

**SOFTWARE READY FOR HARDWARE CERTIFICATION — for the implemented scope**, with the exceptions in §8:

* All 20 independent-audit findings (E2E-01 … E2E-20) are fixed or, for the parts not built, explicitly declared
  `NOT_IMPLEMENTED` in the product. Each fix has a regression test (§2).
* The auditor's own probes now report **11 PASS / 0 FAIL** (were 3 PASS / 8 FAIL), and the ingest replay probe passes.
* 14 further defects were found by the rc2 test campaign and fixed (§2.2), including one safety defect (P6-06).
* Every write operation for AP-505, AP-535, CX 6100 and CX 6200 passes the strict emulated lifecycle (§5).
* Still open in software: 9 register items with no test yet and 6 Central-parity features not built (§8). They are
  not presented as operational anywhere in the GUI or API.
* Blocked outside software: physical hardware, and a CentOS Stream 9 host run (the container images and CentOS
  mirrors are refused by this environment's network policy). §6 shows the database-layer evidence done instead.

## 2. Defect list linked to source

### 2.1 Independent audit findings

| ID | Sev | Fix (files) | Commit(s) | Regression evidence |
|---|---|---|---|---|
| E2E-01 | P0 | Telemetry key now includes interface identity: migration t002, `(source_event_id, device_id, entity_type, entity_id, metric_name, observed_at)`; duplicate identities in a batch are an error (`backend/infrastructure/telemetry/timescale.py`, `domain/model.py`) | 99bca67 | `test_every_port_of_24_and_48_port_switches_is_stored_and_retrievable`; full-stack `test_04`; probe "Per-interface metrics stored distinctly" |
| E2E-02 | P0 | `query()` filters and returns `entity_type/entity_id/tags`; per-port series and entities endpoints; Faceplate port charts | 99bca67, 14047df | same + probe "Telemetry query preserves interface identity" |
| E2E-03 | P0 | Transactional outbox (`infrastructure/outbox.py`): event row in the same transaction as collection state, relayed to JetStream (worker) or in-process; scheduled collection goes through `IngestService` | 99bca67 | `test_one_correlation_id_spans_run_event_points_log_alert_and_ui`; full-stack `test_04`, `test_05`, `test_06`; probe "Scheduled collection routes telemetry through bus/ingest" |
| E2E-04 | P0 | `CxSession`: form-body login, never credentials in the URL; `redact()` on errors/logs; strict emulator refuses query-string login with 400 | 99bca67 | `test_credentials_only_in_form_body_and_csrf_on_every_later_request`; `test_credentials_never_reach_logs_errors_or_audit`; probe "credentials transmitted as body" |
| E2E-05 | P0 | CSRF negotiated (`x-use-csrf-token`), sent on every request, one re-login on 401/CSRF-403 | 99bca67 | `test_strict_emulator_rejects_the_old_query_string_login_and_missing_csrf`; `test_session_expiry_relogs_in_once_and_wrong_credentials_fail_cleanly`; probe "CSRF negotiated and reused" |
| E2E-06 | P0 | Persistent 12-step onboarding workflow (`api/workflows.py`, `GuidedOnboarding.tsx`): site → credentials → discover → register → import/backup → templates/group → preview → now/later → independent approval → apply/readback → monitor → done | 14047df, e6de8ab | `test_real_mode_workflow_runs_end_to_end_and_hardware_writes_stay_locked`; `test_emulated_workflow_reaches_applied_verified_monitored_done`; browser `test_gui_workflow.py` |
| E2E-07 | P1 | Durable discovery jobs (`infrastructure/discovery_jobs.py`): lease, heartbeat, cursor, pause/resume/cancel/retry, rate limit, crash takeover, dedupe | 99bca67 | 5 tests in `test_remediation_discovery_jobs.py`; full-stack `test_02` (worker killed mid-scan) |
| E2E-08 | P1 | Capability registry generated from the writers' `OPERATIONS`; placeholders `implemented=False` | 2faae9a | `test_registry_matches_writers_and_never_claims_unimplemented_or_uncertified`; probe "Adapter registry acknowledges implemented write support" |
| E2E-09 | P1 | API version negotiation (prefers v10.14, else `GET /rest`; v10.09–v10.15 accepted; older → `UNSUPPORTED_API`) | 99bca67 | `test_api_version_negotiated_per_firmware`; `test_unsupported_firmware_is_refused_with_a_clear_category` |
| E2E-10 | P1 | Runs go `COLLECTED → SUCCEEDED` only after the persisted point count is verified; missing store or count mismatch = `PERSIST_FAILED`, retried | 99bca67 | `test_no_false_success_when_telemetry_cannot_be_persisted_then_recovers` |
| E2E-11 | P1 | One `operational_state()` (ONLINE/DEGRADED/STALE/OFFLINE/UNKNOWN) used by collector, NOC, Device 360 and API | 99bca67 | `test_status_agrees_across_collector_noc_device360_and_alerts_through_outage_and_recovery`; full-stack `test_07` |
| E2E-12 | P1 | Live write posture (`infrastructure/posture.py`) replaces hard-coded `LOCKED`; per-device `/capabilities`; certification read from records | 2faae9a | `test_device_capability_report_follows_certification_records` |
| E2E-13 | P1 | Read intake no longer refused when writes are enabled | 99bca67 | `test_probe_works_with_writes_enabled` |
| E2E-14 | P1 | Encrypted change-recovery journal committed before the first write; sweep → `RECOVERY_REQUIRED` + alert; CX checkpoint / Instant inverse-ops recovery; guided Instant restore; full Instant restore shown as UNSUPPORTED | 2faae9a | `test_crash_mid_change_is_recovered_from_the_durable_journal` |
| E2E-15 | P1 | One event contract; correlation ID = run ID (collection) or plan ID (changes) across run, points, ledger, Loki, alert, WebSocket | 99bca67, e2f7588 | `test_one_correlation_id_…`; full-stack `test_04`, `test_08` |
| E2E-16 | P1 | All CX probes/baselines/writes use the one `CxSession` | 99bca67 | CX protocol suite (7 tests) |
| E2E-17 | P1 | Obsolete `tests/e2e/test_browser_live.py` retired; wizard browser test added; each browser suite runs on its own fresh full stack (`tests/e2e/run_isolated.sh`) | e6de8ab | `evidence/e2e-gui/` |
| E2E-18 | P1 | Upgrade and backup/restore proven at the database layer on PostgreSQL 16 + TimescaleDB 2.22; scripts shellcheck-clean. CentOS host run **BLOCKED** (§6) | eac6f56 | `tests/fullstack/test_upgrade_backup_restore.py` |
| E2E-19 | P2 | Provenance labels (EMULATED/DEMO) on executions, devices and capabilities; `/system/features` lists every module honestly, shown on the Platform page | 14047df, 64eca13 | `test_feature_list_declares_notifications_live_and_unbuilt_features_not_implemented` |
| E2E-20 | P2 | Built: enterprise 802.1X SSID + RADIUS, PSK SSID create, switch port description/BPDU guard/storm control/QoS trust, NTP/DNS/syslog; device groups and batch deployment; per-port alerts; LLDP topology; change–fault correlation; alert notifications and escalation. **Not built**, declared `NOT_IMPLEMENTED`: §8 | 2faae9a, 14047df, e2f7588, 64eca13 | `test_new_switch_and_wireless_operations_end_to_end_with_secret_hygiene`; `test_multi_device_deployment_…`; insights suite; notifications suite; full-stack `test_11` |

### 2.2 Defects found and fixed by the rc2 test campaign

| ID | Defect | Fix | Commit | Regression |
|---|---|---|---|---|
| R-01 | A manual "poll now" in the same second as another was silently skipped | Each manual poll is its own run; scheduled windows still dedupe across workers | e2f7588, d5c82ae | `test_two_manual_polls_in_the_same_second_are_both_executed`; `test_collection_plan_success_failure_dedupe_and_experience` |
| R-02 | A REST-reported model such as "6200F 48G" fell back to the generic SNMP adapter | `resolve()` matches 3–4-digit model tokens | e6de8ab | unit test in `tests/unit` |
| R-03 | Approver/auditor could not open the workflow they had to approve | Read roles on workflow GETs | e6de8ab | contract + browser workflow tests |
| R-04 | Monitor step didn't advance and the workflow never reached DONE | Advance on monitor; auto-DONE when every device has a fresh persisted observation | e6de8ab | `test_gui_workflow.py` |
| R-05 | Certification evidence could have stored the per-run lab secret | `_secret` stripped; only masked renders are recorded | 2faae9a | `test_new_switch_and_wireless_operations_end_to_end_with_secret_hygiene` |
| R-06 | Recovery journal written in a second session could deadlock (SQLite) | Committed in the request session | 2faae9a | `test_crash_mid_change_is_recovered_from_the_durable_journal` |
| R-07 | Group-scope template lost its `group_id` | Saved and validated | 14047df | workflow tests |
| R-08 | Emulator gave every switch 48 ports, so tests used ports a 12-port CX 6100 doesn't have | Port count from the model; tests use ports present on each model | d5c82ae, 8cbb19d | full contract suite |
| R-09 | Emulator counted a stale cookie after session expiry as a client violation | Only a missing cookie is a violation | d5c82ae | `test_emulated_certification_campaign` |
| R-10 | **Safety:** an edit made on the device after approval was only detected if a vault capture happened in between | Live pre-change backup compared with the approved baseline (raw, then normalized). Mismatch → zero writes, plan back to `PENDING_APPROVAL` with a new revision and baseline | 8cbb19d | `test_out_of_band_change_after_approval_stops_execution_until_renewed_review` |
| R-11 | No alert notification or escalation existed | Webhook/email/syslog channels, idempotent deliveries, backoff, DEAD state, escalation of unacknowledged alerts | 64eca13 | 5 contract tests + full-stack `test_11` |
| R-12 | Truthful module list not visible in the GUI; compose comment said optimization "never exists" | Platform page "Module status"; comment corrected | 64eca13 | browser walk of `/platform` |
| R-13 | Wizard step bar on DONE pointed at the wrong step | Index fixed | final commit | `test_gui_workflow.py` |
| R-14 | Monitor history showed raw device UUIDs | Hostnames and persisted / awaiting counts | final commit | contract workflow tests |

## 3. Test results (all on the final code)

| Suite | Result | Evidence |
|---|---|---|
| Full regression (backend, unit, contract incl. emulated campaign) | **444 passed, 0 failed** | `evidence/regression/full-regression.log` (verbose, every test name) |
| Cross-service full stack: PostgreSQL 16 + TimescaleDB 2.22.1, NATS JetStream 2.11.9, Loki, Alloy 1.11.0, API, worker, scheduler, CX emulators on real IPs | **14 passed** (11 pipeline/outage + 3 upgrade/restore/retention) | `evidence/fullstack/fullstack-tests.log`, `*.json` |
| Browser E2E (Chrome, each suite on a fresh stack) | **2 suites passed** — wizard end to end + full navigation walk | `evidence/e2e-gui/` (logs + screenshots) |
| Independent auditor probes, re-run unchanged | **11 PASS / 0 FAIL**; ingest replay PASS | `evidence/independent-audit/` |
| Emulated certification campaign (§5) | 52 operation rows PASS (emulated), 2 UNSUPPORTED by design, 0 FAIL | `evidence/certification/` |
| Shell scripts | `bash -n` and shellcheck (warning level) clean | `evidence/centos9/shellcheck.log` |

The cross-service tests compare persisted values, not HTTP codes. For example, `test_04` follows one correlation ID from
the device read through the outbox, JetStream, TimescaleDB rows, the event ledger, Loki and the alert. `test_11`
checks that the webhook payload equals the alert the API serves and the delivery row says `SENT`, exactly once.

## 4. 220-item acceptance register

`docs/Unified_Acceptance_Register_v4.csv`, regenerated by `scripts/reconcile_register_1_2_0_rc2.py`. Every test the
register cites is checked to exist in the rc2 test collection; evidence links point to the rc2 logs.

| Status | rc1 | rc2 |
|---|---|---|
| PASS | 86 | **88** |
| PARTIAL (emulator/native only; hardware or CentOS half BLOCKED) | 64 | **70** |
| BLOCKED (hardware / CentOS host / multi-VM / external party) | 54 | **53** |
| NOT_RUN (software-testable, no test yet) | 16 | **9** |
| Total | 220 | 220 |

No row became PASS on emulator evidence. Phase-by-phase detail is in `docs/RC_1.2.0_TEST_MATRIX.md`.

| Phase | PASS | PARTIAL | BLOCKED | NOT_RUN |
|---|---|---|---|---|
| P1 platform | 23 | 14 | 4 | 0 |
| P2 discovery/onboarding | 7 | 9 | 10 | 0 |
| P3 monitoring | 4 | 5 | 4 | 3 |
| P4 accountability | 7 | 4 | 7 | 1 |
| P5 vault | 7 | 6 | 1 | 2 |
| P6 change control | 9 | 10 | 0 | 0 |
| P7 recovery | 4 | 7 | 2 | 0 |
| P8 AI | 7 | 6 | 1 | 1 |
| P9 optimization | 15 | 3 | 0 | 0 |
| P10 vendors | 4 | 2 | 3 | 0 |
| P11 deployment | 1 | 3 | 21 | 1 |
| CROSS / PRINTER | 0 | 1 | 0 | 1 |

## 5. Emulated write-lifecycle certification (Section 6)

`tests/contract/test_emulated_certification_campaign.py` (set `INM_WRITE_EVIDENCE=1` to regenerate
`evidence/certification/emulated_certification_table.md` and `.json`). For each model, every typed operation goes
through: backup → checkpoint → apply → readback → rollback → full-config comparison, then independent approval.
Then a change plan is executed (`APPLIED_VERIFIED`), a device failure is injected (`ROLLED_BACK`, rollback verified
by full readback), and for CX the session is expired mid-run (exactly one re-login, 0 protocol violations) and
firmware 10.08 is refused (`UNSUPPORTED_API`).

| Model | Firmware | Rows PASS (emulated) | Plan | Injected failure | Notes |
|---|---|---|---|---|---|
| CX 6100 24G | PL.10.14.1000 | 16/16: backup read, 14 typed ops, full restore | APPLIED_VERIFIED | ROLLED_BACK | CSRF/URL violations 0 |
| CX 6200F 48G | ML.10.14.1020 | 16/16: backup read, 14 typed ops, full restore | APPLIED_VERIFIED | ROLLED_BACK | CSRF/URL violations 0 |
| AP-505 | 8.10.0.12 | 10/11: backup read, 9 typed ops | APPLIED_VERIFIED | ROLLED_BACK | full restore UNSUPPORTED (guided procedure) |
| AP-535 | 8.10.0.12 | 10/11: backup read, 9 typed ops | APPLIED_VERIFIED | ROLLED_BACK | full restore UNSUPPORTED (guided procedure) |

The emulators don't model per-model radio chains/RF limits (AP-505 2×2 vs AP-535 4×4), PoE budgets, real reboot
timing, firmware REST quirks beyond the version catalogue, or Instant conductor failover. Those are hardware-only.
The capability matrix (`docs/CAPABILITY_MATRIX_1.2.0_RC2.md`, generated from code by `scripts/capability_matrix.py`)
lists every operation as hardware `UNCERTIFIED`.

## 6. CentOS Stream 9 install / update / backup / restore

| Item | Status | Detail |
|---|---|---|
| Clean install via `deployment/centos9/install.sh` | **BLOCKED** | Container registries (quay.io, Docker Hub) and CentOS mirrors refused by the build environment's egress proxy (HTTP 403) |
| Update via `update.sh` on CentOS | **BLOCKED** | same |
| Backup/restore via `backup.sh` / `restore.sh` on CentOS | **BLOCKED** | same; JetStream/Loki volume archives not exercised |
| rc1 → rc2 schema upgrade with data (management 0009 → 0010, telemetry t001 → t002) | **PASS (database layer)** | PostgreSQL 16 + TimescaleDB 2.22.1; all rows kept, port identity back-filled, rc2 ORM reads upgraded rows — `evidence/fullstack/upgrade_rc1_to_rc2.json` |
| Backup → restore round trip (`pg_dump -Fc`, `timescaledb_pre/post_restore`, as `restore.sh` does) | **PASS (database layer)** | every table's row count and content checksum identical; restored schema at head; migrations a no-op — `evidence/fullstack/backup_restore_round_trip.json` |
| Retention (`drop_chunks`) | **PASS** | only points older than 90 days removed |
| Script static checks | **PASS** | `bash -n` + shellcheck clean |

To close the BLOCKED rows, run on a CentOS Stream 9 VM with registry access:
`sudo bash deployment/centos9/install.sh --mode live`, then `verify.sh`, `backup.sh`,
`restore.sh --target test`, and `update.sh` from a v1.2.0-rc1 install.

## 7. Readiness per phase

| Phase | Software state | What hardware/host certification must still prove |
|---|---|---|
| P1 platform | Distributed stack proven natively: outbox, JetStream, Loki/Alloy, restarts, outages | CentOS container install, volumes, ports, secrets on the host |
| P2 discovery/onboarding | Durable jobs, 12-step workflow, CX session/CSRF/version negotiation on strict emulators | Real AP/CX discovery, credentials, Instant clusters |
| P3 monitoring | Per-port identity, status authority, no false success, retention, notifications | An hour of real metrics; real syslog sources |
| P4 accountability | Hash-chained audit, scoped RBAC, correlation | Device AAA/accounting correlation |
| P5 vault | Encrypted capture, schedule, failure handling, tamper detection | Real full-config capture per model |
| P6 change control | Certification gate, independent approval, out-of-band guard, rollback, journal | Every operation per model/firmware train in a lab |
| P7 recovery | CX checkpoint/restore; Instant inverse ops + guided restore | Real restore and replacement |
| P8 AI | Rule-based, advisory, read-only (labelled) | n/a |
| P9 optimization | Off by default, per-device opt-in, 20/40 MHz only, degradation rollback | Real RF measurement |
| P10 vendors | Aruba adapters implemented; Cisco/UniFi placeholders labelled | Real Aruba devices |
| P11 deployment | DB-level upgrade/restore proven | Everything on CentOS hosts / multi-VM |

## 8. Remaining gaps (not presented as operational)

**Software, not yet tested (register NOT_RUN):** P3-08 bounded saturation/backpressure; P4-09 break-glass audit
workflow (not built); CXAPI-07 metadata-only CX snapshot difference; P5-DEP-01 vault upgrade with versioned records;
P8-DEP-01 one-click model rollout (not built); INTEG-P3-01 SIMS score recompute on frozen fixtures; INTEG-P3-02 AP
sample-history migration; INTEG-P11-04 post-migration scoped report; GAP-T15 isolated AI code reviewer (not built).

**Central-parity features not built**, declared `NOT_IMPLEMENTED` in `/api/v1/system/features` and on the Platform
page: 802.1X/MAB on switch ports; VSF stacking; ACLs and routing; client roaming history and RF interference
analytics; remote diagnostics (ping/traceroute/cable test); CX firmware orchestration (AP firmware staging uses the
legacy, independently approved path; module status `PARTIAL`). Multi-device plans have no cluster-scope impact view
and no stronger approval policy for high-risk batches (P6-10, PARTIAL).

**Outside software:** all hardware certification (AP-505, AP-535, CX 6100, CX 6200), CentOS host runs (§6),
multi-VM topology, AAA/syslog infrastructure drills.

## 9. Reproduce

```bash
.venv/bin/python -m pytest -q backend/tests tests/unit tests/contract            # 444 tests
bash tests/fullstack/stack.sh up                                                 # native distributed stack
INM_FS=1 .venv/bin/python -m pytest -v tests/fullstack/                          # 14 tests
bash tests/fullstack/stack.sh down && bash tests/e2e/run_isolated.sh             # browser suites, fresh stack each
INM_WRITE_EVIDENCE=1 .venv/bin/python -m pytest -q tests/contract/test_emulated_certification_campaign.py
.venv/bin/python scripts/reconcile_register_1_2_0_rc2.py                         # register + matrix
.venv/bin/python scripts/capability_matrix.py                                    # capability matrix
```
