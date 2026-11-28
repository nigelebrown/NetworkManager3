# NetworkManager3 1.3.0-rc18 — protocol assumptions (not yet validated on hardware)

This file adds to `docs/PROTOCOL_ASSUMPTIONS_1.3.0_RC17.md` and `…_RC16.md`. Both still apply.

Each assumption below is implemented in code. Each has been exercised only against this repository's emulators or
fixtures, and must be confirmed or corrected on physical equipment (see the checklist row). Where real behaviour
differs, change the writer/parser **and** the emulator together, then re-run the certification campaign.

| ID | Assumption | Where in code | Validate by |
|---|---|---|---|
| A-CX-14 | RADIUS/AAA on AOS-CX 10.x uses these documented REST resources: `/system/vrfs/{vrf}/radius_servers/{address},{port},{port_type}` (timeout, retries, accounting port, tracking); `/system/aaa_server_groups/{name}`, with membership and priority held on each server's `server_group` map; `/system/aaa_accounting_attributes/port-access` (`accounting_mode`, interim interval); and `/system/interfaces/{port}/port_access_auth_configurations/{dot1x\|mac-auth}` referencing a server group. Shared secrets are write-only and never returned by a GET. | `writers/aruba_cx.py` (RC18_OPS, `_render_rc18`) | HW-CX-22, HW-CX-23 |
| A-CX-15 | `show interface transceiver detail` (AnyCLI, read-only, advertised by the switch) reports, per port and lane, temperature, voltage, bias current, TX and RX power with the module's own alarm/warning thresholds; ports without DOM say so. | `infrastructure/optics.py` `parse_transceiver_detail` | HW-CX-25 |
| A-CX-16 | Single-area OSPFv2 on CX 6200 is `/system/vrfs/{vrf}/ospf_routers/{instance_tag}` with an area and interface membership. CX 6100 has no OSPF licence or feature: detection reports UNSUPPORTED_BY_MODEL and nothing is sent. BGP is not offered on 6100/6200. | `writers/aruba_cx.py`, `api/platform_ops.py` `_device_capabilities` | HW-CX-24 |
| A-IAP-16 | Instant 8.10 `wlan auth-server <name>` accepts `ip`, `port`, `acctport`, `key`, `timeout`, `retry-count`. An SSID profile lists `auth-server` lines in failover order and accepts `server-load-balancing`, `radius-accounting` and `radius-interim-accounting-interval`. `show running-config` returns the auth-server block with the key masked. | `writers/aruba_instant.py` | HW-AP-18, HW-AP-19 |
| A-PRN-01 | Printers accept SNMP SET on: `sysContact.0`, `sysName.0`, `sysLocation.0`; `entPhysicalAssetID.1` (1.3.6.1.2.1.47.1.1.1.1.15.1); `prtGeneralPrinterName.1` (…43.5.1.1.16.1); `prtInputDefaultIndex.1` (…43.5.1.1.6.1), validated against `prtInputName` (…43.8.2.1.12.1); and `prtOutputDefaultIndex.1` (…43.5.1.1.9.1), validated against the media-path table (…43.13.4.1.9.1). Which of these each Kyocera model actually accepts is per model: the per-printer hardware test records it. | `printers/configuration.py` | HW-PRN-10, HW-PRN-11 |
| A-PRN-02 | No standard Printer-MIB object controls energy saving / sleep timers, so it is UNSUPPORTED (no vendor OID is guessed). | same | HW-PRN-12 |
| A-TEAMS-01 | Microsoft Teams incoming webhooks / Workflows accept `{"type":"message","attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","content":{…AdaptiveCard 1.4…}}]}`. | `infrastructure/notifications.py` `teams_card` | HW-INT-01 |
| A-SYSLOG-TCP-01 | The receiving syslog collector accepts RFC 5424 over TCP with RFC 6587 octet-counting framing. | same | HW-INT-02 |
| A-ALLOY-01 | Alloy 1.11 `loki.source.syslog` exposes `__syslog_connection_ip_address` (verified in this workspace with real packets: label `source_ip`). Its RFC 3164 parser rejects a year before the time (`Nov 14 2026 19:31:02 host …`) and counts the rejection in `loki_source_syslog_parsing_errors_total`. A year after the time is parsed with host = year, which the pipeline corrects only when an app tag follows. | `configs/alloy/config.alloy`, `domain/syslog_host.py` | HW-SYS-01 |
| A-CISCO-SYSLOG-01 | Cisco IOS/IOS-XE configuration events are `%SYS-5-CONFIG_I: Configured from <source> by <user> on <line>`. | `domain/attribution.py` | HW-NA-05 |
| A-UNIFI-02 | The UniFi controller reports AP `model` without separators (`U6PRO`). Inventory model names (`U6-Pro`) are matched by their normalised name. | `domain/adapters.py` `resolve` | HW-NA-03 |

## Behaviour that does not depend on a device but must be confirmed operationally

- The syslog bridge trusts an event for attribution only when the sender address Alloy recorded is a registered
  management address. A NAT or relay between the devices and the collector would hide that address. In that case
  attribution stays at timeline-only until the relay preserves the source address or forwards with TLS.
- The onboarding lockout guard assumes a 2-failure / 30-minute budget is below the AAA lockout policy. Set
  `INM_ONBOARDING_MAX_AUTH_FAILURES` below the real policy (RC16-T12).
