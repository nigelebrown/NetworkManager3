# NetworkManager3 1.3.0-rc16 — feature acceptance matrix

**Scope of this evidence:** software only, produced in an isolated Linux workspace (Python 3.13, Node 22, Chrome,
PostgreSQL 16 without TimescaleDB, no Docker, no NATS/Loki, no physical devices, no access to the live server).
Results are from runs in this workspace on the rc16 source tree; see `docs/TEST_RESULTS_1.3.0_RC16.md` for commands.

## Status vocabulary

| Status | Meaning |
|---|---|
| **PASS** | Software behaviour demonstrated by an automated test, simulator run, browser run or measured harness in this workspace. Emulator/simulator evidence only — never hardware certification. |
| **FAIL** | Required software behaviour is missing or incomplete. Listed as a remaining software gap. |
| **UNSUPPORTED** | Deliberately not implemented; the product returns an explicit unsupported / NOT_IMPLEMENTED result. |
| **REQUIRES HARDWARE VALIDATION** | Software path is implemented and simulator-tested, but correctness depends on real vendor/model/firmware behaviour. See `docs/HARDWARE_VALIDATION_CHECKLIST_1.3.0_RC16.md`. |
| **BLOCKED (environment)** | Needs the CentOS/Docker deployment, TimescaleDB, NATS/Loki or production infrastructure not available here. Not hardware, not a code verdict. |
| **NOT VERIFIED** | Not re-audited in this session and no failing evidence found. Carries rc15's status forward; must not be read as PASS. |

**Audit depth:** **D** = traced UI→API→worker→DB→UI and tests added in rc16 · **R** = existing automated tests re-run and
passing, code reviewed at the relevant points but not exhaustively re-traced · **C** = claim checked against code only.

Most requirements have both a software row and a hardware row: a PASS row covers the software path, and the hardware
row stays **REQUIRES HARDWARE VALIDATION** until signed lab evidence exists.

## Summary by requested category

| Category | Items |
|---|---|
| Implemented and tested (software) | Port monitoring groups; CSV mapping/partial import/rejected export; SNMPv1/v2c/v3 (all USM protocols) discovery workflow; partial printer readings and fault preservation; counter-rate safety; alert hysteresis/dedupe/maintenance/recovery; durable bounded discovery jobs; credential profiles/assignment/testing/rotation; device state model; ARM evidence-based recommendations; change governance (approval, backup, lease, readback, rollback, no blind replay); hardware/emulator certification separation; migrations 0004→0014 and dump/restore on PostgreSQL 16; printer list performance under load. |
| Partially implemented | Group assignment from imported "Group" column (reference text only); firmware authenticity (byte integrity, no vendor signature); firmware orchestration (AP stage-only; no CX transport); enterprise WLAN lifecycle; CX 802.1X/MAB; device-log retention; printer configuration (sysLocation only); interference/roaming analytics. |
| Unsupported | VSF stack provisioning; device-initiated ping/traceroute/cable test/packet capture; Cisco/UniFi/generic SNMPv3 switch adapters; Instant automatic full-config restore (guided only); printer firmware, print-job and document inspection, purchase orders; 80 MHz change plans; automatic optimization without per-device enrollment. |
| Requires physical hardware validation | Every read/write against AP-505/AP-535/CX 6100/CX 6200 and each printer model; PoE units; CX AnyCLI command availability; ARM output formats; printer vendor OIDs; SNMPv3 protocol interoperability; device-account lockout behaviour. |

## 1. Device discovery and onboarding

