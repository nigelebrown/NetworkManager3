# Upgrade brief — make the wireless-operations release the standard (legacy 1.2.0-rc2 → standard)

Audience: the engineer or AI assistant updating the production repository `nigelebrown/NetworkManager3`.
Read `CLAUDE.md` first; every rule there still applies.

## 1. What you are given

| File | What it is |
|---|---|
| `legacy-to-standard.patch` | A unified diff from the current legacy tree to the standard (37 files, about +2,960 / −64 lines). Verified to apply with `git apply` and to produce the standard tree byte-for-byte. |
| `NetworkManager3-standard.zip` | The resulting tree, for reference. |
| `docs/AP_HEALTH_AND_DIAGNOSIS.md` | Design, root causes and safety invariants of the release. |
| `docs/WIRELESS_RELEASE_TEST_RESULTS.txt` | Observed results: unit/contract, backend, and a 22-step browser end-to-end run. |

**Apply the patch. Do not re-implement it from this description.** The patch already merges the legacy branch's own
improvements (section 3). Re-implementing it by hand would lose behaviour that the tests check.

## 2. What the legacy tree is missing (added by the patch)

The legacy tree already had an earlier partial port: the fleet, AP health, incidents, guides, diagnosis, client
connectivity and Device 360 pages. These pieces were still missing or wrong:

1. **Cluster-aware AP auto-onboarding** — `backend/infrastructure/ap_autodiscovery.py` and the `/api/v1/wireless/autodiscover/*` endpoints, plus the Auto-onboard panel on Access points.
   - Steps: an unauthenticated SSH host-key scan, then bulk approval of fingerprints, then the run.
   - Credential profiles are tried in order.
   - The cluster's Virtual Controller (VC) is found from `show aps` (the IP marked `*`). The cluster is registered once and every member AP is linked; a second pass links APs that could not be probed themselves.
   - REST enablement is attempted only through the existing gated `ssh.enable_rest_api`. If that is refused, the cluster is read over SSH.
2. **Read-only SSH CLI collector** — `SshShowClient` in `backend/aruba/ssh.py`. `client_for_site` uses it when a cluster's transport is `ssh_cli`.
   - It uses the same command allowlist as REST and handles `--More--` paging.
   - The host key must be pinned before the password is sent.
   - A changed key is refused ("Nothing was sent"), flagged `CHANGED` on the next scan, and can be re-approved.
3. **Duplicate AP records fix** — `backend/infrastructure/persistence/inventory.py`. Cluster projection adopts the imported or onboarded record instead of creating a second one: vendor and serial are compared case-insensitively, with the MAC-as-hostname as a fallback.
   - The operator's physical site is kept.
   - Old duplicates are marked `lifecycle=merged` and excluded in `device_state.canonical_inventory`.
4. **Discovery rules** — `backend/api/discovery_rules.py` and `frontend/src/pages/DiscoveryRules.tsx`, routed at `/discovery`. The old planner moves to `/discovery/planner`.
   - Saved subnets (up to /22, split into /24 batches) and credential profiles are created independently.
   - A rule joins subnets, an ordered list of profiles and follow-up actions, with a manual, daily or weekly schedule.
   - Runs use the existing durable discovery jobs. Failed logins are retried with the next profile, and only verified identities are registered.
   - The scheduler hook is in `scheduled_jobs.discovery_rules`, the API scheduler and the worker housekeeping loop.
5. **Syslog that actually reaches the platform:**
   - `configs/alloy/config.alloy` adds an RFC 3164 listener on 1515, published in `compose.yaml` as `${INM_SYSLOG_BSD_PORT:-514}`.
   - `.env.example` documents `INM_SYSLOG_BIND`; the default `127.0.0.1` is unreachable from devices.
   - `api_v1` maps syslog to devices by hostname, IP or serial for every visible device, not only cluster-projected ones.
6. **The UNKNOWN status dot** is grey everywhere (`index.css`); it was green.
7. **Navigation:**
   - `/aps` is the cluster-grouped fleet, with Auto-onboard.
   - The old per-AP controller table moves to `/aps/controller`.
   - `/aps/fleet` redirects to `/aps`.
   - Links point to Auto-onboard instead of the removed "AP fleet by cluster" item.
8. **Tests and evidence:**
   - `tests/unit/test_wireless_operations.py` (60 tests).
   - `tests/e2e_sim/`: a simulated-hardware browser harness. It is not collected by pytest, and its paths are those of the build environment.
   - The two docs above.

## 3. Legacy-branch improvements that the patch KEEPS (do not remove)

- `require_global_scope` on the fleet-wide routers. It is also applied to `discovery_rules`.
- Per-band channel utilisation on the Wi-Fi dashboard computed from `ap_samples` (`backend/api/data.py`).
- Dashboard coverage computed from the observed AP ids.
- CX serial read from the chassis (`read_cx_system`).
- HTTP 503 → `ArubaRestUnavailableError`.
- Ping status in the REST preflight.
- Per-device SSH trust (`onboarding-ssh-pin:<device>`, FIRST_SEEN / SAVED / CHANGED) in `DeviceRestBootstrap`.
- Printer location fields.
- Collection-page repair.
- The Dockerfile `iputils-ping`.
- The `net.ipv4.ping_group_range` sysctl.
- The "Test & onboard" inventory flow.

