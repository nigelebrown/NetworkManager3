# Phase 1 work packages (as delivered)

Each package is one reviewable commit in this repository (see `git log`). Contracts first, then a bounded change,
then tests with captured output. Hardware steps are NOT_RUN by design (Phase 1 has no device access).

| WP | Commit subject (prefix) | Files / contracts | Tests (evidence) | Rollback note |
|---|---|---|---|---|
| WO-001 | `Baseline: SIMS-WiFi-Manager-main` | frozen source, tag `sims-baseline`, SHA-256 matches handoff manifest | `evidence/baseline/` — 169 passed, frontend build OK | n/a |
| WP1 | `INM WP1-3` | `backend/config.py` (role, profile, write lock), one write gate `device_writes_permitted` across REST/SSH/TFTP/firmware | `test_device_write_routes_refused_and_audited`, `test_write_lock_cannot_be_combined_with_writes`, `test_aruba_client_refuses_writes_under_lock` | revert commit; schema unaffected |
| WP2 | `INM WP1-3` | Alembic `0004` (expand-only), `models_inm.py`: Site/Cluster/Device/Capability, audit chain, grants, revoked tokens, processed events, event records, heartbeats, leases, recommendations, release events | legacy `test_migrations.py` (head now 0004), `test_migration_marks_preexisting_backups_not_restorable` | expand-only: previous code ignores new tables; downgrade = restore backup |
| WP3 | `INM WP1-3` | identity: request-ID middleware, RequestCtx, hash-chained `audit()`, logout revocation, forced password change, auditor/approver roles, site scope in `visible_sites()` | `test_viewer_denied...`, `test_auditor...`, `test_site_scoped_user...`, `test_forced_password_change...`, `test_logout_revokes...`, `test_audit_hash_chain...` | revert |
| WP4 | `INM WP1-3` | Onion `backend/domain` (EventEnvelope v1, DeviceIdentity v1, TelemetryPoint v1, Recommendation v1, ports), `application/` (ingest, health), `infrastructure/` (TimescaleDB, Loki, NATS, repositories, probes) | `tests/unit/test_domain_and_architecture.py`, `tests/unit/test_pipeline_units.py` | revert |
| WP5 | `INM WP4-6` | `workers/network_worker` (lease-guarded legacy poll, inventory projection, deterministic event IDs, durable ingest, DLQ, synthetic job, heartbeat) | `test_demo_parity.py`, `test_lease_one_winner_per_slot`, integration replay/outage/duplicate-worker tests | stop worker; API unaffected |
| WP6 | `INM WP4-6` | `backend/cli.py` migrate / verify / schema-status / record-release; `configs/{nats,loki,alloy}` | `evidence/native/verify_native_stack.log` (14/14 PASS on real services) | n/a |
| WP7 | `INM WP7` | frontend live channel (`services/live.ts`, `useLoad`), K.4 navigation, System health, Switches, Logs, Audit, Users, Recovery, AI learning, password change; `/api/v1` + `/ws/v1/events` | `tests/e2e/test_browser_live.py` 5/5 PASS + screenshots | legacy pages unchanged; revert frontend commit |
| WP8 | `INM WP8` | Dockerfiles, `compose.yaml`, nginx TLS edge, `deployment/centos9/*.sh`, `deployment/tools/schema_gate.py` | compose config + topology check, preflight port conflict, secret idempotency/modes, `test_schema_gate` | scripts are additive |
| WP9 | `INM WP9` | tests, docs, CSV status updates, release package | `evidence/` | n/a |

## INTEG-P1 mapping

| Test | Where |
|---|---|
| INTEG-P1-01 | `evidence/baseline/baseline_pytest.log` (169 passed before refactor); `evidence/tests/legacy_after_integration.log` (after) |
| INTEG-P1-02 | `tests/contract/test_demo_parity.py` + `evidence/e2e/03_legacy_*.png` |
| INTEG-P1-03 | `test_every_legacy_route_still_mounted` + full legacy suite on integrated code |
| INTEG-P1-04 | `test_device_write_routes_refused_and_audited`, verify.sh `worker_has_no_external_route` (Docker only) |
| INTEG-P1-05 | `test_lease_one_winner_per_slot`, `test_second_worker_does_not_duplicate_polls` |
| INTEG-P1-06 | `test_ws_hub_replay_and_resync`, E2E `test_reconnect_after_api_restart_resyncs` |
| INTEG-P1-07 | `test_ai_gateway_forwards_only_two_ollama_calls`, `test_ai_context_has_no_client_identities` (live Ollama model NOT_RUN) |
| INTEG-P1-08 | `test_legacy_backups_never_restorable`, `test_migration_marks_preexisting_backups_not_restorable` |
| INTEG-P1-09 | install/re-run/update on clean CentOS 9 — **NOT_RUN** (no CentOS VM or registry access from the build environment) |
| INTEG-P1-10 | `test_schema_gate` (gate logic); rollback.sh end-to-end on CentOS — NOT_RUN |
| INTEG-P1-11 | `test_ingest_failure_rolls_back_then_retry_applies_once`, `test_nats_outage_degrades_then_recovers_without_duplicates` |
| INTEG-P1-12 | `test_ws_hub_scope_and_privileged_topic_filtering`, `test_live_websocket_only_delivers_granted_site_notices`, `test_site_scoped_user_sees_only_granted_site` |
