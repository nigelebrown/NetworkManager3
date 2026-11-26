# 1.3.0-rc16 — migration, deployment, backup and rollback

The supported path is unchanged from `docs/SERVER_DEPLOYMENT_RUNBOOK.md`: Git is the only source, the server pulls an
approved immutable tag, and no ZIP or source edit is applied on the CentOS host. The ZIP produced with this candidate
is for review and for committing to a branch; it is **not** an installation package.

## 1. What changes on upgrade

| Item | rc15 → rc16 |
|---|---|
| Management schema | **0014 → 0014 (no migration)** |
| Telemetry schema | **t002 → t002 (no migration)** |
| Python lock | `pyasn1` 0.6.4 → 0.6.5 (image rebuild required) |
| Frontend | rebuilt bundle (port groups, import mapping, printer profile protocols) |
| Settings / env | none required. Optional per-site alert override key `clear_threshold`. |
| Existing data | Saved printer SNMPv3 profiles keep SHA-256/AES-128; port groups keep their members; no data rewritten |
| Device-write posture | unchanged (locked unless the existing lab pilot procedure enables it) |

Because the schema is identical, rc15 and rc16 can be rolled back to each other with the standard rollback command.

## 2. Getting the candidate into Git (Codex / engineer workstation)

```bash
git clone github-networkmanager3:nigelebrown/NetworkManager3.git && cd NetworkManager3
git checkout eaf40a93a1420aabe6b02a9386d1a5982d46d96d
git checkout -b release/1.3.0-rc16
# unpack the rc16 ZIP over the working tree (it contains the full tree, no .git)
rsync -a --delete --exclude .git /path/to/NetworkManager3-1.3.0-rc16/ ./
git status && git diff --stat            # review against docs/RELEASE_1.3.0_RC16_CHANGELOG.md
git add -A && git commit -m "1.3.0-rc16: port groups, import mapping, SNMPv3 protocols, hysteresis, rate safety"
git push -u origin release/1.3.0-rc16    # open a PR; CI must pass on this exact SHA
```

Tag only after CI passes and the change is reviewed: `git tag -a v1.3.0-rc16 -m "..." && git push origin v1.3.0-rc16`,
then record the tag in `deployment/releases/approved-demo-tag` through the normal review.

## 3. Pre-deployment checks on the server

```bash
cd /root/NetworkManager3
bash scripts/inm.sh doctor          # preflight
bash scripts/inm.sh status          # record current SHA, version, schema 0014/t002, container health
bash scripts/inm.sh backup          # REQUIRED: encrypted backup of management DB, telemetry DB, vault, config
sudo bash deployment/centos9/restore.sh --archive /var/lib/inm/backups/<backup>.tar.enc --target test
                                    # REQUIRED: prove the backup restores into disposable volumes
```

Record: backup file name, its SHA-256, restore-test transcript, restored row counts, audit-chain result.
**Keep the Fernet key (`SIMS_FERNET_KEY`) and vault master key with the backup's escrow**: saved credentials and
printer SNMP secrets cannot be decrypted after a restore without the same keys.

## 4. Deploy

```bash
bash scripts/inm.sh update --ref v1.3.0-rc16
bash scripts/inm.sh verify          # health + synthetic end-to-end round trip
```

Post-deploy smoke (browser, admin):
1. Platform health green; `/api/v1/ready` → `ready: true`.
2. Port monitoring groups page loads for a viewer account (read-only) and an admin.
3. Device inventory → Import: upload a 3-row test CSV, map columns, confirm the preview — **do not import** on the
   live server unless intended.
4. Printers → Inventory → "Manual registration, credential profiles…" → Discover printers → profile form shows the
   SNMPv3 security level and protocol selectors. Existing profiles list unchanged.
5. Alerts page shows no burst of resolutions/re-openings after the upgrade (hysteresis only reduces churn).

## 5. Migration validation (what was proven, and how to repeat it)

On a disposable PostgreSQL server (never production):

```bash
python scripts/validate_pg_migration_restore.py \
  --admin-url postgresql+psycopg://postgres@127.0.0.1:55432/postgres --pg-bin /usr/lib/postgresql/16/bin
```

Proven in the review workspace (PostgreSQL 16.15): 0004→0014 preserves existing sites; 0013→0014 preserves legacy
certification evidence, separates HARDWARE/EMULATED scope and refuses downgrade; an application-seeded head database
survives `pg_dump -Fc`/`pg_restore` with identical records and a valid audit chain. TimescaleDB telemetry and the
installer's encrypted archive were **not** exercised (no TimescaleDB/Docker in the workspace) — §3's restore test covers them.

## 6. Rollback

| Situation | Action |
|---|---|
| UI/API regression after upgrade, data intact | `bash scripts/inm.sh rollback --to <rc15-tag> --acknowledge-schema-compatible` (schema identical 0014/t002). Then `bash scripts/inm.sh verify`. |
| Data problem | Stop, do not write further. `sudo bash deployment/centos9/restore.sh --archive <pre-update backup> --target test` to inspect; restore live only with `--target live --confirm-overwrite-live-data` and an approved change. |
| SNMPv3 printer profiles created in rc16 with non-default protocols | rc15 ignores the extra fields and uses SHA-256/AES-128 → those printers will fail to poll on rc15. Recreate or avoid such profiles before rolling back. |
| Port groups using rc16-only fields (`description`, `utilization_threshold`) | rc15 ignores the fields; viewers lose access (rc15 is admin-only). No data loss. |

## 7. Monitoring and troubleshooting for administrators

| Symptom | Check | Typical cause |
|---|---|---|
| Port group member "Stale" | Collection page → plan for that switch; worker logs `collection … category=` | worker stopped, credential failure, switch unreachable |
| "Unsupported" on PoE | Device 360 → Ports → PoE status empty | non-PoE port or PoE not reported by firmware (A-CX-06) |
| Utilisation "Not measured" for one interval | normal after counter clear / speed change / first poll | `COUNTER_RESET`, `SPEED_CHANGED` notes |
| Printer never answers with a v3 profile | Profile protocols vs printer config | protocol mismatch looks like unreachable (A-PRN-02) |
| Printer faults remain open | Printer → Raw SNMP data → incomplete metrics | interrupted alert-table walk; faults stay open by design until a complete read |
| Login locked | Users page; wait `LOCK_MINUTES` or admin reset | repeated failed passwords (account lockout) |
| Management DB growth | `SELECT pg_size_pretty(pg_total_relation_size('printer_metric_samples'))` | printer raw samples (~12.4 KB/poll); review retention (A-OPS-02) |
