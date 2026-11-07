# Intelligent Network Manager — Phase 1 (Connected platform + SIMS feature parity)

Release `1.0.0-phase1`. DEMO profile only. **All device writes are locked.** Lineage: SIMS Wi-Fi Manager (frozen at
git tag `sims-baseline`).

## Install on CentOS Stream 9 (one command)

```bash
sudo dnf -y install git
sudo git clone <your-approved-repo-url> /opt/inm && cd /opt/inm && sudo git checkout v1.0.0-phase1
sudo bash deployment/centos9/install.sh --mode demo
```

The installer checks the VM, installs Docker CE if needed, generates unique secrets (`/etc/inm`, mode 0600 —
never printed), builds the three application images, starts the seven roles, verifies them end to end and
writes a release receipt. Re-running it is safe. It prints how to reach the UI (bound to 127.0.0.1:8443 by
default; use an SSH tunnel) and where the one-time admin password is.

| Operation | Command |
|---|---|
| Preflight report | `sudo bash deployment/centos9/doctor.sh` |
| Verify readiness | `sudo bash deployment/centos9/verify.sh` (non-zero exit names each failed dependency) |
| Status | `sudo bash deployment/centos9/status.sh` |
| Backup (encrypted) | `sudo bash deployment/centos9/backup.sh` |
| Prove a backup restores | `sudo bash deployment/centos9/restore.sh --archive <file> --target test` |
| Update to a tag | `sudo bash deployment/centos9/update.sh --ref <tag>` |
| Roll back | `sudo bash deployment/centos9/rollback.sh --to <tag> --acknowledge-schema-compatible` |

Full guide: [docs/DEPLOYMENT_CENTOS9.md](docs/DEPLOYMENT_CENTOS9.md). Evidence and open items:
[docs/PHASE1_EVIDENCE.md](docs/PHASE1_EVIDENCE.md).

## Seven roles (one VM now, seven VMs later)

| # | Role | Container(s) | Notes |
|---|---|---|---|
| 01 | Web/API | `web` (nginx TLS + React), `api` (FastAPI + WebSocket gateway) | legacy `/api/*` preserved; `/api/v1/*` added |
| 02 | Network worker | `worker` | polling (lease per interval), event publisher, durable ingest, synthetic job; no external route |
| 03 | Management DB | `management-db` (PostgreSQL 17) | Alembic `0004` |
| 04 | Time-series DB | `timeseries-db` (TimescaleDB 2.22, PG17) | separate database, `telemetry_points` hypertable |
| 05 | Logging | `loki`, `alloy` (syslog TCP/UDP 1514) | |
| 06 | Event bus | `nats` (JetStream, authenticated users) | streams `INM_EVENTS`, `INM_UI`, `INM_DLQ` |
| 07 | AI | `ai` (+ optional `ollama` profile) | rules only (labelled RULE), no credentials, internal network only |

## Repository layout

```
backend/            SIMS backend preserved (aruba/, monitoring/, experience/, configmgmt/, firmware/, certs/, ai/, api/)
  domain/           INM onion core: entities, EventEnvelope v1, ports (no framework imports — enforced by tests)
  application/      use cases: ingest (idempotent), seven-role health
  infrastructure/   TimescaleDB store, Loki client, NATS JetStream bus, repositories, inventory projection, probes
  presentation/     /api/v1 routes, WebSocket hub
  cli.py            migrate | schema-status | verify | record-release | audit-verify | heartbeat-check
workers/network_worker/   role 02
ai_service/         role 07
frontend/           SIMS React app preserved + live WebSocket channel + new INM pages
deployment/         Dockerfiles, CentOS 9 scripts, schema gate
configs/            nats, loki, alloy, nginx
tests/              unit, contract, integration (real services), e2e (browser)
dev/native_stack.sh developer/CI harness: the same roles as native processes (not a deployment method)
docs/               parity inventory, source→target map, intentional changes, evidence, deployment guide
evidence/           test logs and screenshots produced for this release
```

## Developing

```bash
python3.13 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python -m pytest backend/tests tests/unit tests/contract          # no services needed
cd frontend && npm ci && npm run build
```

`SIMS_ROLE=standalone` (default) runs the original single-process SIMS app for quick UI work.
