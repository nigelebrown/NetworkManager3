"""Pure, deterministic application-side template inheritance. No device I/O."""
from __future__ import annotations

def matches(template, device) -> bool:
    """Apply global -> site -> device. Restrict family and optional model string."""
    if not template.enabled or template.family != device.family:
        return False
    if template.model_contains and template.model_contains.casefold() not in (device.model or "").casefold():
        return False
    if template.scope == "global":
        return not template.site_id and not template.device_id
    if template.scope == "site":
        return template.site_id == device.site_id and not template.device_id
    if template.scope == "device":
        return template.device_id == device.id
    return False

def preview(device, templates: list) -> dict:
    """VLANs accumulate; named SSID/port entries override from global to device.

    No templates can remove globally required VLANs. All settings are only drafts.
    Stable sorting makes results reproducible and gives inspectable provenance.
    """
    order = {"global": 0, "site": 1, "device": 2}
    chosen = sorted((x for x in templates if matches(x, device)),
                    key=lambda x: (order.get(x.scope, 99), x.priority, x.name.casefold(), x.id))
    vlan_ids: set[int] = set()
    ssids: dict[str, dict] = {}
    interfaces: dict[str, dict] = {}
    provenance: dict[str, str] = {}
    for template in chosen:
        doc = template.config_json or {}
        for vlan in doc.get("vlans", []):
            vlan_ids.add(vlan)
            provenance[f"vlan:{vlan}"] = template.name
        for row in doc.get("ssids", []):
            key = row["profile"].casefold()
            ssids[key] = dict(row)
            provenance[f"ssid:{key}"] = template.name
        for row in doc.get("interfaces", []):
            key = row["name"].casefold()
            interfaces[key] = dict(row)
            provenance[f"interface:{key}"] = template.name
    result = {"vlans": sorted(vlan_ids), "ssids": list(ssids.values()),
              "interfaces": list(interfaces.values())}
    return {"device_id": device.id, "family": device.family,
            "source_type": device.source_type, "device_writes": "LOCKED",
            "templates": [{"id": t.id, "name": t.name, "scope": t.scope,
                           "priority": t.priority, "revision": t.revision} for t in chosen],
            "effective": result, "provenance": provenance,
            "applied_to_hardware": False,
            "note": "Application-side preview only. No running configuration was changed."}
