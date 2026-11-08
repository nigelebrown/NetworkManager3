# Phase 2 — Read-only real-device acceptance (first delivery)

**Status:** first functional increment implemented in `NetworkManager3`; review the
latest GitHub Actions run before installing. No live Aruba hardware was available
to this CI environment. `hardware_certified` remains **false** for every device.

## Purpose

Convert an imported device into an auditable acceptance record before asserting
that Aruba API integration is production usable. `backend/domain/phase2_acceptance.py`
uses **existing** inventory and sanitized configuration snapshots; it does not
log on to a switch/AP or modify its configuration.

**Operator journey**

1. Open **Inventory → Add device** and choose AP-505, AP-535, CX 6100 or CX 6200.
2. Select an existing **encrypted credential profile** or create one.
3. Enter a single **authorized private management IP** or at most eight
   authorized addresses; test credentials, then run **Scan approved range**.
4. Review identity, model, serial, firmware, and whether already registered;
   select verified devices for registration.
5. **Read existing configurations**. A snapshot is always explicitly marked
   **PARTIAL** rather than a full restore backup.
6. From **Device details → View imported configuration / edit draft**, inspect
   the **Phase 2 — device acceptance evidence** panel. Each item is either
   `OBSERVED` (supported evidence actually recorded) or `NOT_VERIFIED`.
7. Validate the physical device against the outstanding items. Save proposed
   configuration as a **DRAFT** only. No device-write endpoint is enabled.
8. The wizard displays a fleet-level breakdown: manually registered, REST
   discovered, baseline captured and drafted devices. No physical device is
   automatically certified.

## Device-specific evidence matrix

| Evidence | Aruba AP-505/535 | Aruba CX 6100/6200 |
|---|---|---|
| Identity / model / serial | Read-only Instant REST `show aps` / firmware response | Read-only CX REST system response |
| Existing SSIDs | Parsed from redacted Instant `show running-config` | Not applicable |
| SSID → VLAN mapping | Recorded only when the parser returns a mapping | Not applicable |
| VLAN list | Derived from reported Instant SSID VLAN references, **not a complete VLAN database** | From CX REST `/system/vlans` |
| Ports / interface admin state | Not collected in current AP onboarding | From CX REST `/system/interfaces` |
| Per-port VLAN assignments | Not applicable | Not verified by current adapter |
| RF channels / power / widths | Not verified by current onboarding baseline | Not applicable |
| LACP, PoE, LLDP configuration | Not collected | Not verified by current CX adapter |
| Firmware compatibility | Version string recorded if returned; **not certified by string** | Version string recorded; **not certified by string** |
| Configuration restore | Not available | Not available |
| Hardware changes | **LOCKED** | **LOCKED** |

The intended radio channel widths are **20 MHz and 40 MHz only**.
No 80 MHz setting may be selected by the draft editor.

## API endpoints (administrator only)

- `GET /api/v1/onboarding/devices/{device_id}/acceptance`: single device
  report with model classification, credential binding (not its secret),
  baseline summary, per-field evidence, remaining physical test actions,
  pilot stage, and `hardware_certified=false`.
- `GET /api/v1/onboarding/acceptance`: latest 250 real Aruba devices,
  grouped by recorded pilot stage (a **bounded preview**, not a count of
  all devices when more than 250 exist).

These endpoints make **no outbound network requests** and return neither
plaintext credentials nor encrypted credential data.

## On-site hardware acceptance matrix

For each model **AP-505, AP-535, CX 6100 and CX 6200**, test at least one
approved unit on a pilot VLAN and record:
- Serial, model, firmware, management IP, physical site and test date;
- TLS certificate trust, REST service enabled and credential-auth outcome;
- Whether endpoint discovery correctly identifies the physical serial and model;
- Exact fields captured and fields missing from the read-only baseline;
- Whether the configuration editor reflects only supported data;
- Whether rotating the shared credential updates subsequent authenticated
  reads and does not reveal the password;
- Whether failed or out-of-scope IPs are blocked before traffic is sent;
- Whether a draft remains entirely application-local;
- Any firmware/model incompatibility, parser exception or latency problem.

**Pass for operator pilot:** read-only API works on authorized equipment and
stored data are accurate, missing data are labelled, and audit trails
demonstrate no physical configuration changes.

**Pass for automated hardware deployment:** NOT part of this phase. Requires
separate approved change controls, pre-change full backup, syntax validation,
real-device readback, verification and tested rollback.

## Testing and release

New tests:
- `tests/unit/test_phase2_acceptance.py`: supported model naming,
  partial field classification, and global write-lock constraints.
- `tests/contract/test_phase2_acceptance.py`: manual vs read-only REST
  evidence, credentials secrecy, API permissions and pilot-stage progress.
- `tests/e2e/test_browser_live.py`: browser-visible Phase 2 acceptance panel.

GitHub CI tests the application using simulated or mocked equipment; it cannot
perform the physical hardware certification above.

For CentOS 9, follow `docs/DEPLOYMENT_CENTOS9.md` and the application's
normal encrypted backup/update/rollback procedures. Avoid updating an active
production stack until the full GitHub Actions suite is green.
