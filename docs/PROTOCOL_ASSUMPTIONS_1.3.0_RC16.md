# Protocol and vendor assumptions requiring confirmation (1.3.0-rc16)

Each assumption is what the code currently does where vendor behaviour could not be verified without hardware.
Confirm or correct each one during the hardware campaign (test IDs refer to `HARDWARE_VALIDATION_CHECKLIST_1.3.0_RC16.md`).
When an assumption is wrong the product must return an explicit failure/unsupported result, never a fabricated value.

## AOS-CX (6100 / 6200, 10.14)

| ID | Assumption (code location) | Risk if wrong | Confirm with |
|---|---|---|---|
| A-CX-01 | Interface statistics expose `rx_bytes`, `tx_bytes`, and either `rx_error`/`tx_error` or `rx_errors`/`tx_errors` (`writers/aruba_cx.py` telemetry). | Errors/rates missing → shown as "Not measured", alerts never fire. | HW-CX-02 |
| A-CX-02 | `poe_interface.status.measured_power` (or `power_drawn`) is milliwatts when > 100, otherwise watts. Per-port PoE never exceeds 90 W (802.3bt class 8), so values > 100 can only be mW. | Values reported in W below 100 but actually mW (≤ 100 mW) would be shown as watts. | HW-CX-03 |
| A-CX-03 | `link_speed` is bits per second. | Utilisation wrong by a constant factor (≤ 100 % guard may hide it). | HW-CX-02 |
| A-CX-04 | Counters are 64-bit; a decrease means a clear/reboot, never a wrap (`domain/alert_rules.port_rates`). | A genuine 32-bit wrap loses one interval (no false value). | HW-CX-05 |
| A-CX-05 | PoE and LLDP are read with one GET per **up** port per poll. Device CPU/REST rate limits at 48 ports × 300 s are acceptable. | Switch REST throttling (429 → `RATE_LIMITED`) or CPU load. | HW-CX-02/03 with CPU observed |
| A-CX-06 | A port whose `poe_interface` GET fails has no PoE (shown `Unsupported`). | Transient errors shown as Unsupported for one poll. | HW-CX-04 |
| A-CX-07 | AnyCLI permits only the vendor's read-only `show` list and advertises it at `GET /cli/commands`. | Command refused → UNSUPPORTED (safe). | HW-CX-08 |
| A-CX-08 | Write schemas follow the 10.13/10.14 REST reference for each typed operation. | `DEVICE_REJECTED`/`READBACK_MISMATCH`; rollback via checkpoint. | HW-CX-10 |

## Instant AOS (AP-505 / AP-535, 8.10)

| ID | Assumption | Risk if wrong | Confirm with |
|---|---|---|---|
| A-AP-01 | Instant REST on HTTPS 4343; member APs addressed through the VC; identity from `show aps`. | Identity unverified → writes blocked (safe). | HW-AP-01 |
| A-AP-02 | ARM `show` outputs (rf-summary, history, channels, config) keep the column layouts parsed in `aruba/arm.py` / `parsers.py`. | Parse failure → insufficient evidence (safe), never healthy. | HW-AP-03 |
| A-AP-03 | ARM config key `80Mhz Support` reflects a **capability**; actual operating width is not inferred from it. | — (deliberate) | HW-AP-03 |
| A-AP-04 | Commissioners Office values (5 GHz 149/153/157/161/165, 6–21 dBm, Scanning/Client Aware/ClientMatch/Channel Quality Aware on, prefer 5 GHz) are an initial comparison reference only. Change plans remain 20/40 MHz. | — (owner policy) | owner sign-off |
| A-AP-05 | Firmware member evidence must be per-serial; aggregate VC firmware is not member evidence. | — (enforced) | HW-AP-09 |

## Printers (SNMP)

| ID | Assumption | Risk if wrong | Confirm with |
|---|---|---|---|
| A-PRN-01 | RFC 3805 Printer-MIB / Host Resources OIDs are authoritative; vendor OIDs (Kyocera/Xerox/HP) ship `VENDOR_UNVERIFIED`. | Vendor counters stay UNVERIFIED until compared. | HW-PRN-03 |
| A-PRN-02 | SNMPv3 protocol names map to pysnmp: `SHA` = HMAC-SHA-1-96, `SHA256` = HMAC-192-SHA-256, etc.; `AES128` = CFB128-AES; keys are passphrases localised per engine ID. | A mismatch looks like no response. | HW-PRN-01 |
| A-PRN-03 | The same SNMPv3 user has a write view for `sysLocation.0` when configuration is enabled. | SET rejected → reported, no retry. | HW-PRN-04 |
| A-PRN-04 | Printers report unsupported objects as noSuchObject/noSuchInstance; a reported 0 is a measured zero. | A printer that returns 0 for "unsupported" would show measured zero. Flag per model. | HW-PRN-03 |
| A-PRN-05 | A GETNEXT walk that errors after some rows is truncated, not complete (`SnmpTruncated`). | — (conservative) | HW-PRN-05 |
| A-PRN-06 | UDP timeout 3 s, 1 retry per GET; 45 s per-poll budget. | Slow printers lose late metrics (kept partial). | HW-PRN-03 |

## Platform defaults needing an owner decision

| ID | Default | Decision needed |
|---|---|---|
| A-OPS-01 | Alert clear bands: 5 points (%), 3 dB, 3 °C, 50 % for per-poll delta counters, none for discrete failures; per-site `clear_threshold` override. | Operations to confirm per rule. |
| A-OPS-02 | Printer raw-sample retention 365 days in the management DB (≈ 12.4 KB per poll measured). | Retention period and storage budget (see acceptance PRN-08). |
| A-OPS-03 | Provisional capacity targets (25 concurrent users; 1,000 printers @ 900 s; ≥ 200 APs + ≥ 50 switches @ 300 s). | Confirm the real fleet size and concurrency. |
| A-OPS-04 | Device-log retention: no automatic deletion. | Retention and disposal policy (data controller). |
