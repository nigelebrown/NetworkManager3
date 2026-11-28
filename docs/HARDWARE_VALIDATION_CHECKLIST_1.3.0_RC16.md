# NetworkManager3 1.3.0-rc16 — hardware validation checklist

For the engineer (or Codex) running the physical campaign. **Nothing below has been run.** Every row starts as `NOT_RUN`.
Record results in `docs/Unified_Acceptance_Register_v4.csv` with build SHA, tester, device serial, firmware and
redacted response samples. Emulator results never substitute for a row here.

## Global prerequisites (all tests)

1. Isolated lab VLAN; **spare** devices only — no production uplink, no live guest SSID, no production printer queue.
2. rc16 deployed via `bash scripts/inm.sh update --ref <approved-tag>` after `bash scripts/inm.sh backup`.
   `INM_MODE=live`, `INM_PROFILE=lab-readonly`; device write lock ON for every read test.
3. `INM_READONLY_DEVICE_CIDRS` (or exact approved IPs) contain only the lab addresses.
4. For write tests only: device serial listed in `INM_CERTIFICATION_LAB_SERIALS`, device recorded as a lab device,
   writes enabled for the lab pilot, emergency stop tested, a second (approver) account available.
5. Out-of-band console access to every switch and AP under write test; a factory-default recovery procedure on paper.
6. Record: model/SKU, serial, exact firmware build, REST API version (CX), Instant VC version, printer firmware.
7. Rollback for the whole campaign: `bash scripts/inm.sh rollback --to <previous-tag> --acknowledge-schema-compatible`
   is valid only for schema 0014 tags (rc15 ↔ rc16 share schema 0014/t002). Otherwise restore the pre-update backup.

Status field values per row: `NOT_RUN` → `PASS` / `FAIL` / `BLOCKED` with an artifact link.

---

## A. HPE Aruba AP-505 and AP-535 — Instant AOS 8.10.x (record exact build, e.g. 8.10.0.21)

| ID | Operation | Prerequisites | Expected response / behaviour | Success criteria | Possible failure states | Rollback |
|---|---|---|---|---|---|---|
| HW-AP-01 | REST login (HTTPS 4343), identity, cluster member list | AP in Instant mode; read-only admin account; TLS CA trusted (`INM_ARUBA_VERIFY_TLS=true`) | 200 on login; `show aps`/system JSON with serial, model, firmware; member AP matched by serial | Device 360 shows matching serial/model/build; state ONLINE; no credential in logs | `AUTH_REJECTED`, TLS failure, REST disabled (`REST_DISABLED`), multiple APs without target IP, `IDENTITY_UNVERIFIED` | none (read) |
| HW-AP-02 | SSH first-use host-key pinning (automatic onboarding) | SSH enabled; selected saved profiles | Key pinned only after successful auth + non-empty cluster read; changed key blocked | Pinned key recorded; second run reuses it; manually changed key → blocked for review | wrong-key accepted (**critical fail**), credentials sent before approval | delete pinned key record; re-run |
| HW-AP-03 | ARM reads: `show ap arm rf-summary`, `show ap arm history`, `show ap arm-channels`, neighbours, ARM config | ARM enabled | Parsed channels, quality %, utilisation %, interference index, power; config incl. `80Mhz Support`, allowed 5 GHz list | Optimization page shows fresh measurements; healthy radios produce "Keep native ARM control"; Commissioners Office differences listed, none auto-applied | parse failure (record raw redacted sample), stale/missing → must show as insufficient evidence, not healthy | none |
| HW-AP-04 | `ap.radio.channel`, `ap.radio.tx_power` | write prerequisites; reviewed change plan; independent approval | backup → write → readback equals approved value | readback matches; rollback restores prior value and readback confirms | `READBACK_MISMATCH`, `STALE_APPROVAL` (config changed after approval), ARM immediately overrides (record — not a failure of the write) | executor inverse operation; verify `show ap arm rf-summary` |
| HW-AP-05 | `ap.radio.width` (20/40 only) | as above | 80 MHz request refused before any write; 20/40 written | refusal logged; readback | 80 MHz accepted (**critical fail**) | inverse op |
| HW-AP-06 | `ap.radio.allowed_channels` (5 GHz allowlist, e.g. 149/153/157/161/165) | regulatory domain recorded | channels outside regulatory set refused; write + readback | readback list equals approved | DFS/regulatory mismatch | restore explicit prior list |
| HW-AP-07 | `ap.client_match`, `ap.band_steering`, `ap.airtime_fairness` | as above | readback equals approved | same | parameter names differ per build (record) | inverse op |
| HW-AP-08 | `ap.ssid.create`, `ap.ssid.vlan`, `ap.ssid.enterprise`, `ap.ssid.accounting`, `ap.ssid.roaming` (802.11k/v/r), `ap.ssid.guest_auth` | lab-only SSID; RADIUS lab server | profile created/updated; readback; clients can associate (manual check) | readback + association test recorded | `DEVICE_REJECTED`, partial profile, roles missing | inverse op / delete lab SSID |
| HW-AP-09 | Firmware stage-only (members, targeted version verification, previous-image recovery plan) | image hash/size recorded; maintenance window | images staged, **no automatic reboot**; per-member version evidence | each member serial reports targeted version after operator-controlled reboot | aggregate VC version used as member evidence (**fail**), size/hash mismatch | separately approved previous-image recovery plan |
| HW-AP-10 | Device logs `show log system/security/user` | opt-in schedule on one controller | bounded, redacted entries; duplicates coalesced | entries visible in Device 360; no secrets | format unrecognised → recorded failure, not empty-success | disable schedule |
| HW-AP-11 | Guided full restore | backup artifact of same model/build | operator-guided steps only; no invented REST restore | documented procedure completes | — | factory reset + guided restore |

