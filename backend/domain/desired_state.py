"""Configuration intent -> typed change operations (pure). C04/C05/C08, audit E2E-06/E2E-20.

Templates are merged global -> group -> site -> device (a template may also be restricted to a model with
`model_contains`). A switch template can define reusable PORT ROLES and assign them to port ranges; ranges are
expanded against the ports the specific model actually has (24-port and 48-port CX share one template).

`reconcile()` compares the merged intent with the facts extracted from the device's captured configuration and
returns ONLY the typed operations needed. It never returns an operation that domain validation would refuse
(80 MHz, invalid VLANs, secrets in plans).
"""
from __future__ import annotations

import re

SCOPE_ORDER = {"global": 0, "group": 1, "site": 2, "device": 3}
ROLE_KEYS = {"access_vlan", "trunk_vlans", "native_vlan", "admin_state", "poe", "bpdu_guard", "storm_control_kbps",
             "qos_trust", "description", "lldp"}
SSID_KEYS = {"profile", "essid", "vlan", "rf_band", "channel_width_mhz", "enabled", "security", "secret_ref",
             "radius_name", "radius_ip"}
TOP_KEYS = {"vlans", "ssids", "interfaces", "port_roles", "port_assignments", "system"}
PORT_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{1,3})$")
_IP = re.compile(r"^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$")


class TemplateError(ValueError):
    pass


def _vlan(v) -> bool:
    return type(v) is int and 1 <= v <= 4094


def validate_doc(doc: dict, family: str) -> dict:
    if not isinstance(doc, dict) or set(doc) - TOP_KEYS:
        raise TemplateError(f"Template supports only {sorted(TOP_KEYS)}.")
    if family == "switch" and doc.get("ssids"):
        raise TemplateError("SSID configuration belongs to APs, not switches.")
    if family == "access_point" and (doc.get("interfaces") or doc.get("port_roles") or doc.get("port_assignments")):
        raise TemplateError("Interface/port-role configuration belongs to switches, not APs.")
    vlans = [v if isinstance(v, int) else (v or {}).get("id") for v in doc.get("vlans", [])]
    if any(not _vlan(v) for v in vlans) or len(vlans) != len(set(vlans)) or len(vlans) > 256:
        raise TemplateError("VLAN IDs must be unique integers 1..4094 (max 256).")
    seen = set()
    for s in doc.get("ssids", []):
        if not isinstance(s, dict) or set(s) - SSID_KEYS:
            raise TemplateError(f"SSID fields: {sorted(SSID_KEYS)}; secrets are referenced via secret_ref only.")
        key = str(s.get("profile", "")).strip().casefold()
        if not key or key in seen:
            raise TemplateError("SSIDs require a unique, non-empty profile.")
        seen.add(key)
        if s.get("channel_width_mhz") is not None and s["channel_width_mhz"] not in (20, 40):
            raise TemplateError("Only 20 MHz and 40 MHz wireless channel widths are permitted.")
        if s.get("vlan") is not None and not _vlan(s["vlan"]):
            raise TemplateError("SSID VLAN must be 1..4094.")
        sec = s.get("security")
        if sec is not None and sec not in ("opensystem", "wpa2-psk-aes", "wpa3-sae-aes", "wpa2-enterprise",
                                           "wpa3-enterprise"):
            raise TemplateError("Unsupported SSID security.")
        if sec in ("wpa2-enterprise", "wpa3-enterprise") and not (s.get("radius_ip") and s.get("radius_name")
                                                                 and s.get("secret_ref")):
            raise TemplateError("Enterprise SSIDs need radius_name, radius_ip and secret_ref.")
        if sec in ("wpa2-psk-aes", "wpa3-sae-aes") and not s.get("secret_ref"):
            raise TemplateError("PSK SSIDs need secret_ref.")
    seen = set()
    for p in doc.get("interfaces", []):
        if not isinstance(p, dict) or set(p) - ({"name"} | ROLE_KEYS):
            raise TemplateError("Unsupported interface field.")
        if not PORT_RE.match(str(p.get("name", ""))) or p["name"] in seen:
            raise TemplateError("Interfaces need unique names like 1/1/12.")
        seen.add(p["name"])
        _check_role(p)
    roles = doc.get("port_roles", {})
    if not isinstance(roles, dict) or len(roles) > 32:
        raise TemplateError("port_roles must be an object of up to 32 roles.")
    for name, r in roles.items():
        if not re.match(r"^[a-z][a-z0-9_-]{0,31}$", name) or not isinstance(r, dict) or set(r) - ROLE_KEYS:
            raise TemplateError(f"Invalid port role {name!r}.")
        _check_role(r)
    for a in doc.get("port_assignments", []):
        if not isinstance(a, dict) or set(a) != {"ports", "role"}:
            raise TemplateError("port_assignments entries are {ports, role}.")
        expand(a["ports"], None)
    sysd = doc.get("system", {})
    if not isinstance(sysd, dict) or set(sysd) - {"ntp", "dns", "syslog", "dns_domain"}:
        raise TemplateError("system supports ntp, dns, dns_domain, syslog.")
    for k in ("ntp", "dns", "syslog"):
        if k in sysd and not (isinstance(sysd[k], list) and len(sysd[k]) <= 4 and all(_IP.match(str(x)) for x in sysd[k])):
            raise TemplateError(f"system.{k} must be up to 4 IPv4 addresses.")
    return doc


