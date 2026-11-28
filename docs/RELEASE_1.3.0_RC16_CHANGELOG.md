# NetworkManager3 1.3.0-rc16 — change log

Base: 1.3.0-rc15, commit `eaf40a93a1420aabe6b02a9386d1a5982d46d96d` (management schema 0014, telemetry t002).
This release candidate makes **no schema change** (still 0014 / t002) and **does not change any device-write gate**
(write lock, certification, independent approval, emergency stop, 20/40 MHz policy, `INM_AUTOMATIC_OPTIMIZATION`).
It has not been deployed to the live server, pushed to GitHub or run against physical devices.

## Fixes (defects found during the audit)

| # | Area | Defect in rc15 | Fix | Evidence |
|---|---|---|---|---|
| 1 | Switch telemetry | `port_rates` treated a **missing** error counter as 0, so a counter that appears for the first time produced a false error spike (and could open `SW_PORT_ERRORS`). | Errors counted only when both snapshots carry the counter; missing ≠ zero. | `tests/unit/test_alert_hysteresis.py::test_port_rates_never_fabricate` |
| 2 | Switch telemetry | Interface **speed change** between polls (e.g. 100 M → 1 G renegotiation) computed utilisation against the new speed; values > 100 % were reported. | Utilisation withheld (`SPEED_CHANGED`, `UTILISATION_INCONSISTENT`) instead of reported. | same |
| 3 | Switch telemetry | **Clock skew / duplicate polls**: a 0.5 s interval amplified counter jitter into implausible rates. | Intervals < 5 s (or negative) produce no rates (`INTERVAL_TOO_SHORT_OR_CLOCK_SKEW`). Counter decrease (reset/wrap) still yields no value for that interval and reports `COUNTER_RESET`. | same |
| 4 | Alerting | **No hysteresis**: a value hovering at a threshold (84/86 %) opened and resolved an alert — and notified — every poll. | Clear band per metric (5 points for %, 3 dB/°C, 50 % for per-poll delta counters, none for discrete failures) plus optional per-site `clear_threshold` override; missing data still never resolves. Applies to device and per-port alerts. | `tests/unit/test_alert_hysteresis.py` |
| 5 | Printers | SNMPv3 was **hard-wired to SHA-256/AES-128**. Printers configured for SHA-1, SHA-512, AES-256, MD5/DES or authNoPriv could not be discovered or polled, and the failure looked like "unreachable". | Profiles store `auth_protocol` (SHA256/SHA/SHA224/SHA384/SHA512/MD5), `priv_protocol` (AES128/192/256, 3DES, DES) and `security_level` (authPriv/authNoPriv) beside the encrypted secret. Used by discovery, profile test, polling and the sysLocation configuration path. Profiles saved before rc16 keep SHA-256/AES-128. MD5/DES flagged `weak_protocols`. | `tests/unit/test_printer_srs_units.py::test_snmpv3_protocols_*`, `tests/contract/test_printer_intelligence.py::test_snmpv3_profile_protocol_and_security_level_options`, browser `test_printer_snmpv3_profile_protocol_selection` |
| 6 | Printers | A table walk interrupted by a timeout returned its partial rows **as if complete**; an interrupted alert table therefore counted as fully observed and **closed open faults** that were simply not read. | `SnmpTruncated` keeps the rows read, marks the metric `incomplete_metrics`, and excludes it from `faults_observed` so existing faults are preserved. | `test_truncated_alert_table_keeps_rows_but_never_clears_faults`, `test_one_metric_timeout_keeps_other_valid_readings` |
| 7 | Inventory import | Any single bad row (wrong column count, unknown type, duplicate serial) **rejected the whole CSV**; only two fixed column layouts were accepted; one serial/family collision returned 409 for the whole batch. | Column-mapping step (auto-guess + manual mapping, fixed device type for Central exports), per-row issues, in-place correction of site/IP, rejected-row CSV export with source row numbers, and server `mode: "partial"` that imports valid rows and returns per-row rejections. Default `mode: "atomic"` unchanged. | `tests/contract/test_bulk_device_import.py::test_partial_mode_*`, browser `test_csv_import_column_mapping_partial_and_rejected_export` |
| 8 | Port monitoring groups | Admin-only (NOC viewers could not see the dashboards); **no delete**; no utilisation or throughput; missing **and** old samples both shown as "No data"; PoE threshold on a non-PoE port looked like a data gap; single-port add only; no tests. | Viewer read access with site scoping (scoped users never see members on ungranted sites), audited delete that keeps history, duplicate-name rejection, utilisation threshold and rx/tx bps, `Stale` and `Unsupported` states, server-side location roll-up with anomaly counts, admin-disabled ports reported as Warning not Critical, multi-port checkbox selection, CSV export, history up to 30 days returned in time order with no interpolation. | `tests/contract/test_port_monitoring_groups.py`, browser `test_port_monitoring_group_multi_select_dashboard_history_delete` |
| 9 | Performance | `GET /api/printers` and `/api/printers/overview` issued **two queries per printer**; under 25 concurrent users with 500 printers the list p95 was 7.3 s. | Monitoring and identity rows bulk-loaded (strong references; SQLAlchemy's identity map is weak). | `evidence/load/rc16-25users-before-printer-list-fix.json` vs `…-after-…json` |
| 10 | Dependency | `pyasn1 0.6.4`: four CVEs (CVE-2026-119194/5/6/7, quadratic CPU when decoding crafted BER). SNMP responses from any device on the network are BER-decoded. | Pinned `pyasn1==0.6.5` (pysnmp requires ≥ 0.6.4). | `pip-audit -r requirements.lock` |
| 11 | Frontend | `request()` failed on `204 No Content` (JSON parse). Several form labels wrapped their `<select>`, giving controls an accessible name containing every option. | 204 handled; affected controls use `htmlFor`/`id` labels. | browser suite |
| 12 | UX | Creating a printer credential profile required finding a form three levels deep with no pointer from the discovery form. | Discovery form states where the form is; the form opens automatically when no profile exists. | browser suite |

## Additions

- `scripts/validate_pg_migration_restore.py` (+ `_restore_drill_app.py`): disposable PostgreSQL validation of 0004→0014,
  0013→0014 with existing certification evidence, refused downgrade, and a `pg_dump`/`pg_restore` round trip on which the
  application starts and the audit chain verifies.
- `scripts/load_test.py`: mixed-load harness (separate API and worker processes, PostgreSQL management DB, emulated
  switches and printers, N authenticated users).
- `tests/e2e/ops_stack.py` + `tests/e2e/test_ops_workflows_gui.py`: isolated Chrome tests for the changed workflows.
- `DELETE /api/printers/credential-profiles/{id}` (409 while assigned to monitored printers; audited).
- `DELETE /api/v1/port-groups/{id}`.

## API contract changes

| Endpoint | Change | Compatibility |
|---|---|---|
| `GET/POST /api/printers/credential-profiles` | v3 responses add `auth_protocol`, `priv_protocol`, `security_level`, `weak_protocols`; POST accepts them. | Additive; secrets never returned. |
| `POST /api/v1/quick-devices/bulk` | Optional `mode` (`atomic` default / `partial`); responses add `mode`, `rejected[]`. | Additive. |
| `GET /api/v1/port-groups*` | Now `viewer`+ (was `admin`); dashboard adds `summary`, `locations`, `unsupported`, `support`, `collection_plan`, `freshness_seconds`; statuses add `Stale`, `Unsupported`; history `hours` up to 720, ascending order. | Additive; old status values unchanged in meaning. |
| `POST/PUT /api/v1/port-groups` | Accept `description`, `utilization_threshold`; 409 on duplicate name; up to 500 members. | Additive. |

## Not changed (deliberately)

- No device-write path enabled; no hardware certification recorded; emulator evidence remains EMULATED.
- The 20/40 MHz change-plan policy is untouched. The Commissioners Office 80 MHz **capability** stays a recorded
  baseline difference, not an operating width and not a change target (see protocol assumptions).
- paramiko stays `<4` (SHA-1 RSA still offered) because older Instant APs require it — accepted risk, see go/no-go.
