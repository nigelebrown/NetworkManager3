# NetworkManager3 TODO

> **Reconciled 14 Nov 2026 (build on top of 1.2.0-rc2, schemas 0012 + 0013).** `[x]` = implemented and covered by
> automated tests in this repository (emulated SNMP agents, synthetic telemetry, Chrome E2E). Hardware, CentOS-host
> and real-fleet evidence is **NOT_RUN** and is listed explicitly; nothing below claims an observed hardware result.
> Details: `docs/RELEASE_1.3.0_INTELLIGENCE_AND_PRINTERS.md`; registers `docs/Unified_Acceptance_Register_v4.csv`
> (PRN-T*, NI-T*, PF-T*) and `docs/Feature_Integration_Traceability_v4.csv` (F064–F075).

## Printer Intelligence SRS (14 November 2026)

All SRS items are in the current backlog, including the five items formerly listed as out of scope. This file records work to deliver, not changes to runtime behavior. The deployed printer collector remains read-only. Changes to printer configuration and handling of print jobs, document content, or purchase orders require their own authorization, governance, and acceptance before activation.

### Existing foundation

Printers has manual registration; private IPv4, explicit IP-list and CIDR discovery; saved encrypted SNMPv1/v2c/v3 profiles; read-only test/probe endpoints; basic Printer-MIB identification, representative meter reads, and serial lookup where reported. Discovered printers can be registered inline and their free-text location edited later. Manual meters, supplies, consumable cycles and maintenance exist, alongside basic supply alerts, model cost comparison and rule-based recommendations. These are partial capabilities where noted below. A saved profile or successful one-off scan does not establish continuous monitoring.

### Discovery and integration

- [x] PRN-001/002/008 — Preserve working discovery, credential profiles and manual registration. Add durable discovery jobs with progress, cancellation, results, and per-address errors; CSV import with preview, validation, deduplication and bulk approval for monitoring. Prefer SNMPv3 authPriv and define controlled v2c usage, including policy for legacy v1.
  - Done: Durable `DISCOVERY` jobs (progress, cancel, per-address errors) at `POST /api/printers/discovery`; approval step; CSV preview/validate/dedupe/commit; SNMP policy (v3 preferred, v2c allowed with warning, v1 off by default). Evidence: `tests/contract/test_printer_srs.py`, `tests/e2e/test_printer_gui.py`.
- [x] PRN-003/004/006/009 — Collect manufacturer, exact model, hostname, firmware and serial; preserve original imported text and do not guess abbreviated model names. Detect standard and vendor OID support, select polling profile automatically, fall back to generic monitoring and expose discovery confidence and unsupported OIDs.
  - Done: Identity from sysObjectID/sysDescr/hrDeviceDescr/serial; exact model only from a complete token (abbreviations never expanded; original import text kept); automatic profile selection with generic fallback; discovery confidence and unsupported OIDs stored. Evidence: `tests/contract/test_printer_srs.py`, `tests/unit/test_printer_srs_units.py`.
- [x] PRN-005/007/010 — Link to shared existing site, location, division, section and unit records while preserving optional assignment at onboarding. Reconcile rediscovered IP, firmware and model changes with verified physical identity; preserve separate devices when models repeat or identity is uncertain.
  - Done: `printer_identity` links sites (scoping) plus location/division/section/unit refs, optional at onboarding; re-discovery reconciles firmware/hostname, flags model changes for review and keeps serial conflicts separate (REPLACEMENT intervals, never joined). Evidence: `tests/contract/test_printer_srs.py`.
- [ ] Validate Kyocera ECOSYS MA5500ifx, M2640idw, MA4000cix, MA4000wifx and TASKalfa 6004i; identify exact models behind the Xerox VersaLink B62/C62/C70/C40 and AltaLink B807, HP Color LaserJet and LaserJet 400/MFP entries; validate Epson WF-6590 and WF-M5899; classify the generic unknown entry.
  - Status: **NOT_RUN — requires physical printers.** Vendor OIDs for Kyocera/Xerox/HP ship as `VENDOR_UNVERIFIED` (shown as UNVERIFIED capabilities); Epson uses standard MIB only. The MIB library lets you add/verify vendor mappings without code. Register: PRN-T16.
