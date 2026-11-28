# NetworkManager3 — Aruba device API gap register
Updated for the production-remediation candidate. This register tracks *implemented software*, *emulated protocol checks*,
*hardware certification*, and *missing capabilities* separately. It supersedes any blanket claim that "all APIs work".

## Implemented and exercised in software

| Area | AP-505 / AP-535 | CX 6100 / CX 6200 | Acceptance still needed |
|---|---|---|---|
| Authentication, identity, inventory and read-only telemetry | Instant VC HTTPS:4343 REST | CX REST with negotiated v10.09–v10.15 and CSRF | Physical device read/certificate/firmware compatibility |
| Running configuration, backup and readback | Instant running-config; guided full restore | CX running fullconfigs, checkpoint and restore | Real-device backup/restore validation |
| Typed configuration writes | SSID/VLAN, enterprise SSID, RF 20/40 MHz, power, ClientMatch, band steering, airtime fairness | VLAN, access/trunk, PoE, LLDP, LAG, QoS trust, BPDU guard, NTP, DNS, syslog, etc. | **Every write UNCERTIFIED on physical hardware** |
| Device-originated audit/event logs | Read-only show log system/security/user | REST /logs/audit and /logs/event | Actual firmware log response shapes and account auditing |
| Fixed read-only diagnostics | RF summary, clients, system, wireless log | Port authentication, VSF, PoE, QoS, LACP via documented AnyCLI show commands | Per-model CLI support/command response formats |
| Change governance | Independent approval, per-model/firmware certification, backup, execution, readback, rollback | Same | Controlled lab apply/readback/rollback on each hardware/firmware |
| Frontend | Device 360 Operations → Device logs and Diagnostics | Same | Live device UI evidence |

Neither log retrieval nor diagnostic execution sends configuration changes. CX diagnostics use the documented
REST AnyCLI `POST /cli` transport, but only for fixed `show` commands from a static map after the
switch advertises support via `GET /cli/commands`. Arbitrary command strings are never accepted.

Log and diagnostic replies are size bounded and credential-redacted. The device-originated log API is
separate from NetworkManager3's hash-chained internal audit trail. Logs can be collected on demand or via the opt-in scheduled collector; retained entries are deduplicated and access-scoped. Physical response validation remains necessary.

**Software integration test commands:**
```bash
python -m pytest -q tests/contract/test_device_audit_logs.py tests/contract/test_device_diagnostics.py
python -m pytest -q tests/unit/test_device_write_control_script.py
cd frontend && npm ci && npm run build
```

**Physical certification:** NONE as of the current repository evidence. Emulator passes DO NOT certify hardware.
The global hardware write lock must stay on outside a designated lab pilot. The separate
`scripts/device-write-control.sh` command is an opt-in lab-only operational control, not a substitute
for certification or the independent approver.

## Remaining software gaps / physical blockers

