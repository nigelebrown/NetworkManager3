# NetworkManager3 — Aruba device API gap register
Updated for the current GitHub main. This register tracks *implemented software*, *emulated protocol checks*,
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
separate from NetworkManager3's hash-chained internal audit trail. Logs are read on demand, not yet
persisted as a scheduled device-originated audit stream.

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
| Full enterprise WLAN lifecycle (RADIUS accounting, captive portal, user roles, multi-auth) | **PARTIAL** | Exact Instant model/firmware REST/CLI schemas; typed validators; readback + rollback tests |
| DFS controls and roaming-assist configuration | **PARTIAL** | Approved allowed-channel list, HPE firmware command support, 20/40-MHz policy checks |
| CX 802.1X/MAB port write configuration | **NOT IMPLEMENTED** | REST schema on CX 6100/6200, bypass/fail-open protections, role/AAA testbed |
| VSF stack provisioning | **NOT IMPLEMENTED** | Lab topology, out-of-band console, destructive failure/recovery testing |
| CX ACL/routing/voice/QoS profiles | **PARTIAL / NOT IMPLEMENTED** | Explicit CLI/REST schema and model capability; impact validation/rollback |
| AP/CX firmware rollout orchestration | **PARTIAL** | HPE images, signed firmware metadata, staged upgrade/rollback, device re-certification |
| Client roaming history and interference analytics | **NOT IMPLEMENTED** | Per-AP event streams, timestamp correlation, privacy controls, measured baselines |
| Device-initiated ping/traceroute/cable test/packet capture | **NOT IMPLEMENTED** | Per-model vendor diagnostics API and strongly bounded RBAC/effect controls |
| Transceiver DOM and model-specific port counters | **PARTIAL** | Exact telemetry resource and hardware DOM support per firmware |
| Continuous device accounting/audit log ingestion | **NOT IMPLEMENTED** | Credential-sanitized polls or signed syslog feeds, dedupe, retention, incident correlation |
| Cisco/UniFi/generic SNMPv3 adapters | **PLACEHOLDERS** | New protocol clients + test emulators + hardware scope agreement |
| Production ZTP, multi-site batching and high-risk change review | **PARTIAL** | Phased site deployment, bounded concurrency, blast-radius analysis and approval |
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
