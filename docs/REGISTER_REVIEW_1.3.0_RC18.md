# Acceptance register review — 1.3.0-rc18

Source: docs/Unified_Acceptance_Register_v4.csv (rc17, 281 rows). Output: docs/Unified_Acceptance_Register_v5.csv (303 rows, every rc17 ID preserved). Evidence: junit files in evidence/test-run-20261125T185616Z/ and, for the suites fixed after that run, the logged re-runs in evidence/test-run-20261125T185616Z/rerun-after-fixes/ (named "re-run after fix").

## Result

| Status | Rows |
|---|---|
| BLOCKED | 51 |
| NOT_RUN | 7 |
| PARTIAL | 64 |
| PASS | 181 |

## Review counts

| Item | Rows |
|---|---|
| cat1_PASS | 9 |
| cat3_PASS | 3 |
| cat4_PASS | 6 |
| cat5_BLOCKED | 22 |
| cat5_NOT_RUN | 4 |
| cat5_PARTIAL | 43 |
| cat6_BLOCKED | 29 |
| cat6_NOT_RUN | 2 |
| cat6_PARTIAL | 19 |
| cat7_PARTIAL | 1 |
| cat8_PARTIAL | 1 |
| pass_rechecked_PASS | 62 |
| rc18_PASS | 21 |

## Categories

1. Incomplete software implementation
2. Missing software integration
3. Missing automated tests
4. Missing developer test evidence
5. Physical hardware certification
6. External infrastructure validation
7. Genuinely unsupported vendor capability
8. Outside the approved project scope

## Every reviewed row

