# NetworkManager3 1.3.0-rc18 — handover report

Prepared 25 November 2026 by Claude (build agent) for the owner, for the independent reviewer, and for Codex, who
deploys and runs the hardware campaign. Built against the RC17 corrective development directive.

The build agent had no access to physical devices, the live server or the upstream Git remote. Everything below was
done and tested in an isolated workspace.

## 1. Verdict

| Dimension | Verdict |
|---|---|
| Software implementation (directive §2–§5) | **COMPLETE FOR INDEPENDENT REVIEW.** Every directive item is implemented and developer-tested. Every software gap the register review found (categories 1–4) was completed, not reclassified. What remains needs hardware, external infrastructure, owner authorisation or a vendor capability that does not exist (categories 5–8, each justified per row in `docs/REGISTER_REVIEW_1.3.0_RC18.md`). |
| Production deployment | **NO_GO.** Nothing is hardware-certified. rc18 is not installed on any CentOS server. There is no independent sign-off. The package is unsigned (the owner's key is required and was never available to the build). |
| Hardware certification | **NOT STARTED.** Every row in the rc16, rc17 and rc18 hardware checklists is NOT_RUN. Emulator passes are not certification. |
| ML | **No model is production-validated.** Models trained in this workspace used fixture or synthetic data and are labelled NOT VALIDATED. The statistical engine stays the engine of record until a model trained on real fleet history passes the validation gate and is promoted by an independent approver. |

## 2. What was delivered in rc18

The full list is in `docs/RELEASE_1.3.0_RC18_CHANGELOG.md`. The directive sections map as follows:

- §2.1–2.13: implemented, with the test per item in `docs/ACCEPTANCE_MATRIX_1.3.0_RC18.md`.
- §3: all 281 v4 rows reviewed individually. The result is `docs/Unified_Acceptance_Register_v5.csv`, with every ID
  preserved, a category (1–8) on every non-PASS row, and re-proven evidence for the 62 PASS rows that lacked an
  actual result.
- §4: one command, `scripts/run_full_test_suite.sh`, writes an evidence report. The Docker-free CI is
  `.github/workflows/full-test-suite.yml`. No integration test was removed.
- §5: `docs/WHOLE_APP_REVIEW_1.3.0_RC18.md`.

Reading the register against the code found real software gaps that no text search would have shown. All were
completed in rc18:

- **High-risk / cluster deployments (P6-10).** An impact view and an independent release approval.
- **Onboarding account-lockout guard (RC16-T12).**
- **Device syslog bridge (P3-01, INTEG-P3-03, GAP-T03).** Device syslog in Loki now reaches the device timeline and
  change attribution, and only an event from a verified sender is attributed.
- **Logging-health alerts (P4-08).**
- **Cisco IOS configuration events (P10-02).**
- **UniFi adapter (P10-03).** It could never be selected, and it had no test.
- **Expand/migrate/contract window (P11-DEP-03).**

In total, 16 defect classes were found by running the product and fixed (changelog table). One class covered four
PostgreSQL-only column-length defects that SQLite had hidden. Four were found only because the long-unrun browser
suites (`test_gui_workflow`, `test_live_operations`, `test_demo_gui`) were brought back to green.

## 3. Tests actually executed

See `docs/TEST_RESULTS_1.3.0_RC18.md` and `evidence/test-run-<UTC>/TEST_EVIDENCE_REPORT.md`.

## 4. What remains, and why it is not software

| Category | Examples | Who / what is needed |
|---|---|---|
| 5 Physical hardware | Every device write: CX RADIUS/AAA/OSPF, Instant RADIUS, printer SET, firmware, restore. Also the syslog header formats (HW-SYS-01) and optics formats | Lab AP-505/AP-535/CX 6100/CX 6200, Kyocera fleet models, Cisco switch — run the rc16/rc17/rc18 checklists |
| 6 External infrastructure | Clean CentOS Stream 9 install with registry access; multi-VM topology (P11-*); real AAA server (lockout, accounting outage); real Teams/SIEM; ≥ 7 days of real telemetry for ML validation; independent penetration test | Owner / operations |
| 7 Unsupported vendor capability | Instant full automatic restore (typed reconciliation instead); BGP on CX 6100/6200; printer energy-saving via standard MIBs | none (documented) |
| 8 Outside approved scope / needs authorisation | Print-server agent and document inspection; purchasing automation (both disabled and unmounted); non-Aruba configuration writes | Owner authorisation before any work |

**The one owner decision that is genuinely open:** HW-SYS-01. If a device family sends RFC 3164 syslog with the year
*before* the time, this Alloy release rejects it. The rejection is counted and reported by `inm verify`, so it is
never silent, but those events are not collected. A packet capture decides whether to change the device's syslog
format or add a pre-parser.

## 5. External validation that only the owner/Codex can do

1. **Commit and CI.** Apply the patch (or the full tree) to a branch, let `full-test-suite.yml` run on the exact SHA,
   then review.
2. **Signing.** The owner generates the release key offline, initialises trust on the server and signs the tag tree
   (`docs/DEPLOYMENT_RC18_UPGRADE_BACKUP_ROLLBACK.md` §3).
3. **Server.** Back up, run the restore drill, update (0015 → 0016), then verify (HW-PL-24…26).
4. **Hardware campaign.** Run the rc16, rc17 and rc18 checklists on spare lab devices.
5. **Protocol assumptions.** Confirm or correct A-CX-14…16, A-IAP-16, A-PRN-01/02, A-TEAMS-01, A-ALLOY-01 and
   A-CISCO-SYSLOG-01 (writer and emulator together).
6. **Certification.** Record certification per adapter, model, firmware and HARDWARE. Only then may an operation leave
   UNCERTIFIED.
7. **ML.** After ≥ 7 days of real telemetry, train, compare with the statistical engine on the same windows, and
   promote only if the gate passes. An independent approver signs off.
8. **Independent acceptance.** Re-run the rows marked "independent re-run required", then the security and operations
   sign-off in `PRODUCTION_GO_NO_GO.md`.

## 6. Package contents

| Item | File |
|---|---|
| Full source tree: no `.git`, no `node_modules`, no secrets; includes lockfiles and evidence | `NetworkManager3-1.3.0-rc18.zip` |
| Patch against rc17 (`git diff f6f44ce`) | inside the ZIP: `NetworkManager3-1.3.0-rc17-to-rc18.patch` |
| Change log, matrix, register, review | `docs/RELEASE_1.3.0_RC18_CHANGELOG.md`, `docs/ACCEPTANCE_MATRIX_1.3.0_RC18.md`, `docs/Unified_Acceptance_Register_v5.csv`, `docs/REGISTER_REVIEW_1.3.0_RC18.md` |
| Test results and evidence | `docs/TEST_RESULTS_1.3.0_RC18.md`, `evidence/test-run-<UTC>/` |
| Architecture, deployment, hardware checklist, protocol assumptions, whole-app review | `docs/*_1.3.0_RC18.md`, `docs/DEPLOYMENT_RC18_UPGRADE_BACKUP_ROLLBACK.md` |
| Checksums | `SHA256SUMS` next to the ZIP: integrity only, **not** a signature |

## 7. Invariants for the next engineer

See `CLAUDE.md` → "1.3.0-rc18 invariants". Every rc16/rc17 invariant still applies:

- 20/40 MHz only.
- No blind replay after a timeout.
- Uncertified operations stay blocked.
- The AI has no access to credentials or configuration.
