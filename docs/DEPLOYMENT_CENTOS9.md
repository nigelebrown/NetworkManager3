# Deploying INM Phase 1 on CentOS Stream 9

This release runs the **DEMO profile only**: synthetic Aruba Instant AP-505/515/535 clusters and CX 6100/6200
switches, every item labelled DEMO, no connection to any real device, and every device-write path locked.

## 1. Prepare the VM

| | Recommended | Restricted pilot minimum | Demo evaluation floor |
|---|---|---|---|
| vCPU / RAM | 16 / 64 GB | 8 / 32 GB | 4 / 8 GB (installer warns) |
| Disk | 100 GB OS + 500 GB SSD for `/var/lib/docker` | 300 GB SSD total | 40 GB free |

- CentOS Stream 9, chrony synchronised (audit and log correlation rely on UTC).
- Outbound HTTPS during install to `download.docker.com` (Docker CE), Docker Hub (base and service images),
  `registry.npmjs.org` and `pypi.org` (image builds). For air-gapped sites, pre-load images with `docker load`.
- SELinux may stay enforcing (volumes use `:Z`). firewalld is not modified.
- Each operator needs their own sudo account: the scripts record `SUDO_USER` as the actor of every operation.

## 2. Install

```bash
# The server's repository-scoped SSH deploy key is already authorized.
# Only once, if a checkout does not exist:
git clone github-networkmanager3:nigelebrown/NetworkManager3.git /root/NetworkManager3

# All application source and installer scripts come from GitHub.
cd /root/NetworkManager3 && bash scripts/inm.sh install
```

There is **no local PC, transferred ZIP, or offline source bundle** in the installation
workflow. For subsequent approved releases, run:

```bash
cd /root/NetworkManager3 && bash scripts/inm.sh update
```

The updater reads the approved demo release tag from
`deployment/releases/approved-demo-tag` on GitHub, backs up state, validates database
compatibility, builds from the tagged source and verifies all services. No update is
performed if that channel does not contain an approved immutable Git tag.
For a specific approved release, run `bash scripts/inm.sh update --ref <tag>`.
**Do not manually `git pull` after an installation to simulate an upgrade.**

What happens (transcript in `/var/log/inm/install-<timestamp>.log`):

1. **PRECHECK** — OS, CPU/RAM/disk, time sync, SELinux, Docker, port 8443/1514 conflicts, registry reachability.
   A failed check stops the install before anything is changed.
2. **PREPARE** — Docker CE + Compose plugin if missing; `/etc/inm` (0700) with unique generated secrets in
   `inm.env` (0600), the one-time admin password in `initial-admin-password` (0600), a backup encryption key
   `backup.key` (0600) and a self-signed TLS pair in `tls/`. Existing secrets are never regenerated.
3. **DEPLOY** — pulls the service images, builds `inm/backend`, `inm/ai`, `inm/web`, runs the one-shot schema
   migration (management + telemetry + NATS streams), starts the roles in dependency order and waits for health.
   Resolved image digests are frozen in `/var/lib/inm/images.lock`.
4. **VERIFY** — `verify.sh` (below). Install fails if any check fails.
5. **COMMIT** — release receipt in the database (`release_events` + audit) and `/var/lib/inm/receipts.jsonl`.

## 3. First sign-in

The UI listens on `https://127.0.0.1:8443` only. From your workstation:

```bash
ssh -L 8443:127.0.0.1:8443 you@inm-server      # then open https://localhost:8443
sudo cat /etc/inm/initial-admin-password        # one-time; you must change it at first sign-in
```

Then open **Security → Users** and create a named account for every person (viewer, operator, auditor,
admin). Use "granted sites only" to scope a technician to specific sites. Do not share the admin account.

To expose the UI on the LAN instead, set `INM_WEB_BIND=<server-ip>` in `/etc/inm/inm.env`, install a CA-issued
certificate as `/etc/inm/tls/tls.crt` + `tls.key`, open 8443/tcp in firewalld for the admin network only, and
re-run `install.sh --mode demo`.

## 4. Operate

| Need | Command | Result |
|---|---|---|
| Is everything healthy? | `bash scripts/inm.sh verify` | PASS/FAIL per check; exit 1 names failures |
| Quick status | `bash scripts/inm.sh status` | containers, seven-role health, last receipts |
| Backup | `bash scripts/inm.sh backup` | `/var/lib/inm/backups/inm-backup-<ts>.tar.enc` + `.sha256` + manifest |
| Prove a backup restores | `sudo bash deployment/centos9/restore.sh --archive <file> --target test` | restores into disposable volumes, checks schema, row counts and the audit hash chain, then deletes them |
| Restore over live data | `... restore.sh --archive <file> --target live --confirm-overwrite-live-data` | takes a safety backup first; keeps replaced DBs as `*_replaced_<ts>` |
| Update | `bash scripts/inm.sh update` | backup → migrate → health-gated start → verify; automatic app rollback only when the schema gate allows |
| Roll back | `bash scripts/inm.sh rollback --to <tag> --acknowledge-schema-compatible` | refused (exit 3, nothing changed) when the target cannot run on the current schema |

**Keep `/etc/inm/backup.key` off this server** (password manager or vault). Without it, backups cannot be
decrypted. Backups contain `inm.env`, so treat them as secret.

What `verify.sh` checks: all seven roles running; secrets 0600/0700 and not in git; only `web` and `alloy`
publish ports; the worker has no external network route; each role's measured readiness (PostgreSQL schema
head, TimescaleDB extension + schema, Loki ready, NATS stream + ingest lag, worker heartbeat, AI isolation
contract); legacy API health; the device-write lock returning 403; a synthetic syslog line found in Loki; and a
synthetic event traced worker → NATS → PostgreSQL + TimescaleDB + Loki → the API's WebSocket hub.

## 5. Network exposure

| Port | Bound to | Purpose |
|---|---|---|
| 8443/tcp | `INM_WEB_BIND` (default 127.0.0.1) | HTTPS UI + API + WebSocket |
| 1514/tcp+udp | `INM_SYSLOG_BIND` (default 127.0.0.1) | Syslog test listener (device syslog arrives in Phase 3) |

Databases, NATS, Loki, the API container and the AI service are on internal Docker networks with no host port.

## 6. Optional: local AI assistant

Add `INM_OLLAMA_ENABLED=true` and `INM_OLLAMA_INTERNAL_URL=http://ollama:11434` to `/etc/inm/inm.env`, then
`docker compose --project-name inm --env-file /etc/inm/inm.env --profile ollama up -d ollama ai api` from
`/root/NetworkManager3`, and pull a model inside the `ollama` container. The Ollama container shares only the internal `ai`
network with the AI gateway; it cannot see the databases, the bus or any device network.

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| Install stops at PRECHECK | Read the `[FAIL]` lines; nothing was changed |
| `verify.sh` fails `role:network_worker` | `docker logs inm-worker-1`; heartbeat older than 45 s means the worker is stuck or down |
| UI header says "Reconnecting — polling" | WebSocket path blocked: the page still refreshes by polling; check `docker logs inm-web-1` |
| `event_bus` DEGRADED | ingest backlog > 1000: the worker is behind or down |
| Rollback refused (exit 3) | Expected when the schema moved on; follow the printed manual restore path |
