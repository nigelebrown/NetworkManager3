# NetworkManager3 1.3.0-rc17 — handover report

Prepared 25 November 2026 by Claude (build agent) for the owner and for Codex, who will deploy and run the hardware campaign.

The build agent had no access to physical devices, to the live server or to the upstream Git remote. Everything
below was done and tested in an isolated workspace.

## 1. Verdict

| Dimension | Verdict |
|---|---|
| Production deployment | **NO_GO.** Nothing is hardware-certified; rc17 is not installed on any CentOS server; there is no independent sign-off; the release is unsigned. |
| Software status | **READY FOR HARDWARE VALIDATION — not "SOFTWARE DEVELOPMENT COMPLETE".** Most of the master directive is implemented and developer-tested. The software gaps in §4 remain, and several are real features, not paperwork. |
| Hardware certification | **NOT STARTED.** Every row in both hardware checklists (rc16 + rc17) is NOT_RUN. Emulator passes are not certification. |
| Live server | **NOT DEPLOYED, NOT VERIFIED.** No access was provided. The ZIP gives no server access. |

## 2. What was delivered in rc17

`docs/RELEASE_1.3.0_RC17_CHANGELOG.md` has the full list. The main items:

**Navigation (owner request)**

- One menu entry per purpose: 50 entries became 37, with overlapping screens turned into view tabs.
- The banner/sidebar overlap is fixed.
- Verified in Chrome by `tests/e2e/nav_audit.py`.

**Safety and truthful labels**

- Demo data is refused in live mode.
- The feature registry is computed.
- Retired placeholders return 410 with the live route.

**Switches (CX)**

- ACL, static route, DSCP QoS and VSF typed operations.
- Firmware upload/stage/boot/recover with journal-before-send.
- Ping/traceroute from the device.

**Wireless (Instant)**

- Role/ACL, external captive portal and MAC+802.1X typed operations.
- Typed restore by reconciliation.
- Cluster reboot orchestration after staged firmware.

**Other vendors**

- Read-only SNMPv3 (generic and Cisco) and UniFi adapters.

**Monitoring and analytics**

- Independent active probe; clock-drift detection that downgrades attribution.
- Roaming and interference analytics.

**Printers**

- Storage growth fixed: −92.5 % sample rows for an active printer.
- Internal purchase requests.
- Print-job metadata with in-memory document inspection, governed by policy.

**Data and platform**

- Device-log retention.
- RF optimisation site batches.
- Release signing tool.
- Frontend code-splitting.
- One pre-existing printer-discovery bug fixed.

## 3. Tests actually executed (this workspace)

`docs/TEST_RESULTS_1.3.0_RC17.md` has the commands and numbers. In summary:

- **Unit/contract suite (`tests/`):** 726 collected and run, 50 skipped (integration/browser suites that need their own stack). All pass in the final run; see TEST_RESULTS for the single intermittent test.
- **Legacy suite (`backend/tests`):** 181 passed.
- **Browser (Chrome, isolated stacks):**
  - ops workflows 3/3
  - printer suite 9/9
  - intelligence suite 8/8
  - navigation audit 0 defects
- **PostgreSQL 16 migration and restore drill:** 0004→0015, 0013→0014 and 0014→0015 pass; downgrade is refused; dump/restore is identical.
- **Frontend:** `npm run build` passes; `tsc` clean.
- **Not run:**
  - `tests/e2e/test_gui_workflow.py`, `test_live_operations.py` and `test_demo_gui.py` (they need the full Docker/NATS/Loki/TimescaleDB stack)
  - `tests/integration/*` and `tests/fullstack/*`
  - TimescaleDB-specific paths
  - the load harness after the rc17 changes

## 4. Remaining software gaps (not implemented in rc17)

These are real, listed honestly, and none is blocked by hardware.

