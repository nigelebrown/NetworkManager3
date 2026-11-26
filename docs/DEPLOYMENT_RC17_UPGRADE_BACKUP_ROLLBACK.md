# 1.3.0-rc17 — install, upgrade, migration, backup and rollback

The supported path is unchanged from `docs/SERVER_DEPLOYMENT_RUNBOOK.md`:

- Git is the only source.
- The CentOS Stream 9 server pulls an approved, immutable tag.
- No ZIP or source edit is applied on the host.

The rc17 ZIP is for review and for committing to a branch. It is **not** an installation package, and it is **unsigned**.

## 1. What changes on upgrade

| Item | rc16 → rc17 |
|---|---|
| Management schema | **0014 → 0015**, expand-only. Three new tables: purchase requests, print jobs, print-job findings |
| Telemetry schema | t002 → t002 (no change) |
| Python lock | unchanged |
| Frontend | rebuilt; route-level code-splitting; new navigation (one entry per purpose, related screens as tabs) |
| New env (all default safe) | `INM_ALLOW_DEMO_DATA_IN_LIVE=false`, `INM_SCHEDULED_ACTIVE_PROBE=false`, `INM_CLOCK_DRIFT_TOLERANCE_SECONDS=120` (already in `compose.yaml`) |
| Optional env | `INM_CX_REBOOT_WAIT_SECONDS`, `INM_CX_REBOOT_POLL_SECONDS`, `INM_IAP_REBOOT_WAIT_SECONDS` (600), `INM_IAP_REBOOT_POLL_SECONDS` (30) |
| Policies off by default | automatic purchase requests; print-job collection and inspection; device-log retention; active probes |
| Existing data | Nothing is rewritten. Printer raw samples are stored change-only from now on; old samples follow the existing retention |
| Write posture | unchanged (locked unless the lab pilot procedure enables it) |

Because 0015 adds tables that rc16 does not know, **rc16 refuses to start on a 0015 database**. That is the schema
gate working as designed. Rolling back to rc16 therefore means restoring the pre-update backup (§6).

## 2. Getting rc17 into Git (engineer or Codex workstation)

```bash
git clone github-networkmanager3:nigelebrown/NetworkManager3.git && cd NetworkManager3
git checkout -b release/1.3.0-rc17 <rc16 branch or tag>
# Option A — apply the patch on top of rc16:
git apply --index /path/to/NetworkManager3-1.3.0-rc16-to-rc17.patch
# Option B — unpack the full tree:
rsync -a --delete --exclude .git /path/to/NetworkManager3-1.3.0-rc17/ ./
git status && git diff --stat --cached          # review against docs/RELEASE_1.3.0_RC17_CHANGELOG.md
git commit -m "1.3.0-rc17" && git push -u origin release/1.3.0-rc17   # CI must pass on this exact SHA
```

If rc16 was never pushed upstream, apply both patches in order: first rc16 (against `eaf40a93`), then rc17.

Then sign and tag. `git tag -s` is checked when `INM_REQUIRE_SIGNED_TAGS=1`.

To sign a release package, the release owner runs this with their **own** key. The private key never goes onto the server:

```bash
python3 scripts/release_signing.py keygen --private ~/inm-release.pem --public inm-release.pub   # once
python3 scripts/release_signing.py sign --private ~/inm-release.pem --dir <release-dir>
```

Put `inm-release.pub` on the server at `/etc/inm/release-signing.pub`.

## 3. Pre-deployment checks on the server

```bash
cd /root/NetworkManager3
bash scripts/inm.sh doctor
bash scripts/inm.sh status        # record SHA, version, schema 0014/t002
bash scripts/inm.sh backup        # REQUIRED — the only way back to rc16 after 0015
sudo bash deployment/centos9/restore.sh --archive /var/lib/inm/backups/<backup>.tar.enc --target test   # REQUIRED restore drill
# when installing from a package rather than a tag:
bash deployment/centos9/verify-release.sh --dir <extracted-release> --public /etc/inm/release-signing.pub
```

Escrow `SIMS_FERNET_KEY` and the vault master key together with the backup.

## 4. Deploy

