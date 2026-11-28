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

**Owner directive 2026-11-25 (supersedes the per-device-approval sentence above for site batches):** a *site batch*
(`POST /api/v1/optimization/site-batches`) snapshots the APs registered at one site at request time and creates one
serial-bound enrollment per AP; ONE independent approval arms each member that individually passes the same checks as a
single approval (identity/firmware unchanged, not expired, backup present). There is still no inheritance: APs added to
the site later are never enrolled by an existing batch. Write lock, emergency stop, certification, cooldowns, daily
limits, per-AP disable and batch disable all apply. 80 MHz stays forbidden.

Read `docs/CAPABILITY_COMPLETION_DESIGN.md` §C5a (GAP-13). A global write lock or emergency stop overrides every device enrollment. Do not create a global auto-enable switch, use template inheritance to turn on optimization, allow an ML/LLM service to write directly, or silently activate hardware writes by implementing the design. Implement observe/recommend and shadow mode first. Require independently accepted GAP-T17–GAP-T24 tests, plus device hardware certification, before any real per-device execution.

## Prohibited shortcuts

Do not fabricate successful device tests, hardware capabilities, backups, ML improvements or deployment results. Do not embed device credentials or personal information in Git. Do not enable live network-device writes in Phase 1. Do not silently bypass TLS, alter the approved RF channel restrictions, or allow AI to execute configuration changes directly.


## 1.2.0-rc2 software remediation (branch `release/software-remediation`)

Report: `docs/RELEASE_1.2.0_RC2_SOFTWARE_REMEDIATION.md`. Key invariants added in rc2 — keep them:
* Telemetry identity includes `entity_type/entity_id` (migration t002); never collapse ports/radios/clients.
* Scheduled collection and changes emit events through `infrastructure/outbox.py` in the same transaction; a run is
  SUCCEEDED only after the persisted count is verified. Correlation id = run id (collection) / plan id (changes).
* Device state comes only from `domain/collection.operational_state()` (collector, NOC, Device 360, API agree).
* All CX access goes through `CxSession` (form login, CSRF, version negotiation, redaction). Never credentials in URLs.
* `execute_plan` compares the LIVE pre-change backup with the approved baseline; mismatch = STALE_APPROVAL, zero
  writes, plan back to PENDING_APPROVAL.
* The capability registry is contract-tested against the writers' OPERATIONS; unbuilt features are listed as
  NOT_IMPLEMENTED in `/api/v1/system/features`.
* Tests: `tests/fullstack/` (native distributed stack, `INM_FS=1`), `tests/e2e/run_isolated.sh` (browser suites on a
  fresh stack each), `scripts/reconcile_register_1_2_0_rc2.py`, `scripts/capability_matrix.py`.


## 1.3.0-rc16 invariants (keep them)

Report: `docs/RELEASE_1.3.0_RC16_CHANGELOG.md`; matrix `docs/ACCEPTANCE_MATRIX_1.3.0_RC16.md`.
* Counter rates (`domain/alert_rules.port_rates`): missing ≠ 0; counter decrease, speed change, > 100 % utilisation and
  intervals < 5 s yield no value (with a reason note), never a fabricated or negative one.
* Alerts resolve only on a MEASURED value past the hysteresis clear level (`alert_rules.cleared`); missing data never resolves.
* Printer SNMPv3 protocols live beside the encrypted secret (`printers/snmp.usm_options`); absent fields mean SHA-256/AES-128.
  Build USM objects only through `printers/snmp.build_auth_data`.
* A truncated SNMP walk (`SnmpTruncated`) keeps its rows but never counts as complete (faults/supplies are not cleared).
* Port groups: viewer-readable, site-scoped via the Device 360 visibility rule; statuses Critical/Warning/Unsupported/
  Stale/No data/Healthy; Healthy only with fresh measurements for every configured threshold.
* Bulk inventory import `mode=partial` imports valid rows and reports per-row rejections; `atomic` stays the default.
* List endpoints must not issue per-row queries; keep strong references to preloaded rows (the identity map is weak).

## 1.3.0-rc17 invariants (keep them)

Report: `docs/RELEASE_1.3.0_RC17_CHANGELOG.md`; matrix `docs/ACCEPTANCE_MATRIX_1.3.0_RC17.md`; handover `docs/HANDOVER_1.3.0_RC17.md`.
* Navigation: ONE menu entry per purpose (`frontend/src/components/Layout.tsx` GROUPS). Related screens are `views`
  (tabs) of that entry; never add a second menu entry that leads to overlapping content. `tests/e2e/nav_audit.py`
  checks no sidebar/content/banner overlap, one active entry per route and no duplicate menu targets.
