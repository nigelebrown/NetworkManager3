# Production remediation follow-up — November 24, 2026

This is a software candidate, not production acceptance. Keep device-write gates, independent approval, emergency stop, and 20/40 MHz policy in force.

## Device-originated log collection

- Implemented: opt-in durable per-device schedules, bounded authenticated reads, deduplicated persistence, scoped historical and live-read API access, Device 360 controls, and per-category partial/failure status.
- Enable the worker environment setting `INM_SCHEDULED_DEVICE_LOGS=true` in the managed deployment environment, recreate the worker using the approved deployment procedure, and enable a schedule for each authorized device in Device 360 > Operations. Default is disabled. No schedule starts against hardware in demo mode. Start with one lab switch and one controller; do not enable every AP member of a controller for its shared log view.
- Collection uses the existing target and credential gates, validates CX serial identity, and never invokes configuration writers. AP logs describe the controller view, not a verified per-member event. Interval bounds are 5–1440 minutes; one due device is attempted per worker cycle under an exclusive lease. Logs are deduplicated by scope and normalized content; repeated identical entries lacking reliable source identifiers can be coalesced. A saturated vendor response is marked possibly truncated. This is not a guaranteed complete audit stream.
- Retained timestamps identify collection, not authoritative device-event time. Device-reported actors are not verified attribution. Poll records are kept separate from the syslog/AAA change-attribution timeline. Schedule pause preserves the archive. Establish the retention and disposal policy before fleet rollout; automatic deletion is not enabled.
- Hardware log-format support, throughput, storage growth, retention, privacy and independent security evidence remain to be validated.

## Firmware and identity

- Firmware plans now require a complete SHA-256 and size match at plan creation, staging and download. Stale plans containing only truncated digests must be recreated. This verifies byte integrity, not Aruba signature or trusted-source authenticity.
- Physical device execution and certification require live serial, model family and exact numeric firmware-build agreement with inventory. Instant identity reads target the recorded member AP through the existing controller. Incomplete or mismatched identity blocks execution.
- Legacy hardware certification evidence without the newly verified exact build is no longer sufficient for configuration writes. Rerun and independently approve laboratory certification on the correct physical model/build. Emulator evidence remains separate and cannot certify hardware. No approval gate was removed.
- Firmware staging is still stage-only; vendor signature verification, switch upgrade lifecycle, staged rollout, automatic rollback and physical restore evidence remain separate acceptance work.

## Validation

New tests cover partial log collection, deduplication, scoped access, device identity mismatches, targeted AP identity, secret redaction and same-size firmware substitution. The legacy RF fixture now exercises the actual optional interface-counter and monitor station-list commands. The full GitHub Actions workflow must pass for the exact candidate commit; local test execution and live hardware acceptance have not been independently performed in this review.

Remaining gaps are tracked in the device API gap register and the independent production go/no-go gate. Do not treat this follow-up as closure of vendor-specific enterprise WLAN, DFS/roaming, CX authentication, VSF, advanced policy, live optics, firmware orchestration, HA/DR or physical certification requirements.
