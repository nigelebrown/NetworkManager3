# NetworkManager3 1.3.0-rc18 — architecture (delta over rc17)

The platform shape is unchanged; see `docs/ARCHITECTURE_1.3.0_RC17.md` and `CLAUDE.md` for the base:

- FastAPI API
- network worker
- PostgreSQL management DB (Alembic) + TimescaleDB telemetry
- NATS outbox
- Loki/Alloy logs
- React/Vite SPA
- Docker Compose on CentOS Stream 9

No new service and no external cloud dependency was added. This document covers what rc18 adds and where it lives.

## New and changed components

| Component | Location | Runs in | Purpose |
|---|---|---|---|
| Breach-risk model | `backend/ml/breach_model.py` | API (training on request), worker (daily) | Leakage-free features, logistic regression (IRLS + L2), metrics, PSI drift |
| Model lifecycle service | `backend/ml/service.py`, `backend/api/learning.py` (`/api/v1/ai/ml/*`) | API + worker | Dataset assembly from telemetry, temporal split + embargo, validation gate, registry, inference, scoring, drift, health, scheduled retraining (candidates only), version comparison |
| Engine of record | `backend/api/intelligence.py` `run_predictions` | API + worker | Uses a PRIMARY (validated, healthy) model when one exists; otherwise statistical. Recommendations only from a validated engine |
| Integrations | `backend/infrastructure/notifications.py`, `backend/api/integrations.py`, `backend/security/api_tokens.py` | API + worker (delivery, heartbeats) | SMTP, webhook (bearer/HMAC), Teams, syslog TCP, heartbeat; encrypted per-connection secrets; delivery retry; read-only API tokens; Prometheus endpoint |
| CX RADIUS/AAA/OSPF | `backend/infrastructure/writers/aruba_cx.py` (`RC18_OPS`, `_render_rc18`) | worker via change executor | Documented AOS-CX REST resources (A-CX-14, A-CX-16) |
| Instant RADIUS | `backend/infrastructure/writers/aruba_instant.py` | worker via change executor | Allow-listed CLI lines over pinned SSH (A-IAP-16) |
| Optics | `backend/infrastructure/optics.py` | worker (collection family `optics`) | DOM parse, threshold evaluation, history, trend, alerts |
| Printer settings | `backend/printers/configuration.py` | API (preview/apply), lab hardware test | Standard-MIB SNMP SET with identity check, readback, rollback |
| Emergency access | `backend/api/emergency_access.py` | API | Time-boxed break-glass session; waives only the two-person rule; mandatory review |
| High-risk deployment gate | `backend/api/workflows.py` (`_impact`, `/deployments/{id}/impact`, `/release-approval`) | API | Cluster/site/size impact and independent release approval |
| Onboarding lockout guard | `backend/infrastructure/ap_autodiscovery.py` (`_auth_failures`, `_record_auth_failure`), `automatic_onboarding.py` | worker | Per-account rejection ledger (`app_settings` key `onboarding-auth-failures`) |
| Syslog bridge + logging health | `backend/infrastructure/syslog_bridge.py`, job `syslog_bridge` | worker (every cycle), standalone scheduler (60 s) | Loki → `device_log_events` → `change_attributions`; SYSLOG_SILENT and AAA_EVIDENCE_GAP alerts |
| Migration window | `backend/database/session.py` (`schema_compat_marker`, `assert_schema_current`), migration 0016 | API/worker start-up | Expand/migrate/contract interoperation |
| Release trust | `scripts/release_signing.py`, `deployment/centos9/release-trust.sh`, `common.sh` | server scripts | Ed25519 trust store with rotation/revocation; verification before any release content runs |
| Quiesce gate | `backend/cli.py change-quiesce-check`, `common.sh require_change_quiesce` | server scripts | Refuse update/rollback during an in-flight change |

## Data flow added for device syslog

```
device --syslog--> Alloy :1514 (RFC 5424) / :1515 (RFC 3164)
          labels: source="syslog", host, app_name, severity, source_ip (= TCP/UDP sender, rc18)
     --> Loki
     --> worker job syslog_bridge (cursor in app_settings 'syslog-bridge:cursor', pages of 500, forward)
          map: source_ip == registered mgmt address  -> event stored + attribution allowed
               else hostname/serial/IP in message     -> event stored (timeline only)
               else                                   -> counted UNMAPPED
     --> device_log_events (dedupe: sha256(loki ns | sender | host | line))
     --> change_attributions (VERIFIED only with unique named AAA session + device event naming that user)
     --> logging_health: inm_alerts SYSLOG_SILENT / AAA_EVIDENCE_GAP (auto-resolve)
```

## Data model (migration 0016, expand-only)

| Table / column | Purpose |
|---|---|
| `ml_models` + `target`, `version`, `dataset_source`, `production_validated` (NOT NULL, server default false), `validation_note`, `training_profile`, `health`, `health_detail` | registry |
| `ml_training_runs` | each training request (manual or scheduled) with outcome |
| `ml_drift_reports` | PSI per feature per check |
| `ml_inferences` | scored predictions (probability, confidence, outcome once observed) |
| `api_tokens` | read-only tokens (SHA-256 hash, scopes, expiry) |
| `notification_channels` + `config`, `secret_enc`, `last_test_at`, `last_test_ok` | integration connections |
| widened: `recommendations.rule_version` 64, `notification_deliveries.kind` 24, `change_plans.source` 32 | PostgreSQL length defects |
| `app_settings['schema:compat']` | `{head: "0016", phase: "expand", compatible_readers: ["0015"]}` |

## Security properties added in rc18

- No synthetic or demo model is ever production-validated. Promotion is refused in live mode.
- Every new device write runs through the existing chain: plan, approval, certification, checkpoint, apply,
  readback, rollback and audit. Secrets are write-only.
- API tokens cannot write, cannot exceed viewer, and cannot read denylisted paths. AI services still have no
  credential or configuration surface (`test_rc18_ai_isolation.py`).
- Release verification fails closed. The private signing key is never in the repository, CI or the server.
- Legacy SSH is per device, approved and audited. The global policy stays modern-only.
- The syslog bridge never attributes on a hostname written inside the message.
- Onboarding never drives an account into an AAA lockout.
- A HIGH-risk deployment needs a third person.
