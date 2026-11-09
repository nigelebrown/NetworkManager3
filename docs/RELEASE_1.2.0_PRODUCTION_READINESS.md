# NetworkManager3 1.2.0-rc1 — Production Read/Write Readiness Report

**Branch:** `release/production-readiness` (NOT merged to `main`; merge awaits owner approval)
**Version:** `1.2.0-rc1` · schema head `0009` (expand-only) · date 2026-11-09
**Tested from:** the locally produced source tree in this branch, not GitHub (owner instruction 07:01).

## 0. Verdict in one paragraph

The production read/write engine, certification workflow, backup/restore, monitoring pipeline, alerting
and per-device opt-in optimization are implemented and pass every automated test (397/397) and the live
browser end-to-end run on PostgreSQL 16 + Loki. Every device-side behaviour has been proven **only against
protocol emulators** (labelled EMULATED, never counted as hardware evidence). **No AP-505, AP-535, CX 6100 or
CX 6200 hardware was available, so every hardware write is UNCERTIFIED and hardware acceptance is BLOCKED.**
The release is a release candidate for lab certification, not for production writes. With the default
settings it is safe to deploy for monitoring: the write lock is on, and no operation is certified for hardware.

## 1. Test results

| Suite | Result | Evidence |
|---|---|---|
| Backend + unit + contract (pytest) | **397 passed, 0 failed** | `evidence/tests/regression-1.2.0-rc1.log` |
| Frontend type check + production build | pass | `npm run build` (tsc -b + vite) |
| Live browser E2E on PostgreSQL 16 + Loki, live mode (`tests/e2e/test_live_operations.py`) | **1 passed** (all 36 pages + workflows) | `evidence/e2e-live/*.png`, `RESULT.txt` |
| Emulated certification runs (CX 6100, CX 6200, AP-505, AP-515, AP-535) | CX 9/9, AP 7/7 + restore UNSUPPORTED | `evidence/certification/emulated_certification_table.md`, `…runs.json` |
| Legacy demo-stack browser suite (`tests/e2e/test_browser_live.py`) | **FAIL — obsolete.** It asserts the pre-1.1 "Network overview" landing page and needs the demo compose stack plus a restart hook; superseded by `test_live_operations.py`. Recorded as test debt, not as PASS. | — |
| Hardware certification AP-505 / AP-535 / CX 6100 / CX 6200 | **BLOCKED** (no hardware) | — |
| Clean CentOS Stream 9 install / update / rollback | **BLOCKED** (no VM; Docker Hub unreachable from build environment) | — |

The live E2E proves the GUI workflow end to end:

1. Live mode starts with no demo data.
2. A real device is registered; demo data is loaded with the button.
3. Every page renders without error. Device 360 tabs open, including Ports (faceplate) and Operations.
4. Compliance drill-down; vault capture and integrity verify.
5. Collection on a real device fails honestly (no credential).
6. An engineer submits a change, an independent approver approves it, and **Apply to device** on the real (uncertified, locked) switch → **DENY**.
7. On an EMULATED demo switch: lab certification → independent approval of certification → plan → approval → **Apply → APPLIED_VERIFIED**, with the step timeline (connect, authority, pre-change backup, checkpoint, apply + readback, save, post-change backup).
8. Optimization shows the feature OFF; the device alerts page renders.
9. Signed executive export; demo data removed with the button, leaving only the real device.

## 2. Acceptance register (220 rows) — reconciled

`docs/Unified_Acceptance_Register_v4.csv` (now with `actual_result` and `defect` columns); full phase-by-phase
matrix in **`docs/RC_1.2.0_TEST_MATRIX.md`** (Test ID | Procedure | Expected | Actual | Status | Evidence | Defect).
Regenerate with `python scripts/reconcile_register_1_2_0.py`.

| Status | Rows | Meaning |
|---|---|---|
| PASS | 86 | Software-only control proven by a named test that passed in this run (all 76 cited tests verified PASSED in the log) |
| PARTIAL | 64 | Proven on the protocol emulator or with mocked device I/O; the hardware half is BLOCKED |
| BLOCKED | 54 | Needs lab hardware, a CentOS VM / multi-VM topology, AAA/syslog infrastructure or an external party |
| NOT_RUN | 16 | Software-testable but no test yet (test debt, listed in §6) |