| ID | Requirement | Status | Depth | Evidence / note |
|---|---|---|---|---|
| ON-01 | Discover switches/APs through supported protocols (Instant REST/SSH, AOS-CX REST) | PASS | R | `tests/unit/test_automatic_onboarding.py`, `tests/contract/test_guided_onboarding.py`, `test_remediation_discovery_jobs.py`, CX emulator |
| ON-01H | … on physical AP-505/535, CX 6100/6200 | REQUIRES HARDWARE VALIDATION | — | Checklist HW-AP-01, HW-CX-01 |
| ON-02 | Select multiple saved credential profiles with checkboxes | PASS | R | Printer discovery (`PrinterSuite.tsx` DiscoveryJobs), AP Fleet automatic onboarding policy; `test_printer_large_discovery.py` (selected-profile fallback) |
| ON-03 | Printer SNMP v1, v2c and v3 in one discovery workflow | PASS | D | One job tries selected profiles of any version; v1 disabled by default policy (admin-enabled), v2c allowed with warning; v3 now supports SHA/SHA-2 family/MD5 × AES128/192/256/3DES/DES and authNoPriv. `test_printer_srs.py`, `test_printer_intelligence.py::test_snmpv3_profile_*`, `test_printer_srs_units.py::test_snmpv3_*`, browser `test_printer_snmpv3_profile_protocol_selection` |
| ON-03H | SNMPv3 interoperability per printer model/firmware | REQUIRES HARDWARE VALIDATION | — | HW-PRN-01 |
| ON-04 | CSV upload, column mapping, preview, partial import, export of rejected rows | PASS | D | `BulkDeviceImport.tsx` mapping + row issues; `POST /api/v1/quick-devices/bulk` `mode=partial`; `test_bulk_device_import.py::test_partial_mode_*`; browser `test_csv_import_column_mapping_partial_and_rejected_export` (5-row file: 3 imported, 2 rejected/exported, one row fixed in place) |
| ON-04P | Printer CSV import (preview/validate/dedupe/commit) | PASS | R | `test_printer_srs.py` |
| ON-05a | Site assignment after discovery/import | PASS | R | Site column/inline edit at import; `PATCH /api/v1/quick-devices/{id}/site`; printer `PATCH /api/printers/{id}/location` |
| ON-05b | Group assignment after import | **FAIL** | C | The imported "Group" column is kept as reference text only; it is not mapped to a configuration-template group or monitoring group. Assignment exists separately (templates, port groups) but not as a post-import bulk action. |
| ON-06 | Bulk credential assignment, connection testing, retry, per-device results | PASS | R | `POST /api/v1/onboarding/credentials/assign`, `/credentials/{id}/test`, `/credentials/{id}/devices`; `test_guided_onboarding.py`, `test_quick_device_intake.py` |
| ON-07 | Automatic onboarding with selected saved credentials (leased worker queue, cursor recovery, first-use host-key pinning, changed keys blocked) | PASS | R | `test_automatic_onboarding.py` (12 tests), `test_wireless_operations.py` |
| ON-07H | Device account lockout risk when several profiles are tried | REQUIRES HARDWARE VALIDATION | C | Attempts are bounded by the selected profile list (≤ 20 per printer job); RADIUS/TACACS lockout thresholds on real AAA are unknown. HW-AAA-01 |
| ON-08 | Large ranges: durable jobs, bounded concurrency, batching, cancellation, resume; API not blocked | PASS | R | Printer discovery /9–/32 with lazy cursor, concurrency ≤ 64, batch ≤ 128, pause/resume across worker replacement: `test_printer_large_discovery.py`; onboarding discoveries `control/{action}`. No broad scan was launched in this session. |

## 2. Inventory and monitoring consistency

