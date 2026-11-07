# Intelligent Network Manager — NetworkManager3

Organization-neutral, Python/FastAPI + React network-management platform.

## Status

**Repository initialization / Phase 1 candidate, NOT production certified.** The reviewed source package is `INM-1.0.0-phase1.zip` (provided separately); until its application source is committed to this repository, the repository contains only deployment guidance and **cannot be installed as an application**.

The Phase 1 archive documents seven Docker Compose service roles on one CentOS Stream 9 VM: web/API, network worker, management PostgreSQL, TimescaleDB, Loki/Alloy, NATS JetStream and AI. Device writes remain disabled in demo mode.

**First read:** [docs/PHASE1_REVIEW_AND_RELEASE_GATE.md](docs/PHASE1_REVIEW_AND_RELEASE_GATE.md) and [docs/SERVER_DEPLOYMENT_RUNBOOK.md](docs/SERVER_DEPLOYMENT_RUNBOOK.md).

## Required next step: import the application source

Use the companion local `publish_phase1_source.sh` script supplied with the reviewed package, or import and commit the unzipped application source using Git. Do not commit `*.gitbundle`, evidence screenshots, credentials, local secrets, generated databases, or machine-specific configuration.

When the code has been reviewed and committed, the intended operation on CentOS Stream 9 is:

```bash
sudo dnf -y install git
git clone https://github.com/nigelebrown/NetworkManager3.git
cd NetworkManager3
sudo bash deployment/centos9/install.sh --mode demo
sudo bash deployment/centos9/verify.sh
```

**Do not run these commands until the `deployment/centos9/` scripts and application source exist in this repository.** This private repository requires an authorized GitHub credential, SSH deploy key, or other approved authentication mechanism for server cloning.

The accepted install target is a single VM with 16 vCPU, 64 GiB RAM, approximately 100 GiB OS storage plus 500 GiB SSD-backed Docker data. These are planning estimates, not measured sizing.

## Release gates

- Fresh install and idempotent reinstall on clean CentOS Stream 9.
- Docker image build and pull; networking, bind addresses and secrets validated.
- Database migration, API, worker, log collection, NATS, AI and WebSocket round-trip verified.
- Encrypted backup and isolated restore test.
- Update, rollback and schema incompatibility behaviour tested.
- Source security and image scanning; no device credentials or production management-network access in demo.
- Independent QA approval **before** promotion from demo to a pilot involving real devices.

No AP/switch configuration writes should be enabled by default.