| ID | rc17 status | rc18 status | Category | Result / justification |
|---|---|---|---|---|
| P1-01 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P1-02 | PARTIAL | PARTIAL | 6 | Every role (loki, ai, worker, timescale, postgres, nats) stopped and recovered on the native stack. Docker container-health variant: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised nativel |
| P1-03 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-04 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-05 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-06 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-07 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-08 | PARTIAL | PASS | 4 | Socket-level network trace of a demo session (connect/sendto/getaddrinfo/create_connection): zero non-loopback destinations. Evidence: tests/contract/test_rc18_demo_network_trace.py [unit_contract_postgresql]: 1 passed, 0 failed, 0 skipped; |
| P1-09 | PARTIAL | PARTIAL | 6 | Native full stack: DB and API restart lose no workflow, telemetry or outbox state; container recreate on CentOS BLOCKED / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by  |
| P1-10 | PARTIAL | PARTIAL | 6 | pg_dump -Fc of the running stack restored into disposable databases with timescaledb_pre/post_restore: every table's row count and content checksum identical, live data untouched. JetStream/Loki volume restore via restore.sh on CentOS BLOCK |
| P1-11 | PARTIAL | PARTIAL | 6 | Unit, contract (SQLite and PostgreSQL), legacy, full-stack, integration and browser suites run by scripts/run_full_test_suite.sh (.github/workflows/full-test-suite.yml). Container image scanning needs the registry: requires clean CentOS Str |
| P1-12 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P2-01 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P2-02 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P2-04 | PARTIAL | PARTIAL | 5 | Bad credentials -> AUTH_REJECTED, secret never logged (mocked device); hardware BLOCKED / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification; software evidence tests/contract/test_re |
| P2-05 | PARTIAL | PARTIAL | 5 | Repeat import upserts identity without duplicates (software); hardware re-discovery BLOCKED / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P2-06 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) (Instant cluster) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P2-07 | PARTIAL | PARTIAL | 5 | Capture-from-device + site assignment proven on EMULATED devices; hardware BLOCKED / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P3-01 | BLOCKED | BLOCKED | 5 | Integration gap found and completed in rc18 (category 2): device syslog reached Loki but never the device timeline or attribution (only the admin ingest endpoint fed them). The worker syslog bridge now maps Loki events by the Alloy-recorded |
| P3-02 | PARTIAL | PARTIAL | 5 | Collection pipeline proven on real CX code path with emulated socket; hardware BLOCKED / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P3-03 | PARTIAL | PARTIAL | 5 | Unreachable -> OFFLINE/STALE with last-seen preserved, identical in collector, NOC, Device 360 and alerts; recovery on reconnect (emulated + native stack); hardware half requires lab AP-505/AP-535/CX 6100/CX 6200 (not available) / rc18: req |
| P3-06 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) (an hour of real metrics) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P3-08 | NOT_RUN | PASS | 3 | 3,000 bounded synthetic events + duplicate burst + poison event: <=200 per relay cycle, drained exactly once, poison dead-lettered after MAX_ATTEMPTS, flat per-cycle memory. Defect found and fixed: a malformed outbox envelope raised out of  |
| P4-01 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P4-02 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) + AAA server / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P4-03 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P4-06 | PARTIAL | PARTIAL | 5 | Platform executions attributed APPLICATION (emulated device) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P4-08 | BLOCKED | BLOCKED | 6 | Completed in rc18 (category 1): SYSLOG_SILENT alert when a device that sends syslog stops, AAA_EVIDENCE_GAP when a direct change on a device that normally has AAA accounting is UNKNOWN; both auto-resolve; attribution never asserts an identi |
| P4-09 | NOT_RUN | PASS | 1 | Emergency (break-glass) access implemented as a distinct workflow: time-boxed, reason + ticket, critical notification, waives only the two-person rule for explicitly flagged approvals, governance chain still runs, linked audit trail, mandat |
| P5-01 | PARTIAL | PARTIAL | 5 | AP and CX full-config capture proven on EMULATED devices; hardware BLOCKED / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P5-02 | PARTIAL | PARTIAL | 5 | New version only on change; external modification -> drift (emulated) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P5-05 | PARTIAL | PARTIAL | 5 | Device read fails (HTTP 500): schedule shows FAILED <category>, audit records it, nothing partial is sealed, last good snapshot still listed and integrity PASS (emulated); hardware half requires lab AP-505/AP-535/CX 6100/CX 6200 (not availa |
| P5-06 | BLOCKED | PASS | 4 | Management DB + vault objects restored to an isolated second instance: all artifacts verify with the escrowed key; tampering in the copy is detected there only; a different key cannot open the copy. Evidence: tests/contract/test_rc18_vault_ |
| P6-03 | PARTIAL | PARTIAL | 5 | APPLIED_VERIFIED on EMULATED CX via browser; hardware BLOCKED / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification; software evidence tests/contract/test_production_read_write.py [un |
| P6-04 | PARTIAL | PARTIAL | 5 | Gate requires verified pre-change backup; failure injection on emulator / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification; software evidence tests/contract/test_production_read_wr |
| P6-06 | PARTIAL | PARTIAL | 5 | Defect found and fixed in rc2: execution compared the approved baseline only with the newest vault artifact. Now the live pre-change backup is compared (raw, then normalized); an out-of-band edit stops execution with no write, returns the p |
| P6-07 | PARTIAL | PARTIAL | 5 | Process death mid-change recovered from the durable journal (switch and AP, emulated). requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification Evidence: tests/contract/test_remediation_write_li |
| P6-08 | PARTIAL | PARTIAL | 5 | Rollback proven by full-config comparison (emulated) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification; software evidence tests/contract/test_production_read_write.py [unit_contra |
| P6-10 | PARTIAL | PARTIAL | 5 | rc17 said 'cluster-scope impact view and stronger approval policy NOT implemented' (category 1) - completed in rc18: impact view (devices, sites, every Instant cluster member reached through the VC, operations, digest); HIGH risk (cluster-s |
| P7-01 | PARTIAL | PARTIAL | 5 | Replacement + restore plan on EMULATED devices / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P7-03 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P7-05 | PARTIAL | PARTIAL | 5 | config.restore certified + executed on emulator; hardware BLOCKED / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification; software evidence tests/contract/test_production_read_write.py |
| P7-06 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P7-07 | PARTIAL | PARTIAL | 5 | Post-restore backup with provenance (emulated) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P7-08 | PARTIAL | PARTIAL | 7 | Aruba Instant offers no complete automatic configuration restore; the typed reconciliation (baseline vs running -> governed plan) and guided manual recovery are the supported paths. Evidence: tests/contract/test_rc17_typed_restore.py [unit_ |
| P8-02 | BLOCKED | BLOCKED | 6 | requires real telemetry history / rc18: requires real fleet telemetry history (no production telemetry is available to the build) |
| P8-03 | PARTIAL | PASS | 1 | Trained breach-risk model with temporal train/validation/holdout split and horizon embargo, holdout metrics vs base rate and the statistical engine (fixture and synthetic data; real fleet: NI-T12). Evidence: tests/contract/test_rc18_ml_pipe |
| P8-04 | PARTIAL | PASS | 1 | GET /api/v1/ai/ml/compare evaluates every version on the same most-recent observations, with the statistical engine and base rate. Evidence: tests/contract/test_rc18_ml_pipeline.py [unit_contract_postgresql]: 9 passed, 0 failed, 0 skipped;  |
| P8-08 | PARTIAL | PASS | 4 | Scheduled job scores and issues on one instant (deterministic clock); the worker role runs the scheduled jobs on the full stack (PostgreSQL). Evidence: tests/contract/test_rc18_predictive_clock.py [unit_contract_postgresql]: 6 passed, 0 fai |
| P9-03 | PARTIAL | PARTIAL | 5 | Approved DEVICE_OPT_IN change APPLIED_VERIFIED on EMULATED AP; hardware BLOCKED / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P9-04 | PARTIAL | PARTIAL | 5 | Degradation -> rollback (optimizer outcome check, shadow sim) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| P10-01 | BLOCKED | BLOCKED | 5 | rc17 text 'adapter is placeholder' is superseded: the read-only Cisco IOS-XE SNMP adapter (IF/ENTITY/LLDP MIBs, labelled vendor CPU, write operations UNSUPPORTED) runs through the collection pipeline against an emulated agent. Remaining: a  |
| P10-02 | BLOCKED | BLOCKED | 5 | Completed in rc18 (category 1): Cisco IOS %FAC-SEV-MNEMONIC parsing, %SYS-5-CONFIG_I recognised as a configuration change with its technician, attributed VERIFIED only with a matching named AAA session. Remaining: a real Cisco syslog/AAA so |
| P10-03 | BLOCKED | BLOCKED | 6 | Category 3 completed in rc18: the rc17 UniFi controller adapter had no test; the new test found a defect (no UniFi model name could select the adapter - fixed). Read-only controller collection, AUTH_REJECTED without retry storm, missing ser |
| P10-06 | PARTIAL | PARTIAL | 8 | Compatibility gates are vendor-keyed and tested; configuration restore for non-Aruba vendors is outside the approved scope (Cisco/UniFi/generic adapters are read-only by design). Evidence: tests/contract/test_capability_completion.py [unit_ |
| P11-01 | BLOCKED | BLOCKED | 6 | requires multi-VM production-like environment / rc18: requires a multi-VM, production-like environment (separate VMs per role); not available to the build |
| P11-02 | BLOCKED | BLOCKED | 6 | requires multi-VM production-like environment / rc18: requires a multi-VM, production-like environment (separate VMs per role); not available to the build |
| P11-03 | BLOCKED | BLOCKED | 6 | requires multi-VM production-like environment / rc18: requires a multi-VM, production-like environment (separate VMs per role); not available to the build |
| P11-04 | BLOCKED | BLOCKED | 6 | requires multi-VM production-like environment / rc18: requires a multi-VM, production-like environment (separate VMs per role); not available to the build |
| P11-05 | BLOCKED | BLOCKED | 6 | requires independent security/penetration review / rc18: requires an independent security / penetration review by a party other than the developer |
| P11-06 | BLOCKED | BLOCKED | 6 | requires multi-VM production-like environment / rc18: requires a multi-VM, production-like environment (separate VMs per role); not available to the build |
| P11-07 | BLOCKED | BLOCKED | 6 | requires multi-VM production-like environment / rc18: requires a multi-VM, production-like environment (separate VMs per role); not available to the build |
| P11-08 | BLOCKED | BLOCKED | 6 | requires multi-VM production-like environment / rc18: requires a multi-VM, production-like environment (separate VMs per role); not available to the build |
| P11-09 | BLOCKED | BLOCKED | 6 | requires multi-VM production-like environment / rc18: requires a multi-VM, production-like environment (separate VMs per role); not available to the build |
| AAPI-01 | PARTIAL | PARTIAL | 5 | REST-disabled distinguished from bad credentials (mocked) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| AAPI-02 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) (Instant cluster) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| AAPI-04 | PARTIAL | PARTIAL | 5 | Device rejection recorded safely; no false green: run never SUCCEEDED unless the persisted count is verified (emulated); hardware half requires lab AP-505/AP-535/CX 6100/CX 6200 (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX  |
| AAPI-06 | PARTIAL | PARTIAL | 5 | Parsers tested on reconstructed fixtures; real 8.10 captures BLOCKED / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| CXAPI-01 | PARTIAL | PARTIAL | 5 | Version negotiated per firmware (v10.14 preferred, GET /rest fallback, 10.09-10.15 accepted, 10.08 refused UNSUPPORTED_API) against the strict emulator; hardware half requires lab AP-505/AP-535/CX 6100/CX 6200 (not available) / rc18: requir |
| CXAPI-02 | PARTIAL | PARTIAL | 5 | Every port of 24- and 48-port switches stored and retrievable with its interface identity (strict emulator + native TimescaleDB); hardware half requires lab AP-505/AP-535/CX 6100/CX 6200 (not available) / rc18: requires lab AP-505/AP-535/CX |
| CXAPI-04 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| CXAPI-05 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) + AAA / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| CXAPI-06 | PARTIAL | PARTIAL | 5 | /fullconfigs checkpoint/backup/restore on emulator / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| CXAPI-07 | NOT_RUN | PARTIAL | 5 | Software complete (rc18): snapshots with identical normalised content but different metadata / representation are UNCHANGED, real (incl. order-only) changes are drift. Which metadata a real CX adds to /fullconfigs must be confirmed on hardw |
| CXAPI-08 | PARTIAL | PARTIAL | 5 | Template deploy plan converges (emulated) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| CXAPI-10 | PARTIAL | PARTIAL | 5 | Emulated certification campaign CX 6100 24G / CX 6200F 48G: every op backup/checkpoint/apply/readback/rollback + full restore matches baseline (SOFTWARE PASS ONLY); hardware half requires lab AP-505/AP-535/CX 6100/CX 6200 (not available) /  |
| SEC-01 | PARTIAL | PARTIAL | 6 | Native seven-role run: unauthenticated / wrong-password NATS and DB refused, least-privilege bus identities; rc18 API tokens are read-only. Docker network isolation check: requires clean CentOS Stream 9 VM(s) with container registry access; |
| SEC-02 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| SEC-04 | BLOCKED | PARTIAL | 6 | Telemetry DB stopped/restarted on the native stack: detected, buffered, recovered without duplicates. Separate-VM telemetry DB: requires a multi-VM, production-like environment (separate VMs per role); not available to the build Evidence: t |
| P1-DEP-01 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P1-DEP-02 | PARTIAL | PARTIAL | 6 | rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role integration runs |
| P1-DEP-03 | PARTIAL | PARTIAL | 6 | rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role integration runs |
| P1-DEP-04 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-DEP-05 | PARTIAL | PARTIAL | 6 | rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role integration runs |
| P1-DEP-06 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-DEP-07 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-DEP-08 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-DEP-09 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-DEP-10 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-DEP-11 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-DEP-12 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| P1-DEP-13 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P1-DEP-14 | PARTIAL | PARTIAL | 6 | rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role integration runs |
| P1-DEP-15 | PARTIAL | PASS | 4 | Network trace evidence added: a demo session attempts no connection to any device or external host. Evidence: tests/contract/test_rc18_demo_network_trace.py [unit_contract_postgresql]: 1 passed, 0 failed, 0 skipped; tests/contract/test_rc18 |
| P2-DEP-01 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P4-DEP-01 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P5-DEP-01 | NOT_RUN | PASS | 4 | Artifacts sealed by the previous release's code (rc17 f6f44ce, schema 0015) verify after the 0016 upgrade; a record of a newer format version is refused instead of guessed at. Evidence: tests/contract/test_rc18_vault_upgrade_and_isolated_re |
| P6-DEP-01 | PARTIAL | PARTIAL | 6 | Implemented (rc18): update.sh and rollback.sh refuse while a change executes or awaits recovery (`inm change-quiesce-check`); approved-but-not-started plans are re-validated at execution. Remaining: run on the CentOS target (requires clean  |
| P8-DEP-01 | NOT_RUN | PASS | 1 | One-click model rollout = independent promotion of a versioned candidate (synthetic models refused on live installations), with one-click rollback; scheduled retraining never auto-promotes. Evidence: tests/contract/test_rc18_ml_pipeline.py  |
| P11-DEP-01 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P11-DEP-02 | PARTIAL | PARTIAL | 6 | Unknown major schema refused; cross-VM upgrade BLOCKED / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role integration runs |
| P11-DEP-03 | PARTIAL | PARTIAL | 6 | Completed in rc18 (category 1/3): migrations record a schema:compat window marker; 0016 is expand-only and the previous release's ORM reads/writes a 0016 database; this release runs against a newer expand schema only when the marker lists i |
| P11-DEP-04 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P11-DEP-05 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P11-DEP-06 | PARTIAL | PARTIAL | 6 | Implemented (rc18): install/update/rollback/offline install verify the Ed25519-signed release before using any of its content; trust-store rotation/revocation. Remaining: signed container image digests need the registry (requires clean Cent |
| P11-DEP-07 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P11-DEP-08 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P11-DEP-09 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| P11-DEP-10 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| INTEG-P1-01 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| INTEG-P1-02 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| INTEG-P1-03 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| INTEG-P1-04 | PARTIAL | PARTIAL | 6 | rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role integration runs |
| INTEG-P1-05 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| INTEG-P1-06 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| INTEG-P1-07 | PARTIAL | PARTIAL | 6 | rc18: requires an Ollama runtime with a pulled model on the isolated AI network |
| INTEG-P1-08 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| INTEG-P1-09 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| INTEG-P1-10 | PARTIAL | PARTIAL | 6 | rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role integration runs |
| INTEG-P1-11 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| INTEG-P1-12 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| INTEG-P2-01 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P2-02 | BLOCKED | BLOCKED | 5 | requires real Instant 8.10 captures / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P2-03 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P2-04 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P2-05 | PARTIAL | PARTIAL | 5 | Site reassignment (software) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P3-01 | NOT_RUN | PASS | 3 | Frozen 60-row historical fixture recomputed and compared row by row with an independent implementation of docs/SCORING.md (client, AP, site; coverage and missing-input rules). Evidence: tests/contract/test_rc18_experience_frozen_fixtures.py |
| INTEG-P3-02 | NOT_RUN | PASS | 1 | Implemented `inm migrate-legacy-samples`: SIMS ap_samples -> telemetry in UTC with provenance tags, idempotent, unmapped APs reported, simulated rows never attached to REAL devices; run on SQLite and PostgreSQL. Evidence: tests/contract/tes |
| INTEG-P3-03 | BLOCKED | BLOCKED | 5 | Switch-format syslog through the shipped Alloy -> Loki pipeline, searchable by device and correlated to the device and change (full stack, emulated sender address). Remaining: a real CX switch. Evidence: tests/fullstack/test_fullstack.py [f |
| INTEG-P4-01 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P4-02 | PARTIAL | PARTIAL | 5 | Attribution levels incl. UNKNOWN on missing AAA / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P5-01 | PARTIAL | PARTIAL | 5 | Instant config encrypted in vault (emulated) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P5-04 | PARTIAL | PARTIAL | 5 | Scheduled + pre-change + post-change backups (emulated) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P6-01 | PARTIAL | PARTIAL | 5 | SIMS SSID builders behind execution engine (emulated) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P6-04 | PARTIAL | PARTIAL | 5 | VLAN change with snapshot/readback on emulator + browser / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P7-02 | PARTIAL | PARTIAL | 5 | Recovery to certified emulated device / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P8-01 | PARTIAL | PASS | 1 | As P8-03. Evidence: tests/contract/test_rc18_ml_pipeline.py [unit_contract_postgresql]: 9 passed, 0 failed, 0 skipped; tests/contract/test_rc18_ml_pipeline.py [unit_contract_sqlite]: 9 passed, 0 failed, 0 skipped |
| INTEG-P8-04 | PARTIAL | PASS | 1 | Scheduled retraining creates candidates only; promotion needs an independent approver and a holdout better than the baseline; synthetic models cannot be promoted on live installations. Evidence: tests/contract/test_rc18_ml_pipeline.py [unit |
| INTEG-P9-02 | PARTIAL | PARTIAL | 5 | Degradation stops/rolls back (shadow + emulated) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| INTEG-P11-01 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| INTEG-P11-02 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| INTEG-P11-03 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role inte |
| INTEG-P11-04 | NOT_RUN | PASS | 1 | Defect fixed: signed exports were always all-sites. A site-scoped auditor now receives only the granted sites' devices and audit rows, on data written by the previous release and upgraded. Evidence: tests/contract/test_rc18_vault_upgrade_an |
| GAP-T01 | BLOCKED | BLOCKED | 5 | requires lab AP-505/AP-535/CX 6100/CX 6200 hardware (not available) / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| GAP-T02 | PARTIAL | PARTIAL | 5 | rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification; software evidence tests/contract/test_capability_completion.py [unit_contract_postgresql]: 13 passed, 0 failed, 0 skipped; tests/c |
| GAP-T03 | PARTIAL | PARTIAL | 5 | Config change without AAA -> UNKNOWN with raw evidence, now also through the production syslog path (rc18 bridge) and raising AAA_EVIDENCE_GAP. Remaining: a console change on a real device. Evidence: tests/contract/test_capability_completio |
| GAP-T04 | PARTIAL | PARTIAL | 5 | rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification; software evidence tests/contract/test_capability_completion.py [unit_contract_postgresql]: 13 passed, 0 failed, 0 skipped; tests/c |
| GAP-T05 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T06 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T07 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T08 | PARTIAL | PARTIAL | 5 | rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification; software evidence tests/contract/test_capability_completion.py [unit_contract_postgresql]: 13 passed, 0 failed, 0 skipped; tests/c |
| GAP-T09 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T10 | PARTIAL | PASS | 1 | As P8-03 / INTEG-P8-04. Evidence: tests/contract/test_rc18_ml_pipeline.py [unit_contract_postgresql]: 9 passed, 0 failed, 0 skipped; tests/contract/test_rc18_ml_pipeline.py [unit_contract_sqlite]: 9 passed, 0 failed, 0 skipped |
| GAP-T11 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T13 | BLOCKED | BLOCKED | 6 | requires clean CentOS Stream 9 VM(s) with Docker registry access (not available) / rc18: requires a multi-VM, production-like environment (separate VMs per role); not available to the build |
| GAP-T14 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T15 | NOT_RUN | PASS | 3 | Model context and tool outputs carry no device/client address, identity, secret or DB/bus URL; the AI service refuses platform credentials and has no configuration surface; compose places it on the internal `ai` network only; AI drafts need |
| GAP-T16 | PARTIAL | PARTIAL | 5 | rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification; software evidence tests/contract/test_printer_srs.py [unit_contract_postgresql]: 15 passed, 0 failed, 0 skipped; tests/contract/te |
| GAP-T17 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T18 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T19 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T20 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T21 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T22 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T23 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| GAP-T24 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T01 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T02 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T03 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T04 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T05 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T06 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T07 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T08 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T09 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T10 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T11 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T12 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T13 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T15 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PRN-T16 | NOT_RUN | NOT_RUN | 5 | Hardware not available to build agent / rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| PRN-T17 | NOT_RUN | PASS | 4 | Mixed-load run on PostgreSQL 16 + TimescaleDB 2.22.1: 1,000 emulated printers seeded and polled by a separate worker process while 25 users ran the mixed API load for 120 s (1,432 requests, 0 errors, p95 352 ms; 1,283 printer polls in the s |
| NI-T01 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T02 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T03 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T04 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T05 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T06 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T07 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T08 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T09 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T10 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| NI-T11 | NOT_RUN | PARTIAL | 6 | Worker/API/DB restart and reconciliation proven on the native full stack. CentOS stack: requires clean CentOS Stream 9 VM(s) with container registry access; the same software is exercised natively by the rc18 full-stack and seven-role integ |
| NI-T12 | NOT_RUN | NOT_RUN | 6 | rc18: requires >= 7 days of REAL fleet telemetry to measure predictive skill |
| PF-T01 | PASS(no result) | PASS | - | PASS re-proven with rc18 execution evidence |
| PF-T02 | NOT_RUN | NOT_RUN | 5 | rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| PF-T03 | NOT_RUN | PARTIAL | 5 | Real packets through Alloy found two defects (fixed): the year-as-host correction was labelled but not applied, and the resolver missed the year-after-time shape. Year-before-time headers are rejected by the Alloy RFC 3164 parser and now co |
| RC16-T02 | NOT_RUN | NOT_RUN | 5 | rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| RC16-T05 | NOT_RUN | NOT_RUN | 5 | rc18: requires lab AP-505/AP-535/CX 6100/CX 6200 hardware; emulator evidence is never hardware certification |
| RC16-T11 | NOT_RUN | NOT_RUN | 6 | rc18: requires the CentOS target VM for a 24 h capacity run with real SNMP/REST latency |
| RC16-T12 | NOT_RUN | PARTIAL | 6 | Software gap found and completed in rc18 (category 1): saved profiles could each fail twice per host (REST then SSH with the same password) on every host and run. Now a REST rejection is never retried over SSH (AP and switch paths) and an a |
