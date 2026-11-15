# Printer Intelligence SRS + Network Intelligence completion (on top of 1.2.0-rc2)

Status: **software complete and tested on emulated/synthetic inputs; hardware and CentOS acceptance NOT_RUN.**
Device writes remain LOCKED. Printers are read-only (SNMP GET/GETNEXT only — there is no SET path).

## Schema
| Revision | Content | Kind |
|---|---|---|
| 0012 | 21 printer tables: models, MIB profiles + metric definitions, monitoring state, identity, raw samples, counter intervals, consumables, supply/fault events, status periods, maintenance details, model costs, capabilities, recommendations, jobs, report schedules, daily aggregates, uploaded MIB modules/objects | expand-only |
| 0013 | `recommendations` lifecycle columns (nullable), `ai_rule_definitions`/`ai_rule_versions`, `prediction_runs`/`predictions`, `access_points.observations` (nullable JSON, added only if missing) | expand-only |

`release.json` accepts management schema `0013`; `0011` and `0012` are upgradable. The installer's backup → migrate
→ verify path applies unchanged. Rollback = restore the pre-update backup (expand-only migrations are not
downgraded automatically, per project policy).

## Printer Intelligence (TODO "Printer Intelligence SRS")
* **Collection** — `backend/printers/collector.py`: profile-driven read-only poller (pysnmp; v3 authPriv preferred,
  v2c allowed with a warning, v1 off by default). Generic Printer-MIB/Host-Resources profile plus HP/Kyocera/Xerox
  vendor counters marked `VENDOR_UNVERIFIED`. Every sample keeps raw OID/value, normalised value, source, profile
  version and value state. Missing ≠ zero; a failed poll records reachability only.
* **MIB library** — upload `.mib/.my/.txt/.smi`; parsed as text only (never compiled/executed), imports resolved
  against built-ins and other uploads, objects browsable with suggested metric mappings, profile builder, read-only
  test against a printer, automatic selection by sysObjectID. No print drivers involved.
* **Scheduling** — leased compare-and-set claims, back-off, per-metric schedules, failure isolation; runs in the
  standalone scheduler (`printer_polls` 60 s, `printer_jobs` 30 s, `printer_daily` 24 h) and in the network worker
  (`printer_loop`). 1,000 emulated printers polled in ~18 s on SQLite.
* **Analytics** — validated counter intervals (ROLLOVER/RESET/REPLACEMENT/IMPLAUSIBLE/GAP), hour→year buckets with
  coverage/confidence, duty cycle vs VERIFIED model figures, robust anomalies, supply replacement detection with
  operator confirmation, forecasts with ranges, measured yields/cost per 1,000, fault periods, MTBF/MTTR (WAN/power
  excluded), evidence-based health, cohort benchmarking with sufficiency states, 8+ recommendation rules with review.
* **Reports** — 10 reports × CSV/XLSX/PDF (dependency-free writers), async jobs, schedules delivered through existing
  notification channels (link, not attachment), monthly reconciliation in metadata. Retention: raw 365 d → daily
  aggregates 5 y (configurable).
* **UI** — Printers → Overview, Inventory, Live monitoring, Utilization, Consumables, Maintenance, Model comparison,
  Recommendations, Reports, SNMP & MIB profiles; device tabs Overview, Usage, Consumables, Faults & maintenance,
  Raw SNMP data, Configuration.

### Behaviour change
Printers were admin-only. They now use platform RBAC: viewer/operator **read within their site scope**, admin
changes, approver/admin review recommendations. The P0 test asserting admin-only reads was updated accordingly.

## Network Intelligence (TODO "Network Intelligence & Automation")
* **AI rules** — 15-rule catalogue (site rules, device insights, predictive) with inputs/condition/precedence/output/
  suppression/parameter schema; changes are proposed and approved by a *different* approver/admin; versioned.
  The isolated AI service receives effective parameters (numbers only).
