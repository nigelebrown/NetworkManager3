# Phase 3 — Configuration inheritance (isolated branch)

**Branch:** feature/phase3-config-inheritance. **No merge to main is authorized.**

User process: create **Global** baseline template → add physical **Site**
template → add optional **Device** overrides → preview effective configuration
with field provenance → capture a read-only actual device baseline → save
inherited proposal as an application-side **DRAFT**. Hardware writes are LOCKED.

Scope matching is explicit: global templates apply to selected family and
optional model substring (e.g. 6200F); site templates apply to a real
physical location; device templates target one registered real Aruba device.
Do not confuse physical site IDs with legacy Aruba Instant VC cluster IDs.

VLANs union across levels (mandatory global VLANs are not silently removed).
SSID profiles and switch interfaces override entries with the same
profile/interface key, most-specific scope wins. Priority controls conflicts
within the same scope. The preview includes each contributing template and
per-setting provenance.

Safe authoring supports VLAN 1–4094, SSID→VLAN mapping with **20 or 40 MHz**
only, and switch access/trunk VLAN drafts. No credentials or key material
can be stored in a template. No 80 MHz draft value is accepted. This release
does not configure devices or reconcile an existing running-config.

## Endpoints (admin-only)
- GET /api/v1/config-templates
- POST /api/v1/config-templates
- PATCH /api/v1/config-templates/{id} (revision check)
- GET /api/v1/config-templates/devices/{device_id}/preview
- POST /api/v1/config-templates/devices/{device_id}/draft

The draft endpoint **requires an existing read-only captured baseline**
and never communicates with a device. The UI exposes
**Configuration → Config inheritance**.

## Data and release
An additive Alembic schema 0007 creates inm_config_templates. A real
deployment requires encrypted backup and migration validation before
rollout. Phase 3 is independently based on Phase 1 main and remains
unmerged. Future integration with Phase 2's acceptance evidence UI should
be reviewed during the controlled merge.

## Acceptance
Unit checks cover hierarchy precedence and provenance; contract checks
cover authentication, invalid 80 MHz widths, scope constraints, updates,
stale revisions, and baseline-gated drafts. Chromium exercises one manual
switch registration, global template creation and effective preview.
Physical AP/CX certification and automated device configuration
deployment remain outside the green-CI claim.