| ID | Requirement | Status | Depth | Evidence / note |
|---|---|---|---|---|
| INV-01 | No duplicate device records (serial case-insensitive; controller entry preferred) | PASS | R | `test_port_monitoring_dashboard.py::test_inventory_prefers_controller_entry…`, `test_bulk_device_import.py::test_repeat_import_updates…`, partial-mode duplicate rejection |
| INV-02 | Classification: family collisions rejected | PASS | D | 409 (atomic) / per-row rejection (partial) on serial/family conflict |
| INV-03 | One state model across inventory, AP Fleet, Device 360, NOC | PASS | R | `domain/collection.operational_state()`; `test_monitoring_pipeline.py`, `test_remediation_data_plane.py` |
| INV-03T | Topology reconciliation with the same state model | NOT VERIFIED | — | Not re-traced this session |
| INV-04 | Distinguish online / offline / unknown / stale / failed collection | PASS | R+D | `device_state.state_of` tests; port groups add `Stale`/`No data`/`Unsupported` (D) |
| INV-05 | Missing telemetry never shown healthy; no fabricated measurements | PASS | D | Port groups (`evaluate`), `port_rates` missing≠0, printer special values, health `INSUFFICIENT_DATA`; `test_alert_hysteresis.py`, `test_port_monitoring_groups.py`, `test_printer_srs_units.py` |
| INV-06 | Preserve history, credentials, site assignments, audit evidence across upgrade/restore | PASS | D | `scripts/validate_pg_migration_restore.py` on PostgreSQL 16: records identical after `pg_dump`/`pg_restore`, audit chain verifies; port-group delete keeps telemetry |
| INV-06T | Same for TimescaleDB telemetry and the encrypted installer backup | BLOCKED (environment) | — | Needs CentOS host: `deployment/centos9/backup.sh` + `restore.sh --target test` |

## 3. Switch port monitoring

| ID | Requirement | Status | Depth | Evidence / note |
|---|---|---|---|---|
| PORT-01 | Choose specific ports (multi-select); only group ports get per-port series once a switch has members | PASS | D | `test_port_monitoring_groups.py`, browser test |
| PORT-02 | Named groups across locations (e.g. "AP Uplink", "BWC Dock"), unique names, delete | PASS | D | same |
| PORT-03 | Group dashboard with location-level anomalies and historical graphs | PASS | D | `locations[]` roll-up; history ≤ 30 days, ascending, no interpolation |
| PORT-04 | Link, errors, link resets, utilisation, rx/tx, PoE measurements | PASS | D | CX emulator path |
| PORT-05 | Unsupported measurements explicit | PASS | D | PoE threshold on a port without PoE status → `Unsupported` |
| PORT-06 | Viewer access with site isolation | PASS | D | scoped viewer sees no members on ungranted sites; history 404 |
| PORT-H | PoE units, interface statistics keys, speed field on real CX firmware | REQUIRES HARDWARE VALIDATION | C | HW-CX-03/04, assumption A-CX-02/03 |
| PORT-DOM | Transceiver DOM | REQUIRES HARDWARE VALIDATION | R | Opt-in `INM_CX_OPTICAL_DOM`; single-channel table format only |

## 4. Aruba ARM and wireless optimization

| ID | Requirement | Status | Depth | Evidence / note |
|---|---|---|---|---|
| ARM-01 | Work with native ARM; healthy ARM → keep native control | PASS | R | `aruba/arm.py` `arm_healthy_*`; `test_arm_workflow.py`, `test_arm_collection_recovery.py` |
| ARM-02 | Fresh per-AP measurements, client experience, sustained evidence (≥5 samples, ≥60 %) | PASS | R/C | `assessments()`; single snapshots never propose changes |
| ARM-03 | No false positives on healthy radios; static override → reviewed removal only | PASS | R | same |
| ARM-04 | No AP spacing inferred from cluster membership / BSSID counts | PASS | C | Diagnosis text and confidence downgrade in `_arm_diagnosis` |
| ARM-05 | Commissioners Office = initial comparison reference; capability ≠ operating width; allowed channels/power bounds/controls separated | PASS | C | `_arm_baseline_comparison` (`automatic_application: False`); 80 MHz finding is `info` |
| ARM-06 | Recommendations with evidence, confidence, prerequisites, verification | PASS | R | findings carry `why/actions/evidence/confidence`; proposals require ≥ 900 s since last ARM change |
| ARM-07 | AI grounded in measured facts; cannot bypass change control | PASS | R | AI receives compact facts, advisory only; `test_ai_service_isolation.py`, `test_arm_ai_limits.py` |
| ARM-08 | 80 MHz change plans | UNSUPPORTED | C | Policy: 20/40 MHz only (owner rule) |
| ARM-H | ARM `show` output formats per Instant firmware; channel/power writes | REQUIRES HARDWARE VALIDATION | — | HW-AP-03/04 |