| # | Gap | Notes / suggested approach |
|---|---|---|
| 1 | Trained ML model | Today: rules plus a walk-forward statistical forecast. A trained model needs labelled outcome history from the live network (the prediction scoring loop already collects it) and a validation gate like `PredictionRun.validation_state`. |
| 2 | CX RADIUS server / server-group / 802.1X method creation | Ports can only enable 802.1X/MAB on pre-provisioned methods. Needs REST shapes confirmed on 10.14 (add as A-CX-14). |
| 3 | Instant RADIUS server groups / multiple servers | `ap.ssid.enterprise` binds one server. |
| 4 | Print-server agent (CUPS / Windows Print Server) | The ingestion API, tokens, policy and inspection are done. The agent that reads spool metadata (and optionally extracts text) is not written. This needs an owner decision on where the agent runs and whether text extraction is acceptable on the print servers. |
| 5 | Printer configuration beyond location | Contact, asset tag, defaults — need Printer-MIB/vendor-MIB SET paths per model. |
| 6 | DOM/optics thresholds for other formats | CX DOM is opt-in; additional vendor formats are not built. |
| 7 | Release signing wired into `install.sh`/`update.sh` | Use `verify-release.sh` manually today. |
| 8 | paramiko 3.5.1 advisory (PYSEC-2026-2858) | Not upgraded: paramiko ≥ 4 drops ssh-rsa/SHA-1. Check on hardware whether AP-505/535 on 8.10 negotiate rsa-sha2-256/512; if yes, upgrade to 5.0.0 and re-run the SSH tests. |
| 9 | react-router 6 moderate advisories | Fix needs a React Router 7 migration. |
| 10 | Integrations page | Still a CONCEPT screen (labelled). |
| 11 | Dynamic routing (OSPF/BGP) on CX | Not in scope; the registry says so. |
| 12 | Browser test for the import Group column | The UI is built; no automated browser test yet. |
| 13 | `test_predictive_insufficient_experimental_and_validated_paths` | Failed once in one full run and passed on every re-run. Its `NOW` is wall-clock at import, so it is likely time-dependent. Pre-existing code, untouched by rc17, but it needs a fixed clock. |

## 5. External validation that only Codex/the owner can do

1. **Commit and CI.** Commit rc16 then rc17 to a branch (`docs/DEPLOYMENT_RC17_UPGRADE_BACKUP_ROLLBACK.md` §2), let CI run on the exact SHA, review, and tag (signed).
2. **Server.** Run backup, the restore drill, update to rc17 (migration 0015), then verify (HW-PL-20…23).
3. **Hardware campaign.** Run `HARDWARE_VALIDATION_CHECKLIST_1.3.0_RC16.md` and then `…_RC17.md`, on spare lab devices only.
4. **Protocol assumptions.** Confirm or correct A-CX-09…13, A-IAP-12…15, A-CLOCK-01, A-SNMP-01/02, A-UNIFI-01 and A-PJ-01 (`PROTOCOL_ASSUMPTIONS_1.3.0_RC17.md`). Correct the writer and the emulator together.
5. **Certification.** Record certification per adapter, model, firmware train and HARDWARE. Only then can an operation leave UNCERTIFIED.
6. **Owner decisions.** These are needed before enabling:
   - print-job collection: purpose, legal basis, retention, whether to pseudonymise
   - automatic purchase requests and lead time
   - active probes (`INM_SCHEDULED_ACTIVE_PROBE`)
   - device-log retention days
7. **Independent acceptance.** Re-run the register rows marked "independent re-run required", then the security and operations sign-off in `PRODUCTION_GO_NO_GO.md`.

## 6. Package contents

| Item | File |
|---|---|
| Full source tree (no `.git`, no `node_modules`, no secrets) incl. migrations, adapters, workers, scripts, tests, lockfiles (`requirements.lock`, `frontend/package-lock.json`) | `NetworkManager3-1.3.0-rc17.zip` |
| Patch against rc16 (`git diff 90e3662`) | `NetworkManager3-1.3.0-rc16-to-rc17.patch` |
| Changelog | `docs/RELEASE_1.3.0_RC17_CHANGELOG.md` |
| Acceptance matrix + register | `docs/ACCEPTANCE_MATRIX_1.3.0_RC17.md`, `docs/Unified_Acceptance_Register_v4.csv` |
| Developer test results | `docs/TEST_RESULTS_1.3.0_RC17.md`, `evidence/regression/rc17-*.log`, `evidence/e2e/nav-rc17/`, `evidence/migration/rc17-pg16.txt`, `evidence/load/rc17-printer-storage.json`, `evidence/security/rc17-*` |
| Architecture | `docs/ARCHITECTURE_1.3.0_RC17.md` |
| Install / upgrade / backup / rollback | `docs/DEPLOYMENT_RC17_UPGRADE_BACKUP_ROLLBACK.md` |
| Hardware checklist / protocol assumptions | `docs/HARDWARE_VALIDATION_CHECKLIST_1.3.0_RC17.md`, `docs/PROTOCOL_ASSUMPTIONS_1.3.0_RC17.md` (+ rc16 versions) |
| Gap register / go-no-go | `docs/DEVICE_API_GAP_REGISTER.md`, `docs/PRODUCTION_GO_NO_GO.md` (rc17 sections) |
| Checksums | `SHA256SUMS` (outside the ZIP) — integrity only, **not** a signature |

## 7. Invariants for the next engineer

`CLAUDE.md` → "1.3.0-rc17 invariants". The essentials:

- 20/40 MHz only.
- Never resend a write or boot after a timeout.
- Uncertified operations stay blocked at the execution gate.
- Emulators only behind explicit flags or DEMO devices.
- Print-job content is never stored.
- One menu entry per purpose.