- [x] Reuse platform authentication, organizational RBAC, audit, inventory relationships, discovery, background work, notifications and dashboards. Use PostgreSQL for operational records and TimescaleDB for metric history.
  - Done: Platform JWT/RBAC with site scope (viewer read, admin change, approver review), hash-chained audit, shared jobs/notifications/scheduler/worker. Metric history is stored in the management DB tables (PostgreSQL in production) with daily aggregates; a TimescaleDB hypertable for printer samples is **not** added — see PRN-T17 (NOT_RUN).

### Collection, capability and health

- [x] PRN-011–015 — Extend system and Printer-MIB reads; add vendor-specific definitions, robust scalar and indexed table polling, normalized metric names and units, and per-device/per-metric schedules.
  - Done: Generic Printer-MIB + Host Resources profile, HP/Kyocera/Xerox/Epson profiles, bounded table walks, normalised metric keys/units, per-device interval and per-metric schedules (with companion metrics). Evidence: `tests/contract/test_printer_srs.py`, `tests/unit/test_printer_srs_units.py`.
- [x] PRN-016–021 — Store raw OIDs and values, normalized values, source, time, validation and interpretation metadata. Distinguish missing/unsupported/inconsistent values; implement controlled backoff, last successful poll, metric freshness, administrator-defined profiles without code changes, and a bounded read-only diagnostic walk in the GUI.
  - Done: `printer_metric_samples` keep raw OID/value, normalised value, source, profile+version, value state (VALID/MISSING/UNSUPPORTED/UNKNOWN_SPECIAL/INVALID). Exponential backoff, last success, per-metric freshness, admin-defined profiles (and uploaded MIBs) without code changes, bounded read-only diagnostic walk job in the GUI.
- [x] Per-model/device capability matrix for impressions; mono/colour; duplex; copy/scan; supplies and identifiers; trays; faults; drum/fuser/maintenance kits; firmware and network data. State each as supported, unsupported, unverified, temporarily unavailable or invalid.
  - Done: 20 capabilities with SUPPORTED/UNSUPPORTED/UNVERIFIED/TEMPORARILY_UNAVAILABLE/INVALID and evidence (`GET /api/printers/{id}/capabilities`).
- [x] PRN-022–030 — Track reachability separately from SNMP availability and printer faults; idle/busy/warming/error, low/empty supply, paper empty/jam/tray, service/component replacement. Persist transitions and durations, correlate repeated faults, show operational history and an evidence-based health score.
  - Done: Reachability, SNMP and operational state are separate status periods with durations; faults from hrPrinterDetectedErrorState + prtAlertTable (deduplicated) with start/end/duration; recurrence; evidence-based health score that reports INSUFFICIENT_DATA/STALE rather than healthy. Evidence: `tests/contract/test_printer_srs.py`, `tests/unit/test_printer_srs_units.py`.

### Utilization

- [x] PRN-031–035 — Collect cumulative counters continuously and derive validated hourly, daily, weekly, monthly and yearly deltas and daily/monthly averages. Separate mono and colour where supported; distinguish sheets, pages, impressions, jobs, copies, scans and duplex. Attribute sampled counts to polling intervals; use job accounting only for exact job timestamps.
  - Done: Validated intervals per meter; hour/day/week/month/year buckets with proportional attribution, coverage, confidence, averages over covered time; mono/colour/copy/scan kept separate; sheets never relabelled as impressions; sampled counts only (no job accounting claimed).
- [x] PRN-036–040 — Identify sustained low/high utilization, compare with configurable recommended duty cycles, detect peaks and anomalies, and handle rollover, reset, replacement and implausible changes. Report observation coverage, gaps and confidence. Preserve measured zero separately from a missing value.
  - Done: Duty-cycle assessment vs VERIFIED manufacturer figures (sustained low/high), robust peak/dip detection, ROLLOVER/RESET/REPLACEMENT/IMPLAUSIBLE/GAP classification, coverage/gaps/confidence, measured zero ≠ missing.

### Supplies, cost, maintenance and reliability

- [x] PRN-041–044/048 — Poll indexed toner/ink and component tables, including black/cyan/magenta/yellow, drum, waste toner, fuser and maintenance kits. Capture type, description, quantities and verified product identifiers. Decode Printer-MIB special values `-1`, `-2`, `-3` without treating unknown as empty.
  - Done: Indexed supplies/components (toner/ink/drum/waste/fuser/kits), type/description/capacity/level/part number; `-1/-2/-3` decoded (never empty). Part numbers stay unverified until confirmed.