| Gap | State | Conditions to close |
|---|---|---|
| Instant automatic full-config restore | **GUIDED / UNSUPPORTED** | Aruba-supported restoration method validated on AP firmware; no invented REST endpoint |
| Full enterprise WLAN lifecycle (RADIUS accounting, captive portal, user roles, multi-auth) | **EXTENDED / PARTIAL** | Typed RADIUS accounting and existing guest external portal/MAC/pre-auth role bindings are implemented with readback and rollback. Creating and editing complete role ACLs, external portal profiles, and other multi-auth modes remain incomplete. |
| DFS controls and roaming-assist configuration | **IMPLEMENTED IN CANDIDATE / HARDWARE PENDING** | Typed 5 GHz channel allowlist and 802.11k/v/r; persisted site constraints, device regulatory checks, 20/40 MHz limits, explicit restorable baseline; validate physical syntax and behavior. |
| CX 802.1X/MAB port write configuration | **EXTENDED / PARTIAL** | Separate approved enable-only operations for existing RADIUS-backed method configurations on disabled client-mode access ports; checkpoint, readback, rollback. New method/group provisioning, policy enforcement and physical authentication tests still outstanding. |
| VSF stack provisioning | **NOT IMPLEMENTED** | Lab topology, out-of-band console, destructive failure/recovery testing |
| CX ACL/routing/voice/QoS profiles | **EXTENDED / PARTIAL** | Voice VLAN advertisement on existing static VLANs plus QoS trust. Complete ACL, routing and QoS policy lifecycle remains outstanding. |
| AP/CX firmware rollout orchestration | **EXTENDED / PARTIAL** | AP stage-only workflow now records planned members, verifies targeted running versions and creates separately approved previous-image recovery plans. Vendor signature verification, automatic reboot/rollback and CX firmware transport remain outstanding. |
| Client roaming history and interference analytics | **EXTENDED / PARTIAL** | Opt-in native ClientMatch action history, estimated timestamps, site-scoped pseudonyms and separate UI evidence; full association stream, roam outcome verification and interference correlation remain incomplete. |
| Device-initiated ping/traceroute/cable test/packet capture | **NOT IMPLEMENTED** | Per-model vendor diagnostics API and strongly bounded RBAC/effect controls |
| Transceiver DOM and model-specific port counters | **IMPLEMENTED FOR DOCUMENTED SINGLE-CHANNEL TABLE / HARDWARE PENDING** | Opt-in structured SFP/SFP+ DOM readings with missing values, supported packet counters and live faceplate. Other table formats, lane-level DOM, thresholds and physical support remain unverified. |
| Continuous device accounting/audit log ingestion | **EXTENDED / HARDWARE PENDING** | Opt-in scheduled read-only device log ingestion, sanitization, dedupe and retention; validate physical log formats and external accounting feed. |
| Cisco/UniFi/generic SNMPv3 adapters | **PLACEHOLDERS** | New protocol clients + test emulators + hardware scope agreement |
| Production ZTP, multi-site batching and high-risk change review | **EXTENDED / PARTIAL** | Bounded site waves, single initial hardware canary, durable exclusive batch lease, persistent failure pause, explicit review and recovery before resume. Factory ZTP, independently reviewed blast-radius analysis and fleet-scale validation remain incomplete. |
| Production HA/DR, long-term load and external penetration testing | **BLOCKED** | Production infrastructure and independent test environment |
| Physical AP-505/AP-535/CX 6100/CX 6200 read/write certification | **BLOCKED** | Isolated lab devices, firmware inventory, credentials, reachable VLAN and signed test results |

## Next acceptance campaign (must use physical equipment)

1. On a **spare** AP-505 and AP-535: test Instant login, read config, RF, clients, logs, diagnostics; then
   use a separate account to approve each write operation and verify safe rollback.
2. On a **spare** CX 6100 and CX 6200: negotiate REST version, test interface/PoE/LLDP/VSF visibility,
   logs, backup and configuration diff; then certify typed writes on disconnected test ports/VLANs.
3. Record firmware train, hardware SKU, response samples (redacted), per-operation PASS/FAIL,
   before/after configuration hashes and rollback integrity. No successful emulator test may be entered
   as physical evidence.
4. Keep automatic optimization OFF until the applicable RF operations have hardware certification.
   Enforce the approved 20/40 MHz widths; 80 MHz must remain forbidden.

Vendor references:
- HPE Aruba Networking Instant 8.x CLI Bank: `show log` and `show log system/security`.
- HPE Aruba Networking AOS-CX 10.14 REST Guide: `/logs/event`, `/logs/audit`,
  `/cli/commands` and `/cli` for documented show commands.
- On-device REST Swagger reference remains the authority for exact firmware/model write schemas.

### Candidate wireless additions

