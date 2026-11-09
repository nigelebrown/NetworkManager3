# NetworkManager3 — Production Read/Write Completion and Acceptance Directive

**Owner instruction to Claude / engineering and QA**
**Repository:** `nigelebrown/NetworkManager3`
**Platform:** CentOS Stream 9
**Target:** Secure, functional, fully tested **read/write** production application for supported network hardware.
**Status:** Requirements / test directive — **not a certificate of production readiness**.

## Mission

Complete an independent, feature-by-feature audit, remediation, and end-to-end test of **all 11 development phases** and cross-phase GUI workflows. Implement defects found, rerun tests and record evidence. Do not claim a phase production-ready based on code presence, a successful API call, simulation, or a page rendering.

Read-only mode is a selectable operational safety mode, **not the final product objective**. Authorized operators must be able to make real configuration changes through supported device interfaces with pre-change validation, backups, approval rules, write execution, post-change readback, audit logs, and tested rollback/recovery.

**Important:** The phase titles below are acceptance workstreams. Reconcile them with the actual repository phase definitions and acceptance register. Never rewrite the documented history just to make statuses appear green.

## Phase acceptance workstreams

| Phase | Production requirement | Acceptance evidence |
| --- | --- | --- |
| **1 — Platform foundation** | CentOS Stream 9 deployment, services, database, authentication, secrets, updates, backups | Clean install, upgrade, restart, resilience, backup/recovery, security |
| **2 — Discovery and onboarding** | CIDR scan execution incl. `/24`, scoped scheduled jobs, credential profiles, device detection, guided import/onboarding | Real permitted-subnet scan, progress/retry/cancel, import, duplicate recognition |
| **3 — Monitoring and telemetry** | Live AP/switch status, interfaces, PoE, RF, clients, logs, alerts, history | Device-to-UI metric validation, outages, reconnection |
| **4 — Accountability** | RBAC, approver separation, identity integration, complete audit trail, drift detection | Allow/deny permission tests, audit checks, drift tests |
| **5 — Configuration vault** | Encrypted backups, versions/diff, integrity, restore | Scheduled backup and verified restoration to compatible lab device |
| **6 — Configuration management** | Base/global, site, group, model and device inheritance; approvals and real hardware writes | Actual push, hardware readback, conflict handling and rollback |
| **7 — Device replacement** | Guided replacement and compatible restore | Replacement lab device configured, service restored and verified |
| **8 — AI analysis** | Analyze Now, history, learning, evidence-based optimization recommendations | Reproducible representative-data validation, accuracy criteria |
| **9 — Automated optimization** | Explicit device/site opt-in, bounded policy, dry run, cooldown, rollback and kill switch | Controlled hardware write, feedback and safe failure tests |
| **10 — Vendor support** | Production drivers only for declared, demonstrably supported models/firmware | Per-model capability and read/write certification |
| **11 — Production operations** | Scale, uptime, observability, security, disaster recovery, release procedures | Capacity, failure injection, recovery, deployment and operations signoff |

Also audit all exposed or promised functions outside these workstreams: printer intelligence, reporting, dashboards, device replacement, notifications, third-party integrations and the isolated AI coding reviewer. Remove or unmistakably label unsupported/incomplete functionality. Do not present a stub as a working feature.

## Primary device certification

- Aruba Instant AP-505 and AP-535.
- Aruba CX 6100 and CX 6200.

For each model **and actual firmware**, verify supported APIs/protocols, discovery, authentication, current configuration read/import, write, readback, backup, restoration, telemetry, failure handling and exact supported capabilities. Do not infer live compatibility from source-code tests.

**Switch operations:** VLAN create/edit, access/trunk assignment, LACP, LLDP, PoE, administrative port state, status and configuration inheritance.

**AP operations:** SSID/VLAN mapping, ARM, channel/power configuration, ClientMatch, band steering, airtime fairness, DFS control, site/cluster management and RF telemetry.

**Mandatory RF restriction:** Approved channel widths are **20 MHz and 40 MHz only**. **Never set, recommend or automatically use 80 MHz.**

Other vendors (for example Cisco or UniFi) may only be advertised as fully supported after equivalent hardware/firmware certification. Label scaffolding and planned adapters accurately.

## Mandatory guided user experience

Complete this workflow entirely from the web GUI:

