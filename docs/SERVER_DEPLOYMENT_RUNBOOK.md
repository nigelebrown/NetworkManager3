# Server deployment runbook — CentOS Stream 9

**Applicability:** `INM-1.0.0-phase1` demo candidate after all source files have been committed to this private repository. **Not a certified production release.**

## VM specification

Recommended for the full single-VM demo: **16 vCPU, 64 GiB RAM, 100 GiB operating-system disk, 500 GiB separate SSD-backed Docker data disk, 1 GbE**. Restricted demo evaluations may run with fewer resources but require observation of headroom. A GPU is not required for the rule-based AI service.

Use a supported hypervisor. Confirm NTP/chrony and enough free space, and assign a dedicated non-root operator with sudo rights. Allocate the Docker data directory to the SSD-backed volume **before** installing the application.

## Prerequisites

- CentOS Stream 9 with outbound access to approved image registries and GitHub, or an approved offline image mirror.
- Git, sudo, and an authorized SSH deploy key / Git credential to clone the **private** repository.
- Phase 1 source committed: `compose.yaml`, `backend/`, `frontend/`, `deployment/`, `configs/`, `release.json`.
- A reviewed immutable Git release tag and matching source commit. Maintain the release SHA and image digests in the acceptance record.
- No device credentials and no real management-network connectivity in the demo environment.

## Initial checkout (after source import)

```bash
sudo dnf -y install git
git clone git@github.com:nigelebrown/NetworkManager3.git
cd NetworkManager3
git fetch --tags
git checkout <approved-release-tag>
sudo bash deployment/centos9/doctor.sh
sudo bash deployment/centos9/install.sh --mode demo
sudo bash deployment/centos9/verify.sh
sudo bash deployment/centos9/status.sh
```

For an HTTPS clone, use an authorized Git credential helper. Never put personal-access tokens in shell history or clone URLs. Confirm the installer is sourced from the reviewed commit.

## Browser access

Phase 1 binds the web UI to `127.0.0.1:8443` by default. From a secure workstation:

```bash
ssh -L 8443:127.0.0.1:8443 <operator>@<server>
```

Open `https://localhost:8443` and verify the self-signed pilot certificate fingerprint independently, or install a trusted certificate. The initial admin credential is stored only on the server in `/etc/inm/initial-admin-password`; change it immediately and create named individual accounts.

## Repeatable administration

```bash
# Safe no-change/diagnostics
sudo bash deployment/centos9/doctor.sh
sudo bash deployment/centos9/status.sh
sudo bash deployment/centos9/verify.sh

# Required before any update
sudo bash deployment/centos9/backup.sh
# Restore rehearsal in disposable environment:
sudo bash deployment/centos9/restore.sh --archive /var/lib/inm/backups/<backup>.tar.enc --target test

# Approved, tagged update only
sudo bash deployment/centos9/update.sh --ref <new-approved-tag>
# Rollback only when current schema is compatible:
sudo bash deployment/centos9/rollback.sh --to <old-approved-tag> --acknowledge-schema-compatible
```

The Git checkout must be clean before updating. Do not change source code on the server. A failed backup, failed image verification, failed schema compatibility check, failed health check or failed restore rehearsal must block promotion.

## Clean CentOS acceptance evidence

Capture:

1. Source commit, tag, `VERSION` and `release.json`; image digest list.
2. `doctor.sh`, `install.sh` and `verify.sh` transcripts, all service states and environment integrity checks.
3. Initial and repeated installation with unchanged secrets, data volumes and expected records.
4. Synthetic NATS event round-trip to worker, management DB, TimescaleDB, Loki and WebSocket client.
5. Isolated role failure/recovery for management DB, telemetry DB, bus, logging and AI.
6. Full encrypted backup + `restore.sh --target test`, with restored-row and audit-chain evidence.
7. Approved update/rollback, including rejection of incompatible schema versions.
8. Browser evidence showing current system health, module states, and **DEMO / RULES ONLY / PLANNED** markings.
9. Security scan / SBOM, inbound/outbound port review, secrets exclusion, and audit trace.
10. Signed independent acceptance decision and open risk list.

## Important implementation warnings

- `images.lock` is recorded by the candidate scripts, but the current source was **not proven** to enforce immutable image-digest reuse at every reinstall. Pin and verify image digests as part of release acceptance; do not rely on mutable `latest` tags.
- The candidate scripts have not been exercised in Docker on an actual clean CentOS Stream 9 VM. Both Docker registry resolution and installer verification are outstanding.
- The Linux Docker runner must not reach production network devices in Phase 1; preserve device-write locks.
- The optional Ollama component requires separate sizing and a fixed reviewed image/model version.

## Scale-out later

All seven logical roles start as separate containers on the demo VM. When moving production services to separate VMs, configure service DNS/endpoints, TLS trust, database credentials, firewall rules, central secrets, backups, HA and monitoring independently. Docker Compose alone does not orchestrate containers across multiple hosts.
