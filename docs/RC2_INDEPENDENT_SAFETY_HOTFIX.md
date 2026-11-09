# NetworkManager3 rc2 — Independent software-safety hotfix

**Status:** Source-level corrective patch prepared for review and development testing. **NOT production certified.**

This hotfix was developed against the uploaded `v1.2.0-rc2` release source and the independent rc2 audit. It does not enable physical-device configuration writes. The default hardware write lock remains on.

## Fixes delivered

| Audit issue | Corrective change | Independent validation |
| --- | --- | --- |
| RC2-IND-01 | Atomic per-device lease acquisition with a unique holder, durable commit before I/O, holder fencing/renewal; reused in approved plans, lab certification and journal recovery | Concurrent independent SQLite sessions: 1 accepted / 1 denied; separate PostgreSQL/multiworker validation still required |
| RC2-IND-02 | Atomic notification claim (`PENDING -> SENDING`), claim expiry, unique worker token, stable outbound idempotency key and retry | 2 parallel worker sessions sent exactly one pending alert in isolated test; external exactly-once cannot be guaranteed |
| RC2-IND-03 | Site-aware workflow/group/deployment reads and operations using explicit legacy-to-physical-site mapping | Scoped user denied other-site workflow list/read; full role/site/route matrix still pending |
| RC2-IND-04 | Older device events cannot overwrite state with a newer observation | Old failure ignored, new failure accepted |
| RC2-IND-05 | Backend step prerequisites and multi-device plan-completion invariant; pending/failed plans block DONE | Independent tests including missing device, failed plan and duplicate plan IDs |
| RC2-IND-06 | CX rejects HTTP redirect, HTML login and success without a session cookie | Mocked login-response tests |
| RC2-IND-07 | Structured secret-field masking in CX error logging | Redaction probes for JSON, query-form and header-like values |
| RC2-IND-08 | Webhook path/query withheld from channel-list responses; sensitive exception URLs are never recorded | Secret URL masking and exception tests |
| RC2-IND-09 | Outbound webhook guard: HTTPS by default, explicit host allowlist, no redirects, reject non-public DNS targets | Loopback target rejected in isolated test; DNS rebinding still requires network egress controls |
| RC2-IND-10 | Queue WebSocket invalidations until management DB transaction commit | Independent writer/reader checks notification observes committed row |
| RC2-IND-11 | CX login verified as `application/x-www-form-urlencoded` body, CSRF and session cookie checked | Strict in-process CX emulator and session-expiry tests; firmware validation pending |
| RC2-IND-13 | Discovery owner identity includes host, process and unique instance token | Code inspection; two-replica crash test pending |
| RC2-IND-14 | Cancelling wizard revokes unexecuted pending/approved plans and refuses cancellation while executing | Source check; live API race test pending |
| RC2-IND-15 | Release schema metadata and device configuration acceptance panel use current per-device certification; outdated "no hardware-write functions" assertions corrected | Static checks; UI build pending npm dependency installation |

Additional corrections: deferred configure-later workflows now resume through a dedicated preview/reapproval API and wizard button; cross-site notification retries are denied; cancellation uses a conditional plan-state update so it cannot revoke a plan that began executing. Notification UI labels `SENDING` and at-least-once delivery accurately, webhook test receiver gets an explicit development allowlist, and the schema-version unit assertions were migrated to `0011`.

## Schema migration

New expand-only management migration `0011_concurrent_safety.py` adds `claim_owner` and `claim_expires_at` to `notification_deliveries` and indexes the claim expiry. The deployment release manifest now declares `0011` and includes `0010` as an upgradeable starting point.

**Required:** Validate migration and rollback-by-backup on a disposable PostgreSQL 16 clone, then run a CentOS Stream 9 development deployment before any operational use.

## Validation performed in this environment

- `python -m compileall -q backend`: PASS.
- `bash -n` on deployment scripts: PASS (syntax only).
- Independent safety regressions: **13 PASS** (SQLite, mocked CX/emulator; no hardware).
- Existing selected unit tests: **129 PASS**, excluding `test_pipeline_units.py`; a test-only `bcrypt` import shim was needed because the package is absent in this runtime, so this does NOT validate production authentication/hashing.
- Full 444-test rc2 suite, distributed stack, PostgreSQL multiworker tests, browser build and physical Aruba certification: **NOT independently rerun here** due missing dependencies/services. Do not mark these PASS.

## Required final release gates

1. Full Python/contract/test-suite passing with **real bcrypt and all project dependencies**.
2. Strict CX and Instant emulators across firmware capability variations, CSRF/login/outage conditions.
3. PostgreSQL multithread/multiprocess lease and notification claim tests, including crash/TTL conditions.
4. Multi-site role matrix on all read/write/history/export/WebSocket endpoints.
5. Full stack collector → NATS → TimescaleDB/PostgreSQL → Loki → alert → WebSocket → UI contract validation.
6. `npm ci && npm run build` and browser E2E on the corrected frontend.
7. CentOS 9 installation, migration/backup/restore, rollback rehearsal and health verification.
8. Only then connect **isolated lab Aruba hardware** to certify individual firmware/model capabilities. Keep all production hardware writes **LOCKED** before certification.

## Not implemented / out of scope

This patch does not create Aruba Central parity or new firmware orchestration, 802.1X/MAB CX configuration, VSF, ACL/routing, deep client roaming analytics, or full Instant configuration restore. Their published feature status must remain accurate. No production-readiness declaration is made.
