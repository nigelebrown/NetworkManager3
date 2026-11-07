# Intentional differences from the SIMS baseline (Phase 1)

Every legacy screen, route and test is preserved. These are the deliberate, reviewed differences. Each needs
product-owner acceptance as part of the Phase 1 parity sign-off (INTEG-P1-02/03).

| # | Area | Baseline (SIMS) | Integrated (INM Phase 1) | Why | Evidence |
|---|---|---|---|---|---|
| 1 | Polling | APScheduler inside the FastAPI process | Network worker container owns polling (lease per interval); API role never polls. `SIMS_ROLE=standalone` keeps the original behaviour for developers | K.2 "move timers and device access out of FastAPI startup"; no duplicate jobs when API scales/restarts | `tests/contract/test_demo_parity.py` (identical outputs), `test_second_worker_does_not_duplicate_polls` |
| 2 | Device writes | Gated by `SIMS_ALLOW_CONFIG_CHANGES` | Additional hard `SIMS_DEVICE_WRITE_LOCK=true`: startup refuses `ALLOW_CONFIG_CHANGES=true`; apply / REST-enable routes return 403 `device_write_locked` and are audited; all three client-level guards use one `device_writes_permitted` gate | CLAUDE.md: all real device writes disabled in Phases 1-2 | `test_device_write_routes_refused_and_audited`, `test_aruba_client_refuses_writes_under_lock`, verify.sh |
| 3 | Live mode | `SIMS_MODE=live` allowed | Refused while `SIMS_INM_PROFILE=demo` | Phase 1 has no device access; Phase 2 adds the lab-readonly profile | `test_demo_profile_refuses_live_mode` |
| 4 | Alembic head | `0003` | `0004` (expand-only: adds INM tables/columns; never rewrites legacy data) | Audit chain, identity, generic inventory, idempotency | legacy `test_migrations.py` line 43 assertion updated from `0003` to `0004` — the only edit to a legacy test |
| 5 | Audit | Rows without request/session context | Hash-chained AuditEvent v1 fields (request ID, role, session hash, channel, prev/event hash); legacy rows tagged `LEGACY` | F016; tamper evidence | `test_audit_hash_chain_detects_modification_and_deletion` |
| 6 | Login | JWT only, no logout | Logout revokes the token server-side; bootstrap/created/reset accounts must change password at first sign-in | F003; no shared identities | `test_logout_revokes_token...`, `test_forced_password_change_blocks_everything_else` |
| 7 | Roles | admin / operator / viewer | + auditor (audit explorer) and approver (reserved for Phase 4); optional per-user site scope | F003 / P1-06 "unscoped technician" | `test_site_scoped_user_sees_only_granted_site` |
| 8 | Config backups | Redacted snapshots shown as backups | Same snapshots, labelled `SANITIZED_DISPLAY` / `MIGRATED_REDACTED`, `restorable=false`, never restore candidates | Appendix O.2 | `test_legacy_backups_never_restorable`, `test_migration_marks_preexisting_backups_not_restorable` |
| 9 | AI assistant | Calls Ollama on localhost | Same code calls the isolated `ai` service, which forwards only `/api/tags` and `/api/chat` to an optional internal Ollama; the AI container has no DB/bus/device credentials or route | K.2 AI isolation | `test_ai_gateway_forwards_only_two_ollama_calls`, `test_ai_service_refuses_platform_secrets`, verify `ai:isolation-contract` |
| 10 | Navigation / branding | Horizontal 9-item menu, "SIMS Wi-Fi Manager" | Grouped sidebar (K.4); product name "Intelligent Network Manager"; unfinished modules show PLANNED + phase | K.1 / K.4 | E2E screenshots `evidence/e2e/` |
| 11 | Page refresh | `useLoad` polls every 30 s | WebSocket invalidation re-queries affected pages immediately; polling remains as fallback (stretched ×4 while live) | F010 | `test_live_update_without_reload`, `test_reconnect_after_api_restart_resyncs` |
| 12 | Rate limiting | Same | The app-wide limiter dependency takes `HTTPConnection` so it also applies to WebSockets | Bug found during E2E | `test_websocket_requires_valid_token` |