def _check_role(r: dict) -> None:
    if r.get("access_vlan") is not None and not _vlan(r["access_vlan"]):
        raise TemplateError("access_vlan must be 1..4094.")
    if r.get("trunk_vlans") is not None and not (isinstance(r["trunk_vlans"], list) and r["trunk_vlans"]
                                                and all(_vlan(x) for x in r["trunk_vlans"])):
        raise TemplateError("trunk_vlans must be a non-empty list of 1..4094.")
    if r.get("access_vlan") is not None and r.get("trunk_vlans"):
        raise TemplateError("A port is either access or trunk.")
    for k, ok in (("admin_state", ("up", "down")), ("poe", ("enabled", "disabled")), ("lldp", ("enabled", "disabled")),
                  ("bpdu_guard", ("enabled", "disabled")), ("qos_trust", ("none", "cos", "dscp"))):
        if r.get(k) is not None and r[k] not in ok:
            raise TemplateError(f"{k} must be one of {ok}.")
    if r.get("storm_control_kbps") is not None and not (type(r["storm_control_kbps"]) is int
                                                        and 64 <= r["storm_control_kbps"] <= 10_000_000):
        raise TemplateError("storm_control_kbps must be 64..10000000.")
    if r.get("description") is not None and not (isinstance(r["description"], str) and len(r["description"]) <= 64):
        raise TemplateError("description is at most 64 characters.")


def expand(spec: str, available: list[str] | None) -> list[str]:
    """'1/1/1-1/1/20,1/1/48' -> port names; filtered to the ports the model really has (when given)."""
    out: list[str] = []
    for part in str(spec).replace(" ", "").split(","):
        a, _, b = part.partition("-")
        ma, mb = PORT_RE.match(a), PORT_RE.match(b or a)
        if not ma or not mb or ma.group(1, 2) != mb.group(1, 2) or int(mb.group(3)) < int(ma.group(3)):
            raise TemplateError(f"Invalid port range {part!r} (use 1/1/1-1/1/24).")
        for n in range(int(ma.group(3)), int(mb.group(3)) + 1):
            out.append(f"{ma.group(1)}/{ma.group(2)}/{n}")
    return [p for p in out if available is None or p in set(available)]


def merge(chosen: list) -> dict:
    """chosen: templates already filtered for the device, objects with scope/priority/name/config_json."""
    chosen = sorted(chosen, key=lambda x: (SCOPE_ORDER.get(x.scope, 99), x.priority, x.name.casefold(), x.id))
    eff = {"vlans": set(), "ssids": {}, "interfaces": {}, "port_roles": {}, "port_assignments": [], "system": {}}
    prov: dict[str, str] = {}
    for t in chosen:
        doc = t.config_json or {}
        for v in doc.get("vlans", []):
            vid = v if isinstance(v, int) else v.get("id")
            eff["vlans"].add(vid)
            prov.setdefault(f"vlan:{vid}", t.name)
        for s in doc.get("ssids", []):
            eff["ssids"][s["profile"].casefold()] = {**eff["ssids"].get(s["profile"].casefold(), {}), **s}
            prov[f"ssid:{s['profile']}"] = t.name
        for p in doc.get("interfaces", []):
            eff["interfaces"][p["name"]] = {**eff["interfaces"].get(p["name"], {}), **p}
            prov[f"interface:{p['name']}"] = t.name
        for name, r in (doc.get("port_roles") or {}).items():
            eff["port_roles"][name] = dict(r)
            prov[f"role:{name}"] = t.name
        eff["port_assignments"] += [dict(a, _from=t.name) for a in doc.get("port_assignments", [])]
        for k, v in (doc.get("system") or {}).items():
            eff["system"][k] = v
            prov[f"system:{k}"] = t.name
    return {"vlans": sorted(eff["vlans"]), "ssids": list(eff["ssids"].values()),
            "interfaces": list(eff["interfaces"].values()), "port_roles": eff["port_roles"],
            "port_assignments": eff["port_assignments"], "system": eff["system"], "provenance": prov,
            "templates": [{"id": t.id, "name": t.name, "scope": t.scope, "priority": t.priority} for t in chosen]}


