# Claude engineering instructions — NetworkManager3

## Product and constraints

Build the organization-neutral Intelligent Network Manager using Python/FastAPI, React/TypeScript, Onion Architecture, PostgreSQL, TimescaleDB, Loki/Alloy, NATS JetStream, isolated Python AI and a one-VM Docker Compose test deployment that can be separated into seven production roles.

Read `docs/ARCHITECTURE_AND_PHASED_BUILD.md`, `docs/PHASE1_REVIEW_AND_RELEASE_GATE.md` and `docs/SERVER_DEPLOYMENT_RUNBOOK.md` **before modifying source**.

## First work order

1. The complete application source is already committed to this private GitHub repository. Treat Git as the **only** application source-of-truth. Do not accept PC ZIP transfers or direct edits on the CentOS server; preserve existing review documents and app lineage.
2. Use the supported server entrypoint `bash scripts/inm.sh install`; future upgrades must be approved immutable Git tags using `bash scripts/inm.sh update`. Do not bypass backups, schema compatibility or acceptance gates. Confirm exact source commit SHA and required directories.
3. Set up Python dependencies from `requirements.lock` and frontend dependencies via `npm ci`. Run legacy, unit, contract and integration tests. Collect actual results; never copy PASS from an earlier report into a new report.
4. Implement or correct all blockers for a clean CentOS Stream 9 Docker install. Run installer preflight, installation, repeat installation, full service health, synthetic end-to-end data round-trip, encrypted backup and disposable restore, failed update/rollback and negative security tests.
5. Fix the immutable image digest guarantee: recording `images.lock` alone does not enforce pinned digest reuse. Resolve pinned image digest values in a reviewed release manifest and refuse unapproved image drift.
6. Preserve the demo network-isolation and device-write lock, fail closed on audit/permission failures.
7. Add accurate Phase 1 test evidence, unknowns and signoff request. **Do not mark Phase 1 accepted or start Phase 2 without independent acceptance.**

**Deployment architecture:** CentOS pulls application source directly from `nigelebrown/NetworkManager3` using a read-only SSH deploy key. No Windows PC, upload bundle, manual `git pull` of an already installed release or source change on the server is permitted. The approved demo tag pointer lives in `deployment/releases/approved-demo-tag` and may remain empty until explicitly released.\n\n## Definition of done for every subsequent module

- Contract and migration design reviewed.
- Working UI + backend endpoints + background worker contract.
- Positive/negative and security unit tests, integration and E2E tests.
- Authenticated WebSocket UI behaviour and reconciliation.
- Database migration/restart/backup/restore evidence.
- One-command install or update on a clean CentOS test instance, with rollback tested.
- Structured audit event linked to named operator / service identity.
- Evidence register updated with actual observed results, build SHA and tester.
- Vendor specific behaviour checked against supplied Instant AOS 8.10 guide or CX 10.14 OpenAPI; capability not assumed from unrelated models.

## Prohibited shortcuts

Do not fabricate successful device tests, hardware capabilities, backups, ML improvements or deployment results. Do not embed device credentials or personal information in Git. Do not enable live network-device writes in Phase 1. Do not silently bypass TLS, alter the approved RF channel restrictions, or allow AI to execute configuration changes directly.
