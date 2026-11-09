# Intelligent Network Manager — architecture and phased delivery specification

**Status:** working design baseline; Phase 1 candidate is under independent verification.  
**Audience:** developer, QA engineer, release engineer and executive reviewer.  
**Scope:** vendor-neutral application with Aruba Instant (AP-505/AP-535) and Aruba CX (6100/6200) as initial devices; Cisco and UniFi are future adapters. All manufacturer capabilities are runtime-probed and hardware-certified.

## Design extension — outstanding capability contracts (2026-11-08)

**This master design incorporates [the capability-completion specification](CAPABILITY_COMPLETION_DESIGN.md) as a normative addendum.** Its GAP-01–GAP-18 requirements, C1–C9 implementation contracts, WP-01–WP-12 development work packages and GAP-T01–GAP-T16 acceptance cases fill in the workflow, data, security and test detail behind the phases below. They are **planned design**, not automatically implemented functionality; the existing source and release manifest determine present runtime capability.

**Immediate usable base (retain rather than rebuild):** Git-only CentOS + Compose skeleton, demo/read-only onboarding, saved credentials, authorized bounded discovery, manual import, real device snapshots, safe configuration drafts, PostgreSQL/TimescaleDB/Loki/NATS and rule-based AI. The repository currently targets management schema `0006`; upgrades from earlier schemas must have a tested backup-first migration path before declaring success.

**Next capability delivery:** scheduled read-only AP/CX polling (WP-02), device/syslog/AAA evidence (WP-03), and the *draft-only* inheritance engine (WP-04). Add AI training data/provenance and outcome-feedback contracts in the base without claiming trained ML. Full config vault, independent approvals, actual writes, replacement and autonomous adjustment remain behind later gates. All real network-device writes stay `LOCKED`.

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

## 8. Mandatory capability completion index

| Contract | Governing specification | Build and acceptance gate |
|---|---|---|
| Read-only live telemetry, source quality and user experience | [C1](CAPABILITY_COMPLETION_DESIGN.md#c1--inventory--live-telemetry-gap-01-gap-02) | WP-02; GAP-T01/T02 |
| Syslog, audit, AAA and technician change attribution | [C2](CAPABILITY_COMPLETION_DESIGN.md#c2--syslog-aaa-accounting-and-technician-attribution-gap-03-gap-04) | WP-03; GAP-T03/T04 |
| Encrypted restorable device configuration and replacement | [C3](CAPABILITY_COMPLETION_DESIGN.md#c3--configuration-vault-and-replacement-gap-05-gap-10) | WP-05/WP-07; GAP-T07/T08 |
| Template inheritance, approvals and Aruba device policy | [C4](CAPABILITY_COMPLETION_DESIGN.md#c4--template-inheritance-and-approval-controlled-changes-gap-06gap-09) | WP-04/WP-06; GAP-T05/T06 |
| AI feedback, model learning and bounded optimization | [C5](CAPABILITY_COMPLETION_DESIGN.md#c5--explainable-ai-learning-and-outcome-tracking-gap-11gap-13) | WP-08/WP-09; GAP-T09/T10/T11 |
| Cisco/UniFi, multi-VM, executive views, isolated AI reviewer | [C6–C8](CAPABILITY_COMPLETION_DESIGN.md) | WP-10/WP-11; GAP-T12–T15 |
| Printer model and maintenance intelligence | [C9](CAPABILITY_COMPLETION_DESIGN.md#c9--printer-intelligence-completion-remains-a-distinct-bounded-module-gap-18) | WP-12; GAP-T16 |

**Scope and provenance note:** This index covers the known requirements in the repository and project discussion. The separate shared ChatGPT URL could not be retrieved; its undisclosed contents cannot yet be declared reconciled. Never infer completion from a phase heading alone.
