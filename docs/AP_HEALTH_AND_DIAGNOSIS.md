# Wireless operations release — fixes, design and test evidence (2026-11-14)

Branch-ready change set for the issues reported against the live JCF deployment (518 devices onboarded,
247 APs, 271 CX switches, 2 polled Instant clusters). Every hardware-facing statement below is **software-tested
only**; real-hardware evidence is `NOT_RUN` until observed on the CentOS host.

## Root causes found

| Symptom | Root cause | Fix |
|---|---|---|
| Devices show a **green dot but UNKNOWN** | `.inm-dot` CSS default is green; `tone("UNKNOWN")` returned `""` | grey `unknown` tone; NOC tiles show `state_reason` |
| ~219 APs UNKNOWN / "never" | Only 2 Instant clusters (VCs) are registered. Instant serves REST only on the VC, so per-AP REST probes of member APs cannot work, and imported APs were never linked to a cluster | Cluster-aware **auto-onboarding** (below) |
| Duplicate AP records | Cluster projection looked up `vendor == "aruba"`; imported/onboarded records use `"Aruba"` → a second record was created and the imported one (with site, credential, approvals) was hidden | `sync_inventory` adopts the imported record (case-insensitive vendor + serial, or MAC-as-hostname), keeps the operator's physical site, retires stale duplicates as `lifecycle=merged` |
| Some clusters have REST disabled | REST cannot be read; the only enable path was per-AP manual | Read-only **SSH CLI collector** (`SshShowClient`) + optional gated `allow-rest-api` bootstrap per cluster |
| Wi-Fi dashboard "28/28 online, health 100" | Denominator was only APs in polled clusters | Coverage tile (observed / registered), banner when APs are unobserved, 2.4 + 5 GHz utilisation, clients by band, honest labels |
| No 2.4 GHz on Access points | Inventory table only rendered `util_5g_percent`; N+1 metric calls per AP | New fleet API with both radios (channel / busy / noise) per AP |
| Logs "off" / UNMAPPED syslog | (a) syslog mapping only matched hostnames of cluster-projected devices; (b) Instant/CX send RFC 3164 which the RFC 5424 listener drops; (c) syslog binds to 127.0.0.1 by default | Map by hostname, IP or serial for all visible devices; RFC 3164 listener on 1515 published as `${INM_SYSLOG_BSD_PORT:-514}`; `.env.example` documents `INM_SYSLOG_BIND` |
| Switch health 100 with CPU 73 % | Penalties only above 85 % CPU; no switch factors | Elevated CPU/memory, fans, PSU, temperature, port-error penalties; health returns the inputs it checked |
| Device insights NO_TELEMETRY | Switches had credentials but no collection plan | One-click bulk read-only plans for every credentialed switch (`/api/v1/guide/actions/auto-plans`) |
| Device inventory GUI contract tests failing (pre-existing) | "Approve IP & Test" / "Test & Approve All" flows had been dropped | Restored exact-/32 approval flow and batch actions |

## New capabilities

* **AP auto-onboarding** (`backend/infrastructure/ap_autodiscovery.py`, Access points → Auto-onboard):
  1. bulk *unauthenticated* SSH host-key scan → 2. administrator bulk-approves fingerprints (`APPROVE HOST KEYS`) →
  3. run. For each unlinked AP: try bound credential (or every enabled AP profile, binding the first that works),
  REST `show aps` on the AP IP, else read-only SSH `show aps` on an approved host; VC = row marked `*`; register the
  cluster once; every member serial/name/MAC is linked (one login per cluster); second pass links APs that could not
  be probed themselves; first poll + inventory projection. Optional REST bootstrap goes only through the existing
  gated `ssh.enable_rest_api`. Credentials are never sent to a host whose key was not approved.
* **Fleet by cluster** (`GET /api/v1/wireless/fleet`; Access points and Wireless & RF health pages): grouped and
  filterable by cluster, state, tier and search; both radios; health tier; per-AP "why / what to do".