## 4. Decisions taken while merging (review these explicitly)

1. **One SSH trust store.** `ap_autodiscovery.pinned_fingerprint` also accepts keys the onboarding flow saved under `onboarding-ssh-pin:<device>` = `{ip, fingerprint}`, so an AP approved there is not asked again. Keys approved in Auto-onboard are stored under `ssh-hostkey:<ip>`.
2. **Batch "Enable REST on eligible APs" removed from Device inventory.** The repository's own safety contract `tests/unit/test_gui_ip_approval_contract.py` asserts that `DeviceInventory.tsx` never calls `enableRestApi(`, and the legacy tree failed that test.
   - The button is now a link to Access points → Auto-onboard, which enables REST once per cluster (on the VC) through the same gate.
   - The per-device "Check SSH / Enable REST" bootstrap is unchanged.
   - If the owner wants the batch button back, change the contract test in the same reviewed commit.
3. **Inventory labels restored next to the legacy ones,** because the contract test requires them:
   - "Approve IP & Test" (exact /32 approval flow);
   - "Test & Approve All";
   - "Test All (no approvals)";
   - "Test connection";
   - "Onboard Successful — REST-verified (n)".

   "Test & onboard all" and "Test & onboard" are kept.
4. **One contract-test assertion was updated:** `capture(device.id, result.profileId)` → `capture(device.id, batch.profileId)`. This follows the legacy refactor that passes the batch explicitly; the behaviour is the same.

## 5. Invariants — must remain true

- No new device-write path. SSH collection is read-only (allowlist), with the host key pinned before authentication.
- The REST bootstrap only runs through `ssh.enable_rest_api` and its existing gates: write lock, exact /32 approval, pinned key.
- Optimization:
  - a cluster selection creates one pending enrollment per AP, each needing its own independent approval;
  - nothing is inherited from the cluster;
  - only 20/40 MHz channels are allowed.
- An unmeasured input is never rated healthy: an AP with no evidence is `UNRATED`, never Gold.
- Client identities are masked for viewers and removed before any AI call. AI output is advisory only.
- Every new action is audited.
- No schema migration: workflow state lives in `app_settings` under these keys:
  - `incident:`
  - `ap-health:latest`
  - `ap-health-snap:`
  - `ssh-hostkey[-pending]:`
  - `site-transport:`
  - `ap-autodiscovery:job`
  - `disc-subnet:` / `disc-rule:` / `disc-run:`

## 6. How to apply and verify (in this order)

```bash
git checkout -b release/wireless-ops-standard
git apply --check legacy-to-standard.patch && git apply legacy-to-standard.patch
python -m compileall -q backend workers ai_service tests
cd frontend && npm ci && npm run build && cd ..          # build BEFORE the tests: contract tests read frontend/dist
python -m pytest -q tests/unit tests/contract             # expected: 402 passed
python -m pytest -q backend/tests                         # expected: 178 passed
```

Then follow the normal release process in `CLAUDE.md`: commit on the branch, review, approved tag, `bash scripts/inm.sh update`, and evidence registers marked `NOT_RUN` until observed on the host.

## 7. Acceptance on the real network (not yet done)

1. Set `INM_SYSLOG_BIND` and point the Instant and CX syslog at `<server>:514`. Confirm that Logs → Syslog shows MAPPED lines. Note: syslog hostnames cannot contain spaces, so confirm what your firmware sends.
2. Create a discovery rule for one small management subnet with your AP credential profile, and run it.
3. On Access points → Auto-onboard: scan the keys, verify them against the AP console, approve, then run. Do this on ONE cluster first.
4. Check that the cluster's APs turn ONLINE, that the Wi-Fi dashboard coverage rises, and that `/ap-health` rates the APs.
5. Rotate nothing. Confirm that no configuration was written: check the audit log for `ap_auto_onboarding` entries.

## 8. Paste-ready instruction

> You are updating the NetworkManager3 repository (read CLAUDE.md first). Apply the attached
> `legacy-to-standard.patch` on a new branch `release/wireless-ops-standard` exactly as given — do not re-implement it.
> It brings the legacy 1.2.0-rc2 tree up to the wireless-operations standard described in
> `docs/STANDARD_UPGRADE_BRIEF.md`, keeping the legacy branch's own improvements listed in section 3 of that brief.
> After applying: build the frontend first, then run `pytest tests/unit tests/contract` (expect 402 passed) and
> `pytest backend/tests` (expect 178 passed). Report any difference from those numbers, and anything that does not apply
> cleanly, instead of "fixing" it by deleting tests or weakening checks. Review the four merge decisions in section 4 and
> list them in the PR description. Do not enable device writes, do not change the write lock, and do not mark hardware
> evidence as PASS — record it as NOT_RUN until it is observed on the CentOS host.
