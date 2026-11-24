# AP ARM optimization upgrade — validation report
Date: 24 November 2026 (America/Bogota)

## Deployment and source
- Portal: https://172.17.71.20:8787
- Deployed application revision: b4025b0264b8bd4e96c0ee14f4db9973229ea0fa
- Source branch: codex/production-ap-optimization
- Review: https://github.com/nigelebrown/NetworkManager3/pull/35
- API, worker and web containers healthy. TLS-verified /healthz returned ok.
- Effective INM_AUTOMATIC_OPTIMIZATION=true in API and worker. Device enrollment, authorization, certification and evidence checks still govern execution.
- This upgrade is pushed to its review branch and deployed for testing. PR 35 and its prerequisite branches have not been merged to main.

## Initial Commissioners Office reference
The real Commish-Office AP (10.190.97.205, AP-505) was read successfully over pinned SSH. The saved label is “Commissioners Office — initial 5 GHz reference”.
- Enabled 5 GHz channels: 149, 153, 157, 161, 165.
- Shared ARM transmit-power bounds: 6–21 dBm.
- Scanning, Client Aware and ClientMatch enabled; prefer-5ghz.
- 80 MHz capability enabled; 160 MHz disabled. Capability is not proof of actual operating width or a performance fault.
- One verified observation: 5 GHz channel 165 at 18 dBm, utilization 9%, quality 99%; 2.4 GHz channel 11 at 12 dBm, utilization 6%, quality 99%.

This is an initial comparison reference, not a mandatory fleet template, a proven minimum for every site or a guarantee of performance. Actual channel and power choices remain distinct from configured bounds. Regulatory permissions, RF conditions, client capabilities and site requirements must determine any eventual change.

## Implemented behavior
- Independent bounded worker collection reads native ARM RF summary, channel history, neighbors, configuration and AP environment. Requests queue collection instead of blocking the web API.
- Direct AP identity is checked before first-use key enrollment under the existing onboarding policy. Known changed keys are not accepted automatically.
- Diagnosis distinguishes fresh, stale and missing measurements. Cluster size, neighbor BSSID counts and capability flags do not establish AP distance or performance faults.
- Rules favor native ARM. Single snapshots and cumulative counters do not justify forced channel or power changes.
- Proposed hardware adjustments require measured evidence, freshness, allowed channels, backup and normal change-control checks. Preparing a plan is idempotent.
- The optimizer uses successful executions for budgets/cooldowns, checks sustained client impact, pauses on unverified execution and uses exact recovery state for rollback.
- Missing ARM evidence is counted as insufficient evidence; issues needing manual investigation are counted separately from a healthy no-adjustment decision.
- The interface shows live ARM evidence, initial-reference differences and a real reviewed change-plan action.
- Local AI receives compact measured facts with bounded context, generation and time limits. It is advisory; deterministic findings and change-control checks govern execution.
- Fixed a reproduced audit-lock deadlock involving concurrent first-use collections. PostgreSQL uses database locking; SQLite retains its process lock.

## Validation evidence
These are separate, overlapping runs; their counts must not be added together.
- Earlier combined ARM, production read/write, wireless operations, diagnostics and audit regression run: 100 passed.
- Existing AI and AI-service-isolation regression run: 31 passed.
- Compact AI plus workflow and existing AI regression run: 48 passed.
- Final deployed-revision ARM intelligence, audit concurrency, ARM workflow and AI-limit suite: 33 passed, 1 warning, 21.04 seconds.
- Frontend TypeScript/Vite build passed. Existing large-bundle advisory remains.
- Live pinned SSH ARM reads succeeded on Commish-Office and CP-Legal-OfficeAP. These are collection checks, not write certification of every AP.
- Concurrent PostgreSQL audit writers completed. A consistent verification checked 20,070 audit entries with no broken chain entries.
- Live authenticated AI diagnosis returned HTTP 200, available=true, model qwen2.5:3b, in 10.5 seconds. Its advisory answer agreed with leaving healthy native ARM in control. Model wording such as “optimal” is not proof of an optimal site design.
- Final headless Edge browser check passed: optimization page, real ARM diagnosis dialog, Commissioners Office reference and change-management page. Zero browser page errors.
- Browser screenshot: outputs/arm-ui-test/arm-diagnosis.png.

## Use and remaining limits
Open Optimization, select an AP and choose “Review live ARM measurements and proposed adjustments”. Refresh measurements if stale. When evidence supports a writable proposal, prepare its reviewed change plan and follow the existing approval/execution workflow.

No blind fleet RF changes, bulk import or network sweep was performed for this upgrade. Healthy ARM required no forced adjustment. The validation does not certify every hardware/firmware combination, all fleet measurements or every section of the application. Reliable distance/coverage conclusions still need appropriate RF evidence and, where relevant, physical layout or survey information.

## Primary references
- HPE Aruba Instant ARM show commands: https://arubanetworking.hpe.com/techdocs/CLI-Bank/Content/instant/sh-ap-arm.htm
- HPE Aruba Instant ARM configuration: https://arubanetworking.hpe.com/techdocs/Instant_8.x_WebHelp/Content/instant-ug/arm/conf-arm-feature.htm