- [x] PRN-045–047/049–050 — Detect replacements with operator confirmation where needed; retain supply event and cycle history, measured yields and purchase/replacement costs. Forecast replenishment with uncertainty and compare verified cost per 1,000 impressions.
  - Done: Replacement detection (auto-confirm clear cases, operator confirmation otherwise), event/cycle history, measured yields and costs, forecast with ~95% range, cost per 1,000 from verified costs.
- [x] PRN-051–054/059–060 — Extend maintenance records with incident/fault type, reported/restored times, parts, technician, vendor, cost, warranty and expected useful life. Correlate faults to service incidents and import Service Desk records.
  - Done: Maintenance details (incident type, cause, reported/restored, parts, technician, vendor, cost, warranty, meter), fault↔incident correlation, Service Desk CSV import (deduplicated by ticket).
- [x] PRN-055–058 — Define operating-time windows and qualifying failure types; compute MTBF, MTTR, incidents per 100,000 impressions, and maintenance frequency by model. Exclude WAN or power interruptions from mechanical failure counts unless supported by evidence.
  - Done: Configurable operating-time window; MTBF/MTTR/incidents per 100k/maintenance frequency by model; WAN/power/user causes excluded unless recorded MECHANICAL with evidence.

### Benchmarking, recommendations and reporting

- [x] FR-07 — Compare verified model cohorts by in-service count; total and median monthly volume per device; recommended-capacity utilization; supplies and measured yields; cost per 1,000 impressions; incidents, MTBF and MTTR; and total cost of ownership where complete. Normalize by workload and segment mono/colour, multifunction/print-only and inkjet/laser. Show suitability by workload, sample size, completeness and confidence, including insufficient-evidence states.
  - Done: Cohorts by model and segment (colour/MFP/technology) with workload band, sample size, completeness, confidence, INSUFFICIENT_EVIDENCE, TCO only when cost data complete (`GET /api/printers/models/compare`).
- [x] FR-08 — Expand rules for undersizing, consolidation/relocation, supply or model alternatives, standardization risk, mono replacement and low-cost/reliable standardization candidates. Show reason codes, measured supporting data, analysis period, assumptions and confidence. Track authorized human review and approval.
  - Done: 8 rules (undersized, consolidation, relocation, supply/model alternative, standardisation risk, mono replacement, standardisation candidate, replenish, recurring fault) with reason codes, evidence, period, assumptions, confidence, dedupe and approver/admin review.
- [x] Deliver fleet inventory, daily utilization, monthly utilization, consumable efficiency, model reliability, high utilization, low utilization, maintenance, standardization and executive fleet reports.
  - Done: All 10 reports implemented and tested in every format.
- [x] Support manufacturer/model/location/division/date/category/device filters; CSV, Excel and PDF exports; report jobs and optional scheduled delivery. Reconcile totals with validated intervals and support drill-down without losing the reporting period.
  - Done: Filters, CSV/Excel/PDF (dependency-free writers, CSV-injection safe), async report jobs, scheduled delivery via notification channels, monthly reconciliation in report metadata, drill-down to device pages.
- [x] Extend Printers navigation with Overview, Inventory, Live Monitoring, Utilization, Consumables, Maintenance, Model Comparison, Recommendations, Reports, and SNMP & MIB Profiles. Device detail tabs: Overview, Usage, Consumables, Faults & Maintenance, Raw SNMP Data, and Configuration with read-only attributes and profile settings.
  - Done: All ten workspaces and six device tabs (Overview, Usage, Consumables, Faults & Maintenance, Raw SNMP Data, Configuration). Evidence: `tests/e2e/test_printer_gui.py` + screenshots in `evidence/e2e/printers/`.

### Data contracts and APIs

- [x] Extend `printer_devices` with shared organizational references and verified identity. Add or map existing entities for `printer_models`, `printer_mib_profiles`, `printer_metric_definitions`, `printer_metric_samples`, `printer_counter_intervals`, `printer_consumables`, `printer_supply_events`, `printer_fault_events`, `printer_maintenance_records`, `printer_model_costs`, `printer_recommendations`, and `printer_capabilities`. Reuse existing readings, cycles and maintenance where appropriate. Define provenance, indexes, migrations and retention.
  - Done: Migration 0012 (expand-only) adds models, MIB profiles/metric definitions, monitoring, identity, samples, intervals, consumables, supply/fault events, status periods, maintenance details, model costs, capabilities, recommendations, jobs, report schedules, daily aggregates and the MIB library. Existing readings/cycles/maintenance reused.