## 3. Phase-by-phase readiness

| Phase | Scope | Software state | Hardware / environment | Phase status |
|---|---|---|---|---|
| P1 Platform & deployment | 7 roles, compose, installer `--mode live\|demo`, worker on device network, standalone scheduler | Implemented; tests pass | Clean CentOS install BLOCKED | PARTIAL |
| P2 Discovery & onboarding | /24 background CIDR discovery, allowlists, credential profiles, identify, import config | Implemented; CX probe URL defect fixed | Real discovery BLOCKED | PARTIAL |
| P3 Monitoring | CX telemetry (interfaces, rates, errors, PoE, LLDP, subsystems), Instant monitoring, NOC, Device 360, faceplate | Implemented; port metric naming defect fixed; down devices now score 0 | Real metrics BLOCKED | PARTIAL |
| P4 Accountability | Hash-chained audit, attribution levels, who-changed-what, APPLICATION attribution of executions | Implemented | AAA correlation BLOCKED | PARTIAL |
| P5 Vault | Envelope encryption, capture-from-device, scheduled capture + drift/compliance alerts, pre/post-change backup | Implemented | Hardware capture BLOCKED | PARTIAL |
| P6 Change control | Typed ops, two-person approval (new engine and legacy SIMS path), certified execution gate, readback, automatic rollback, render preview | Implemented; legacy approval bypass fixed | Hardware execution BLOCKED | PARTIAL |
| P7 Replacement | Compatibility gates, CX full-config restore plan, Instant guided restore | Implemented (CX); Instant full restore UNSUPPORTED by design | BLOCKED | PARTIAL |
| P8 Learning | Rule analysis, feedback, temporal split, model comparison gate, hourly scheduled analysis | Implemented | Real history BLOCKED | PARTIAL |
| P9 Optimization | Per-device DEVICE_OPT_IN, feature flag (default off), scope/expiry/limit/cooldown, standing approval, outcome rollback, emergency stop | Implemented | BLOCKED (needs certified hardware) | PARTIAL |
| P10 Other vendors | Adapter registry; Cisco/UniFi placeholders return UNSUPPORTED | Placeholders only | BLOCKED | PARTIAL |
| P11 Production topology | Multi-VM, DR, pen test, load | Not exercised | BLOCKED | BLOCKED |

## 4. Supported read/write functions by model and firmware

**Hardware status for every row: UNCERTIFIED.** "Emulator" means the operation is implemented in the writer
and passed apply → readback → rollback → full-config comparison against the protocol emulator.

### Aruba CX 6100 / CX 6200 — AOS-CX 10.14 REST (`/rest/v10.14`)

| Function | Read | Write (emulator) | Hardware |
|---|---|---|---|
| Full running config backup (`/fullconfigs/running-config`) | yes | — | UNCERTIFIED |
| Checkpoint + full-config rollback / restore (`config.restore`) | — | PASS | UNCERTIFIED |
| VLAN create (`switch.vlan.define`) | yes | PASS | UNCERTIFIED |
| Access VLAN (`switch.port.access_vlan`) | yes | PASS | UNCERTIFIED |
| Trunk allowed VLANs (`switch.port.trunk_vlans`) | yes | PASS | UNCERTIFIED |
| Admin state (`switch.port.admin_state`) | yes | PASS | UNCERTIFIED |
| PoE enable/disable (`switch.port.poe`) + PoE status | yes | PASS | UNCERTIFIED |
| LLDP per port (`switch.lldp`) + LLDP neighbours | yes | PASS | UNCERTIFIED |
| LACP LAG members (`switch.lag.members`) | yes | PASS | UNCERTIFIED |
| Interface statistics / rates / errors, subsystems (fans, PSU, temperature) | yes | — | UNCERTIFIED |
| Aruba Central management-authority detection / disable (`switch.central.disable`, needs `acknowledge_takeover`) | yes | implemented | UNCERTIFIED |
| Templates → deploy plan (only needed ops) | — | PASS | UNCERTIFIED |

### Aruba AP-505 / AP-535 (also AP-515) — Instant AOS 8.10 (REST `/rest/ssid`, show-cmd; pinned-key SSH CLI)

