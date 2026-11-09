# Claude engineering instructions — NetworkManager3

## Product and constraints

Build the organization-neutral Intelligent Network Manager using Python/FastAPI, React/TypeScript, Onion Architecture, PostgreSQL, TimescaleDB, Loki/Alloy, NATS JetStream, isolated Python AI and a one-VM Docker Compose test deployment that can be separated into seven production roles.

Read `docs/ARCHITECTURE_AND_PHASED_BUILD.md`, **`docs/CAPABILITY_COMPLETION_DESIGN.md`**, `docs/GUIDED_DEVICE_ONBOARDING.md`, `docs/PHASE1_REVIEW_AND_RELEASE_GATE.md` and `docs/SERVER_DEPLOYMENT_RUNBOOK.md` **before modifying source**. The capability-completion addendum is part of the governing architecture, not optional background reading.

## First work order

1. The complete application source is already committed to this private GitHub repository. Treat Git as the **only** application source-of-truth. Do not accept PC ZIP transfers or direct edits on the CentOS server; preserve existing review documents and app lineage.
2. Use the supported server entrypoint `bash scripts/inm.sh install`; future upgrades must be approved immutable Git tags using `bash scripts/inm.sh update`. Do not bypass backups, schema compatibility or acceptance gates. Confirm exact source commit SHA and required directories.
3. Set up Python dependencies from `requirements.lock` and frontend dependencies via `npm ci`. Run legacy, unit, contract and integration tests. Collect actual results; never copy PASS from an earlier report into a new report.
4. Implement or correct all blockers for a clean CentOS Stream 9 Docker install. Run installer preflight, installation, repeat installation, full service health, synthetic end-to-end data round-trip, encrypted backup and disposable restore, failed update/rollback and negative security tests.
5. Fix the immutable image digest guarantee: recording `images.lock` alone does not enforce pinned digest reuse. Resolve pinned image digest values in a reviewed release manifest and refuse unapproved image drift.
6. Preserve the demo network-isolation and device-write lock, fail closed on audit/permission failures.
7. Add accurate Phase 1 test evidence, unknowns and signoff request. **Do not mark Phase 1 accepted or start Phase 2 without independent acceptance.**

**Deployment architecture:** CentOS pulls application source directly from `nigelebrown/NetworkManager3` using a read-only SSH deploy key. No Windows PC, upload bundle, manual `git pull` of an already installed release or source change on the server is permitted. The approved demo tag pointer lives in `deployment/releases/approved-demo-tag` and may remain empty until explicitly released.\n\n## Implementation work order for missing capabilities

The outstanding design now consists of **GAP-01–GAP-18**, **WP-01–WP-12**, and **GAP-T01–GAP-T16** in `docs/CAPABILITY_COMPLETION_DESIGN.md`. Convert a work package into a short-lived feature branch; implement in this order unless explicitly reprioritized:

1. Preserve existing schema `0006` onboarding, read-only configuration drafts, demo data, audit and real manual imports. Do not introduce another parallel device onboarding stack.
2. WP-01: establish on-host Git-managed CentOS acceptance, backup and *test-restore* evidence. A source-only doc or GitHub CI run is not host acceptance.
3. WP-02: add scheduled **read-only** Aruba Instant/CX collection with capability checks, clear data quality labels, rate limits, job leases and real hardware tests.
4. WP-03: syslog, AAA and external-change attribution with `VERIFIED / CORRELATED / UNKNOWN`; never invent a technician.
5. WP-04: deterministic global → family → model/port layout → site → device **draft-only** template inheritance, with provenance and 20/40-MHz-only RF policy.
6. Prepare (not activate) ML datasets, feedback and measurable outcomes, then WP-05–WP-12 under their independent safety gates.

For each package create domain contracts, migration and compatibility proof (when needed), API, worker, UI, audit, test evidence and rollback procedure. Update `docs/Feature_Integration_Traceability_v4.csv` and `docs/Unified_Acceptance_Register_v4.csv` with **NOT_RUN** until observed on the stated environment. Never mark `DESIGNED_NOT_IMPLEMENTED` as operational. Unretrievable external ChatGPT share text is not authority to invent additional features; reconcile that source separately when supplied.

## Definition of done for every subsequent module

- Contract and migration design reviewed.
- Working UI + backend endpoints + background worker contract.
- Positive/negative and security unit tests, integration and E2E tests.
- Authenticated WebSocket UI behaviour and reconciliation.
- Database migration/restart/backup/restore evidence.
- One-command install or update on a clean CentOS test instance, with rollback tested.
- Structured audit event linked to named operator / service identity.
- Evidence register updated with actual observed results, build SHA and tester.
- Vendor specific behaviour checked against supplied Instant AOS 8.10 guide or CX 10.14 OpenAPI; capability not assumed from unrelated models.

## Owner directive (2026-11-09 06:33) — Phase 9 per-device optimization re-enabled; supersedes the earlier "no automatic optimization" limitation

Automatic optimization exists again, but only as **per-device opt-in** (`DEVICE_OPT_IN`) and only when ALL hold:
the platform flag `INM_AUTOMATIC_OPTIMIZATION=true` (default **false**; with it false the API refuses
DEVICE_OPT_IN enrollment and the optimizer denies everything), a device-ID/serial-bound enrollment approved by
an independent approver, operation/value scope (20/40 MHz only, never 80), expiry, daily limit, cooldown,
write certification of the operation for that model/firmware, a fresh measured metric, no global write lock
and no emergency stop. Every automatic change runs through `execute_plan` (verified backup, checkpoint,
readback, automatic rollback) and is rolled back if the outcome degrades. Never inherited from site, model,
group or template. Implementation: `backend/infrastructure/optimizer.py`; release notes
`docs/RELEASE_1.2.0_PRODUCTION_READINESS.md`.

Hardware writes: the engine (`backend/infrastructure/change_executor.py`) only writes to HARDWARE when device
writes are enabled, the write lock is off and each operation is CERTIFIED_WRITE for that adapter + model family +
firmware train + HARDWARE environment. Protocol-emulator (EMULATED) certification never certifies hardware.
Do not record PASS for a hardware requirement on emulator evidence; use PARTIAL (emulator) + BLOCKED (hardware).

## Device optimization authorization — mandatory product rule

**Automatic optimization is OFF by default, with no implicit site, model, group, organization or template inheritance.** Monitoring, rule/ML analysis, and recommendations may run without writing. Only explicitly selected individual devices may be configured for automatic optimization, using a device-ID/serial-bound enrollment, named requester and independent approver, approved operation/value constraints, expiry, schedule, cooldown and immediate disable action. Multi-select is merely a batch *proposal* producing separate approvals per device; newly discovered, transferred and replacement devices begin OFF.

Read `docs/CAPABILITY_COMPLETION_DESIGN.md` §C5a (GAP-13). A global write lock or emergency stop overrides every device enrollment. Do not create a global auto-enable switch, use template inheritance to turn on optimization, allow an ML/LLM service to write directly, or silently activate hardware writes by implementing the design. Implement observe/recommend and shadow mode first. Require independently accepted GAP-T17–GAP-T24 tests, plus device hardware certification, before any real per-device execution.

## Prohibited shortcuts

Do not fabricate successful device tests, hardware capabilities, backups, ML improvements or deployment results. Do not embed device credentials or personal information in Git. Do not enable live network-device writes in Phase 1. Do not silently bypass TLS, alter the approved RF channel restrictions, or allow AI to execute configuration changes directly.
