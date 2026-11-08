# Intelligent Network Manager — architecture and phased delivery specification

**Status:** working design baseline; Phase 1 candidate is under independent verification.  
**Audience:** developer, QA engineer, release engineer and executive reviewer.  
**Scope:** vendor-neutral application with Aruba Instant (AP-505/AP-535) and Aruba CX (6100/6200) as initial devices; Cisco and UniFi are future adapters. All manufacturer capabilities are runtime-probed and hardware-certified.

## 1. Non-negotiable platform design

- **Presentation:** React + TypeScript; event-driven WebSocket refresh with REST reconciliation on reconnect.
- **Core:** Python FastAPI with Onion/Clean Architecture. Domain + application layers do not import vendor SDKs or database implementations.
- **Workers:** independently restartable discovery, monitoring, telemetry ingestion and future configuration orchestration.
- **Event bus:** NATS JetStream with persisted commands/events, idempotency keys, retry and dead-letter queues.
- **Management data:** PostgreSQL, versioned migrations, inventory, sites, RBAC, workflows, audits and approvals.
- **Metrics:** TimescaleDB, retention/downsampling, device/radio/port/client data and quality measurements.
- **Logs:** Grafana Alloy + Loki for device syslogs, worker events and correlation.
- **AI:** isolated Python analysis service for rules first, then statistically trained/anomaly/forecasting models; model registry and measured outcomes.
- **Deployment:** seven independently deployable logical roles as containers in **one Docker Compose application** on a clean CentOS Stream 9 pilot VM; separately located VMs later without changing domain contracts.
- **Security:** per-user authorization, least privilege, no production secrets in Git, audit-chain integrity, per-device allowed CIDRs, explicit approval for writes and no device-network route for Phase 1 demo worker.

## 2. Reuse-first strategy

Retain from the existing SIMS Wi-Fi Manager: React views, Aruba Instant API adapter and telemetry parsers, client experience scoring, alerting, existing firmware/certificate workflows, legacy read-only configuration comparison, authenticated operations and AI assistant. Refactor behind vendor-neutral ports. Preserve behaviour through regression tests before replacing/retiring any legacy route.

Do not claim that an existing redacted config snapshot is a restorable device image. Do not treat a demo switch as a CX production adapter. Mark unavailable modules accurately: `DEMO`, `RULES_ONLY`, `READ_PLAN_ONLY`, or `PLANNED`.

## 3. Delivery phases and acceptance gates

| Phase | Deliverable | Mandatory demonstration / pass criterion |
|---|---|---|
| **1 — platform foundation** | Full Docker skeleton, seven roles, React + FastAPI + WebSockets, demo data, source-parity integration | One-command install/reinstall on clean CentOS; health checks, NATS-to-DB-to-log-to-WebSocket round-trip; isolated restore; 100% agreed Phase 1 test evidence |
| **2 — real read-only onboarding** | Aruba Instant AP-505/AP-535 and CX 6100/6200 adapters, credentials, inventory, new vs existing discovery | Onboard one AP and both CX models; verify serial, firmware, cluster/site and capabilities; **no writes** |
| **3 — telemetry and log operations** | Live RF, client and port counters, syslog intake, time-series history, alerts | Correlate device events with correct timestamps and device identities; dashboards show actual values and data gaps |
| **4 — attribution and accountability** | Named technician accounts, AAA accounting ingestion, application audit, out-of-band drift attribution | Change made via app has initiator, approver, time, before/after; direct-device change is detected and attributed only when accounting evidence is sufficient |
| **5 — secure configuration vault** | Encrypted backups, retention, hashes, diffs, scheduled snapshots, verification | A versioned backup is tied to device identity and verified; backup/restore-to-test succeeds; protected credentials never leaked |
| **6 — controlled configuration** | Global→model→site→device inheritance, policies, config plans, peer approval, pre/post-change backups and rollback | Approved changes only; one technician cannot self-approve; rejected/failed changes cause no unauthorized writes; limits on AP RF widths (20/40 MHz only) |
| **7 — replace device** | Compatible hardware replacement wizard, template/snapshot selection, prechecks, staged restore and readback | Restore tested on same model/compatible firmware; new serial tracked; all operational checks and audit trail pass |
| **8 — AI learning centre** | Analyze Now, hourly recommendations, history, MLflow versions, held-out evaluation, feedback | Show historical baseline, model version, evidence, confidence/calibration and measured recommendation quality; never label a simple rule as trained ML |
| **9 — safe optimization** | Measured-outcome evaluation and bounded automated adjustment | Policy constraints, change approval, cooldown, rollback, no repeated oscillation, comparison to native Aruba ARM/ClientMatch |
| **10 — other vendor adapters** | Cisco IOS/IOS XE and Ubiquiti UniFi adapters using capability contracts | Correctly detect supported/unsupported operations; vendor adapters cannot bypass central write/audit policy |
| **11 — production scale-out** | Distribute services onto separate VMs, secured service networking, HA/DR, backups, observability | Fail individual roles; prove recovery, restore, loss of broker, database failover and end-to-end consistency; zero undocumented change to domain behaviour |