| Function | Read | Write (emulator) | Hardware |
|---|---|---|---|
| Running config backup | yes | — | UNCERTIFIED |
| SSID → VLAN (`ap.ssid.vlan`) | yes | PASS | UNCERTIFIED |
| Channel + EIRP (`ap.radio.channel`) | yes | PASS | UNCERTIFIED |
| Channel width 20/40 (`ap.radio.width`; 80 MHz refused before any device contact) | yes | PASS | UNCERTIFIED |
| ARM min/max TX power (`ap.radio.tx_power`) | yes | PASS | UNCERTIFIED |
| ClientMatch (`ap.client_match`) | yes | PASS | UNCERTIFIED |
| Band steering (`ap.band_steering`) | yes | PASS | UNCERTIFIED |
| Airtime fairness (`ap.airtime_fairness`) | yes | PASS | UNCERTIFIED |
| Full-config restore | — | **UNSUPPORTED** (guided Replace-device procedure) | n/a |

Firmware trains certified so far: none on hardware. Certification is keyed by adapter + model family +
firmware train (e.g. `10.14`, `8.10`) + environment; a new train starts UNCERTIFIED.

## 5. Gap assessment mapping (Aruba Central vs NM3 1.1.0)

Status: **DONE-SW** = implemented and tested in software/emulator, hardware BLOCKED · **PARTIAL** · **OPEN**.

| ID | Gap | 1.2.0-rc1 status | Notes |
|---|---|---|---|
| C01 | Real configuration deployment | DONE-SW | `execute_plan`; the 501 is gone |
| C02 | CX switch writes | DONE-SW | VLAN, access/trunk, admin, PoE, LLDP, LACP |
| C03 | AP configuration writes | DONE-SW | SSID VLAN and RF through the same engine |
| C04 | Configuration inheritance | PARTIAL | Global → site → device + deploy plan; device groups / site collections OPEN |
| C05 | Switch model templates | PARTIAL | VLAN/access/trunk/admin templates converge; 24/48-port role profiles OPEN |
| C06 | Configuration backup | DONE-SW | Capture from device + scheduled capture |
| C07 | Configuration restore | PARTIAL | CX full restore DONE-SW; Instant guided |
| C08 | Automatic configuration audit | PARTIAL | Scheduled capture → drift + compliance alerts; correction is a reviewed deploy plan (no auto-correct by design) |
| C09 | Production change orchestration | PARTIAL | Execution, verification, rollback DONE-SW; multi-device batching OPEN |
| C10 | Management authority detection | DONE-SW | Central/AirWave detection; writes refused; explicit takeover op |
| C11 | Full SSID configuration (802.1X, RADIUS, captive portal, roles) | OPEN | |
| C12 | AP RF configuration | DONE-SW | Channel, width (20/40), power |
| C13 | Advanced RF settings | PARTIAL | ARM power, ClientMatch, band steering, ATF DONE-SW; DFS controls / roaming assist OPEN |
| C14 | Switch L2 security | OPEN | |
| C15 | Switch authentication | OPEN | |
| C16 | QoS and voice | OPEN | |
| C17 | Switch system settings | OPEN | |
| C18 | VSF stacking | OPEN | |
| C19 | Firmware lifecycle | OPEN | Legacy Instant staging unchanged |
| C20 | Zero-touch provisioning | OPEN | Discovery → import → template deploy plan exists, but needs approval at each step |
| C21 | Device replacement | PARTIAL | Restore plan for CX; hardware BLOCKED |
| C22 | Advanced switch features | OPEN | |
| M01 | AP system health | PARTIAL | Existing collection; hardware BLOCKED |
| M02 | RF monitoring | PARTIAL | |
| M03 | Wireless client health | PARTIAL | |
| M04 | Client connection failures | OPEN | |
| M05 | Client roaming | OPEN | |
| M06 | Channel interference | OPEN | |
| M07 | RF optimization statistics | PARTIAL | Optimizer compares pre/post utilization and rolls back on degradation; no full before/after dashboard |
| M08 | Switch interface monitoring | DONE-SW | Per-port state, speed, rates, errors, discards, history |
| M09 | PoE monitoring | DONE-SW | Per-port PoE status/power; alert rules |
| M10 | LLDP and topology | PARTIAL | Neighbours on faceplate; topology map OPEN |
| M11 | Switch hardware health | PARTIAL | Fans/PSU/temperature from subsystems; transceivers OPEN |
| M12 | Switch port visualization | DONE-SW | Faceplate with PoE/LLDP, port detail, "change this port" |
| M13 | Authentication monitoring | OPEN | |
| M14 | Network diagnostics (ping, traceroute, cable test, capture) | OPEN | |
| M15 | Real-time troubleshooting | PARTIAL | Poll-now; no short-interval live session |
| M16 | Alert coverage | PARTIAL | 18 rules + drift/compliance, dedupe, measured-only resolve, per-site thresholds, maintenance windows; escalation OPEN |
| M17 | Monitoring history | PARTIAL | |
| M18 | Unified network health | OPEN | |
| M19 | Change correlation | OPEN | |
| M20 | Monitoring data integrity | PARTIAL | CX doubled-path and port-metric defects fixed; hardware ingestion BLOCKED |