## B. HPE Aruba CX 6100 and CX 6200 — AOS-CX 10.14 (record build, e.g. ML.10.14.1020 / PL.10.14.x)

| ID | Operation | Prerequisites | Expected response / behaviour | Success criteria | Possible failure states | Rollback |
|---|---|---|---|---|---|---|
| HW-CX-01 | REST login (form body), CSRF, version negotiation v10.09–v10.15, identity | REST enabled, read-only user | `/rest` versions list; login 200; `/system` serial/platform/software_version | negotiated version recorded; identity matches inventory | `AUTH_REJECTED`, `REST_DISABLED`, version unsupported, redirect (`DEVICE_REJECTED`) | none |
| HW-CX-02 | Interfaces + statistics + link_resets (scheduled collection) | collection plan, 300 s | `statistics.rx_bytes/tx_bytes/rx_error/tx_error` integers; `link_speed` in bps | port group shows util/rx/tx; counters monotonic; first poll stores baseline only | key names differ (`rx_errors` vs `rx_error`); speed not in bps (assumption A-CX-03) | none |
| HW-CX-03 | PoE per port `poe_interface.status.measured_power` / `power_drawn` | PoE device attached to a lab port | value in a known unit | Port group PoE equals the switch CLI `show power-over-ethernet` (±0.5 W) | unit mismatch (mW vs W, assumption A-CX-02) → **fail**, fix scaling | none |
| HW-CX-04 | Non-PoE port in a group with a PoE minimum | 6200 non-PoE SKU or uplink port | `poe_interface` 404 | dashboard shows `Unsupported` (not Healthy, not No data) | shows Healthy (**fail**) | none |
| HW-CX-05 | Counter reset (`clear interface statistics`) and link speed renegotiation (force 100 M) | lab port with traffic | next interval: no rate, `COUNTER_RESET` / `SPEED_CHANGED`; following interval resumes | no negative/spike values, no false `PORT_ERRORS` alert | spike recorded (**fail**) | restore speed auto |
| HW-CX-06 | Alert hysteresis on a real port near 85–90 % | traffic generator | one alert opens; stays open between 80–85 %; resolves below 80 % | one alert + one recovery notification | open/resolve churn | stop traffic |
| HW-CX-07 | LLDP neighbours, VSF/stack visibility, fans/PSU/temperature | — | values in Device 360 | match CLI | — | none |
| HW-CX-08 | AnyCLI allowlisted diagnostics; optics (`show interface transceiver detail`, `show interface dom`) | `GET /cli/commands` lists them | allowed only when advertised; else UNSUPPORTED | output bounded/redacted | command executed when not advertised (**fail**) | none |
| HW-CX-09 | Backup (running fullconfig), checkpoint, diff | vault configured | encrypted artifact; round-trip hash equal | `BACKUP_UNVERIFIED` never ignored | — | none |
| HW-CX-10 | Typed writes: `switch.vlan.define`, `switch.vlan.voice`, `switch.port.access_vlan`, `switch.port.trunk_vlans`, `switch.port.admin_state`, `switch.port.description`, `switch.port.poe`, `switch.port.qos_trust`, `switch.port.storm_control`, `switch.port.stp_bpdu_guard`, `switch.lag.members`, `switch.lldp`, `switch.system.dns`, `switch.system.ntp`, `switch.system.syslog`, `switch.central.disable` | disconnected test ports/VLANs; independent approval | checkpoint → write → separate GET readback equals approved value | each operation PASS individually; `CERTIFIED_WRITE` recorded for adapter+model family+firmware train+HARDWARE | `READBACK_MISMATCH`, `STALE_APPROVAL`, `AUTH_REJECTED` during write (must **not** be replayed), `CONNECTION_LOST` (must reconcile, not resend) | checkpoint restore + readback |
| HW-CX-11 | `switch.port.auth_dot1x`, `switch.port.auth_mab` (enable-only) | pre-provisioned RADIUS-backed method; port admin-down, access, client-mode | enable only; never disables auth | readback; real supplicant/MAB test recorded | fallback/override role present → refused | checkpoint restore |
| HW-CX-12 | Session expiry mid-write | shorten session timeout | write returns `AUTH_REJECTED`; executor reconciles via readback; no duplicate write in `/logs/audit` | exactly one write in device audit log | duplicate write (**critical fail**) | checkpoint restore |
| HW-CX-13 | Firmware read (current/primary/secondary, boot slot) | — | read-only firmware resource | values shown; no upload/reboot | — | none |