- [x] `GET /api/printers` (exists) — complete SRS filters, pagination and organizational scoping.
  - Done: Filters, pagination (`X-Total-Count`), site scoping.
- [x] `GET /api/printers/{id}` (exists) — complete verified identity, status, freshness and operational evidence.
  - Done: Adds verified identity, monitoring state, freshness, health evidence and identity changes.
- [x] `POST /api/printers/discovery` — add SRS job-oriented endpoint and job status/results; existing credential-profile discovery and probes are synchronous.
  - Done: Job contract + `GET /api/printers/jobs/{id}`, cancel, approve.
- [x] `GET /api/printers/{id}/capabilities` — publish the capability matrix.
- [x] `GET /api/printers/{id}/metrics` — historical samples, raw/normalized provenance, time and metric filters.
- [x] `GET /api/printers/{id}/utilization` — validated time buckets, workload indicators, coverage and confidence.
- [x] `GET /api/printers/{id}/consumables` — supply status, events, completed cycles, forecasts and costs.
- [x] `GET /api/printers/{id}/faults` — operational history, durations and service incident links.
- [x] `GET /api/printers/models/compare` — complete benchmarking; existing `/api/printers/comparison` covers part of the requirement.
- [x] `GET /api/printers/recommendations` — fleet recommendation retrieval and review status; existing `POST /api/printers/analyse-now` is an initial analysis action.
  - Done: Plus `POST /recommendations/run` and `/recommendations/{id}/review`.
- [x] `GET /api/printers/reports` — report catalogue and job status; add generation, download and scheduling contracts.
  - Done: Plus generate (202 job), download and schedule endpoints.
- [x] `POST /api/printers/{id}/poll` — authenticated, audited read-only diagnostic job using the assigned profile; add diagnostic-walk, polling schedules, profile management, imports and review actions as needed. Long operations use asynchronous job contracts.
  - Done: Audited read-only poll job; diagnostic-walk, monitoring/schedule, profile management, MIB upload, imports and review actions added.

### Operational readiness and acceptance

- [x] Scale to at least 1,000 printers with horizontal worker scaling, bounded leases, network isolation, schedule fairness and failure isolation from other device types. Expose queue depth, poll success, freshness and worker errors.
  - Done: Leased CAS claims, bounded concurrency, fair ordering, per-printer failure isolation, separate printer loop in the worker; queue depth, success, freshness and errors exposed. 1,000 emulated printers in ~18 s (SQLite). CentOS/PostgreSQL scale run: NOT_RUN (PRN-T17).
- [x] Verify credentials remain encrypted, access is role/site scoped, printer polling is read-only, and profile changes, maintenance edits and recommendation decisions are audited. Follow platform conventions and avoid fabricating zeros after missed polls.
  - Done: Covered by `test_security_credentials_never_returned_reads_scoped_and_changes_audited`; emulator proves GET/GETNEXT only.
- [x] Implement configurable raw retention (initial target 12 months) and validated daily/monthly aggregates (initial target at least five years). Confirm storage and operational policy.
  - Done: Policy setting (default 365 days raw / 5 years aggregates) with daily rollup before deletion. **Operational policy confirmation is the owner's decision.**
- [ ] Test live identity, status, counters and supply polling on the listed hardware and document limitations. Cover indexed supplies, valid deltas, reset/rollover, disconnect/recovery, missing/invalid/zero distinction, replacement history, qualifying maintenance, sparse model cohorts, monthly reconciliation, unauthorized access and audit, recovery and credential security. Record hardware results only when observed.
  - Status: Software cases all covered with emulated agents (`tests/contract/test_printer_srs.py`). **Hardware portion NOT_RUN** — no observed hardware results are recorded (PRN-T16).

### Items formerly outside phase one

- [ ] Remote printer configuration, including clearly scoped SNMP SET and device-side controls where supported.
  - Status: **BLOCKED — needs separate authorisation.** This file and CLAUDE.md require governance and acceptance before any printer write (SNMP SET); not built. The collector has no SET path.
