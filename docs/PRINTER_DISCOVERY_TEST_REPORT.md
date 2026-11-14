# Printer discovery and dashboard test report

## Result

The printer range discovery branch passed the printer contract and analytics tests, a production frontend build, the full GitHub Actions workflow, and checks on the CentOS test deployment. Real printer discovery remains a hardware acceptance item. The browser test simulates the discovery response and uses the real API to register the printer.

| Evidence | Result |
|---|---|
| Tested and deployed revision | `194818c25f14058c3a4f26d5591ee3fc072aafe8` |
| Printer contract and analytics suite | 22 passed in a disposable container using isolated databases |
| GitHub backend regression, unit and contract suites | Passed |
| GitHub frontend TypeScript and Vite build | Passed |
| GitHub full-stack TLS, migration, WebSocket and Chromium workflow | Passed |
| CentOS deployment verification | Passed, including the device-write lock, worker egress, syslog and event round trip |
| Deployed printer UI and API | HTTPS served the new discovery bundle, the API advertised the new route, and both dashboard and discovery returned HTTP 401 without authentication |
| Physical printer acceptance | Pending |

[Pull request #14](https://github.com/nigelebrown/NetworkManager3/pull/14) · [Successful CI run](https://github.com/nigelebrown/NetworkManager3/actions/runs/43565938291)

## Code walkthrough

| File | Responsibility |
|---|---|
| `frontend/src/pages/Printers.tsx` | Shows printer totals and consumable alerts. Discovery accepts an address or CIDR. A result can open registration with its management IP and hostname filled in. Registration captures manufacturer, model, serial, and physical assignment. Inventory displays the management IP and all location fields, including branch and building. |
| `frontend/src/services/printers.ts` | Defines the discovery result type and sends the authenticated request using the shared API client. |
| `backend/api/printers.py` | Authorizes administrators, validates the entire usable scope before sending probes, limits discovery to one range request at a time per API process, caps concurrent probes, and audits the range and counts. Discovery results are reviewed before inventory registration. |
| `backend/printers/snmp.py` | Reuses the single-target SNMPv3 authPriv reader with SHA-256 and AES-128. Only read requests go to the device. Range results require a Printer-MIB marker response. Meter readings retain their reported units when recognized. Credentials are used for the request and are not stored. |
| `backend/printers/analytics.py` | Calculates meter deltas, consumable usage, low-supply status, and comparisons from recorded observations. |

## Section-by-section checks

| Section | Coverage |
|---|---|
| Access and scope | Tested authentication, rejection of a range broader than /24, and rejection of an unapproved range before any probes execute. |
| Discovery and identification | Contract test sends a bounded /30 through the API with a mocked SNMP transport, identifies one printer, and filters the other response. |
| Review and registration | Chromium submits the discovery form, opens a simulated result, verifies prefilled identity, enters branch and serial, saves through the real API, and checks the rendered inventory. Contract tests cover duplicate serial handling and incompatible model rejection. |
| Meter and consumables | Contract tests cover large counters, reset handling, separating sheets from impressions, known versus unknown supply levels, and low and critical alerts. |
| Maintenance, yield and cost | Contract and analytics tests cover maintenance history, closing cycles, calculation from meter readings, and handling missing or insufficient evidence. |
| Deployment | Verified the served HTTPS bundle, API route, authentication, dependency health, and deployment smoke checks on CentOS. |

## Hardware acceptance checklist

1. Open **Printers → Discover printers / test SNMPv3**. Enter an approved printer IP or subnet and the read-only SNMPv3 credentials in the app. The test server has `10.190.1.0/24` and `10.192.57.0/24` approved. Choose the subnet that actually contains printers.
2. Confirm the returned address and device identity against the printer label and confirm the reported meter and units against the printer panel. For a range scan, include one known printer and one non-printer in a small approved scope before testing a full subnet.
3. Choose **Review and register**, supply the verified manufacturer, model, serial and branch, then confirm the inventory row and detail page.
4. Compare recorded consumable and meter readings with the printer. Confirm the displayed provenance and check how each vendor reports unsupported or unavailable measurements.
5. Measure full-range duration and discovery accuracy using production printer models. Printers without the standard marker response or with different SNMP security settings need separate compatibility testing.

Discovery is a one-time operation. Scheduled printer polling and automatic import of SNMP meter or supply readings are outside this release. Record and review physical-device evidence before production acceptance.
