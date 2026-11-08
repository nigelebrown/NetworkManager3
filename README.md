> **Fast-track: usable AP + switch demo on CentOS, with no PC transfers.**
>
> From your existing CentOS Git checkout:
>
> ```bash
> cd /root/NetworkManager3 && git pull --ff-only origin main && bash scripts/rebuild-demo.sh
> ```
>
> This replaces **only INM Docker containers**, preserves named volumes, installs from Git,
> runs end-to-end verification, and sets a restricted demo login:
> **`admin` / `demo123!`**. The web interface is available through an SSH tunnel at
> **https://localhost:8443** (the server listens on **127.0.0.1:8443** only while using
> the deliberately weak demo password).
>
> Open **Inventory → Add device** to add an AP-505/AP-535 or CX 6100/CX 6200,
> either by entering real observed readings or by an approved read-only REST probe.
> The probe defaults to OFF until you run:
>
> ```bash
> cd /root/NetworkManager3 && bash scripts/allow-readonly-device-range.sh 10.103.5.20/32
> ```
>
> Replace that example address with your actual AP/switch management IP.
> **No device configuration writes are enabled.** See
> [the complete quick-start guide](docs/QUICK_START_REAL_DEVICES.md).
>
# Intelligent Network Manager — Git-managed Phase 1 Demo

The complete application source is hosted in the **private**
[nigelebrown/NetworkManager3](https://github.com/nigelebrown/NetworkManager3) repository.
**No local PC, ZIP uploads or manual source transfers are required.** Device
configuration writes are locked in this Phase 1 demo.

The CentOS server's GitHub read-only SSH deploy key is already configured.

**First installation — one command after cloning:**

```bash
# Only required once if /root/NetworkManager3 is not already present:
git clone github-networkmanager3:nigelebrown/NetworkManager3.git /root/NetworkManager3
# On your already-cloned server:
cd /root/NetworkManager3 && bash scripts/inm.sh install
```

**Operations, all from this Git checkout:**

| Operation | Command |
|---|---|
| Install or same-version repair | `bash scripts/inm.sh install` |
| Approved release update | `bash scripts/inm.sh update` |
| Update to an explicit approved Git tag | `bash scripts/inm.sh update --ref <tag>` |
| Read-only diagnostics | `bash scripts/inm.sh doctor` |
| Verify services and data pipeline | `bash scripts/inm.sh verify` |
| View status | `bash scripts/inm.sh status` |
| Encrypted backup | `bash scripts/inm.sh backup` |
| Gate-compatible rollback | `bash scripts/inm.sh rollback --to <tag> --acknowledge-schema-compatible` |

Updates use Git tags (not arbitrary branch changes), with a backup, migration
compatibility checks and health verification. Running `update` without a tag reads
`deployment/releases/approved-demo-tag` from GitHub's `main` branch. No tag is
approved yet; the command refuses to proceed until an approved tag is published.

**Release status:** GitHub CI has passed Python regressions, React build, full Docker
Compose synthetic integration and automated browser workflows. Actual installation
on the target CentOS Stream 9 VM still requires acceptance testing. Base images and
build packages come from approved registries; **application source comes only from Git**.

# Intelligent Network Manager — Phase 1 (Connected platform + SIMS feature parity)

Release `1.0.0-phase1`. DEMO profile only. **All device writes are locked.** Lineage: SIMS Wi-Fi Manager (frozen at
git tag `sims-baseline`).

## Install on CentOS Stream 9 (one command)

```bash
cd /root/NetworkManager3 && bash scripts/inm.sh install
```

The installer checks the VM, installs Docker CE if needed, generates unique secrets (`/etc/inm`, mode 0600 —
never printed), builds the three application images, starts the seven roles, verifies them end to end and
writes a release receipt. Re-running it is safe. It prints how to reach the UI (bound to 127.0.0.1:8443 by
default; use an SSH tunnel) and where the one-time admin password is.

| Operation | Command |
|---|---|
| Preflight report | `bash scripts/inm.sh doctor` |
| Verify readiness | `bash scripts/inm.sh verify` (non-zero exit names each failed dependency) |
| Status | `bash scripts/inm.sh status` |
| Backup (encrypted) | `bash scripts/inm.sh backup` |
| Prove a backup restores | `sudo bash deployment/centos9/restore.sh --archive <file> --target test` |
| Update to a tag | `bash scripts/inm.sh update` |
| Roll back | `bash scripts/inm.sh rollback --to <tag> --acknowledge-schema-compatible` |

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
