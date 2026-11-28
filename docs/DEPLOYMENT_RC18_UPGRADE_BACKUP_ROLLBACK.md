# 1.3.0-rc18 — install, upgrade, migration, backup and rollback

The supported path is unchanged from `docs/SERVER_DEPLOYMENT_RUNBOOK.md`:

- Git is the only source.
- The CentOS Stream 9 server pulls an approved, immutable tag, or an owner-signed offline package (§3).
- No source edit is made on the host.

The rc18 ZIP produced by the build agent is for review and for committing to a branch. It is **not signed**, because
signing needs the owner's private key, which the build never had. It must not be installed as-is.

## 1. What changes on upgrade

| Item | rc17 → rc18 |
|---|---|
| Management schema | **0015 → 0016**, expand-only. New tables: ml_training_runs, ml_drift_reports, ml_inferences, api_tokens. New columns on ml_models and notification_channels. Three columns widened. Migration-window marker `schema:compat` |
| Telemetry schema | t002 (no change) |
| Python | `paramiko>=5.1,<6` (modern algorithms only) |
| Frontend | rebuilt; `react-router` 7 |
| Release trust | install/update/rollback/offline-install verify the signed release **before** using any of its content and stop if verification fails (§3) |
| Update safety | `update.sh` and `rollback.sh` refuse while a change is executing or awaiting recovery (`inm change-quiesce-check`, exit 5) |
| New env, safe defaults (`compose.yaml`) | `INM_FEATURE_PURCHASING=false`, `INM_FEATURE_PRINT_JOBS=false`, `INM_SSH_LEGACY_COMPAT=false` |
| Optional env | see the table below |
| Existing data | Nothing is rewritten. Legacy SIMS AP samples can be copied into telemetry with `inm migrate-legacy-samples` (§7) |
| Write posture | unchanged: locked unless the lab pilot procedure enables it |

| Optional env | Default | Effect |
|---|---|---|
| `INM_SCHEDULED_ML` | true | daily model scoring, drift check and retraining check (candidates only) |
| `INM_SYSLOG_BRIDGE` | true | worker reads device syslog from Loki into device timelines and attribution |
| `INM_SYSLOG_SILENCE_MINUTES` | 60 | SYSLOG_SILENT threshold |
| `INM_AAA_GAP_WINDOW_HOURS` | 24 | AAA_EVIDENCE_GAP look-back |
| `INM_ONBOARDING_MAX_AUTH_FAILURES` | 2 | rejected logins per account before onboarding holds it back; set **below** the AAA lockout policy |
| `INM_ONBOARDING_AUTH_FAILURE_WINDOW_MINUTES` | 30 | window for the count above |
| `INM_DEPLOYMENT_HIGH_RISK_DEVICES` | 10 | deployments of this size or larger need a release approval |
| Prometheus endpoint (Integrations → Monitoring switch, not an env var) | off | `/api/v1/metrics` for API tokens with the `metrics` scope or an admin session |

Because 0016 adds tables and columns that rc17 does not know, **rc17 refuses to start on a 0016 database**. rc17
predates the migration-window marker, so going back to rc17 means restoring the pre-update backup (§6).

From rc18 on, an expand-only migration in a later release declares `compatible_readers`. While that marker lists
0016, rc18 keeps running against the newer schema. Once a contract migration retires it, rc18 refuses to start.

## 2. Getting rc18 into Git (engineer workstation)

```bash
git clone github-networkmanager3:nigelebrown/NetworkManager3.git && cd NetworkManager3
git checkout -b release/1.3.0-rc18 <rc17 branch or tag>
git apply --index /path/to/NetworkManager3-1.3.0-rc17-to-rc18.patch      # or rsync the full tree (excluding .git)
git status && git diff --stat --cached          # review against docs/RELEASE_1.3.0_RC18_CHANGELOG.md
git commit -m "1.3.0-rc18" && git push -u origin release/1.3.0-rc18    # CI (full-test-suite.yml) must pass on this SHA
```

If rc16 or rc17 was never pushed upstream, apply the patches in order: rc16, then rc17, then rc18.

## 3. Owner-controlled signing and server trust (one-time setup, then per release)

The release owner signs on their **own offline workstation**. The private key never reaches a server, CI or the build
agent. Automated tests use disposable keys only.

```bash
# once, offline
python3 scripts/release_signing.py keygen --private ~/inm-release.pem --public inm-release.pub
python3 scripts/release_signing.py fingerprint --public inm-release.pub       # read this out to the server admin
# every release: sign the exact tagged tree, commit the two files at the tree root, then tag
python3 scripts/release_signing.py sign-git --private ~/inm-release.pem --repo . --ref release/1.3.0-rc18 --out .
# or for an offline package directory
python3 scripts/release_signing.py sign --private ~/inm-release.pem --dir <release-dir>
```

On the server (root), once:

```bash
sudo bash deployment/centos9/release-trust.sh init --public inm-release.pub --fingerprint <fingerprint confirmed out of band>
sudo bash deployment/centos9/release-trust.sh status
```

To rotate the key, the owner creates a rotation statement signed by the current key; the server applies it:

```bash
python3 scripts/release_signing.py rotation --private ~/inm-release.pem --new-public new.pub [--revoke-current] --out rotation.json
sudo bash deployment/centos9/release-trust.sh rotate --statement rotation.json
```

From then on, install, update, rollback and offline install all refuse a release whose `SHA256SUMS` signature is
missing, wrong, or made by an untrusted or revoked key, or whose files do not match the manifest. Each script exits
non-zero before executing any of the release's content.