## 5. Printers

| ID | Requirement | Status | Depth | Evidence / note |
|---|---|---|---|---|
| PRN-01 | Discovery, onboarding, credential selection, polling, dashboards | PASS | R | `test_printer_srs.py`, `test_printer_gui.py` (rc15), `test_printer_large_discovery.py` |
| PRN-02 | Retain valid partial SNMP readings when another measurement times out | PASS | D | `test_one_metric_timeout_keeps_other_valid_readings`, `test_poll_retains_counters_when_later_metrics_exceed_time_budget` |
| PRN-03 | Preserve existing faults when fault collection is incomplete (timeout **or truncated table**) | PASS | D | `test_truncated_alert_table_keeps_rows_but_never_clears_faults` (fixed in rc16) |
| PRN-04 | Configuration workflow with preview, readback, rollback (sysLocation) | PASS | R | `test_printer_configuration.py` (serial check before SET, no SET retry, compensating rollback, recovery record) |
| PRN-04H | Printer SNMP SET on real models | REQUIRES HARDWARE VALIDATION | — | gated by recorded hardware write+rollback evidence per printer; HW-PRN-04 |
| PRN-05 | Other printer configuration, firmware, job interception, document inspection, purchase orders | UNSUPPORTED | C | Need separate authorisation (TODO.md) |
| PRN-06 | Universal vendor/model support | UNSUPPORTED | C | Vendor OIDs ship `VENDOR_UNVERIFIED`; MIB upload maps others |
| PRN-07 | List/overview performance at 500 printers under 25 users | PASS | D | p95 7.26 s → 0.39 s (`evidence/load/`) |
| PRN-08 | Raw-sample storage within the single-VM budget | **FAIL** (decision needed) | D | Measured ≈ 12.4 KB per poll (≈ 32 samples). Upper bound at 900 s × 1,000 printers × 365-day raw retention ≈ 1.2 GB/day, ≈ 435 GB/yr in the management DB. Requires an owner retention decision and/or moving samples to TimescaleDB (PRN-T17). |

## 6. Configuration and recovery

| ID | Requirement | Status | Depth | Evidence / note |
|---|---|---|---|---|
| CFG-01 | Validate identity (serial, model family, exact firmware build) and management authority before writes | PASS | R | `change_executor` IDENTITY_*/MANAGED_BY_*; `test_production_read_write.py`, `test_independent_safety_regressions.py` |
| CFG-02 | Hardware/emulator certification separation | PASS | R+D | schema 0014 constraint; PostgreSQL migration check (legacy evidence kept, HARDWARE+EMULATED both allowed, duplicates rejected) |
| CFG-03 | Approval, backup, execution lease, readback, audit, recovery | PASS | R | `test_remediation_write_lifecycle.py`, `test_emulated_certification_campaign.py` |
| CFG-04 | Emulator success never treated as hardware certification | PASS | R | lab-serial allowlist; `test_emulated_certification_campaign.py` |
| CFG-05 | Never replay a configuration write after a timeout or auth expiry | PASS | R/C | CX: non-GET never re-sent after 401/CSRF; transport error → `CONNECTION_LOST` → reconcile/rollback; printer SET `retries=0`; `test_remediation_cx_protocol.py` |
| CFG-06 | Unsupported operations return clear actionable results | PASS | R | `/api/v1/system/features` NOT_IMPLEMENTED registry; 501 helpers |
| CFG-07 | Templates, inheritance, wave deployment | PASS | R | `test_capability_completion.py`, `test_remediation_workflow.py` |
| CFG-08 | Firmware authenticity: vendor signature verification | **FAIL** | C | SHA-256 + size only (byte integrity) |
| CFG-09 | Firmware: compatibility checks and interrupted-upgrade recovery | REQUIRES HARDWARE VALIDATION | R | AP stage-only with member verification and previous-image recovery plan; no automatic reboot/rollback; CX firmware transport **FAIL** (not implemented) |
| CFG-10 | VSF provisioning; device-initiated diagnostics (ping/traceroute/cable/pcap) | UNSUPPORTED | C | gap register |
| CFG-11 | Instant automatic full-config restore | UNSUPPORTED | C | guided restore only |
| CFG-12 | Enterprise WLAN full lifecycle; CX 802.1X/MAB provisioning; ACL/routing/QoS | **FAIL** (partial) | C | Enable-only/binding-only subsets implemented; see gap register |
| CFG-H | Every typed write on physical hardware | REQUIRES HARDWARE VALIDATION | — | All UNCERTIFIED |

