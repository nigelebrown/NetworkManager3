# NetworkManager3 1.3.0-rc17 — change log

Base: 1.3.0-rc16 (local commit `90e3662`, itself on upstream `eaf40a93`). Management schema **0015** (new,
expand-only), telemetry **t002** (unchanged). Patch: `NetworkManager3-1.3.0-rc16-to-rc17.patch`.

All evidence below is developer testing in this workspace against labelled emulators (EMULATED) or pure functions.
Nothing in this release has been run against physical Aruba, Cisco or UniFi equipment or the live server.

## Navigation: one menu entry per purpose (owner request, 25 Nov 2026)

- The sidebar went from 50 entries to 37. Each entry now has a single purpose (shown as its tooltip and beside the
  view tabs). Screens that showed overlapping content are now **views (tabs) of one entry**:
  - Command center + Wi-Fi view
  - Device inventory + Access points + AP controller view + CX switches
  - Wireless clients + Connectivity journeys + Experience scores
  - RF health + AP health report
  - Ports, PoE & LLDP + Port monitoring groups
  - Device alerts & incidents + Wi-Fi client alerts
  - SSID policies + Legacy WLAN settings
  - Change plans + Approvals queue + Group deployment
  - Configuration vault + Restore & replace + Recovery journal
  - Recommendations + Device insights
  - AI learning centre + AI rules + Site rules
  - Onboarding + Discovery + Range planner + Manual/CSV intake + Inventory import
- Every route still exists (bookmarks keep working). Exactly one menu entry is active for any route, including
  device drill-downs (`/devices/:id` → Device inventory, `/devices/:id/360` → Device 360°).
- Fixed: the demo/write-lock/TLS status banners ran underneath the fixed desktop sidebar. They now start at the
  content edge.
- The active menu entry scrolls into view.
- Verified with `tests/e2e/nav_audit.py` in Chrome:
  - 37 entries at 1440 px and 1180 px, plus 66 view tabs
  - 0 overlaps between the sidebar and the content, top bar or banners
  - 0 duplicate menu targets, and 0 routes with more than one active entry
  - no horizontal scroll
  - the sidebar stays hidden at 390 px
  - Evidence: `evidence/e2e/nav-rc17/`.

## Implemented

| Area | Change |
|---|---|
| Demo safety | Live mode refuses `demo-load` unless `INM_ALLOW_DEMO_DATA_IN_LIVE=true`. The refusal is audited (`demo_dataset_load_refused`). |
| Truthful labels | `/system/features` is computed. Predictive shows the latest run's validation state. Firmware, ACL/routing and stacking are `IMPLEMENTED_UNCERTIFIED`. Diagnostics are `PARTIAL`. Cisco/UniFi are `READ_ONLY`. Print jobs are `OFF_BY_POLICY`. Roaming is `LIVE_INFERRED`. Menu tags read these values. The static "VALIDATED" tag and the `Planned.tsx` placeholder page were removed. Retired 501 endpoints now return 410 with the live route. |
| Device logs | Retention policy (30–3,650 days, approval reference). Preview and apply. Rows referenced by change attribution are kept. Runs daily in the worker. |
| RF optimization | Site batch enrolment. Each AP still passes every per-device gate, an independent approver is required, and nothing is inherited. |
| Printer storage (RC16 finding) | A raw sample is stored only when a value changes or the heartbeat expires (default 6 h, configurable 1–24 h). Previously every poll stored one. |
| CX switch | IPv4 ACL define/apply, static routes, DSCP QoS policy, and VSF member provisioning (VSF-capable platforms only). All are typed, validated, readback-checked and rollback-capable. |
| CX firmware | Image store (vendor SHA-256 must match) → plan (train/downgrade rules) → independent approval (24 h) → stage to the non-booted slot with readback → boot (journal `BOOT_SENT` and commit before sending; reconnect; verify version and serial) → recover (boot the previous slot). |
| AP firmware | Cluster reboot orchestration: independent approval bound to the member inventory, `REBOOT_SENT` journalled before `reload all`, never resent, bounded member verification. |
| Instant WLAN | New typed operations:<br>• `ap.role.define` (user role with ordered ACL rules)<br>• `ap.captive_portal.external` (HTTPS by default; fail-through refused)<br>• `ap.ssid.multi_auth` (MAC + 802.1X on an enterprise SSID)<br>All refuse to overwrite existing objects, use the allowlisted CLI grammar, are readback-checked, and roll back by inverse operation. |
| Instant restore | Typed reconciliation: compares the vault baseline with the running config and produces a PENDING_APPROVAL change plan of typed operations. 80 MHz, deleted secrets and untyped lines are reported, never replayed. |
| Diagnostics | Ping and traceroute from a device over pinned SSH, using fixed templates with a per-device lock. Cable test and packet capture are explicitly UNSUPPORTED, with reasons. |
| Non-Aruba | Generic SNMPv3, Cisco IOS-XE (SNMP) and UniFi controller adapters are **read-only** and run through the normal collection pipeline. SNMP plans bind a saved SNMP profile. |
| Monitoring | Independent active probe (TCP reachability and RTT), opt-in and limited to allow-listed registered devices. Device clock offset from the HTTP Date header. New `PROBE_UNREACHABLE` and `CLOCK_DRIFT` alerts. Clock drift blocks VERIFIED change attribution. |
| Analytics | Roaming: AP pairs, ping-pong, excessive roamers, sticky candidates. Interference: noise/retry/utilisation correlation from measured samples only. Viewers see masked MACs. |
| Printers | Internal purchase requests: dedupe, independent approval, PO reference, CSV export, outbox notifications, and automatic requests from measured LOW/EMPTY or forecast within the lead time (off by default). |
| Printers | Print-job metadata plus in-memory document inspection. Off until a purpose and an approval are recorded. Agents use hashed ingestion tokens. Content is never stored (SHA-256 and masked tokens only). User pseudonyms are optional. Job-level reads are admin/auditor only and audited. Daily retention purge. |
| Release | `scripts/release_signing.py` (Ed25519 over SHA256SUMS) and `deployment/centos9/verify-release.sh`. |
| Frontend | Route-level code-splitting: main chunk 1,311 KB → 210 KB. |
| Bug fix (pre-existing since baseline) | The printer discovery IP list was split on the literal characters `\` and `s` instead of whitespace, so "a, b" sent " b" and was rejected (422). Found by the printer browser suite. Fixed. |

## Database

- Migration **0015** (expand-only) adds three tables: `printer_purchase_requests`, `print_jobs` and `print_job_findings`.
- Downgrade raises. Restore the pre-upgrade backup instead.
- `release.json` accepts 0015 and can upgrade from 0004–0014.

## New settings (all default to the safe value)

| Setting | Default |
|---|---|
| `INM_ALLOW_DEMO_DATA_IN_LIVE` | `false` |
| `INM_SCHEDULED_ACTIVE_PROBE` | `false` |
| `INM_CLOCK_DRIFT_TOLERANCE_SECONDS` | `120` |
| `INM_CX_REBOOT_WAIT_SECONDS` / `INM_CX_REBOOT_POLL_SECONDS` | — |
| `INM_IAP_REBOOT_WAIT_SECONDS` / `INM_IAP_REBOOT_POLL_SECONDS` | — |

Further policies are stored in the application, not as environment variables:

- `printer_purchasing_policy.automatic` (default false)
- `print_job_policy.enabled` (default false)
- `device_log_retention` (off)

## Not changed

- 20/40 MHz only; no code path configures 80 MHz.
- AI has no credential access and no write path outside change governance.
- Write lock, emergency stop, certification scope, independent approval and no blind replay are unchanged.
