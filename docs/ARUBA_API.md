# Aruba Instant 8.10 API — compatibility ledger

Status key: **DOCUMENTED** = in Aruba's Instant 8.x REST API guide (per my knowledge; I could not reach the AP or Aruba's site from the build
environment) · **PENDING** = must be confirmed on your AP-505 · **UNSUPPORTED** = not available in Phase 1.

| Capability | Endpoint / command | Status | Notes |
|---|---|---|---|
| Authenticate | `POST /rest/login` `{"user","passwd"}` → `sid` | DOCUMENTED; you confirmed login works | `sid` is held in memory only, never logged |
| Run show command | `GET /rest/show-cmd?iap_ip_addr=<vc>&cmd=<cmd>&sid=<sid>` | DOCUMENTED | Command is URL-encoded, never put in a shell |
| Enable the REST API | SSH: `configure terminal` / `allow-rest-api` / `end` / `commit apply` | DOCUMENTED (REST guide), SSH session PENDING on your AP | REST is off by default and cannot enable itself. SIMS pins the SSH host key, runs only this fixed list |
| Logout | `POST /rest/logout` | DOCUMENTED | Best effort |
| AP inventory | `show aps` | PENDING (format) | Parser accepts structured JSON or a text table; unknown columns are kept as extras |
| Clients | `show clients` | PENDING (format) | |
| Firmware | `show version` | PENDING (format) | |
| Per-AP firmware | none verified | UNSUPPORTED in Phase 1 | Shown as unavailable rather than assumed from the VC |
| Client VLAN | not in `show clients` | PENDING | May need `show ap debug client-table` (probed by `verify_live --probe`) |
| RSSI | not in `show clients` | UNSUPPORTED in Phase 1 | `show clients` Signal is shown as SNR. Confirm meaning in Step 3 |
| RF per radio: channel, EIRP, noise floor, channel utilization, retry | `show ap arm rf-summary` | **VERIFIED** on AP-505/AP-535, Instant 8.13 (format) | Run per AP with `iap_ip_addr`. Radio values come from the row matching that radio's current ARM channel. Noise is a positive magnitude in the table (92 = -92 dBm). `retry`/`phy-err`/`mac-err` unit not confirmed, stored raw, not scored |
| Uptime, CPU %, memory % | `show ap debug system-status` | **VERIFIED** (format) | Latest row of "CPU and Memory Usage" (not the 1-hour peak); "AP Uptime" text. Output is large (~100 KB) |
| Per-AP targeting of the two commands above | `iap_ip_addr=<ap ip>` | PENDING on non-conductor APs | Documented parameter. If an AP rejects it the metrics stay unavailable and the poll warns; after 3 consecutive failures remaining APs are skipped |
| BSS table, summary | `show ap bss-table`, `show summary` | Format seen, **not consumed** | bss-table adds channel width (e.g. 2.4GHz/HE/20MHz) for Phase 4. `show summary` has the cluster AP list but no per-AP uptime |
| Radio counters | `show ap debug radio-stats` | Format seen, **not consumed** | Output shows one radio without saying which, so SIMS does not guess. Needs a per-radio form of the command |
| Read configuration | `show running-config` | PENDING (format) | Parser expects `wlan ssid-profile <name>` blocks; run `verify_live` |
| Create/modify SSID (incl. VLAN) | `POST /rest/ssid` | DOCUMENTED, PENDING on your AP | Master AP only. Omitted-field behavior undefined, so apply is read back |
| Rename AP | `POST /rest/hostname` | DOCUMENTED, PENDING on your AP | Sent to the VC with `iap_ip_addr` of the target AP |
| Delete SSID | `{"action":"delete"}` | UNDOCUMENTED, not implemented | Plan shows `no wlan ssid-profile <name>` for the AP CLI |
| Join an AP to a different cluster | — | UNSUPPORTED | No documented REST call; decided by the network. SIMS only re-assigns inventory |
| Stage firmware | `POST /rest/os-upgrade` `{"upgrade-info": {"auto-reboot": "no", "<Class>-url": "<url>"}}` | DOCUMENTED (8.10 REST guide), **PENDING on your cluster** | URLs only, no file upload. SIMS serves the image at a token link and requests `auto-reboot: no`. The guide's table says yes/no but its template shows a boolean; SIMS sends the string `"no"`. **Test on a lab AP first.** Staging state cannot be read back, so apply is not "verified" until you reboot and compliance changes |
| Reboot the cluster | — | NOT IMPLEMENTED | Do it from the AP CLI (`reload`) or WebUI after staging |
| Upload WebUI/REST server certificate | CLI `copy tftp <ip> <file> uiserver cert <passphrase> format pem` | DOCUMENTED (CLI Bank, 8.3+), **PENDING on your AP** | No REST call exists. TFTP only. SIMS runs a one-file, one-client TFTP server and checks afterwards that port 4343 presents the new certificate |
| Firmware compliance | `show version` | Derived | Cluster-level; no verified per-AP version source |

Safety: `backend/aruba/client.py` accepts only commands on `READ_ONLY_COMMANDS`, rejects control characters, and
limits the character set.

Adding a command: add it to the allowlist, add a parser with a mock-response test, run `verify_live --probe`
against the AP, then flip its row above to verified.