* **AP health — Gold / Silver / Bronze** (`/ap-health`): thresholds identical to the 24 Apr 2026 action report (all 21
  AP tiers reproduced in tests). Evidence = syslog counts per AP computed inside Loki (metric query), ingested device
  log events, or an imported Aruba event export (CSV); plus the measured point-in-time condition (offline, 2.4/5 GHz
  utilisation, noise, CPU, memory, client load). Worse tier wins; nothing measured → UNRATED (never Gold by default).
  Snapshots, tier-movement comparison, CSV export and print-to-PDF.
* **Root-cause diagnosis** (`backend/domain/wifi_diagnosis.py`; "Diagnose" on Experience, Access points, RF health,
  Client connectivity, Device 360°, AP health, Optimization): co-channel contention with named neighbours and the
  least-used 20/40 MHz channel, 2.4 GHz overlap, noise, density, weak-signal/sticky clients, band imbalance, legacy
  rates, CPU/memory, rejects / deauth top client / DHCP / auth from events. Optional on-prem AI narrative over the same
  evidence (pseudonymised; advisory).
* **Client connectivity** (`/client-journeys`, replaces the CONCEPT page): KPIs, cluster / AP / SSID breakdowns,
  filters, per-client journey (connect → roam → disconnect) and diagnosis.
* **NOC incidents** (`/api/v1/incidents`, Alerts & incidents): assign (acknowledges), link ticket, notes, resolve
  "no ticket required" or "ticket resolved", **auto-recovered** when monitoring measures the condition cleared (with
  "ticket still open" flag), reopen, recurrence detection, timeline, audit.
* **Device 360°**: real health with inputs, labelled telemetry, metric list from what was actually collected, Radios &
  clients and Incidents tabs, Diagnose, capture-config CTA when best practice is UNKNOWN.
* **Optimize by cluster**: pick a cluster → per-AP diagnosis, proposed channel, enrollment and blockers; propose for
  selected APs creates **one pending enrollment per AP** (still independently approved; nothing inherited). Enrollment
  table shows device names and next action, with approve/disable.
* **Guided workflows** (`/api/v1/guide/{monitoring|insights|learning|assistant|optimization|incidents}`): step status
  computed from live data, "Do this next", one-click bulk actions, and a DEMO-labelled sample-network run.

## Storage

Workflow metadata (incident work, AP health snapshots, pinned SSH keys, cluster transport, discovery job state) is
kept in `app_settings` JSON rows — no schema migration. Keys: `incident:<alert>`, `ap-health:latest`,
`ap-health-snap:<id>`, `ssh-hostkey[-pending]:<ip>`, `site-transport:<site>`, `ap-autodiscovery:job`.

## Safety invariants kept

No new device-write path. SSH collection uses the existing read-only command allowlist and a pinned host key.
REST bootstrap only through the pre-existing gated function. Optimization proposals are per device and never armed
here. Channel proposals limited to 20/40 MHz policy channels. Client identities are masked for viewers and removed
before any AI call.

## Test evidence (this environment, software only)

`tests/unit/test_wireless_operations.py` — 58 tests: PDF tier parity (21 APs + 9/6/6 totals), classifier, condition
tiers, fleet grouping and both bands, report + snapshots + movement, CSV import, client connectivity + masking,
dashboard coverage, inventory adoption (no duplicate), auto-onboarding (one cluster login links all members; no SSH
without approved key; fingerprint mismatch refused), SSH client allowlist/paging, incidents (assign / ticket /
resolve / reopen / auto-recovery / RBAC), Device 360 insight, diagnosis rules and endpoints, cluster optimization,
guides and bulk plans. Full-suite results are in `docs/WIRELESS_RELEASE_TEST_RESULTS.txt`.

Hardware acceptance (NOT_RUN): SSH `show aps` paging on AP-505 8.10.0.21; RFC 3164 syslog from Instant into Loki;
auto-onboarding against the real clusters; REST bootstrap under the production write policy.
