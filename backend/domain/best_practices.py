"""Configuration best-practice and intended-vs-installed compliance (pure).

Evaluates the device's ACTUAL configuration text (a vault artifact, i.e. what is really on the box)
against Aruba Instant / AOS-CX operational best practice, and against the INTENDED configuration
(effective inherited template). No config -> every rule is UNKNOWN; nothing is assumed compliant.

Result per rule: PASS | FAIL | WARN | UNKNOWN | N/A, with the evidence line that decided it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

SEVERITY_WEIGHT = {"critical": 5, "high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class Rule:
    id: str
    family: str          # access_point | switch
    category: str        # Security | RF | Operations | Layer 2 | Visibility | Roaming
    severity: str
    title: str
    why: str
    fix: str
    check: Callable[[str], tuple[str, str | None]]


def _find(pattern: str, text: str):
    return re.search(pattern, text, re.I | re.M)


def present(pattern: str, *, missing: str = "FAIL"):
    def f(text):
        m = _find(pattern, text)
        return ("PASS", m.group(0).strip()) if m else (missing, None)
    return f


def absent(pattern: str):
    def f(text):
        m = _find(pattern, text)
        return ("FAIL", m.group(0).strip()) if m else ("PASS", None)
    return f


def _width(text):
    m = _find(r"^\s*(?:channel-width|.*-channel-width)\s+(80|160)\S*|80mhz|160mhz", text)
    return ("FAIL", m.group(0).strip()) if m else ("PASS", None)


def _open_ssid(text):
    m = _find(r"^\s*opmode\s+opensystem\b", text)
    if not m:
        return ("PASS", None)
    return ("WARN", m.group(0).strip()) if _find(r"\bcaptive-portal\b|\benhanced-open\b|\bowe\b", text) else ("FAIL", m.group(0).strip())


def _wpa3(text):
    m = _find(r"^\s*opmode\s+(wpa3\S*|enhanced-open)", text)
    if m:
        return ("PASS", m.group(0).strip())
    return ("WARN", None) if _find(r"^\s*opmode\s+wpa2", text) else ("UNKNOWN", None)


def _basic_rates(text):
    m = _find(r"^\s*[ag]-basic-rates\s+(1|2|5|6)\b", text)
    return ("FAIL", m.group(0).strip()) if m else (
        ("PASS", _find(r"^\s*[ag]-basic-rates.*", text).group(0).strip()) if _find(r"^\s*[ag]-basic-rates", text) else ("WARN", None))


def _min_tx(text):
    m = _find(r"^\s*(?:min-tx-power|a-min-tx-power|g-min-tx-power)\s+(\d+)", text)
    if not m:
        return ("WARN", None)
    return ("PASS", m.group(0).strip()) if 6 <= int(m.group(1)) <= 18 else ("FAIL", m.group(0).strip())


def _hostname(text):
    m = _find(r"^\s*hostname\s+(\S+)", text)
    if not m:
        return ("FAIL", None)
    return ("FAIL", m.group(0)) if m.group(1).lower() in ("switch", "6100", "6200", "6300", "aruba", "instant") else ("PASS", m.group(0).strip())


def _vlan1_access(text):
    m = _find(r"^\s*vlan access 1\s*$", text)
    return ("FAIL", m.group(0).strip()) if m else ("PASS", None)


def _bpdu(text):
    if not _find(r"^interface \d+/\d+/\d+", text):
        return ("N/A", None)
    m = _find(r"spanning-tree bpdu-guard|loop-protect", text)
    return ("PASS", m.group(0)) if m else ("FAIL", None)


RULES: tuple[Rule, ...] = (
    # ---------------- Aruba AOS-CX switches
    Rule("CX-SEC-01", "switch", "Security", "high", "SSH management enabled",
         "Encrypted CLI access is required; it also enables attributed sessions.",
         "ssh server vrf mgmt", present(r"^\s*ssh server vrf \S+")),
    Rule("CX-SEC-02", "switch", "Security", "critical", "Telnet disabled",
         "Telnet sends credentials in clear text.", "no telnet server", absent(r"^\s*telnet server")),
    Rule("CX-SEC-03", "switch", "Security", "high", "No SNMP v1/v2c communities",
         "Community strings are clear-text shared secrets; use SNMPv3 authPriv.",
         "Remove snmp-server community; configure snmpv3 user ... auth sha ... priv aes",
         absent(r"^\s*snmp-server community\s+\S+")),
    Rule("CX-SEC-04", "switch", "Security", "high", "Central AAA (TACACS+/RADIUS)",
         "Named technician accounts are required for change attribution (VERIFIED).",
         "tacacs-server host <ip> ... ; aaa authentication login default group tacacs local",
         present(r"^\s*(tacacs-server|radius-server) host\s+\S+")),
    Rule("CX-SEC-05", "switch", "Visibility", "high", "AAA accounting enabled",
         "Without accounting the system cannot prove who changed the device.",
         "aaa accounting all-mgmt default start-stop group tacacs", present(r"^\s*aaa accounting\s+\S+")),
    Rule("CX-SEC-06", "switch", "Security", "medium", "Login banner configured",
         "Legal notice for authorised access.", "banner motd !...!", present(r"^\s*banner (motd|exec)")),
    Rule("CX-OPS-01", "switch", "Operations", "high", "NTP time source",
         "Correct timestamps are required to correlate logs, AAA and telemetry.",
         "ntp server <ip> iburst ; ntp enable", present(r"^\s*ntp server\s+\S+")),
    Rule("CX-OPS-02", "switch", "Visibility", "high", "Syslog to management platform",
         "Device events feed drift detection and attribution.",
         "logging <inm-ip> udp 1514 vrf mgmt", present(r"^\s*logging\s+\d+\.\d+\.\d+\.\d+")),
    Rule("CX-OPS-03", "switch", "Operations", "low", "Timezone configured",
         "Consistent local time on CLI output.", "clock timezone america/bogota", present(r"^\s*clock timezone\s+\S+", missing="WARN")),
    Rule("CX-OPS-04", "switch", "Operations", "low", "Unique hostname",
         "Default hostnames make inventory and logs ambiguous.", "hostname <site>-<role>-<n>", _hostname),
    Rule("CX-L2-01", "switch", "Layer 2", "critical", "Spanning tree enabled",
         "Prevents layer-2 loops from taking the site down.", "spanning-tree", present(r"^\s*spanning-tree\s*$|^\s*spanning-tree mode")),
    Rule("CX-L2-02", "switch", "Layer 2", "high", "Edge-port loop protection",
         "BPDU guard / loop-protect on access ports stops rogue switches.",
         "interface 1/1/x ; spanning-tree bpdu-guard ; loop-protect", _bpdu),
    Rule("CX-L2-03", "switch", "Layer 2", "medium", "Access ports not on VLAN 1",
         "VLAN 1 is the default/native VLAN and should carry no user traffic.", "vlan access <user-vlan>", _vlan1_access),
    Rule("CX-L2-04", "switch", "Visibility", "medium", "LLDP enabled",
         "Topology discovery and AP uplink identification depend on LLDP.", "lldp", absent(r"^\s*no lldp\s*$")),
    # ---------------- Aruba Instant APs
    Rule("AP-RF-01", "access_point", "RF", "critical", "Channel width 20/40 MHz only",
         "Organisation policy: wider channels reduce reuse in dense deployments; 80/160 MHz refused.",
         "Set radio profile channel width to 20 or 40 MHz", _width),
    Rule("AP-RF-02", "access_point", "RF", "medium", "Band steering enabled",
         "Moves capable clients to 5 GHz for capacity and experience.", "band-steering-mode prefer-5ghz",
         present(r"band-steering(-mode)?\s+(prefer-5ghz|balance-bands|force-5ghz)|^\s*band-steering\s*$")),
    Rule("AP-RF-03", "access_point", "RF", "medium", "ClientMatch enabled",
         "Steers sticky clients to better APs.", "arm ; client-match", present(r"^\s*client-match\s*$")),
    Rule("AP-RF-04", "access_point", "RF", "medium", "Minimum transmit power floor set (6-18 dBm)",
         "Prevents ARM from dropping power so low that clients hold on to distant APs.",
         "arm ; min-tx-power 12", _min_tx),
    Rule("AP-RF-05", "access_point", "RF", "medium", "Legacy low basic rates disabled",
         "1/2/5.5/6 Mbps basic rates waste airtime.", "a-basic-rates 12 24 ; g-basic-rates 12 24", _basic_rates),
    Rule("AP-RF-06", "access_point", "RF", "low", "Airtime fairness",
         "Stops slow clients from monopolising airtime.", "air-time-fairness-mode fair-access",
         present(r"air-time-fairness-mode\s+(fair-access|preferred-access)", missing="WARN")),
    Rule("AP-ROAM-01", "access_point", "Roaming", "low", "802.11k/v/r roaming assistance",
         "Faster, client-assisted roaming for voice and video.", "dot11k ; dot11v ; dot11r",
         present(r"^\s*dot11(k|v|r)\b", missing="WARN")),
    Rule("AP-SEC-01", "access_point", "Security", "critical", "No open SSIDs without captive portal",
         "Open networks expose user traffic.", "opmode wpa3-aes-ccm-128 (or wpa2-aes)", _open_ssid),
    Rule("AP-SEC-02", "access_point", "Security", "low", "WPA3 / Enhanced Open in use",
         "WPA3 protects against offline dictionary attacks.", "opmode wpa3-sae-aes / wpa3-aes-ccm-128", _wpa3),
    Rule("AP-SEC-03", "access_point", "Security", "high", "No SNMP v1/v2c communities",
         "Use SNMPv3.", "remove snmp-server community", absent(r"^\s*snmp-server community\s+\S+")),
    Rule("AP-SEC-04", "access_point", "Security", "medium", "Max clients per SSID set",
         "Protects capacity on busy APs.", "max-clients-threshold 64", present(r"max-clients-threshold\s+\d+", missing="WARN")),
    Rule("AP-OPS-01", "access_point", "Operations", "high", "NTP time source",
         "Time correlation for logs and experience data.", "ntp-server <ip>", present(r"^\s*ntp-server\s+\S+")),
    Rule("AP-OPS-02", "access_point", "Visibility", "high", "Syslog to management platform",
         "Feeds event timeline and drift detection.", "syslog-server <inm-ip>", present(r"^\s*syslog-server\s+\S+")),
    Rule("AP-OPS-03", "access_point", "Operations", "low", "Timezone configured",
         "Consistent timestamps.", "clock timezone ...", present(r"^\s*clock timezone\s+\S+", missing="WARN")),
)


def evaluate(family: str, config_text: str | None) -> dict:
    config_text = normalize_config(config_text)
    rules = [r for r in RULES if r.family == family]
    results = []
    for r in rules:
        if not config_text:
            status, ev = "UNKNOWN", None
        else:
            status, ev = r.check(config_text)
        results.append({"id": r.id, "category": r.category, "severity": r.severity, "title": r.title,
                        "status": status, "evidence": ev, "why": r.why, "fix": r.fix})
    return {"results": results, **score(results), "config_available": bool(config_text)}


def score(results: list[dict]) -> dict:
    scored = [x for x in results if x["status"] in ("PASS", "FAIL", "WARN")]
    if not scored:
        return {"score": None, "grade": "UNKNOWN", "counts": _counts(results)}
    total = sum(SEVERITY_WEIGHT[x["severity"]] for x in scored)
    got = sum(SEVERITY_WEIGHT[x["severity"]] * (1 if x["status"] == "PASS" else 0.5 if x["status"] == "WARN" else 0)
              for x in scored)
    s = round(100 * got / total, 1)
    crit_fail = any(x["status"] == "FAIL" and x["severity"] == "critical" for x in scored)
    grade = "A" if s >= 90 and not crit_fail else "B" if s >= 75 and not crit_fail else "C" if s >= 60 else "D"
    return {"score": s, "grade": grade, "counts": _counts(results), "critical_failures": crit_fail}


def _counts(results):
    c: dict[str, int] = {}
    for x in results:
        c[x["status"]] = c.get(x["status"], 0) + 1
    return c


# ---- intended (template) vs installed (actual config) --------------------------------------
def installed_facts(family: str, text: str) -> dict:
    vlans = sorted({int(v) for v in re.findall(r"^\s*vlan (\d{1,4})\s*$", text, re.M) if 1 <= int(v) <= 4094})
    access = {m.group(1): int(m.group(2)) for m in re.finditer(
        r"^interface (\S+)\s*\n(?:(?!^interface ).*\n)*?\s*vlan access (\d+)", text + "\n", re.M)}
    admin, trunks, cur = {}, {}, None
    for line in text.splitlines():
        m = re.match(r"^interface (\S+)", line)
        if m:
            cur = m.group(1)
            admin[cur] = "up"
            continue
        if cur and line.startswith(" "):
            s_ = line.strip()
            if s_ == "shutdown":
                admin[cur] = "down"
            elif mt := re.match(r"^vlan trunk allowed ([\d,\-]+)$", s_):
                vals = []
                for part in mt.group(1).split(","):
                    a, _, b = part.partition("-")
                    vals += list(range(int(a), int(b) + 1)) if b else [int(a)]
                trunks[cur] = sorted(vals)
        elif not line.startswith(" "):
            cur = None
    essids = sorted(set(re.findall(r"^\s*essid\s+(.+?)\s*$", text, re.M)))
    widths = sorted({int(w) for w in re.findall(r"channel-width\s+(\d+)", text)})
    return {"vlans": vlans, "access_vlans": access, "essids": essids, "channel_widths": widths,
            "admin": admin, "trunks": trunks}


def intended_vs_installed(family: str, intended: dict | None, text: str | None) -> dict:
    if not intended:
        return {"status": "NO_INTENT", "differences": [], "note": "No inherited template applies to this device."}
    if not text:
        return {"status": "UNKNOWN", "differences": [], "note": "No captured configuration; cannot verify."}
    have = installed_facts(family, normalize_config(text))
    diffs = []
    want_vlans = sorted({v if isinstance(v, int) else v.get("id") for v in intended.get("vlans", [])})
    for v in want_vlans:
        if family == "switch" and v not in have["vlans"]:
            diffs.append({"field": f"vlan:{v}", "intended": "present", "installed": "missing"})
    for port in intended.get("interfaces", []):
        want = port.get("access_vlan")
        got = have["access_vlans"].get(port["name"])
        if want is not None and got != want:
            diffs.append({"field": f"interface:{port['name']}:access_vlan", "intended": want, "installed": got})
    for ssid in intended.get("ssids", []):
        if ssid.get("essid") and ssid["essid"] not in have["essids"]:
            diffs.append({"field": f"ssid:{ssid['essid']}", "intended": "present", "installed": "missing"})
        w = ssid.get("channel_width_mhz")
        if w and have["channel_widths"] and any(x != w for x in have["channel_widths"]):
            diffs.append({"field": f"ssid:{ssid.get('essid')}:channel_width", "intended": w,
                          "installed": have["channel_widths"]})
    return {"status": "COMPLIANT" if not diffs else "DRIFT", "differences": diffs, "installed": have}


# ---- AOS-CX JSON configuration support -------------------------------------------------------------
def _vals(obj, *keys):
    for k in keys:
        v = obj.get(k) if isinstance(obj, dict) else None
        if v is not None:
            return v
    return None


def cx_json_to_cli(cfg: dict) -> str:
    """Render the rule-relevant parts of an AOS-CX JSON configuration (REST /fullconfigs) as CLI-equivalent
    lines so the same rules apply to captured hardware configs. Accepts both the fullconfigs table names
    (System, VLAN, Interface, NTP_Association, ...) and the REST resource names (system, vlans, interfaces).
    MAPPING STATUS: table/attribute names follow the AOS-CX 10.14 schema; verify on hardware."""
    out: list[str] = []
    sysd = _vals(cfg, "System", "system") or {}
    if sysd.get("hostname"):
        out.append(f"hostname {sysd['hostname']}")
    if sysd.get("timezone") or (sysd.get("other_config") or {}).get("timezone"):
        out.append(f"clock timezone {sysd.get('timezone') or sysd['other_config']['timezone']}")
    banner = (sysd.get("other_config") or {}).get("banner") or sysd.get("banner")
    if banner:
        out.append("banner motd !...!")
    if (_vals(cfg, "SSH_Server", "ssh_server") or sysd.get("ssh_server_vrf") or
            any("ssh" in str(k).lower() for k in (sysd.get("other_config") or {}))):
        out.append("ssh server vrf mgmt")
    if _vals(cfg, "Telnet_Server", "telnet_server") or sysd.get("telnet_server_enable"):
        out.append("telnet server")
    for a in (_vals(cfg, "NTP_Association", "ntp_associations") or {}):
        out.append(f"ntp server {a}")
    for r in (_vals(cfg, "Syslog_Remote", "syslog_remotes") or {}).values() if isinstance(
            _vals(cfg, "Syslog_Remote", "syslog_remotes"), dict) else []:
        if isinstance(r, dict) and r.get("remote_host"):
            out.append(f"logging {r['remote_host']}")
    for t, kw in (("Tacacs_Server", "tacacs-server"), ("Radius_Server", "radius-server")):
        for host in (_vals(cfg, t, t.lower()) or {}):
            out.append(f"{kw} host {str(host).split(',')[0]}")
    if _vals(cfg, "AAA_Accounting_Attributes", "aaa_accounting") or (sysd.get("aaa") or {}).get("accounting"):
        out.append("aaa accounting all-mgmt default start-stop group tacacs")
    for c in (_vals(cfg, "SNMP_Community", "snmp_communities") or {}):
        out.append(f"snmp-server community {c}")
    stp = sysd.get("stp_config") or _vals(cfg, "Spanning_Tree", "stp")
    if stp and str((stp or {}).get("admin_status", "up")).lower() == "up":
        out.append("spanning-tree")
    if sysd.get("lldp_enable") is False or (sysd.get("other_config") or {}).get("lldp_enable") == "false":
        out.append("no lldp")
    for vid in sorted((_vals(cfg, "VLAN", "vlans") or {}), key=lambda x: int(x) if str(x).isdigit() else 0):
        if str(vid).isdigit():
            out.append(f"vlan {vid}")
    for name, it in sorted((_vals(cfg, "Interface", "interfaces", "Port") or {}).items()):
        if not isinstance(it, dict) or not re.match(r"^\d+/\d+/\d+$", str(name)):
            continue
        out.append(f"interface {name}")
        tag = str(it.get("vlan_tag") or "").rsplit("/", 1)[-1]
        if it.get("vlan_mode") == "access" and tag.isdigit():
            out.append(f"    vlan access {tag}")
        if it.get("vlan_mode") in ("native-untagged", "native-tagged", "trunk"):
            trunks = sorted(int(str(x).rsplit("/", 1)[-1]) for x in (it.get("vlan_trunks") or [])
                            if str(x).rsplit("/", 1)[-1].isdigit())
            if trunks:
                out.append("    vlan trunk allowed " + ",".join(map(str, trunks)))
        if str(it.get("admin") or it.get("admin_state") or "up").lower() == "down":
            out.append("    shutdown")
        if (it.get("stp_config") or {}).get("bpdu_guard_enable") or it.get("bpdu_guard"):
            out.append("    spanning-tree bpdu-guard")
        if it.get("loop_protect_enable"):
            out.append("    loop-protect")
    return "\n".join(out) + "\n"


def normalize_config(text: str | None) -> str | None:
    if not text:
        return text
    s = text.lstrip()
    if s.startswith("{"):
        import json
        try:
            return cx_json_to_cli(json.loads(s))
        except ValueError:
            return text
    return text