* **Recommendations** — one write path (`infrastructure/recommendation_store.py`): dedupe per rule+target,
  suppression after dismissal, last-seen/occurrences, reason codes, affected devices, evidence links, triage with
  audit. `GET/POST /api/v1/intelligence/recommendations…`.
* **Device insights** — adds NOT_MONITORED, TELEMETRY_STALE and COLLECTION_FAILING with precedence over thresholds.
* **Predictive health** — `baseline-trend-v1` (hour-of-week baseline + recent deviation + bounded trend). Walk-forward
  backtest per rule; a rule is OPERATIONAL only with ≥30 windows, Brier skill > 0 and calibration gap ≤ 0.25;
  otherwise EXPERIMENTAL and never surfaced as a recommendation. Outcomes are scored after each window.
* **Operational analytics** — site/device aggregation, live/demo separation, NO_DATA explicit, drill-down, export,
  reconciliation flag.
* **Learning centre** — model comparison, one active model (previous kept as SUPERSEDED), rollback.
* **Assistant** — deterministic rules fallback when the on-prem model is disabled/unreachable/busy/times out, with
  citations and data freshness. *Behaviour change:* those cases now return 200 with `engine: RULES_FALLBACK` instead
  of 409/503/429 (tests updated).
* **Inventory** — `GET /api/v1/intelligence/inventory` checks that every workspace action maps to a registered route/job.

## Platform follow-ups
* AP dashboard (Device 360): free memory MiB, uplink rates from timed counter deltas, over-the-air neighbour AP /
  station counts by band, connected-station signal distribution, Client Match reported as NOT_COLLECTED.
  New read-only command `show ap monitor client-list` added to the allow-list. Parsers are labelled
  `PARSED_FORMAT_UNVERIFIED` until hardware captures are added.
* Syslog: year-as-host correction in `/api/v1/logs` mapping and an Alloy `loki.process` stage (Alloy run NOT_RUN).

## New settings / environment
| Variable | Default | Purpose |
|---|---|---|
| `INM_PRINTER_POLLING` | true | scheduled printer polling |
| `INM_PRINTER_POLL_BATCH` / `INM_PRINTER_POLL_CONCURRENCY` | 200 / 32 | per-cycle claim size / parallel polls |
| `INM_SCHEDULED_PREDICTIVE` | true | 6-hourly score + predict (needs telemetry store) |
| `INM_PRINTER_EMULATION` | false | test harness only; the emulator registry is empty in production processes |

Printer management addresses must be inside the approved read-only ranges (`allow-readonly-device-range.sh`).

## Evidence (this environment, build agent — independent re-run required)
* Full non-hardware suite: `evidence/tests/full-suite-2026-11-14.log`.
* Printer: `tests/contract/test_printer_srs.py` (15), `tests/unit/test_printer_srs_units.py` (32),
  Chrome `tests/e2e/test_printer_gui.py` (9) via `tests/e2e/printer_stack.py` — screenshots/exports in `evidence/e2e/printers/`.
* Intelligence: `tests/contract/test_network_intelligence.py` (11), `tests/unit/test_intelligence_units.py` (20),
  Chrome `tests/e2e/test_intelligence_gui.py` (8) via `tests/e2e/intelligence_stack.py` — `evidence/e2e/intelligence/`.
* Follow-ups: `tests/unit/test_platform_followups.py` (17).

## NOT_RUN (must not be reported as PASS)
PRN-T16 live printers (Kyocera/Xerox/HP/Epson list) · PRN-T17 CentOS PostgreSQL/worker scale · NI-T11 CentOS
restart/recovery and live read-only cases · NI-T12 predictive skill on real telemetry · PF-T02 AP extras on captured
hardware output · PF-T03 Alloy syslog correction on real packets. Items needing separate authorisation (printer
configuration/firmware, print-job interception, document-content inspection, automated purchase orders) were not built.