**Select site → Discover CIDR/range → Create or reuse credentials → Detect and identify APs/switches → Recognize existing managed devices → Import current device settings → Choose global/site/group/model templates → Preview effective settings/diff → Validate and approve → Execute real hardware changes → Read back and verify → Monitor and optimize (if allowed) → Backup and restore.**

Existing devices must prepopulate known values and support import, edit now, or edit later. Each wizard must expose state, permissions, expected results and recoverable errors. No intended operator workflow should require hidden API calls or manual database edits.

### Confirmed discovery design gap

The current discovery/onboarding implementation rejects live submissions over **eight IP addresses**, while a CIDR planner is separate from actual scan execution. Implement **authorized /24 scanning** using queued, rate-limited jobs with progress, scoped credentials, concurrency limits, deduplication, cancellation, retry and resumability. Do not bypass authorization or enable unbounded network scans.

## Production-safe read/write requirements

1. Role-based access for reading, changing, approving, optimizing and restoring, with appropriate separation of duties.
2. Encrypted credential storage, rotation and non-disclosure in logs and UI.
3. Pre-change backup, device-model/firmware compatibility check and validated intended config diff.
4. Approval/maintenance-window controls as configured by policy.
5. Actual write via supported interfaces, followed by device-state readback and service-health verification.
6. Explicit outcomes for partial success, timeouts, disconnections, conflicts, unsupported functions and rollback failure.
7. Tested rollback where supported, or a verified documented recovery procedure otherwise.
8. Tamper-resistant, attributable audit logs recording actor, approver, target, operation, evidence and outcome.
9. **Automatic optimization only by explicit opt-in per site/device**, with bounded policy, dry run, change budget, cooldown, exclusions, emergency stop and rollback/recovery.
10. Do not activate autonomous writes across the production fleet before controlled hardware certification and independent approval.

## Mandatory tests for EVERY feature and phase

Maintain a single acceptance matrix with columns:

`Test ID | Phase | Requirement | Device/model/firmware | Test procedure | Expected | Actual | Status | Evidence link | Defect | Retest | Signoff`

Permitted statuses: **PASS / FAIL / PARTIAL / BLOCKED / NOT RUN**. A simulated success cannot satisfy hardware write acceptance.

- Reconcile the current **220-check acceptance register** and prior **378 automated-test** claim with current code; execute tests again instead of copying past PASS results.
- Run unit, contract/API, integration, GUI browser, end-to-end, negative, authorization and regression suites.
- Exercise fresh production-style install, upgrades/migrations, restart, failover, restoration, performance, alarm/recovery and deployment rollback.
- Include actual hardware change/readback and restoring compatible lab hardware for all critical write functions.
- Retain screenshots, commands, sanitized logs, device readback/diffs and version identifiers as evidence.
- Do not treat missing dependencies, credentials, firmware or test devices as PASS: record **BLOCKED** with a precise prerequisite.

### Release gates

A phase may be called **PRODUCTION PASS** only after:
- Its mandatory requirements are implemented and integrated, with no placeholder paths.
- Applicable automated and real-device tests pass, with retrievable evidence.
- Critical/high-severity security and data-integrity issues are remediated.
- Recovery, observability and operator documentation are validated.
- An independent authorized reviewer signs off.

Do **not** declare all phases complete while any mandatory item is FAIL, PARTIAL, BLOCKED or NOT RUN.

## Git workflow

This directive is approved documentation on `main`. Perform code remediation in a **dedicated implementation/testing branch**, commit and push incrementally, and create a reviewable pull request. **Do not merge implementation changes to `main` without explicit owner approval.** Preserve migrations, working behavior and deployment compatibility.

## Required final handoff

1. Phase-by-phase status dashboard: PASS / FAIL / PARTIAL / BLOCKED / NOT RUN.
2. Full feature-gap/defect register, mapped to implementation commits.
3. Updated acceptance matrix and automated test run results.
4. Real-device certification matrix for AP-505, AP-535, CX 6100 and CX 6200, including firmware and read/write capabilities.
5. Real execution/readback/backup/restore/rollback evidence.
6. Security, scalability, HA/DR and failure-recovery results.
7. CentOS Stream 9 clean-install, upgrade, rollback, troubleshooting and operator runbooks.
8. Branch/commit links, release candidate and honest unresolved blocker list.

**Start now with the current repository and acceptance artifacts. Fix, test, and verify phase by phase. Never substitute a simulation for real hardware acceptance or claim readiness unsupported by evidence.**