- `ap.ssid.roaming`, `ap.ssid.accounting`, `ap.radio.allowed_channels`: explicit typed change plans, independent approval, readback, inverse rollback, per-operation hardware certification still required.
- Native ClientMatch history is opt-in via worker setting `INM_CLIENT_MATCH_HISTORY=true`. Requires an approved reachable AP and a verified local serial; malformed or unsupported responses are recorded as failures. History is bounded to 300 records/AP and seven days in queries and subsequent captures; disable collection if not approved by the data controller. Estimated action times are not measured roam completion times. No raw client MAC is persisted by this collector; site-keyed HMAC is used for lookup. Existing client inventory remains subject to existing privacy controls.
- New tests: `tests/unit/test_wireless_policy_extensions.py`, `tests/unit/test_client_match_history.py`. CI must pass at the new candidate SHA before release.

### Additional candidate controls

- Fix the Instant emulator command prefix collision (`a-channel` versus `a-channels`). Prior CI: 632 passed, seven backend failures from that collision, frontend and full stack passed. The new exact candidate must pass CI.
- `INM_CX_OPTICAL_DOM=true` enables structured optional optics polling. Read-only `show interface dom` is used only after the device advertises the command. Unsupported or unrecognized output is not recorded as healthy. HPE AOS-CX 10.14 6200 Fundamentals guide: https://www.arubanetworks.com/techdocs/AOS-CX/10.14/PDF/fundamentals_6200.pdf
- Guest role binding is limited to already configured guest SSIDs, explicit restorable external portal/MAC/role settings and named existing roles. It does not create access policies or perform real authentication tests. HPE Instant CLI reference: https://arubanetworking.hpe.com/techdocs/CLI-Bank/Content/instant/wlan-ssid-prof.htm
- Firmware member verification requires an exact pre-stage inventory (up to 128 members), an allowlisted controller/member address and targeted serial/IP evidence; aggregate VC firmware does not count as individual evidence. Recovery creates a new stage-only plan and never triggers automatic reboot. Verify the prior version and each image class; old incomplete plans may require recreation. No physical acceptance is implied.
- Template deployment runs in waves of up to 25 plans with independent per-plan approval and persistent failure review. Production batches enforce a first single-device canary and one physical site per wave, and always stop on failure. No unattended fleet-wide writes.

### NAC, voice and monitoring integrity

- `switch.port.auth_dot1x` and `switch.port.auth_mab` have separate capability and certification records. Only preprovisioned methods with a RADIUS group may be enabled; the port must be administratively down, access-mode, and explicitly client-mode. Reject configured fallback/override roles and never disable authentication through this operation. Real NAC behavior and every applicable policy path must be validated on isolated physical equipment before any certification approval. See HPE AOS-CX v10.13 REST reference: https://developer.arubanetworks.com/aoscx/v10.13/reference/patch_system-interfaces-interface-name-port-access-auth-configurations-port-access-auth-configuration-authentication-method .
- `switch.vlan.voice` requires an existing static non-default VLAN and explicit approval. The writable baseline is retained while setting the documented `voice` Boolean, then verified by a separate GET. See https://developer.arubanetworks.com/aoscx/v10.13/reference/put_system-vlans-vlan-id .
- Missing or partially parsed wireless inventories no longer imply disconnects/offline APs, new poor-experience observations, or zero aggregate measurements. Incomplete aggregate polls leave a history gap rather than fabricated data. Firmware alert resolution requires current evidence.
- The previous candidate `064fe7c23098ef20046a4d26fc31639d1641fe67` passed CI (651 isolated tests, 180 legacy tests, one browser test; run 45539462515). That success does not certify these subsequent changes. Re-run all CI and laboratory tests before release.

## 1.3.0-rc16 update (software audit, 25 November 2026)

Verified against code in this candidate; nothing here is hardware evidence. Full matrix: `docs/ACCEPTANCE_MATRIX_1.3.0_RC16.md`;
hardware campaign: `docs/HARDWARE_VALIDATION_CHECKLIST_1.3.0_RC16.md`; assumptions: `docs/PROTOCOL_ASSUMPTIONS_1.3.0_RC16.md`.

### Closed in software (rc16)