## 7. Alerts, notifications, logs, audit

| ID | Requirement | Status | Depth | Evidence / note |
|---|---|---|---|---|
| ALR-01 | Deduplication (one row per rule/device/port) | PASS | R+D | `test_monitoring_pipeline.py`, `test_alert_hysteresis.py` |
| ALR-02 | Hysteresis | PASS | D | new in rc16 |
| ALR-03 | Maintenance-window suppression | PASS | R | `test_unreachable_threshold_override_and_maintenance_suppression` |
| ALR-04 | Recovery (auto-resolve on measured recovery, incident AUTO_RECOVERED) | PASS | R | `test_wireless_operations.py` |
| ALR-05 | Notification delivery failures and retries | PASS | R | `test_remediation_notifications.py` |
| LOG-01 | Device log collection: opt-in, bounded, deduplicated, scoped | PASS | R | `test_device_log_collection.py`, `test_device_audit_logs.py` |
| LOG-02 | Device-log retention/disposal | **FAIL** | C | Automatic deletion not enabled (documented in rc15 follow-up) |
| AUD-01 | Hash-chained audit; survives backup/restore | PASS | D | restore drill `audit_chain_ok: true` |

## 8. Production engineering

| ID | Requirement | Status | Depth | Evidence / note |
|---|---|---|---|---|
| SEC-01 | Authentication, RBAC, site scoping, forced password change, token revocation | PASS | R | `test_security_and_contracts.py` |
| SEC-02 | Account lockout (application users) | PASS | D | observed during load harness (repeated bad passwords locked accounts) |
| SEC-03 | Secret storage (Fernet), redaction, credentials never returned | PASS | R+D | printer profile responses contain no secrets (rc16 test) |
| SEC-04 | Input validation | PASS | R | pydantic schemas; CSV formula-injection-safe exports |
| SEC-05 | Python dependency vulnerabilities | PASS (with accepted risk) | D | pyasn1 fixed; paramiko 3.5.1 SHA-1 RSA accepted for legacy Instant APs |
| SEC-06 | Frontend dependency vulnerabilities | PASS (with accepted risk) | D | vite/esbuild = dev-server only; react-router open-redirect not reachable (no user-controlled navigation) — upgrade to v7 recommended |
| SEC-07 | Container configuration, deployment secrets, image digests | BLOCKED (environment) | — | needs Docker/CentOS (`verify-production.sh`) |
| SEC-08 | External penetration test | BLOCKED (environment) | — | |
| OPS-01 | Migrations against existing data | PASS | D | PostgreSQL 16 |
| OPS-02 | Backup restoration and deployment recovery in isolation | PASS (management DB) / BLOCKED (full) | D | dump/restore drill PASS; installer encrypted backup, Timescale, rollback scripts need CentOS |
| OPS-03 | Health/readiness | PASS | R | `/api/v1/ready`, `/healthz`, platform health page |
| OPS-04 | Capacity at provisional targets | PASS (harness) | D | 25 users, p95 226 ms, 0 errors; see test results |
| OPS-05 | Representative production capacity (TimescaleDB, NATS, real SNMP/REST latency) | BLOCKED (environment) | — | |
| UI-01 | Changed pages: forms, buttons, navigation, accessibility labels | PASS | D | 3 browser tests, no page errors / HTTP 5xx |
| UI-02 | Every other page re-checked in a browser | NOT VERIFIED | — | rc15 browser suites need the full stack |
| UI-03 | Frontend bundle size | **FAIL** (advisory) | C | single 1.27 MB JS chunk; code-splitting not done |
