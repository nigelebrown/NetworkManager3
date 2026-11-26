# NetworkManager3 1.3.0-rc17 — hardware validation checklist

**Nothing below has been run.** Every row starts as `NOT_RUN`. This file **adds** to
`docs/HARDWARE_VALIDATION_CHECKLIST_1.3.0_RC16.md`. Every rc16 row (HW-AP-01…11, HW-CX-01…13, printers,
platform) still applies to rc17 and must be run as well.

Record results in `docs/Unified_Acceptance_Register_v4.csv`. Each result needs:

- build SHA
- tester
- device serial
- firmware
- redacted request/response samples

An emulator pass is never a hardware pass.

## Changed global prerequisites

1. rc17 uses management schema **0015**. Rolling back to rc16/rc15 (schema 0014) is **not** schema-compatible: restore the pre-update backup (`docs/DEPLOYMENT_RC17_UPGRADE_BACKUP_ROLLBACK.md`).
2. Every write row needs all of the following:
   - a lab serial listed in `INM_CERTIFICATION_LAB_SERIALS`
   - the write lock released for the pilot only
   - an independent approver account
   - out-of-band console access
   - a factory-recovery procedure on paper
3. Use spare devices only. 80 MHz must never appear in any device readback.

## A. Instant AP-505 / AP-535 (8.10.x) — new in rc17

| ID | Operation | Expected behaviour | Success criteria | Failure states | Rollback |
|---|---|---|---|---|---|
| HW-AP-12 | `ap.role.define` with rules e.g. `rule 10.0.0.0 255.0.0.0 match tcp 443 443 permit`, `rule any any match any any any deny` | CLI accepted line by line (A-IAP-13); `show running-config` contains the block with rules in order | Readback block equals the rendered lines exactly; a lab client in that role reaches only permitted destinations (manual test) | `% invalid input` (record exact syntax for the build); rule reordering by the device (record, then adjust readback) | Inverse: `no wlan access-rule <role>` (only if created by this plan) |
| HW-AP-13 | `ap.captive_portal.external` (server, port, `url "…"`, `https`, redirect-url) | Profile created; no `server-fail-through` | Readback lines present; guest client is redirected to the lab portal over HTTPS | Different keyword names on the build (A-IAP-14); device adds defaults (allowed, record) | `no wlan external-captive-portal <profile>` |
| HW-AP-14 | `ap.ssid.multi_auth` enable/disable on a lab enterprise SSID | `mac-authentication`, `l2-auth-failthrough`, `set-role-mac-auth <role>` | A known MAC lands in the MAC role; an unknown MAC falls through to 802.1X; readback matches | Failthrough semantics differ (A-IAP-15) — **fail** if unknown MACs are admitted without 802.1X | Inverse restores the prior three settings |
| HW-AP-15 | Cluster reboot after staged firmware (`reload all`) | One command only; session drop tolerated; every planned member returns on the target image | `REBOOT_VERIFIED`; device audit shows exactly one reload; no second send after a timeout | A member stays on the old image → `REBOOT_UNVERIFIED` (expected handling); reload not cluster-wide (A-IAP-12) → **fail** | Separately approved previous-image recovery plan + reboot |
| HW-AP-16 | Typed reconciliation from a vault baseline | Plan contains only typed ops; 80 MHz / deleted SSIDs listed as unrestorable | After approval and apply, a second reconciliation reports `in_sync` | Untyped line replayed (**critical fail**) | Executor rollback per op |
| HW-AP-17 | Active probe + clock (VC on 4343) | TCP connect RTT; HTTP `Date` header present | Offset within ±2 s of NTP-synced server when the VC uses NTP; deliberately skewed VC (lab) → CLOCK_DRIFT | No `Date` header (A-CLOCK-01) → clock stays UNKNOWN (acceptable, record) | none |

## B. AOS-CX 6100 / 6200 (10.14) — new in rc17