def port_intent(eff: dict, ports: list[str]) -> dict[str, dict]:
    """Per-port desired settings: role assignments (later templates win per port), then explicit interfaces."""
    out: dict[str, dict] = {}
    for a in eff.get("port_assignments", []):
        role = eff["port_roles"].get(a["role"])
        if role is None:
            raise TemplateError(f"Port role {a['role']!r} is assigned but not defined.")
        for p in expand(a["ports"], ports):
            out[p] = {**dict(role), "_role": a["role"]}
    for i in eff.get("interfaces", []):
        if not ports or i["name"] in ports:
            out[i["name"]] = {**out.get(i["name"], {}), **{k: v for k, v in i.items() if k != "name"}}
    return out


# ---- facts from captured configuration ------------------------------------------------------------------------
def _tail_id(ref) -> int | None:
    s = str(ref or "").rsplit("/", 1)[-1]
    return int(s) if s.isdigit() else None


def cx_facts(cfg: dict) -> dict:
    """Structured facts from an AOS-CX JSON configuration (REST resource or fullconfigs table names).
    MAPPING STATUS: emulator- and OpenAPI-derived; confirmed on hardware during certification."""
    vl = cfg.get("vlans") or cfg.get("VLAN") or {}
    ifs = cfg.get("interfaces") or cfg.get("Interface") or {}
    ports: dict[str, dict] = {}
    for name, it in ifs.items():
        if not isinstance(it, dict) or not PORT_RE.match(str(it.get("name") or name)):
            continue
        poe = (it.get("poe") or {}).get("config") or {}
        ports[str(it.get("name") or name)] = {
            "vlan_mode": it.get("vlan_mode"), "access_vlan": _tail_id(it.get("vlan_tag")),
            "trunk_vlans": sorted(x for x in (_tail_id(v) for v in it.get("vlan_trunks") or []) if x),
            "admin_state": it.get("admin") or it.get("admin_state") or "up",
            "description": it.get("description") or "",
            "bpdu_guard": "enabled" if (it.get("stp_config") or {}).get("bpdu_guard_enable") else "disabled",
            "storm_control_kbps": (it.get("rate_limits") or {}).get("broadcast"),
            "qos_trust": it.get("qos_trust") or "none",
            "poe": "disabled" if poe.get("admin_disable") else "enabled",
            "lldp": "disabled" if (it.get("other_config") or {}).get("lldp_enable_dir") == "off" else "enabled"}
    vrf = (cfg.get("vrfs") or {}).get("mgmt") or {}
    return {"vlans": sorted(int(k) for k in vl if str(k).isdigit()), "ports": ports,
            "ntp": sorted(str(v.get("address")) for v in (cfg.get("ntp_associations") or {}).values()),
            "syslog": sorted(str(v.get("remote_host")) for v in (cfg.get("syslog_remotes") or {}).values()),
            "dns": [vrf.get("dns_name_servers", {}).get(str(i)) for i in range(len(vrf.get("dns_name_servers") or {}))],
            "dns_domain": vrf.get("dns_domain_name")}


def instant_facts(text: str) -> dict:
    blocks: dict[str, dict] = {}
    cur = None
    for raw in text.replace("\r", "").split("\n"):
        if raw and not raw.startswith(" "):
            m = re.match(r"^wlan ssid-profile (\S+)$", raw.strip())
            cur = m.group(1) if m else None
            if cur:
                blocks[cur] = {}
        elif cur and raw.strip():
            k, _, v = raw.strip().partition(" ")
            blocks[cur][k] = v
    arm = {}
    for ln in re.findall(r"^arm\n((?: .*\n?)*)", text, re.M)[:1]:
        for x in ln.splitlines():
            k, _, v = x.strip().partition(" ")
            arm[k] = v
    return {"ssids": blocks, "wide_bands": arm.get("wide-bands")}