| Gap | Was | Now |
|---|---|---|
| Printer SNMPv3 protocol coverage | SHA-256/AES-128 only | SHA/SHA-224/256/384/512/MD5 × AES-128/192/256/3DES/DES, authPriv and authNoPriv; weak protocols flagged. **Hardware interoperability pending (HW-PRN-01).** |
| Printer fault preservation on truncated walks | partial alert table closed open faults | truncated tables kept as partial, never authoritative |
| Switch counter-rate integrity | missing counters as zero; speed change and short intervals produced implausible values | withheld with reason codes |
| Alert hysteresis | none | per-metric clear bands + per-site override |
| Port monitoring groups | admin-only, no delete/utilisation/stale/unsupported, single-port add | complete software workflow (see changelog #8) |
| Inventory CSV import | whole-file rejection, fixed layouts | mapping, per-row rejection, partial import, rejected export |
| Printer list scale | 2 queries per printer | bulk load; p95 7.3 s → 0.39 s at 500 printers / 25 users (harness) |

### Still open (unchanged unless noted)

| Gap | State |
|---|---|
| Group assignment from imported group column | **NEW — PARTIAL**: column kept as reference text only |
| Printer raw-sample storage budget | **NEW — DECISION NEEDED**: ≈ 12.4 KB/poll in the management DB; 365-day default |
| Device-log retention/disposal | not automatic |
| Firmware vendor signature verification; CX firmware transport | not implemented |
| VSF provisioning; device-initiated diagnostics; Cisco/UniFi/generic SNMPv3 switch adapters | not implemented |
| Enterprise WLAN full lifecycle; CX 802.1X/MAB provisioning; ACL/routing/QoS lifecycle | partial |
| Instant automatic full restore | guided only |
| Physical AP-505/AP-535/CX 6100/CX 6200 and printer certification | **BLOCKED** — no hardware access in this review |
| HA/DR, CentOS install/restore, TimescaleDB retention, external penetration test | **BLOCKED (environment)** |


## 1.3.0-rc17 update (25 November 2026)

**Implemented in software. EMULATED evidence only, so REQUIRES HARDWARE VALIDATION:**

- **CX:**
  - `switch.acl.define/apply`, `switch.route.static`, `switch.qos.dscp_policy`, `switch.vsf.member`
  - firmware upload/stage/boot/recover (journal-before-send; version+serial verification)
  - ping/traceroute
- **Instant:**
  - `ap.role.define`, `ap.captive_portal.external`, `ap.ssid.multi_auth`
  - cluster reboot after staging (`reload all`, never resent)
  - typed reconciliation restore
- **Read-only adapters:** generic SNMPv3, Cisco IOS-XE SNMP, UniFi controller.
- **Active probe and clock offset** (HTTP Date).

**UNSUPPORTED by design or vendor:**

- 80 MHz configuration
- Instant full-config restore (typed reconciliation instead)
- CX cable test and packet capture
- VSF on CX 6100
- any write through non-Aruba adapters

**Still open:**

- CX RADIUS server/group/method creation
- Instant multi-server RADIUS groups
- dynamic routing
- additional DOM formats
- print-server agent
- printer settings beyond location

Protocol assumptions for all new paths: `docs/PROTOCOL_ASSUMPTIONS_1.3.0_RC17.md`.


## One-click selected AP optimization

The Optimization screen now queues a durable per-AP workflow rather than running an unrelated fleet batch. Analyze Now requires no enrollment or backup and cannot produce a change plan. Optimize Now verifies hardware identity, seals a full backup, requests fresh ARM evidence, and reuses the current enrollment or requests a new explicit bounded enrollment when the feature is enabled. The worker waits for independent enrollment approval, re-collects ARM evidence, creates an exact reviewed change plan, and resumes after approval. Existing independent standing approval is honored only within its authorized operation and value limits. The worker rechecks live evidence after approval and uses the certified change engine for execution and exact-journal outcome recovery. No recommendation is invented if ARM measurements are missing or healthy. Workflow state, reasons, backup/plan IDs and findings are persisted; interrupted writes remain subject to the existing recovery journal. The fleet optimizer skips APs with active workflows. Approval notification is available as in-app workflow/enrollment status; external notification delivery is not added by this change. Physical validation and exact-commit CI are required before release.