- [ ] Printer firmware upgrades, compatibility checks, authorization, audit, maintenance windows and recovery.
  - Status: **BLOCKED — needs separate authorisation** (device writes).
- [ ] Print-job interception and exact job-accounting integration, with defined purpose, access, retention and authorization.
  - Status: **BLOCKED — needs separate authorisation, purpose and retention policy.** No job data is collected.
- [ ] Document-content inspection, with approved policy, purpose, access, retention and audit.
  - Status: **BLOCKED — needs an approved privacy/purpose policy.** No document content is collected.
- [ ] Automated purchase orders, with procurement-system integration, approval, audit and safeguards against duplicate orders.
  - Status: **BLOCKED — needs procurement integration and approval design.** Supply forecasts and replenish recommendations exist as advisories only.

## Network Intelligence & Automation

Complete and verify all eight workspaces in the existing navigation. Start from the current UI, routes, rule engines, model lifecycle and worker services. For each workspace, inventory the UI actions, API contracts, jobs, persisted outputs and RBAC/site scope. Record which paths are implemented, partial, concept-only or missing. Implement the gaps and require end-to-end evidence before calling the workspace operational.

- [x] **RF optimization** — Verify and complete measured RF inputs, AP/client diagnosis, history, cluster proposals, per-device enrollments, shadow previews, decisions, outcomes, scheduling, pause/disable and emergency stop. Exercise independent approval, expiry, cooldown, rate limits, backup, readback and rollback in isolated tests. Preserve the 20/40 MHz policy, device-specific certification and lock enforcement.
  - Done: Existing gates verified by the existing suites (independent approval, expiry, cooldown, daily budget, 20/40 MHz-only, certification, global lock, emergency stop, backup/readback/rollback, STALE_APPROVAL): `tests/contract/test_production_read_write.py`, `test_capability_completion.py`, `test_remediation_write_lifecycle.py`, `tests/unit/test_capability_domain.py`; page in the browser suite. Hardware execution remains UNCERTIFIED/locked.
- [x] **Recommendations** — Verify evidence-backed recommendations from scheduled and manual runs, freshness and deduplication, reason codes, affected devices, evidence links, triage, acknowledgement/dismissal, audit and outcome tracking. Complete missing UI and API paths and ensure decisions survive refresh and restart.
  - Done: Unified store with dedupe/suppression/last-seen/occurrences, reason codes, affected devices, evidence links, triage (acknowledge/dismiss/resolve/reopen) with audit, outcomes, survive restart (DB). Evidence: `tests/contract/test_network_intelligence.py`, `tests/e2e/test_intelligence_gui.py`.
- [x] **Predictive health** — Replace concept-only surfaces with supported predictions using historical evidence, baselines, validated features, explicit prediction windows, confidence, model/version provenance and insufficient-data states. Evaluate actual predictive performance, calibration, drift and false alerts before surfacing model claims as operational.
  - Done: `baseline-trend-v1` with explicit window, probability, confidence, provenance, INSUFFICIENT_DATA; walk-forward backtest (Brier skill, calibration, precision/recall, false alerts), drift; only VALIDATED rules surface recommendations; outcome scoring. **Real-fleet performance: NOT_RUN** (NI-T12).
- [x] **Operational analytics** — Validate aggregation across devices, sites and time windows, live/demo separation, freshness, report filters, drill-down and exports. Reconcile dashboard KPIs with persisted sources and preserve gaps and unknowns.
  - Done: Site/device aggregation over 1h/24h/7d/30d, live/demo separation, freshness/coverage, NO_DATA explicit, drill-down, CSV/XLSX/PDF export, reconciliation check.
- [x] **AI learning centre** — Verify feedback and outcome capture, dataset construction, temporal splits, training jobs, evaluation, model comparison, shadow runs, independent promotion, rollback and audit. Expose job progress, model metrics, limitations and provenance. Use authorized data only and test sparse datasets and unavailable dependencies.
  - Done: Existing feedback/outcomes/temporal-split training/independent promotion verified; added model comparison, one-active-model supersession and rollback (to previous or rules-only); sparse datasets refused.
