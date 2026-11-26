# NetworkManager3 1.3.0-rc17 — protocol assumptions (unvalidated on hardware)

This adds to `docs/PROTOCOL_ASSUMPTIONS_1.3.0_RC16.md`, which still applies.

Each assumption below is implemented in code, exercised only against this repository's emulators, and must be
confirmed or corrected on physical equipment (see the checklist row). Where real behaviour differs, change the
writer/parser **and** the emulator together, then re-run the certification campaign.

| ID | Assumption | Where in code | Validate by |
|---|---|---|---|
| A-CX-09 | IPv4 ACLs are created/replaced at `/system/acls/{name},ipv4` with `cfg_aces` keyed by sequence, and attached via `aclv4_in_cfg` on the interface | `writers/aruba_cx.py` `_render_rc17` | HW-CX-14 |
| A-CX-10 | Static routes live at `/system/vrfs/{vrf}/static_routes/{prefix}` (prefix URL-encoded) with `static_nexthops` keyed by index | same | HW-CX-15 |
| A-CX-11 | QoS DSCP remark uses `/system/classes`, `/system/policies` and the interface `policy_in_cfg` | same | HW-CX-16 |
| A-CX-12 | VSF members are configured at `/system/vsf_members/{n}` with `link` port lists; only VSF-capable platforms (6200/6300) accept them | same; `apply` platform check | HW-CX-17 |
| A-CX-13 | Firmware upload is `PUT /firmware?image=primary|secondary` with a multipart `fileupload` part. Boot is `POST /boot?image=…`. `/firmware` reports per-slot versions after upload | `writers/aruba_cx.py` `firmware_upload/firmware_boot`; `firmware/cx_firmware.py` | HW-CX-18/19 |
| A-IAP-12 | Instant CLI `reload all` (exec mode on the VC) reboots every cluster member, and the session may drop before a reply | `firmware/ap_reboot.py` | HW-AP-15 |
| A-IAP-13 | `wlan access-rule <role>` takes `rule <ip|any> <mask|any> match <any|tcp|udp> <start|any> <end|any> <permit|deny>` lines, kept in order | `writers/aruba_instant.py` `role_lines`; `aruba/ssh.py` grammar | HW-AP-12 |
| A-IAP-14 | `wlan external-captive-portal <p>` accepts `server`, `port`, `url "<path>"`, `https`, `redirect-url`; fail-through is off unless configured | `portal_lines` | HW-AP-13 |
| A-IAP-15 | In an enterprise ssid-profile, `mac-authentication` + `l2-auth-failthrough` + `set-role-mac-auth <role>` gives "MAC first, then 802.1X" | `ap.ssid.multi_auth` render | HW-AP-14 |
| A-CLOCK-01 | Aruba management web servers (CX 443, Instant 4343) return an RFC 7231 `Date` header reflecting the device clock on an unauthenticated `GET /` | `infrastructure/device_probes.py` | HW-AP-17, HW-CX-21 |
| A-SNMP-01 | IF-MIB `ifHCInOctets/ifHCOutOctets`, `ifHighSpeed`, ENTITY-MIB `entPhysicalSerialNum/ModelName` and LLDP-MIB `lldpRemSysName` are present on supported switches | `infrastructure/adapters/snmp_switch.py` | HW-NA-01/02 |
| A-SNMP-02 | Cisco CPU 5-minute load is `cpmCPUTotal5minRev` (1.3.6.1.4.1.9.9.109.1.1.1.1.8); labelled unverified until confirmed | same | HW-NA-01 |
| A-UNIFI-01 | UniFi Network controller exposes `/api/s/{site}/stat/device` and `/stat/sta` after a local-account login (classic API path; UniFi OS consoles prefix `/proxy/network`) | same, `unifi_controller_collector` | HW-NA-03 |
| A-PJ-01 | A print-server agent can supply job id, queue/printer reference, user, pages, copies, colour, duplex, completion time and (optionally) extracted text, posting JSON to `/api/print-jobs/ingest` | `api/print_jobs.py` | HW-PJ-01 (agent not yet written) |

## Inference caveats (not protocol assumptions, but documented limits)

- **Roams** are inferred from successive client polls. A roam that starts and ends between two polls is invisible. Ping-pong uses a 10-minute window.
- **Interference** is a correlation of measured noise floor with retry rate per 5-minute bucket. It needs at least 6 paired buckets, and correlation is not proof of a non-Wi-Fi source.
- **Clock offset** resolution is 1 s (HTTP Date). The tolerance defaults to 120 s.