## C. Printers (SNMP) — per model; record firmware

Models in the fleet register: Kyocera ECOSYS MA5500ifx, M2640idw, MA4000cix, MA4000wifx, TASKalfa 6004i; Xerox
VersaLink B62/C62/C70/C40, AltaLink B807; HP Color LaserJet MFP M281fdw (rc15 live single read only), HP LaserJet 400
family; Epson WF-6590, WF-M5899; one unclassified entry.

| ID | Operation | Prerequisites | Expected response / behaviour | Success criteria | Possible failure states | Rollback |
|---|---|---|---|---|---|---|
| HW-PRN-01 | SNMPv3 interoperability: each printer's configured auth/priv pair (SHA-1/SHA-2/MD5 × AES-128/256/DES; authNoPriv where used) | v3 user on the printer; matching rc16 profile | sysDescr/sysObjectID returned | profile test 200; weak protocols flagged in UI | wrong protocol → indistinguishable from unreachable (record) | none |
| HW-PRN-02 | v2c and v1 (only if policy enables v1) in the same discovery job as v3 | profiles of each version selected | each printer bound to the profile that answered | job results list the working profile per address | v1 used while policy disabled (**fail**) | delete profiles |
| HW-PRN-03 | Full poll: identity, prtMarkerLifeCount + unit, supplies table, alerts, hrPrinterDetectedErrorState, trays | monitoring approved | values VALID/UNSUPPORTED/UNKNOWN_SPECIAL as reported; no fabricated zeros | matches the printer's front-panel / web page; vendor OIDs move from UNVERIFIED only after comparison | special values (-1/-2/-3) shown as empty (**fail**) | none |
| HW-PRN-04 | sysLocation SET (preview → apply → readback → rollback) | hardware write+rollback evidence required by the app; printer serial verified | serial checked before SET; one SET (no retry); readback; compensating rollback | location changed then restored; recovery record correct on timeout | SET retried (**fail**), serial mismatch not blocking (**fail**) | rollback action in UI |
| HW-PRN-05 | Fault preservation: pull network cable mid-walk; open a door/jam | active fault on printer | interrupted walk → `incomplete_metrics`, existing faults stay open; real clearance closes them | no fault closed by a partial read | fault closed on timeout (**fail**) | reconnect |
| HW-PRN-06 | Counter reset/replacement (main board swap or counter reset where supported) | — | `RESET`/`REPLACEMENT` interval classification | no negative utilisation | — | none |

## D. AAA / account safety

| ID | Operation | Prerequisites | Expected | Success | Failure | Rollback |
|---|---|---|---|---|---|---|
| HW-AAA-01 | Automatic onboarding with 3 saved profiles where 2 are wrong, against RADIUS/TACACS-backed device accounts | lab AAA server with lockout policy recorded | at most one attempt per selected profile per device per run | no AAA account lockout; attempts visible in AAA logs | lockout (**fail** → reduce profiles per run, document) | unlock account |
| HW-AAA-02 | Credential rotation (`/credentials/{id}/rotate`) with devices bound | new password on devices first | subsequent polls succeed; old secret no longer used | — | polls fail after rotation | rotate back |

## E. Fleet-scale / environment (not hardware, but not runnable here)

| ID | Test | Success criteria |
|---|---|---|
| ENV-01 | CentOS clean install, repeat install, `verify.sh`, encrypted `backup.sh` + `restore.sh --target test` | restored rows + audit chain verified |
| ENV-02 | 1,000 printers at 900 s, ≥ 200 APs, ≥ 50 switches at 300 s, 25 users, 24 h on the target VM | collection freshness ≥ 99 % within 3 × interval; API p95 < 1 s; storage growth measured and within the agreed retention budget |
| ENV-03 | TimescaleDB retention policy active (`timescale_retention_active`) | job present; chunks dropped after retention |