**Gate rule:** no phase is complete without implementation evidence, negative tests, rollback/recovery tests, security review, accepted risk exceptions and explicit non-developer sign-off. Preserve build commit SHA and artifacts for every test.

## 4. Cross-cutting audit/backup contract

Every application change must store: immutable change ID, device ID / serial, site, requested config diff, requester identity, source IP/session, independent approver, timestamp (UTC), pre-change backup/hash, operation executed, readback, post-change backup/hash, outcome, rollback state, software version and correlated syslog/accounting events.

Device drift is not inherently proof of who changed it. Capture ArubaOS-CX `/logs/audit` and `/logs/accounting` where supported, correlate AAA identities and sessions; otherwise display **operator unknown** and an evidence quality indicator.

Backups must be encrypted, versioned, integrity-checked, segregated from interactive accounts, restored in a safe test, and compatible with the model/firmware before a device-replacement workflow is approved.

## 5. Source-backed API adaptation

- **Instant AOS-8.10:** separate adapter; REST login/logout over HTTPS port 4343 with SID/session management; REST API may require enablement on conductor/standalone. Do not assume cluster-member direct API access.
- **AOS-CX 10.14:** separate adapter using `/rest/v10.14`; capability matrix includes config retrieval/diff, fullconfigs, logs/audit, logs/accounting, logs/event, interfaces, VLANs and related resources. Verify model/firmware and auth negotiation at runtime.
- Network management source documents are reference evidence, not proof that every model supports all calls.

## 6. One-command release operations

Every release must provide and test `doctor.sh`, `install.sh --mode demo`, `verify.sh`, `status.sh`, `backup.sh`, `restore.sh --target test`, `update.sh --ref <tag>`, and compatibility-gated `rollback.sh`.

Installation must run a state machine **preflight → prepare → deploy → verify → receipt** and return nonzero on failure. Updates must take a verified backup first, refuse unsupported/downgrade schemas, build or use approved pinned images, verify all services, and record both success and failure.

The application source and deployment scripts are committed to GitHub. The **only supported server entrypoint** is `bash scripts/inm.sh install` for initial installation and `bash scripts/inm.sh update` for approved Git-tag updates, with `doctor`, `verify`, `status`, `backup`, and `rollback` subcommands. No Windows PC or transferred ZIP is involved. Their presence alone is insufficient: see the clean-CentOS release gate and unresolved image-digest enforcement note in `PHASE1_REVIEW_AND_RELEASE_GATE.md`.

## 7. Acceptance evidence format

For each test record: test ID, phase, feature/contract, environment, preconditions, steps, expected result, observed result, PASS/FAIL/PARTIAL/NOT_RUN, build SHA, tester, UTC timestamp, captured logs, supporting screenshot/trace and decision-maker sign-off.

**Do not proceed to real device configuration until the Phase 4–6 controls have been independently demonstrated.**
