# NetworkManager3 1.3.0-rc17 — acceptance matrix

**Legend**

| Status | Meaning |
|---|---|
| **PASS** | Developer-tested in this workspace. EMULATED where marked. Never a hardware pass. |
| **PARTIAL** | Software built; part not done (stated). |
| **NOT_IMPLEMENTED** | No software for it in this release. |
| **UNSUPPORTED** | The vendor capability does not exist or is refused by design, with the reason. |
| **REQUIRES HARDWARE VALIDATION** | Software done; it cannot be accepted without physical equipment (checklist row). |

The machine-readable record is `docs/Unified_Acceptance_Register_v4.csv`: 281 rows, including RC17-T01…T17 and 5 updated rows.

- 142 PASS
- 67 PARTIAL
- 53 BLOCKED (hardware/server)
- 19 NOT_RUN

The rc16 matrix (`ACCEPTANCE_MATRIX_1.3.0_RC16.md`) remains the baseline for rows not listed here.

## A. Discovery and onboarding

| Capability | Status | Evidence / reason |
|---|---|---|
| Guided onboarding, discovery rules, range planner, CSV/bulk import (atomic/partial) | PASS | rc16 + `test_bulk_device_import.py` |
| Group assignment from an imported Group column | PARTIAL | UI built on the existing device-group APIs (`BulkDeviceImport.tsx`); the browser path is not covered by an automated test yet |
| SNMP adapters bound to saved SNMP profiles in collection plans | PASS (EMULATED) | `test_rc17_snmp_adapters.py` |
| Discovery against real subnets | REQUIRES HARDWARE VALIDATION | rc16 HW-AP-01/02, HW-CX-01 |

## B. Aruba wireless (Instant 8.10)

| Capability | Status | Evidence / reason |
|---|---|---|
| Channel/power/width (20/40 only)/allowed channels/ARM features/SSID create/enterprise/roaming/accounting/guest auth | PASS (EMULATED) · REQUIRES HARDWARE VALIDATION | rc16 rows HW-AP-04…08 |
| User roles with ACL rules, external captive portal, MAC + 802.1X multi-auth | PASS (EMULATED) · REQUIRES HARDWARE VALIDATION | `test_rc17_instant_wlan_security.py` (14); HW-AP-12…14 |
| 80 MHz configuration | UNSUPPORTED (policy) | Refused in validation, render, rollback and reconciliation |
| Firmware staging + cluster reboot orchestration + member verification | PASS (unit) · REQUIRES HARDWARE VALIDATION | `test_ap_reboot_orchestration.py`; HW-AP-09, HW-AP-15 (A-IAP-12) |
| Automatic restore by typed reconciliation | PASS (EMULATED) · REQUIRES HARDWARE VALIDATION | `test_rc17_typed_restore.py`; HW-AP-16 |
| Full-config restore on Instant | UNSUPPORTED (vendor) | Instant has no full-config restore API; guided procedure + typed reconciliation instead |
| Roaming / interference analytics | PASS (inferred from polls) | `test_rc17_probes_roaming.py`; caveats in the protocol assumptions |
| Enterprise RADIUS server-group objects beyond `ap.ssid.enterprise`'s single server | NOT_IMPLEMENTED | Listed in the handover §4 |

## C. Aruba CX (10.14)

| Capability | Status | Evidence / reason |
|---|---|---|
| rc16 typed writes (VLAN/port/LAG/PoE/LLDP/NTP/DNS/syslog/QoS trust/storm/BPDU) | PASS (EMULATED) · REQUIRES HARDWARE VALIDATION | rc16 HW-CX-10 |
| IPv4 ACL define/apply, static route, DSCP policy, VSF member | PASS (EMULATED) · REQUIRES HARDWARE VALIDATION | `test_rc17_switch_ops.py` (12); HW-CX-14…17 |
| Firmware upload/stage/boot/recover | PASS (EMULATED) · REQUIRES HARDWARE VALIDATION | `test_rc17_cx_firmware.py`; HW-CX-18/19 |
| Ping/traceroute from switch | PASS (EMULATED) · REQUIRES HARDWARE VALIDATION | `test_rc17_active_diagnostics.py`; HW-CX-20 |
| Cable test, packet capture | UNSUPPORTED | No REST/AnyCLI path that is safe and bounded; the reasons are returned by the API |
| 802.1X/MAB enable on ports | PARTIAL | Enable-only on pre-provisioned RADIUS methods; creating RADIUS servers/groups/methods is NOT_IMPLEMENTED |
| Dynamic routing (OSPF/BGP) | NOT_IMPLEMENTED | Out of rc17 scope; registry says so |
| VSF on 6100 | UNSUPPORTED (platform) | Refused before any write |

