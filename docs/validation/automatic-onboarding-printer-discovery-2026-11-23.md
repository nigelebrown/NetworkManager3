# Automatic onboarding and printer discovery validation

Date: 2026-11-23 (America/Bogota)
Portal: https://172.17.71.20:8787
GitHub: https://github.com/nigelebrown/NetworkManager3/pull/33

## Deployed result

The test server runs the automatic-onboarding branch application from commit 81b6c6304745fb3cf9238cb6a8e5876863a08763. Subsequent branch commits add one enrollment regression test and correct its fixture; application source is unchanged. The server source checkout is at d0a85c56a77cac54a271a19684fba2e70fa23ac7. Main has not been merged with these changes.

The API, worker and web containers are healthy. The external TLS-verified /healthz returns ok.

## Printer discovery

The previous form rejected ranges broader than /22. The updated API accepts management CIDRs from /9 to /32 and bounded IP lists. It stores a network and cursor rather than expanding millions of hosts.

- One form selects multiple saved SNMPv1, v2c and v3 profiles.
- Only selected profiles are attempted; the successful profile is recorded and assigned when a discovered printer is approved for monitoring.
- Version policy is enforced at start and resume. SNMPv1 remains disabled by the existing policy; the form identifies blocked profiles and links to profile/settings management.
- Default concurrency is 16 with 32-address batches. Configurable concurrency is bounded at 64; a batch is capped at 128.
- Each batch saves partial results and the contiguous completed cursor. Worker replacement can repeat read-only work from an uncommitted batch, then continue without duplicating discovered printers.
- Pause/resume persists in the database. The page polls saved status instead of abandoning the job after two minutes.
- Previous discoveries can be reopened and partial discoveries approved. Per-address failures are retained up to 2,000 entries.

Live preview of 10.128.0.0/9 returned HTTP 200, 8,388,606 addresses, 262,144 default batches, concurrency 16, starts_scan=false. No full /9 scan was launched. A range this large can take days, depending on reachability, selected profiles and worker scheduling; this is not a throughput benchmark.

A single-address discovery of 10.190.68.8 succeeded: HP Color LaserJet MFP M281fdw, serial VNBNL25BNK, saved SNMPv2c profile. Progress 1/1, no errors. It was not imported as part of this test.

Previous saved scans were recovered through the API: 10.190.0.0/22 succeeded with 3 printers, and 10.190.104.0/24 succeeded with 1 printer.

## Automatic AP/switch onboarding

API-local background threads were replaced with a leased worker queue. It commits after each device and recovers the cursor after restarts. The interrupted legacy run was preserved under ap-autodiscovery:interrupted-2026-11-23; its saved clusters remain reusable.

The saved policy is enabled for AP-Standard (AP), standard (switch), and NetworkSwitches (switch). It operates on registered inventory and fresh authenticated discovery results, not an unsolicited network-range sweep. The worker started a bounded queue of 200 existing devices. This report does not claim that queue has finished or that all devices were authenticated.

The process tries selected saved profiles, verifies identity, binds a working profile, registers/links AP cluster members and enables read-only collection. Switches receive an active collection plan after verified REST identity. Fresh authenticated discovery preserves the existing physical site, including serial comparisons with different letter case.

First-use SSH keys are pinned after successful saved-credential authentication and a nonempty cluster read. Known keys that change remain blocked for review. Failed authentication does not pin a key.

Automatic REST bootstrap remains off in the live policy. Its optional implementation uses existing write/emergency-stop checks and fixed device bootstrap commands. It does not enable arbitrary configuration or automatic RF optimization.

Live exceptions:
- GreenAcres-Grd-AP, 10.104.2.10: SSH_UNREACHABLE.
- HR-Office, 10.190.33.155: IDENTITY_UNVERIFIED; no usable cluster-member identity was returned.
- A stale merged local inventory record was excluded from a requested one-device run.

The worker completed these bounded checks without losing the API. Physical device reachability and identity-read issues remain to be resolved separately; no successful new physical AP enrollment is claimed by these checks.

## Verification

101 distinct automated checks passed across:
- Automatic onboarding and wireless operations.
- Existing printer SRS workflows.
- Remediation discovery workflows.
- Large discovery planning, lazy range boundaries, pause/resume, replacement workers, selected-profile fallback and matched monitoring assignment.
- Fresh authenticated enrollment and existing-site preservation.

The principal combined run passed 99 checks and found a missing provenance attribute in the new test reader. After fixing that test double, all 9 large-discovery tests passed. The final 12 automatic-onboarding tests passed, including the added site-preservation test. No application failure from those runs remains unresolved.

TypeScript compilation and Vite production build passed. Vite emitted its existing large JavaScript chunk advisory. No new interactive browser verification was performed; UI behavior is supported by the build and API/worker contracts.

## Use

Refresh the printer page, select the saved profile checkboxes, enter the range, and preview the plan. Start discovery when ready; progress and results remain saved when leaving the page. Pause/resume is available for active/interrupted scans. Approve selected identified printers to add monitoring with the working profile.

Use AP Fleet to inspect the automatic-onboarding policy, selected profile checkboxes, progress and per-device exceptions. Credential success and actual collection health are separate results.

The portal password was not reset or changed.
