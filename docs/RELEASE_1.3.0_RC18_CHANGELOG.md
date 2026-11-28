# NetworkManager3 1.3.0-rc18 — change log

Base: 1.3.0-rc17 (local commit `f6f44ce`). Management schema **0016** (new, expand-only), telemetry **t002**
(unchanged). Patch: `NetworkManager3-1.3.0-rc17-to-rc18.patch`.

Built against the RC17 corrective development directive. All evidence is developer testing in this workspace:

- pure functions
- labelled emulators (EMULATED)
- a native distributed stack: PostgreSQL 16 + TimescaleDB 2.22.1, NATS 2.11.9, Loki 3.5.5, Alloy 1.11.0, API, worker
  and HTTPS device emulators
- headless Chrome

**Nothing has been run against physical Aruba, Cisco, UniFi or printer equipment, or on the CentOS target server.**
No synthetic-data model is production-validated.

## Directive section 2 — implemented

| § | Area | Change | Tests |
|---|---|---|---|
| 2.1 | ML pipeline | Breach-risk model: 12 leakage-free features computed only from data before each cutoff; logistic regression (Newton/IRLS, L2 picked on validation); temporal 60/20/20 split with a horizon embargo; holdout metrics against base rate and the statistical engine on the same windows; versioned registry with dataset source; validation gate (holdout n ≥ 100, ≥ 10 positives, Brier skill ≥ 0.05, AUC ≥ 0.65, beats statistical). Modes: PRIMARY, SHADOW and STATISTICAL_FALLBACK. Drift (PSI) and live performance (Brier skill on scored windows) feed health. Scheduled retraining creates candidates only. Promotion needs an independent approver, and synthetic models are refused on live installations. Admin settings and a dashboard (AI learning centre, Predictive health). | `test_rc18_ml_pipeline.py`, `test_integrations_ml_gui.py` |
| 2.2 | CX RADIUS lifecycle | `switch.aaa.radius_server`, `switch.aaa.server_group` (priority, primary/secondary), `switch.aaa.accounting` (port-access), `switch.port.auth_policy` (802.1X / MAB with server group). Port authentication client status is monitored. Every operation is a governed, certified, readback-verified, rolled-back write; secrets are never recorded. | `test_rc18_cx_aaa_ospf.py` |
| 2.3 | Instant RADIUS | `ap.radius.server` (`wlan auth-server`, timeout, retry-count, accounting port); SSID primary/failover order and load balancing (`ap.ssid.auth_servers`); accounting and interim interval (`ap.ssid.accounting`); secret rotation needs the previous secret; full rollback. A missing secret refuses the plan before anything is sent (the placeholder-secret path was removed). | `test_rc18_instant_radius.py` |
| 2.4 | Printer fleet | Paper-size and annual reports; counter-reset handling. Purchasing and print-job modules are **not mounted** unless `INM_FEATURE_PURCHASING` / `INM_FEATURE_PRINT_JOBS` is `true`; the screens show an authorisation gate. | `test_rc18_printer_reports.py` |
| 2.5 | Printer configuration | Standard-MIB settings: sysLocation, sysContact and sysName, ENTITY asset ID, prtGeneralPrinterName, default input tray and default media path (each validated against the printer's own table). Each setting goes through preview, authorisation, identity verification, SNMP SET, readback, rollback and audit. A per-printer hardware test is limited to lab serials. Energy saving is reported UNSUPPORTED (no standard object). | `test_rc18_printer_settings.py`, `test_printer_configuration.py` |
| 2.6 | Optics | Parses DOM (temperature, voltage, bias, TX/RX power per lane) from the advertised show command. Uses vendor thresholds. NOT_MEASURED and UNSUPPORTED are never shown as healthy. History and degradation trend are kept. Per-port alerts resolve only on a measured healthy reading. | `test_rc18_optics.py` |
| 2.7 | Release signatures | Install, update, rollback and offline install verify the Ed25519-signed release before using any of its content. A failed check stops the install. The trust store supports controlled rotation and revocation. Tests use disposable keys only; owner signing procedure in the deployment guide. | `test_release_signing.py` |
| 2.8 | Paramiko advisory | Paramiko 5. Modern algorithms only, with host-key pinning. Legacy devices use an approved, audited, per-device OpenSSH transport (`INM_SSH_LEGACY_COMPAT`, default off). The global SSH policy is not weakened. | `test_ssh_policy.py` |
| 2.9 | React Router | `react-router` 7. Covers protected routes, deep links, lazy routes, history, logout, session expiry and role navigation. | `test_routing_rc18.py` |
| 2.10 | Integrations | One Integrations screen covering SMTP (per-connection TLS mode and login), webhook (bearer / HMAC `X-INM-Signature`), Microsoft Teams (Adaptive Card), syslog TCP (RFC 6587 octet counting) and heartbeat push. Credentials are write-only and Fernet-encrypted. Also provides test, enable/disable, deliveries, retry and audit. Read-only API tokens (`inm_pat_…`, SHA-256 at rest, GET/HEAD only, capped at viewer) and an opt-in Prometheus endpoint. | `test_rc18_integrations.py` |
| 2.11 | OSPF/BGP per model | `switch.route.ospf` (single area) on CX 6200 only. CX 6100 is UNSUPPORTED_BY_MODEL. BGP is UNSUPPORTED_BY_MODEL on 6100/6200. Dynamic routing writes use the full approval, backup and rollback chain. | `test_rc18_cx_aaa_ospf.py` |
| 2.12 | Imported Group column | CSV Group column flows through mapping, validation, import, assignment (create, existing or skip, optional reassign), inheritance and the inventory Groups column. Verified in the browser and the database. | `test_rc18_group_import.py`, `test_group_import_gui.py` |
| 2.13 | Deterministic predictive test | Uses a controllable clock (hour boundaries, half-open windows); scheduled jobs score and issue predictions on one instant. | `test_rc18_predictive_clock.py` |

## Directive section 3 — register review: software found incomplete and completed

The review of all 281 records found the following non-PASS rows had **software** work outstanding, not only
hardware. Each one was completed in this release and was not reclassified as a hardware or infrastructure blocker.

| Row | Was | Completed |
|---|---|---|
| P6-10 | Cluster-scope impact view and stronger approval for high-risk multi-device changes "NOT implemented" | `GET /api/v1/deployments/{id}/impact` lists devices, sites, every Instant cluster member reached through the virtual controller (including members not in the batch), operations and a digest. A deployment is HIGH risk when it is cluster-scoped, spans more than one site or reaches `INM_DEPLOYMENT_HIGH_RISK_DEVICES` (default 10). A HIGH-risk deployment executes only after a release approval of the *current* impact by an approver who neither created it nor approved a plan in it. The approval goes stale on any change. Refusals are audited. Emergency access does not waive this approval. The Group deployment screen shows the impact and the release step. |
| RC16-T12 | Automatic onboarding could lock out shared/AAA accounts: every saved profile was tried over REST *and* SSH on every host, every run | A REST rejection is never retried over SSH with the same password (AP and switch paths). An account rejected `INM_ONBOARDING_MAX_AUTH_FAILURES` times (default 2) within `INM_ONBOARDING_AUTH_FAILURE_WINDOW_MINUTES` (default 30), on any host in any run, is held back (`AUTH_ATTEMPTS_CAPPED`). Each rejection is audited, and held accounts are shown on the AP fleet screen. |
| P3-01, INTEG-P3-03, GAP-T03 | Device syslog reached Loki but never the device timeline or the attribution engine (only an admin ingest endpoint fed them) | Worker **syslog bridge**. It reads Loki forward from a cursor in bounded pages (idempotent) and maps each event by the sender address Alloy records (new `source_ip` label). The event is stored on the device timeline. Configuration changes are attributed with the same engine. A hostname-only match is kept for the timeline but never attributed. Proven with real packets through the shipped Alloy pipeline (`test_10c`). |
| P4-08 | No logging-health alerting | `SYSLOG_SILENT`: a device that sends syslog has gone quiet longer than `INM_SYSLOG_SILENCE_MINUTES` (default 60). `AAA_EVIDENCE_GAP`: a direct change on a device that normally has AAA accounting was attributed UNKNOWN in the last `INM_AAA_GAP_WINDOW_HOURS` (24). Both resolve automatically. |
| P10-02 | Cisco configuration events not recognised | Parses Cisco IOS/IOS-XE `%FAC-SEV-MNEMONIC` events. `%SYS-5-CONFIG_I` is a configuration change with its technician, VERIFIED only with a matching named AAA session. Parser `syslog-norm-1.1`. |
| P10-03 | rc17 UniFi controller adapter had no test | New test through the collection pipeline. **Defect found and fixed**: adapter resolution matched only 3–4 digit model numbers, so no UniFi model (`U6-Pro`, `U7-Pro`) could ever select the UniFi adapter. |
| P11-DEP-03 | No old/new interoperation window | Expand migrations record `schema:compat` `{head, phase, compatible_readers}`. This release starts against a newer schema only inside a declared expand window and refuses on contract or when no marker exists. The previous release's ORM reads and writes a 0016 database (0016 is expand-only). |
| P3-08, P4-09, CXAPI-07, P5-DEP-01, P5-06, INTEG-P3-01, INTEG-P3-02, INTEG-P11-04, GAP-T15, P1-08, P6-DEP-01, PF-T03 | Missing tests, evidence or implementation (see `docs/REGISTER_REVIEW_1.3.0_RC18.md`) | Ingest saturation and poison event; emergency (break-glass) access; config-change vs metadata-only distinction; previous-release vault artifacts after upgrade; isolated restore; frozen experience fixtures; `inm migrate-legacy-samples`; site-scoped exports; AI isolation; demo network trace; update/rollback quiesce gate; year-in-header syslog through Alloy |

## Defects found by running the product (fixed)

| Defect | Where found |
|---|---|
| `recommendations.rule_version` VARCHAR(16), `notification_deliveries.kind` VARCHAR(12), `change_plans.source` VARCHAR(16) and `ml_training_runs.status` too short for real values. PostgreSQL refused predictive recommendations, printer-report notifications and optimizer rollback plans. SQLite had hidden this. | contract suite on PostgreSQL |
| PostgreSQL sessions returned non-UTC timestamps (now `-c timezone=UTC`) | contract suite on PostgreSQL |
| `deliver_due` left a failed delivery stuck in SENDING when enqueue and deliver ran in one session | integrations test |
| One malformed outbox envelope raised out of `relay()` and blocked every relay cycle | ingest saturation test |
| Signed exports ignored the requester's site scope | register review (INTEG-P11-04) |
| Updates and rollbacks did not check for in-flight changes | register review (P6-DEP-01) |
| Instant SSID creation could send a placeholder secret | sweep |
| Emulator transceiver output was not labelled EMULATED | sweep |
| Alloy year-as-host: correction labelled but not applied; year-after-time shape missed | real packets through Alloy |
| UniFi adapter could never be selected | new UniFi test |
| Onboarding could push shared accounts towards AAA lockout | register review (RC16-T12) |
| Device syslog never reached attribution in a deployment | register review (P3-01) |
| Signed exports (GAP-T14) existed in the API but no screen offered them. Executive overview now has signed inventory / audit-log / change-attribution exports (CSV + signature record) for admins and auditors | browser suite `test_live_operations` |
| Guided onboarding: a failed workflow load (for example HTTP 429) showed the start page and "No workflows yet". It now shows the error with Retry | browser suite `test_gui_workflow` |
| Guided onboarding: after "Start read-only monitoring (configuration pending)" the device states were not shown anywhere in the workflow. They are now shown with Poll again | browser suite `test_gui_workflow` |
| Two first requests on a fresh database both seeded the AI rule catalogue, and the loser answered HTTP 500 (seen as a failed model-training request). Seeding is now per rule in a savepoint | browser suite `test_integrations_ml_gui` → `test_rc18_rule_seed_race.py` |

Test infrastructure corrected while bringing every required suite to green. No assertion was weakened, and each
change follows a deliberate product behaviour:

- The runner started the next isolated stack before the previous one had exited (connection refused).
- The native integration tests restarted roles with the wrong interpreter.
- Browser selectors went stale after the rc17 navigation: breadcrumbs repeat the page name, Device 360 sections are
  ARIA tabs, and the monitoring button was renamed.
- Live mode refuses demo data unless explicitly opted in: `test_live_operations` now runs with the audited opt-in.
- Live dashboards never show DEMO devices: the test now asserts that.
- The 42- and 39-screen sweeps run as their own paced administrator, inside the per-user API limit (300/min). That
  limit is unchanged, and a request-limit notice now fails the sweep.
- The workflow poll waits for data to render before counting.
- A Loki-less stack's 503 "Logging service not configured" is accepted only after it is re-checked against the API.

## Whole-application sweep (section 5)

- Silent `except: pass` handlers now log or carry a reason.
- The concept/placeholder pages and the `_not_implemented` stubs were removed, along with dead workspace kinds.
- `scripts/audit_frontend_api.py`: every frontend API call maps to a registered backend route (0 missing).
- `scripts/audit_column_lengths.py` checks literal values against column lengths.

## Test infrastructure (section 4)

`scripts/run_full_test_suite.sh` is one command that provisions and runs the suites below, then writes
`evidence/test-run-<UTC>/TEST_EVIDENCE_REPORT.md`:

- unit and contract suites (SQLite and PostgreSQL)
- legacy suite
- frontend build and audits
- PostgreSQL migration and restore
- full distributed stack and upgrade/backup/restore
- seven-role native integration
- every browser suite on a fresh stack
- mixed load with 1,000 printers

The CI equivalent is `.github/workflows/full-test-suite.yml` (no Docker required). No integration test was removed.

## Schema 0016 (expand-only)

- ML training runs, drift reports, inferences and model registry columns
- API tokens
- per-channel integration configuration and encrypted secret
- the three widened columns
- the migration-window marker

Downgrade raises: restore the pre-update backup instead.