## D. Printers

| Capability | Status | Evidence / reason |
|---|---|---|
| Monitoring, supplies, faults, utilisation, reports, SNMPv3 protocols | PASS | rc16 + printer browser suite 9/9 (rc17 run) |
| Raw-sample storage growth (RC16 finding) | PASS | Change-only with a 6 h heartbeat: 33 → 2.09 samples/poll after the first (−92.5 % rows, `evidence/load/rc17-printer-storage.json`); bytes derived from the rc16 PostgreSQL ratio |
| Internal purchase requests | PASS | `test_rc17_purchasing_print_jobs.py` (3) |
| Print-job metadata + document inspection (server side) | PASS | `test_rc17_purchasing_print_jobs.py` (5) |
| Print-server agent (CUPS/Windows) | NOT_IMPLEMENTED | Ingestion API exists; no agent in this repository (handover §4) |
| Printer configuration beyond location (contact, asset, tray/defaults) | NOT_IMPLEMENTED | Location write only (rc15); handover §4 |
| Discovery IP-list parsing | PASS (bug fixed) | Pre-existing split bug found by the browser suite and fixed |

## E. Monitoring / NOC

| Capability | Status | Evidence / reason |
|---|---|---|
| Collection, freshness, alerts with hysteresis, port groups, NOC | PASS | rc16 |
| Independent active probe (INTEG-P3-05) | PASS (EMULATED) | `test_rc17_probes_roaming.py`; HW-AP-17, HW-CX-21 |
| Clock-drift detection (P3-05) | PASS (EMULATED) | Same; A-CLOCK-01 |
| Non-Aruba read-only telemetry (GAP-T12) | PASS (EMULATED) · REQUIRES HARDWARE VALIDATION | HW-NA-01…04 |
| DOM/optics thresholds in other formats | PARTIAL | CX DOM opt-in (`INM_CX_OPTICAL_DOM`) unchanged; additional formats/thresholds NOT_IMPLEMENTED |

## F. Optimisation and AI

| Capability | Status | Evidence / reason |
|---|---|---|
| Per-device RF optimisation + site batch enrolment | PASS (EMULATED) | `test_capability_completion.py` |
| Predictive health (walk-forward statistical forecast) | PASS | Status is the latest run's validation state |
| Trained ML model | NOT_IMPLEMENTED | Rules plus a statistical forecast only; the registry says `RULES_ONLY` |
| AI access to credentials or direct writes | UNSUPPORTED (by design) | No path exists; unchanged |

## G–I. Configuration lifecycle, security/admin, data

| Capability | Status | Evidence / reason |
|---|---|---|
| Vault, diff, drift, approvals, certification gate, recovery journal | PASS | rc16 |
| Device-log retention | PASS | `test_rc17_batch1.py` |
| Demo data refused in live mode | PASS | `test_capability_completion.py` |
| Release signing / verification | PARTIAL | Tool and verify script PASS (`test_release_signing.py`); not wired into `install.sh`/`update.sh`; rc17 is unsigned (no owner key) |
| Migration 0014 → 0015 on PostgreSQL 16 | PASS | `evidence/migration/rc17-pg16.txt` (TimescaleDB not available here) |
| pip-audit | PARTIAL | paramiko 3.5.1 PYSEC-2026-2858 (fix 5.0.0) is **not upgraded**: paramiko ≥ 4 drops ssh-rsa/SHA-1 that older Instant APs need. Owner decision plus a hardware check (does AOS 8.10 on AP-505/535 offer rsa-sha2?) |
| npm audit (production deps) | PARTIAL | 2 moderate (react-router 6 open-redirect advisories). The fix needs a React Router 7 major upgrade; not done in rc17 |

## J. Frontend

| Capability | Status | Evidence / reason |
|---|---|---|
| One menu entry per purpose; no menu/content overlap | PASS | `tests/e2e/nav_audit.py`: 74 entry visits, 66 tabs, 0 overlaps / duplicates / multi-active |
| Route code-splitting | PASS | Main chunk 1,311 KB → 214 KB |
| Concept pages (Integrations) | PARTIAL | Still labelled CONCEPT in the menu; no integration back-end |
| Client connectivity journeys | PASS | Rendered from poll-inferred events (caveat shown) |

## K. CentOS installation / deployment

| Capability | Status | Evidence / reason |
|---|---|---|
| Install / update / backup / restore / rollback scripts | REQUIRES SERVER VALIDATION | No server access in this workspace; HW-PL-20…23 |
| Deployment of rc17 to the live server | NOT DONE | Not claimed |