```bash
bash scripts/inm.sh update --ref v1.3.0-rc17    # backup -> checkout -> build -> migrate 0014->0015 -> health gate
bash scripts/inm.sh verify
```

Post-deploy smoke checks (admin in a browser):

1. Platform health is green, and `/api/v1/ready` returns `ready: true`.
2. Every sidebar entry opens its own screen. Entries with related screens show view tabs. No banner is hidden behind the menu.
3. `/api/v1/system/features` shows `print_jobs: OFF_BY_POLICY`, `vendors_cisco_unifi: READ_ONLY`, and firmware `IMPLEMENTED_UNCERTIFIED`.
4. Printers shows the new Supply purchasing and Print jobs tabs. Print jobs shows "COLLECTION OFF".
5. Data management shows Device-log retention as off.
6. `bash scripts/inm.sh status` reports management schema `0015` and telemetry `t002`.

## 5. Migration validation

On a disposable PostgreSQL server only:

```bash
python scripts/validate_pg_migration_restore.py --admin-url postgresql+psycopg://postgres@127.0.0.1:55432/postgres \
  --pg-bin /usr/lib/postgresql/16/bin
```

See `docs/TEST_RESULTS_1.3.0_RC17.md` for what was run in this workspace.

## 6. Rollback

| Situation | Action |
|---|---|
| UI or API regression, data intact | Restore the pre-update backup to return to rc16. This is a **schema change**, so `inm.sh rollback --acknowledge-schema-compatible` is refused by design:<br>• `sudo bash deployment/centos9/restore.sh --archive <pre-update backup> --target live --confirm-overwrite-live-data`<br>• then `bash scripts/inm.sh update --ref v1.3.0-rc16`<br>Data written after the upgrade is lost. Export purchase requests (CSV) first if any exist. |
| Problem limited to a new feature | Leave rc17 running and keep the feature's policy off:<br>• purchasing automatic = false<br>• print jobs disabled; revoke the ingestion tokens<br>• `INM_SCHEDULED_ACTIVE_PROBE=false`<br>Nothing new runs unless enabled. |
| Navigation confusion | Every old URL still works; bookmarks are unaffected. |

## 7. Operating the new features

| Feature | Enable | Where |
|---|---|---|
| Active probe + clock drift | `INM_SCHEDULED_ACTIVE_PROBE=true` in `.env` and restart the worker. Only registered devices inside `INM_READONLY_DEVICE_CIDRS` are probed | Device 360 → Independent active probe; alerts `PROBE_UNREACHABLE`, `CLOCK_DRIFT` |
| Device-log retention | Data management → Device-log retention (30–3,650 days + approval reference) | Daily in the worker |
| Automatic purchase requests | Printers → Supply purchasing → Automatic requests | Daily in the worker; CSV export; notification channels |
| Print jobs | Printers → Print jobs → Policy:<br>1. purpose and approval reference<br>2. create an ingestion token<br>3. configure the print-server agent | Retention purge daily |
| CX firmware | Firmware lifecycle → CX switch firmware | Hardware writes must be enabled for the lab pilot |
| AP cluster reboot | Firmware lifecycle → staged change → Approve cluster reboot (independent) → Reboot cluster | Never resent; recovery is a separate plan |
| Typed Instant restore | Backup & restore → version → Typed restore (Instant) | Creates a change plan that needs approval |

## 8. Troubleshooting

| Symptom | Check | Typical cause |
|---|---|---|
| Probe shows UNREACHABLE but polling works | Port in the Device 360 probe line (443 / 4343 / 22) | Management ACL allows the poller's API port but not a bare TCP connect from the worker |
| Clock UNKNOWN | Device returns no `Date` header (A-CLOCK-01) | Acceptable; attribution stays at the normal rules |
| Print jobs ingest 403 | Policy disabled | Expected until a purpose and approval are recorded |
| Print jobs ingest 401 | Token revoked or mistyped | Create a new token; tokens are shown once |
| Purchase request 409 | An open request already exists for that supply | Close or cancel it first |
| `demo-load` 403 in live | Refused by design | Use the lab profile, or set `INM_ALLOW_DEMO_DATA_IN_LIVE=true` knowingly (audited) |