# ---- reconcile ----------------------------------------------------------------------------------------------
def reconcile(family: str, eff: dict, facts: dict, *, ports: list[str] | None = None) -> list[dict]:
    ops: list[dict] = []
    if family == "switch":
        for v in eff.get("vlans", []):
            if v not in facts["vlans"]:
                ops.append({"op": "switch.vlan.define", "value": v})
        have = facts["ports"]
        for p, want in sorted(port_intent(eff, ports or sorted(have)).items(),
                              key=lambda kv: [int(x) for x in kv[0].split("/")]):
            h = have.get(p)
            if h is None:
                continue                      # this model does not have the port (e.g. 1/1/48 on a 24-port)
            if want.get("access_vlan") is not None and (h["vlan_mode"] != "access" or h["access_vlan"] != want["access_vlan"]):
                ops.append({"op": "switch.port.access_vlan", "port": p, "value": want["access_vlan"]})
            if want.get("trunk_vlans") and h["trunk_vlans"] != sorted(want["trunk_vlans"]):
                ops.append({"op": "switch.port.trunk_vlans", "port": p, "value": sorted(want["trunk_vlans"]),
                            **({"native": want["native_vlan"]} if want.get("native_vlan") else {})})
            for key, op in (("admin_state", "switch.port.admin_state"), ("poe", "switch.port.poe"),
                            ("lldp", "switch.lldp"), ("bpdu_guard", "switch.port.stp_bpdu_guard"),
                            ("qos_trust", "switch.port.qos_trust"), ("description", "switch.port.description")):
                if want.get(key) is not None and h.get(key) != want[key]:
                    ops.append({"op": op, "port": p, "value": want[key]})
            if want.get("storm_control_kbps") is not None and h.get("storm_control_kbps") != want["storm_control_kbps"]:
                ops.append({"op": "switch.port.storm_control", "port": p, "broadcast_kbps": want["storm_control_kbps"]})
        sysd = eff.get("system") or {}
        for ip in sysd.get("ntp", []):
            if ip not in facts["ntp"]:
                ops.append({"op": "switch.system.ntp", "value": ip})
        for ip in sysd.get("syslog", []):
            if ip not in facts["syslog"]:
                ops.append({"op": "switch.system.syslog", "value": ip})
        if sysd.get("dns") and (facts["dns"] != list(sysd["dns"]) or
                                (sysd.get("dns_domain") and facts["dns_domain"] != sysd["dns_domain"])):
            ops.append({"op": "switch.system.dns", "value": list(sysd["dns"]),
                        **({"domain": sysd["dns_domain"]} if sysd.get("dns_domain") else {})})
        return ops
    have = facts["ssids"]
    widths = set()
    for s in eff.get("ssids", []):
        prof = s["profile"]
        h = have.get(prof)
        sec = s.get("security")
        if h is None and sec in ("wpa2-enterprise", "wpa3-enterprise"):
            ops.append({"op": "ap.ssid.enterprise", "ssid": prof, "essid": s.get("essid") or prof, "vlan": s["vlan"],
                        "security": sec, "radius_name": s["radius_name"], "radius_ip": s["radius_ip"],
                        "secret_ref": s["secret_ref"]})
        elif h is None and sec is not None:
            ops.append({"op": "ap.ssid.create", "ssid": prof, "essid": s.get("essid") or prof, "vlan": s["vlan"],
                        "security": sec, **({"secret_ref": s["secret_ref"]} if s.get("secret_ref") else {})})
        elif h is not None and s.get("vlan") is not None and h.get("vlan") != str(s["vlan"]):
            ops.append({"op": "ap.ssid.vlan", "ssid": prof, "value": s["vlan"]})
        if s.get("channel_width_mhz") in (20, 40):
            widths.add(s["channel_width_mhz"])
    if len(widths) == 1:
        w = widths.pop()
        if facts.get("wide_bands") != ("none" if w == 20 else "5ghz"):
            ops.append({"op": "ap.radio.width", "value": w})
    return ops
