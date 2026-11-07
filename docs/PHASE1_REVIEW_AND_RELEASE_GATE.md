# Phase 1 source review and release gate

**Reviewed candidate:** `INM-1.0.0-phase1.zip`  
**Review date:** 2026-11-07  
**Scope:** organization-neutral Intelligent Network Manager, Phase 1 demonstration environment.  
**Decision:** **Conditional acceptance for continued engineering only. Not approved for production or device writes.**

## What is implemented in the candidate archive

| Area | Source evidence | Review |
|---|---|---|
| FastAPI / React | `backend/main.py`, `backend/presentation/`, `frontend/src/` | Present; real-time WebSocket client and gateway code present |
| Deployment | `compose.yaml`, `deployment/centos9/*.sh` | Nine primary containers across seven logical roles; no clean-CentOS runtime evidence |
| Network workers | `workers/network_worker/main.py` | Demo worker and ingestion code present |
| Management DB | `backend/database/` | PostgreSQL + Alembic migration scaffolding present |
| Metrics | `backend/infrastructure/telemetry/timescale.py` | Separate TimescaleDB integration present |
| Logs | `configs/alloy/`, `configs/loki/` | Syslog intake and Loki integration present |
| Messaging | `backend/infrastructure/bus/nats_bus.py` | NATS JetStream contracts and worker implementation present |
| AI | `ai_service/`, `frontend/src/pages/AiLearning.tsx` | Rule-based recommendations, not proof of trained ML model |
| Audit | `frontend/src/pages/Audit.tsx` and backend audit models | Audit groundwork; complete technician attribution remains a future gate |
| Recovery | `frontend/src/pages/Recovery.tsx` | Planned UI; certified encrypted device backup/restore not yet complete |
| Switches | `frontend/src/pages/Switches.tsx`, `backend/infrastructure/adapters/demo_switch.py` | Demonstration adapter; real Aruba CX validation is Phase 2 |

## Test evidence and limitations

The candidate's `docs/PHASE1_EVIDENCE.md` reports:
- Original SIMS baseline: 169 tests passed.
- Integrated SIMS suite: 169 tests passed.
- New INM unit and contract: 48 passed.
- Native-process integration (without Docker containers): 13 passed.
- Browser E2E: 5 passed.

**Those are build-agent reports, not independent certification.** The same report explicitly states container images could not be downloaded and the application was not deployed on a clean CentOS Stream 9 VM.

The uploaded 196-test register records **23 PASS, 13 PARTIAL, 160 NOT_RUN** overall. For Phase 1 (41 tests), **23 PASS, 13 PARTIAL, 5 NOT_RUN**. These statuses must not be presented as all 196 tests passing.

Independent inspection in this review:
- All 10 `deployment/centos9/*.sh` scripts passed `bash -n` syntax validation.
- 116 Python source/test files parsed without syntax errors.
- Running the automated tests in the review environment stopped during test collection because the local environment lacks `bcrypt`. This **is not a test-suite failure**; the isolated verification environment needs the project dependencies installed before running the tests.

## Mandatory gates before first clean-server acceptance

1. Import the complete reviewed source (excluding secrets, evidence screenshots and the embedded Git bundle) into this private repository. Verify the tree; create a traceable release commit and tag.
2. Use a clean CentOS Stream 9 VM (recommended planning size: 16 vCPU, 64 GiB RAM, 100 GiB OS plus 500 GiB Docker storage). Require storage headroom, UTC time sync and verified registry access.
3. Execute `sudo bash deployment/centos9/doctor.sh` and `sudo bash deployment/centos9/install.sh --mode demo` against the tagged source.
4. Run `sudo bash deployment/centos9/verify.sh`. Record output and `docker compose ps`, inspect all service health probes and bound ports.
5. Re-run `install.sh --mode demo`. Confirm the same secrets and volumes remain and no migration/data loss occurs.
6. Exercise a complete demo event from NATS through worker, TimescaleDB, Loki and API to the WebSocket React dashboard. Confirm exactly-once *logical* records after replay/deduplication.
7. Stop PostgreSQL, TimescaleDB, Loki, NATS, AI and the worker individually. Verify accurate degraded status, bounded timeouts and recovery.
8. Complete encrypted `backup.sh` and `restore.sh --target test` exercise. Verify actual restored rows/logical state and key retention; do not test restoration against live data without an explicit change plan.
9. Test one-command `update.sh --ref <approved-tag>`, failed-update rollback, and schema incompatibility rejection. Ensure no automatic destructive migration or false success receipt.
10. Run `npm ci && npm run build`, all Python tests with pinned dependencies, container/image vulnerability scans, dependency checks, secret scanning and independent acceptance signoff.

## Important unresolved requirements

- Read-only discovery of AP-505/AP-535 and CX 6100/CX 6200 against real hardware is a **separate Phase 2 gate**; `demo_switch.py` is not a hardware driver.
- Configuration write locks must remain enabled until approval, technician identity, pre/post-change backup, restore verification, RBAC and independent audit tests pass.
- Device-local changes must be distinguished from application-issued changes. Use per-technician AAA accounting when available; don't attribute unidentified changes to a person based only on drift.
- ML learning is not established by a rules endpoint or an Ollama assistant. Training datasets, evaluation, model versions and observed-outcome feedback are later phases.
- A `docs/` update alone does not deploy an application: all source code and `deployment/centos9/` scripts must be present in GitHub before server installation.

## Independent reviewer decision

**Proceed with source import and a clean CentOS demo acceptance. Do not enable writes or advertise Phase 1 as production-ready.** Preserve all test logs, failure cases, installer receipts and signoff for an accountable release history.
