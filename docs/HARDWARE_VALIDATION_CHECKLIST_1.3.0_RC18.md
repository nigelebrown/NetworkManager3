# NetworkManager3 1.3.0-rc18 — hardware validation checklist

**Nothing below has been run.** Every row starts as `NOT_RUN`. This file **adds** to the rc16 and rc17 checklists.
Every earlier row still applies to rc18 and must be run as well.

Record results in `docs/Unified_Acceptance_Register_v5.csv` (row RC18-T17 links here). Each result needs:

- build SHA
- tester
- device serial
- firmware
- redacted request/response samples

An emulator pass is never a hardware pass.

## Global prerequisites (changed in rc18)

1. rc18 uses management schema **0016**. Rolling back to rc17 (0015) is not schema-compatible: restore the
   pre-update backup (`docs/DEPLOYMENT_RC18_UPGRADE_BACKUP_ROLLBACK.md`).
2. Every write row needs all of the following:
   - a lab serial in `INM_CERTIFICATION_LAB_SERIALS`
   - the write lock released for the pilot only
   - an independent approver account
   - out-of-band console access
   - a factory-recovery procedure on paper
3. Use spare devices only. 80 MHz must never appear in any readback.
4. A multi-device or cluster deployment that the impact view marks HIGH needs a third person for the release
   approval (neither the creator nor any plan approver).

## A. AOS-CX 6100 / 6200 (10.14)

| ID | Operation | Success criteria | Failure states | Rollback |
|---|---|---|---|---|
| HW-CX-22 | `switch.aaa.radius_server` create, change timeout/retries, delete, against a lab RADIUS server (A-CX-14) | Readback fields equal the plan; `show radius-server` agrees; the shared secret never appears in any API response, log or audit row | Resource key format differs (record exact key); secret echoed (**critical fail**) | Executor rollback (inverse op) / checkpoint |
| HW-CX-23 | `switch.aaa.server_group` + `switch.aaa.accounting` (start-stop, interim) + `switch.port.auth_policy` dot1x and MAB on a lab port | A lab supplicant authenticates through the primary server. With the primary down it fails over to the secondary. Accounting start/stop records arrive. The client-status view matches `show port-access clients` | Priority semantics differ; MAB order differs; accounting not sent | Checkpoint restore; port policy removal |
| HW-CX-24 | OSPF single area on a 6200 pair; same request on a 6100 | 6200: adjacency FULL, route learnt, readback equal. 6100: refused as UNSUPPORTED_BY_MODEL before any write (packet capture shows no PATCH/POST) | Any write reaching the 6100 (**critical fail**) | Checkpoint restore |
| HW-CX-25 | Optics on SFP/SFP+ with DOM, a module without DOM, and an empty cage (A-CX-15) | Values equal `show interface transceiver detail`. No-DOM shows UNSUPPORTED, never OK. A low-RX lab attenuator raises a WARNING/CRITICAL alert, which resolves on a healthy reading | Format differs per firmware (record a capture for the fixture) | none (read-only) |
| HW-CX-26 | Group deployment to two switches in two sites | Impact view shows MULTI_SITE. Execution refused until a third person approves the release; after approval, waves run one site at a time and stop at the first failure | Execution possible without release (**critical fail**) | per-plan rollback |

## B. Instant AP-505 / AP-535 (8.10)

| ID | Operation | Success criteria | Failure states | Rollback |
|---|---|---|---|---|
| HW-AP-18 | `ap.radius.server` create/modify (timeout, retry-count, acct port) and SSID `ap.ssid.auth_servers` primary + failover + load balancing (A-IAP-16) | Running-config block equals the rendered lines (key masked). A lab client authenticates. With the primary down it fails over to the secondary | Keyword names differ on the build; key shown unmasked in a response (**critical fail**) | Inverse lines restore the prior list and order |
| HW-AP-19 | `ap.ssid.accounting` enable + interim interval; secret rotation (needs the previous secret) | Accounting start/interim/stop records arrive at the lab server. After rotation the client still authenticates; rollback restores the previous secret | A rotation without the previous secret is accepted (**fail**) | Executor rollback |
| HW-AP-20 | Cluster deployment touching one AP of a multi-AP cluster | Impact view lists every cluster member as reached (CLUSTER_SCOPE) and requires the release approval | Members not listed (**fail**) | per-plan rollback |

## C. Printers (Kyocera fleet models)

| ID | Operation | Success criteria |
|---|---|---|
| HW-PRN-10 | Per-printer hardware test (lab serial) for location, contact, name, asset ID and printer name (A-PRN-01) | Each setting is recorded SUPPORTED (SET + readback equal + restored) or UNSUPPORTED (error status recorded). No setting left changed after the test |
| HW-PRN-11 | Default input tray and default media path via governed change | Index validated against the printer's own table; readback equal; rollback restores the original index |
| HW-PRN-12 | Energy saving | Reported UNSUPPORTED (A-PRN-02); no SET sent |

## D. Syslog, AAA and logging health

| ID | Operation | Success criteria |
|---|---|---|
| HW-SYS-01 | Packet capture of the syslog header emitted by AP-505/AP-535 (8.10) and CX 6100/6200 (10.14) to UDP 1515 and 1514 (A-ALLOY-01) | Record the exact header. If it puts the year before the time, Alloy rejects it: confirm `inm verify` reports the parse errors and change the device's syslog format or add a pre-parser. **Owner decision required** |
| HW-SYS-02 | Configuration change on each family by a named TACACS+/RADIUS user | Event on the device timeline within one bridge cycle; attribution VERIFIED with that user; without the AAA session → UNKNOWN plus an AAA_EVIDENCE_GAP alert |
| HW-SYS-03 | Stop syslog on one device for longer than `INM_SYSLOG_SILENCE_MINUTES` | SYSLOG_SILENT opens, then resolves after syslog resumes |
| HW-SYS-04 | Onboarding with two wrong profiles and one right one against a lab AAA server with a 3-failure lockout | The account is never locked. Rejected profiles are held (AUTH_ATTEMPTS_CAPPED) after 2 failures; the right profile onboards |

## E. Non-Aruba (read-only)

| ID | Operation | Success criteria |
|---|---|---|
| HW-NA-05 | Cisco IOS-XE `configure terminal` change by a TACACS+ user, syslog to the collector (A-CISCO-SYSLOG-01) | `%SYS-5-CONFIG_I` on the device timeline, attributed VERIFIED with the user |

## F. Integrations and platform

| ID | Operation | Success criteria |
|---|---|---|
| HW-INT-01 | Teams connection test to a real Teams channel (A-TEAMS-01) | The card renders. A failure is shown on the connection with the HTTP status |
| HW-INT-02 | Syslog-TCP integration to the site SIEM (A-SYSLOG-TCP-01) | Events parsed by the SIEM with correct framing |
| HW-PL-24 | Upgrade rc17 → rc18 with `scripts/inm.sh update` on CentOS Stream 9 | Signature verified before any content is used; quiesce gate passes; backup taken; 0015 → 0016; `verify.sh` green |
| HW-PL-25 | Update with a modified or unsigned package | Refused before any file is used (exit non-zero); installed release untouched |
| HW-PL-26 | Update while a change is executing | Refused by the quiesce gate (exit 5); succeeds once the change finishes |