* Demo data cannot be loaded in live mode unless INM_ALLOW_DEMO_DATA_IN_LIVE=true (refused + audited otherwise).
* `/system/features` statuses are computed or truthful (IMPLEMENTED_UNCERTIFIED ≠ certified). Never relabel upward
  without evidence.
* Printer raw samples: stored on change or when the heartbeat (default 6 h) expires — not every poll.
* Device-log retention is off until an admin sets 30–3650 days + approval reference; attribution evidence is kept.
* CX ACL/route/QoS/VSF and firmware (upload/stage/boot/recover) and Instant role/portal/multi-auth are typed,
  readback-verified, rollback-capable — and UNCERTIFIED until hardware evidence exists. 80 MHz is never configured.
* Firmware/AP reboot: journal BOOT_SENT/REBOOT_SENT and commit BEFORE sending; never resend; verify version+serial.
* Non-Aruba adapters (generic SNMPv3, Cisco IOS-XE SNMP, UniFi) are READ-ONLY; SNMP plans bind a saved SNMP profile.
* Purchase requests: one open request per printer supply; the requester never approves their own manual request.
* Print jobs: OFF until purpose (20+ chars) + approval reference; document text is inspected in memory and
  discarded (SHA-256 + masked tokens only); job-level reads admin/auditor only and audited; retention purge daily.
* Active probes touch only registered REAL devices at allow-listed addresses (INM_SCHEDULED_ACTIVE_PROBE, default off);
  a probe never fills a missing counter. Clock drift > tolerance blocks VERIFIED attribution.
* Release packages are verified with `scripts/release_signing.py verify` (Ed25519); the private key never ships.

## 1.3.0-rc18 invariants (keep them)

Report: `docs/RELEASE_1.3.0_RC18_CHANGELOG.md`; matrix `docs/ACCEPTANCE_MATRIX_1.3.0_RC18.md`; handover
`docs/HANDOVER_1.3.0_RC18.md`; register `docs/Unified_Acceptance_Register_v5.csv` (all v4 IDs preserved).
* ML (`backend/ml`): features only from data before the cutoff; labels None when the window was not observed;
  temporal split + embargo; a model is PRIMARY only when validated on a real-data holdout AND healthy. Synthetic/demo
  models are never production-validated and cannot be promoted in live mode. Retraining makes candidates only;
  promotion is an independent human decision. Without a validated PRIMARY model the statistical engine decides.
* RADIUS/AAA/OSPF/printer SET are ordinary governed writes (preview, approval, identity check, readback, rollback,
  audit). Secrets are write-only and never logged; a missing secret refuses the plan before anything is sent.
  OSPF only where the model supports it (CX 6200); BGP is UNSUPPORTED_BY_MODEL. Unsupported settings are reported per
  model, never simulated.
* Optics: NOT_MEASURED / UNSUPPORTED are never healthy; alerts resolve only on a measured healthy reading.
* Purchasing and print-job routers are mounted only when `INM_FEATURE_PURCHASING` / `INM_FEATURE_PRINT_JOBS` = true.
* Integrations: credentials write-only (Fernet); API tokens are read-only (GET/HEAD, viewer cap, path denylist).
* Releases: install/update/rollback/offline install verify the Ed25519 signature before using any content and fail
  closed. `update.sh`/`rollback.sh` refuse while a change is in flight (`inm change-quiesce-check`).
* SSH: modern algorithms only; legacy only per approved device + `INM_SSH_LEGACY_COMPAT=true`, audited.
* Multi-device deployments: HIGH risk (cluster scope, multi-site, ≥ `INM_DEPLOYMENT_HIGH_RISK_DEVICES`) needs a
  release approval of the current impact digest by someone who neither created it nor approved a plan in it.
* Automatic onboarding never retries a REST-rejected password over SSH and holds back an account after
  `INM_ONBOARDING_MAX_AUTH_FAILURES` rejections in the window (any host, any run) — never risk an AAA lockout.
* Device syslog (Alloy -> Loki) reaches timelines/attribution through `infrastructure/syslog_bridge.py`. Only an event
  whose Alloy `source_ip` is a registered management address may be attributed; hostname-only matches are timeline-only.
* Schema: migrations are expand-only within a release line; an expand migration writes `schema:compat`
  (head, phase, compatible_readers). `assert_schema_current` accepts a newer schema only inside that window.
* Every column that stores enumerated strings must fit its longest value on PostgreSQL (`scripts/audit_column_lengths.py`);
  run the contract suite on PostgreSQL (`INM_TEST_PG_ADMIN_URL`) before release — SQLite does not enforce lengths.
* `scripts/run_full_test_suite.sh` is the one-command evidence run; never remove an integration test because the
  current workspace cannot run it.