Integration findings from the assessment: the `ports_up`/`interfaces_up` defect is **fixed** (canonical
`ports_*` + aliases). The two-engine bypass is **fixed**: the legacy SIMS apply path now needs independent
approval and respects the emergency stop. The Central management-authority issue is **addressed** (C10).

## 6. Blockers, risks and test debt

**Blockers (owner action needed)**
1. Lab hardware: one each of AP-505, AP-535, CX 6100, CX 6200 on the target firmware trains, on an isolated
   lab VLAN, with lab credentials. Run Certification → independent approval for each model/firmware.
2. Clean CentOS Stream 9 VM with Docker registry access for P1-DEP / P11-DEP rows.
3. AAA (RADIUS/TACACS+ accounting) and syslog sources for attribution rows.
4. Independent security review / penetration test (P11-05).

**Risks**
- Emulator fidelity: emulators follow the CX 10.14 OpenAPI and Instant 8.10 CLI as documented. Real
  firmware may differ (field names, `commit apply` semantics, LAG PUT shape). This is exactly why hardware
  certification is mandatory before any write.
- Instant CLI over SSH depends on a pinned host key and on the VC/AP topology; cluster (VC vs member) behaviour
  is untested on hardware.
- `switch.central.disable` removes Central's management of a switch. It needs an explicit acknowledgement and
  should be certified on a lab switch first.
- Automatic optimization: keep `INM_AUTOMATIC_OPTIMIZATION=false` until channel/power ops are hardware-certified
  and a pilot AP is chosen.
- Expand-only migration 0009 has no automated downgrade; rollback uses code rollback + DB backup (§7).

**Test debt (NOT_RUN, 16 rows):** P2-09, P3-07, P3-08, P4-09, P5-05, P6-06, P6-10, P7-08, CXAPI-07, P5-DEP-01,
P6-DEP-01, P8-DEP-01, INTEG-P3-01, INTEG-P3-02, INTEG-P11-04, GAP-T15 (isolated AI code reviewer — GAP-17
not implemented). Also: rewrite or retire `tests/e2e/test_browser_live.py`.

## 7. CentOS Stream 9 deployment and rollback (1.2.0-rc1)

Follow `docs/DEPLOYMENT_CENTOS9.md` and `docs/SERVER_DEPLOYMENT_RUNBOOK.md`. 1.2.0-specific steps:

**Install / update**
```bash
cd /root/NetworkManager3
bash scripts/inm.sh backup                                   # always first on an existing server
bash scripts/inm.sh update --ref v1.2.0-rc1                  # after the tag is approved and pushed
# new install:  bash scripts/inm.sh install --mode live
bash scripts/inm.sh verify
```
The schema migrates 0008 → 0009 (nine new tables, one new column; expand-only). Safe defaults in
`/etc/inm/inm.env`:
```
INM_MODE=live
INM_DEVICE_WRITE_LOCK=true          # no hardware writes
INM_ALLOW_CONFIG_CHANGES=false
INM_AUTOMATIC_OPTIMIZATION=false    # DEVICE_OPT_IN enrollment refused
INM_READONLY_DEVICE_CIDRS=<lab/management ranges>   # scripts/allow-readonly-device-range.sh
```