Offline install (no Git access on the server):

```bash
sudo bash deployment/centos9/offline-install.sh --package NetworkManager3-1.3.0-rc18.tar.gz --mode live
```

## 4. Pre-deployment checks

```bash
cd /root/NetworkManager3
bash scripts/inm.sh doctor
bash scripts/inm.sh status        # record SHA, version, schema 0015/t002
bash scripts/inm.sh backup        # REQUIRED - the only way back to rc17 after 0016
sudo bash deployment/centos9/restore.sh --archive /var/lib/inm/backups/<backup>.tar.enc --target test   # REQUIRED drill
docker compose exec api python -m backend.cli change-quiesce-check    # exit 0 = no change in flight
```

Escrow `SIMS_FERNET_KEY` and the vault master key separately from the backups. An isolated restore cannot open the
vault without them (`tests/contract/test_rc18_vault_upgrade_and_isolated_restore.py`).

## 5. Deploy and verify

```bash
bash scripts/inm.sh update --ref v1.3.0-rc18    # verify signature -> quiesce gate -> backup -> checkout -> build -> 0015->0016 -> health gate
bash scripts/inm.sh verify                      # includes syslog-parse-errors (Alloy RFC 3164 rejections)
```

Post-deploy smoke checks (admin, browser):

1. Platform health is green and `/api/v1/ready` is `ready: true`. `inm.sh status` shows schema `0016` / `t002`.
2. `/api/v1/system/features` shows:
   - `printer_purchasing` and `print_jobs`: `DISABLED_PENDING_AUTHORIZATION`
   - `ssh_legacy_compat` off
   - `ai_learning`: `RULES_AND_STATISTICAL`, until a model trained on **real** data is validated and promoted
3. Integrations opens with no connections. The Notifications screen is routing only.
4. AP fleet → Automatic onboarding shows the account-lockout guard line.
5. Group deployment shows an Impact panel for a new deployment.

## 6. Rollback

| Situation | Action |
|---|---|
| Regression, data intact | Restore the pre-update backup, then update to the rc17 tag. Schema 0016 → 0015 is not reversible in place (the 0016 downgrade raises by design):<br>• `sudo bash deployment/centos9/restore.sh --archive <pre-update backup> --target live --confirm-overwrite-live-data`<br>• `bash scripts/inm.sh update --ref v1.3.0-rc17`<br>Data written after the upgrade is lost. Both steps are refused while a change is in flight. |
| Problem in one new feature | Leave rc18 running and turn the feature off:<br>• `INM_SCHEDULED_ML=false`<br>• `INM_SYSLOG_BRIDGE=false`<br>• disable the integration connection<br>• revoke API tokens |

## 7. Operating the new features

| Feature | How | Notes |
|---|---|---|
| Trained models | AI learning centre → Trained models: train (real fleet history), compare, promote (independent approver), roll back | A synthetic or demo model can never be promoted on a live installation. Without a validated PRIMARY model the statistical engine stays the engine of record |
| Integrations | Integrations → add connection (SMTP / webhook / Teams / syslog TCP / heartbeat) → Test → Enable | Secrets are write-only. Deliveries can be retried. API tokens are read-only and shown once |
| RADIUS / AAA / OSPF | Change plans → new plan with the rc18 operations | Same governance chain. OSPF on CX 6200 only. Every device write is uncertified until the hardware checklist passes |
| Printer settings | Printers → device → Settings: preview → approve → apply | Run the per-printer hardware test first (lab serials only) |
| High-risk deployments | Group deployment → Impact → a third person approves the release | Needed when cluster-scoped, multi-site or ≥ `INM_DEPLOYMENT_HIGH_RISK_DEVICES` |
| Emergency access | Platform → Emergency access (reason, ticket, ≤ 240 min) | Waives only the two-person rule for flagged approvals; another administrator must review the session afterwards |
| Legacy SSH device | Platform → SSH legacy compatibility (per device) **and** `INM_SSH_LEGACY_COMPAT=true` | Every legacy session is audited; global SSH policy is unchanged |
| Legacy SIMS samples | `docker compose exec api python -m backend.cli migrate-legacy-samples --dry-run`, then without `--dry-run` | Idempotent; unmapped APs reported; simulated rows never attached to real devices |
| Purchasing / print jobs | Only after written authorisation: set `INM_FEATURE_PURCHASING` / `INM_FEATURE_PRINT_JOBS=true` and restart | Not mounted otherwise |

## 8. Troubleshooting

| Symptom | Check | Typical cause |
|---|---|---|
| Update refused, exit 5 | `inm change-quiesce-check` | A change is executing or awaiting recovery. Finish it or resolve the recovery journal entry |
| Update refused before checkout | `release-trust.sh status`; `verify-release.sh` output | Package not signed by a trusted key, or modified |
| Device events on the Logs screen but not on the device timeline | `labels.source_ip` in Loki vs the device's management address | A NAT/relay hides the sender. Events matched only by hostname are not attributed (by design) |
| `syslog-parse-errors` in `inm verify` | Alloy `loki_source_syslog_parsing_errors_total` | Device sends a year-before-time RFC 3164 header (HW-SYS-01) |
| Onboarding shows AUTH_ATTEMPTS_CAPPED | AP fleet → lockout guard line | Saved credential wrong. Fix it and wait for the window to end |
| Deployment execute 409 "separate reviewer" | Group deployment → Impact | HIGH-risk batch. A third person must approve the release. If the batch changed, approve again |