- [x] **Device insights** — Complete rule-based findings from device, RF, client, event and collection data. Show supporting evidence and last observation, handle unmonitored and stale devices, and verify scheduled/manual analysis, deduplication, drill-down and lifecycle actions.
  - Done: Catalogue-driven rules incl. NOT_MONITORED, TELEMETRY_STALE, COLLECTION_FAILING with precedence; evidence + last observation; scheduled and manual analysis, dedupe, triage, rule links.
- [x] **Assistant** — Verify ask, explanation, summary and recommendation flows against scoped real data, with citations or evidence links, freshness, redaction, timeouts, read-only tool boundaries and graceful rules fallback when the on-prem model is unavailable. Test unsupported questions, misleading inputs and cross-site access denial.
  - Done: Rules fallback when the model is disabled/unreachable/busy/timeouts, citations + data freshness, read-only (misleading write instructions only reported), unsupported questions, cross-site denial. Evidence: `backend/tests/test_ai.py`, `tests/contract/test_network_intelligence.py`.
- [x] **AI rules** — Review the existing rule catalogue, defaults and thresholds. Verify each rule's inputs, conditions, precedence, output, suppression and version history. Fill gaps in management, validation, review, audit and links from triggered findings to their rule definitions.
  - Done: 15-rule catalogue with inputs, condition, precedence, output, suppression, parameter schema; propose → independent approve; version history; findings link to rule version. Evidence: `tests/contract/test_network_intelligence.py`, `tests/e2e/test_intelligence_gui.py`.
- [x] **API and worker inventory** — Review current AI status/model/ask/explain/summary/recommend routes, learning history/feedback/outcomes/model training and promotion/shadow routes, optimization preview/enrollment/evaluation/decisions and stop controls, and analytics/insights/recommendation/rule endpoints. Map every GUI action to an authenticated route, background job or documented read-only operation. Catalogue missing contracts, implement them, and verify event delivery, polling, retries, idempotency and result persistence.
  - Done: `GET /api/v1/intelligence/inventory` maps every workspace action to a live-checked route or job (test asserts completeness); scheduled predictive job added to scheduler and worker.
- [ ] **Release acceptance** — Add targeted unit and contract checks and authenticated browser E2E scenarios for all eight workspaces. Test both normal and failure paths, permissions and site scoping, offline-model fallback, data freshness, restart/recovery and worker-to-database-to-UI reconciliation. Use isolated fixtures or emulated hardware first, then verify safe live read-only cases on CentOS. Preserve write-lock and certification requirements and record unobserved hardware evidence as NOT_RUN. Keep each workspace open until its acceptance checks pass.
  - Status: Software acceptance done: unit + contract (`tests/contract/test_network_intelligence.py`, `tests/unit/test_intelligence_units.py`) and Chrome E2E for all eight workspaces (`tests/e2e/test_intelligence_gui.py`). **Remaining NOT_RUN:** CentOS restart/recovery and live read-only cases (NI-T11), real-fleet predictive evaluation (NI-T12).

## Other known platform follow-ups

- [x] AP dashboard: free memory in MiB, uplink receive/transmit rate from verified counter deltas, discovered neighboring AP/client counts, and band-specific Client Match or verified observed-station signal distribution. The pilot exposed memory, Ethernet counters and monitored AP/client tables; the Client Match command was rejected. Record gaps and never substitute same-channel inventory for observed neighbors.
  - Done: Free memory (MiB), uplink rx/tx from verified counter deltas (reset/rollover/gap handled), over-the-air neighbour AP and station counts by band, connected-station signal distribution; Client Match reported NOT_COLLECTED with reason. Parsers labelled PARSED_FORMAT_UNVERIFIED until a hardware capture is added (PF-T02 NOT_RUN).
- [ ] Syslog: verify correct AP/switch inventory mapping on real packets, including malformed year-as-host labels, and persist the scoped pilot ingress rules after review.
  - Status: Year-as-host correction implemented (API mapping + Alloy stage) and unit-tested. **Remaining NOT_RUN:** verification on real packets through Alloy (PF-T03) and persisting the pilot ingress rules after review (host firewall — owner action).
- [ ] Wireless hardware acceptance: record real AP discovery, SSH fallback, syslog and monitoring evidence per check. Keep device writes locked and unobserved evidence as NOT_RUN.
  - Status: **NOT_RUN — hardware.** Writes stay locked.