**Enabling lab writes (lab VLAN only)**
1. `INM_DEVICE_WRITE_LOCK=false`, `INM_ALLOW_CONFIG_CHANGES=true`; recreate api + worker.
2. Device 360 → Operations: check management authority; for APs scan and pin the SSH host key.
3. Certification page: run on the lab device (unused test port, LAG ports, test VLAN); a second person approves.
4. Raise a change plan → independent approval → Apply; confirm APPLIED_VERIFIED and the readback.
5. Re-lock (`INM_DEVICE_WRITE_LOCK=true`) when not actively changing.

**Rollback**

The schema gate protects rollback. v1.1.0 declares `accepts_management_schemas: ["0008"]`, so once 1.2.0 has
migrated the database to 0009, `bash scripts/inm.sh rollback --to v1.1.0 --acknowledge-schema-compatible` is
**refused with exit 3 and changes nothing**. That is intended: there is no schema downgrade. The supported
rollback to 1.1.0 is:
```bash
# 1. rehearse first (disposable volumes, separate compose project)
sudo bash deployment/centos9/restore.sh --archive /var/lib/inm/backups/<pre-1.2.0-backup>.tar.enc --target test
# 2. check out the old release, then restore the pre-update backup over live data
#    (restore.sh takes a fresh backup of the current data before overwriting)
git -C /root/NetworkManager3 checkout v1.1.0
sudo bash deployment/centos9/restore.sh --archive /var/lib/inm/backups/<pre-1.2.0-backup>.tar.enc --target live --confirm-overwrite-live-data
bash scripts/inm.sh verify
```
Data recorded after the update (certifications, executions, alerts) is lost by this path; it stays in the fresh
backup restore.sh takes. Rolling back between 1.2.0 release candidates that share schema 0009 uses the normal
`inm.sh rollback`. Device-level rollback of an individual change is automatic on failure. To undo a successful
change, raise a reverse change plan or a vault restore plan.
**Not verified on CentOS (BLOCKED, §6).**

**Emergency stop:** Optimization page → Emergency stop (pauses all enrollments and automatic changes);
the global write lock (`INM_DEVICE_WRITE_LOCK=true`) overrides everything.

## 8. Defects found and fixed in this release

| Defect | Impact | Fix |
|---|---|---|
| CX probe used `https://ip/rest/v10.14` as its base and appended `/rest/v10.14/...` | Every real CX switch would have answered 404 | `quick_devices._probe_cx` base = `https://ip`; test_cx_probe_uses_single_rest_prefix |
| `ports_*` vs `interfaces_*` metric names | Switch port metrics mis-filed; port alerts never fired | Canonical names + aliases |
| Legacy SIMS apply needed no approval | Two-person rule bypass | `apply_change` needs an independent approver; emergency stop honoured |
| Down devices scored healthy | NOC hid outages | `noc.health` scores down = 0 |
| "owe" matched inside "power" | False open-SSID findings | Word-bounded check |
| Template deploy never converged | Admin/trunk state not parsed | `installed_facts` returns admin + trunks |
| Hard-wired demo deployment; worker without device network; standalone without collection | Not a live system | `--mode live`, worker network + allowlist, scheduler jobs |
| API rate limit 300/min | NOC with many tiles throttled | 1500, configurable |
| Instant restore recorded as certification FAILED | Misleading certification state | Recorded UNSUPPORTED |
| Change-plan capability showed UNSUPPORTED for implemented ops | Misleading | Shows certification state (CERTIFIED_WRITE / UNCERTIFIED) |
| DEVICE_OPT_IN enrollment accepted with the feature flag off | Opt-in could be armed while platform off | Refused (422) unless `INM_AUTOMATIC_OPTIMIZATION=true` |

## 9. Git

Branch `release/production-readiness` from `main` (`ce63b5c`, 1.1.0). Commits: `16fad23` (write engine,
certification, discovery, vault, Phase 9), `26f43f3` (CX probe fix, port metrics, telemetry/faceplate,
alerts, Central detection, unified approval, template deploy), and the 1.2.0-rc1 completion commit (GUI
wiring, E2E, register, this report). Proposed tag: `v1.2.0-rc1`. **Do not merge to `main` until the owner
approves**; production writes additionally require the hardware certification in §6.