| ID | Operation | Expected behaviour | Success criteria | Failure states | Rollback |
|---|---|---|---|---|---|
| HW-CX-14 | `switch.acl.define` + `switch.acl.apply` (ingress, lab port) | ACL created/replaced; `aclv4_in_cfg` on the port references it | GET readback equals entries; lab traffic permitted/denied as specified | Body shape differs (A-CX-09); 6100 ACL entry limits — record max | Checkpoint restore; detach (`value: null`) |
| HW-CX-15 | `switch.route.static` (non-default prefix first; default route only with acknowledgement) | Route under `/system/vrfs/<vrf>/static_routes` | Readback next-hop equals approved; `show ip route` shows it | Path/encoding (A-CX-10); route installed but inactive (record) | Checkpoint restore |
| HW-CX-16 | `switch.qos.dscp_policy` (class + policy + apply) | Class, policy and port policy created | Readback; DSCP remark verified with packet capture on a lab host | Object model differs per release (A-CX-11) | Checkpoint restore |
| HW-CX-17 | `switch.vsf.member` on a 6200 pair (two spare units, console on both) | Member provisioned; link-up on the VSF ports | Stack shows 2 members; reboot behaviour recorded | 6100 must be refused before any write (UNSUPPORTED); member renumbering (A-CX-12) | Remove member via checkpoint + console |
| HW-CX-18 | Firmware upload to the non-booted slot (`PUT /firmware?image=…` multipart) | Upload accepted; slot version readback equals the image version | Vendor SHA-256 recorded; mismatched image refused before upload | Multipart field name or size limit (A-CX-13); upload timeout → retry the upload only (never boot) | Delete the staged image / leave the old slot booted |
| HW-CX-19 | Boot staged slot (`POST /boot?image=…`) then verify | One boot request; `BOOT_SENT` journalled first; switch returns; version + serial verified | `BOOTED_VERIFIED` within `INM_CX_REBOOT_WAIT_SECONDS` | Switch returns on the old image → `BOOT_UNVERIFIED`, then `recover` | `recover` boots the previous slot (no fresh approval) |
| HW-CX-20 | Ping / traceroute from the switch (VRF default and mgmt) | Pinned SSH; fixed command; bounded output | Output matches the CLI; concurrent second request refused (busy) | Host key mismatch → refused before sending (expected) | none |
| HW-CX-21 | Active probe (443) + HTTP Date clock offset | As HW-AP-17 | As HW-AP-17 | As HW-AP-17 | none |

## C. Non-Aruba (read-only) — new in rc17

| ID | Device | Expected | Success criteria | Failure states |
|---|---|---|---|---|
| HW-NA-01 | Cisco Catalyst (IOS-XE) with SNMPv3 authPriv, saved profile bound to the plan | `sysDescr`, ENTITY serial/model, IF-MIB ports, 64-bit counters, LLDP, CPU (OID marked unverified) | Values equal `show interfaces` / `show inventory`; missing counters absent, not zero | CPU OID differs by platform (A-SNMP-02) → record; counter wrap handled |
| HW-NA-02 | Generic SNMPv3 switch | Same as HW-NA-01 without the CPU OID | Same | Device lacks IF-MIB HC counters → rates absent (expected) |
| HW-NA-03 | UniFi Network controller (read-only local account) | Device list, ports, clients per AP | Counts equal the controller UI | API path/version differs (A-UNIFI-01); self-signed TLS must follow the platform TLS policy |
| HW-NA-04 | Any write attempt on the above | Refused centrally (`READ_ONLY`) | Nothing reaches the device (packet capture) | Any SET/POST observed (**critical fail**) |

## D. Printers and print servers — new in rc17

| ID | Operation | Expected | Success criteria |
|---|---|---|---|
| HW-PR-12 | Storage growth after the change-only rule | Over 24 h at 900 s polling, unchanged printers store ≤ 4 heartbeat samples | Measure `printer_samples` rows/day for 10 printers; record bytes/printer/day |
| HW-PR-13 | Automatic purchase request from a real LOW toner | One request with measured evidence; no duplicate the next day | Requires `printer_purchasing_policy.automatic=true` in the lab only |
| HW-PJ-01 | Print-server agent (CUPS or Windows) posting metadata with an ingestion token | Jobs appear; duplicates ignored; revoked token refused | **No agent exists in this repository yet** — the integration is BLOCKED until one is written and approved (see the handover) |
| HW-PJ-02 | Document inspection with a synthetic test document (fake card number, "CONFIDENTIAL") | Findings stored as masked tokens; no document text in the database | DB dump grep for the test strings returns nothing |

## E. Platform (CentOS Stream 9 server)

| ID | Operation | Success criteria |
|---|---|---|
| HW-PL-20 | Upgrade rc16 → rc17 with `scripts/inm.sh update` | Backup taken; migration 0014 → 0015 applied; `verify.sh` green |
| HW-PL-21 | Restore the pre-update backup to return to rc16 | rc16 runs on the restored 0014 database |
| HW-PL-22 | `deployment/centos9/verify-release.sh` with the release owner's public key | Signed package verifies; a modified file is refused (exit 2) |
| HW-PL-23 | Navigation on the server's real browser fleet (1366×768 and 1920×1080) | No menu/content overlap; every view tab loads |
