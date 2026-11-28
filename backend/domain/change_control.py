"""WP-06 / GAP-07, GAP-08, GAP-09 — typed change plans, RF/switch policy, approvals and
execution gate (pure).

The execution gate is evaluated at execution time and returns DENY unless *every* guard passes.
In this release `device_write_lock` is on and no adapter is CERTIFIED_WRITE, so the gate always
returns DENY — by construction, not by a special case.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

ALLOWED_WIDTHS_MHZ = (20, 40)          # 80/160 MHz are never permitted
APPROVAL_TTL = timedelta(hours=24)
AP_OPERATIONS = {"ap.radio.channel", "ap.radio.width", "ap.radio.tx_power", "ap.ssid.vlan",
                 "ap.band_steering", "ap.client_match", "ap.airtime_fairness", "ap.ssid.create", "ap.ssid.enterprise",
                 "ap.ssid.roaming", "ap.ssid.accounting", "ap.radio.allowed_channels", "ap.ssid.guest_auth",
                 "ap.role.define", "ap.captive_portal.external", "ap.ssid.multi_auth",
                 "ap.radius.server", "ap.ssid.auth_servers"}
SWITCH_OPERATIONS = {"switch.vlan.define", "switch.port.access_vlan", "switch.port.trunk_vlans",
                     "switch.port.admin_state", "switch.port.poe", "switch.lag.members",
                     "switch.lldp", "config.restore", "switch.central.disable", "switch.port.description",
                     "switch.port.stp_bpdu_guard", "switch.port.storm_control", "switch.port.qos_trust",
                     "switch.system.ntp", "switch.system.dns", "switch.system.syslog", "switch.vlan.voice", "switch.port.auth_dot1x", "switch.port.auth_mab",
                     "switch.acl.define", "switch.acl.apply", "switch.route.static", "switch.qos.dscp_policy", "switch.vsf.member",
                     "switch.aaa.radius_server", "switch.aaa.server_group", "switch.aaa.accounting", "switch.port.auth_policy",
                     "switch.route.ospf"}
# Operations whose secret (PSK / RADIUS shared secret) is referenced by an encrypted credential profile id.
# Plans never contain the secret itself; the executor resolves it only at apply time.
SECRET_REF_OPS = {"ap.ssid.create", "ap.ssid.enterprise", "switch.aaa.radius_server", "ap.radius.server"}
_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$"


def _ipv4(v) -> bool:
    import ipaddress
    try:
        return isinstance(v, str) and isinstance(ipaddress.ip_address(v), ipaddress.IPv4Address)
    except ValueError:
        return False
VALID_2G = set(range(1, 14))
VALID_5G = {36, 40, 44, 48, 52, 56, 60, 64, 100, 104, 108, 112, 116, 120, 124, 128, 132, 136,
            140, 144, 149, 153, 157, 161, 165}
DFS_5G = set(range(52, 145))


class PolicyViolation(ValueError):
    pass


# Standard 40-MHz primary/secondary pairs for the supported 5-GHz channel set.
# Device regulatory domain and local policy remain additional constraints.
PAIR_5G = {36, 44, 52, 60, 100, 108, 116, 124, 132, 140, 149, 157}


def channel_components(value) -> set[int]:
    import re
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,3}\+?", value):
        raise PolicyViolation("Allowed channels must use 20-MHz channel numbers or 40-MHz lower-channel '+' notation.")
    ch = int(value.rstrip("+"))
    if ch not in VALID_5G or (value.endswith("+") and ch not in PAIR_5G):
        raise PolicyViolation("Invalid 5-GHz channel or 40-MHz channel pair.")
    return {ch, ch + 4} if value.endswith("+") else {ch}


def normalize_channel_list(values, *, excluded_channels=None, allow_dfs=False) -> list[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= 48:
        raise PolicyViolation("Supply a nonempty list of at most 48 allowed 5-GHz channels.")
    tokens = []
    for value in values:
        components = channel_components(value)
        if components & (excluded_channels or set()):
            raise PolicyViolation("An allowed channel or bonded secondary channel is excluded by site policy.")
        if components & DFS_5G and not allow_dfs:
            raise PolicyViolation("DFS channels require an approved site compatibility profile.")
        tokens.append(str(int(str(value).rstrip("+"))) + ("+" if str(value).endswith("+") else ""))
    if len(set(tokens)) != len(tokens):
        raise PolicyViolation("Duplicate allowed channel.")
    if not any(not t.endswith("+") for t in tokens):
        raise PolicyViolation("Include at least one 20-MHz channel.")
    return sorted(tokens, key=lambda t: (int(t.rstrip("+")), t.endswith("+")))



def validate_operation(op: dict, *, family: str, excluded_channels: set[int] | None = None,
                       allow_dfs: bool = False, uplink_ports: set[str] | None = None) -> dict:
    """Validate one typed operation. Raises PolicyViolation before any adapter is involved."""
    kind = op.get("op")
    excluded_channels = excluded_channels or set()
    if kind == "switch.central.disable":
        if family != "switch" or op.get("acknowledge_takeover") is not True:
            raise PolicyViolation("Taking configuration authority from Aruba Central needs acknowledge_takeover=true "
                                  "and the device removed from its Central group first.")
        return dict(op)
    if kind == "config.restore":
        if not isinstance(op.get("artifact_id"), str) or len(op["artifact_id"]) != 36:
            raise PolicyViolation("config.restore needs a vault artifact_id.")
        return dict(op)
    if family == "access_point":
        if kind not in AP_OPERATIONS:
            raise PolicyViolation(f"Operation {kind!r} is not an AP operation.")
        if kind == "ap.radio.allowed_channels":
            channels = normalize_channel_list(op.get("value"), excluded_channels=excluded_channels, allow_dfs=allow_dfs)
            # The approval binds the validated channel set and explicit DFS permission.
            return {**op, "value": channels, "allow_dfs": bool(allow_dfs)}
        if kind in ("ap.role.define", "ap.captive_portal.external", "ap.ssid.multi_auth"):
            return _validate_rc17_wlan(kind, op)
        if kind in ("ap.radius.server", "ap.ssid.auth_servers"):
            return _validate_rc18_wlan(kind, op)
        if kind == "ap.ssid.guest_auth":
            import re as _re
            for field in ("ssid", "portal_profile", "pre_auth_role", "mac_role"):
                if not isinstance(op.get(field), str) or not _re.fullmatch(_NAME, op[field]):
                    raise PolicyViolation(f"{field} must identify an existing safe-named profile or role.")
            if op.get("acknowledge_guest_auth_change") is not True:
                raise PolicyViolation("Explicitly acknowledge guest authentication and role changes.")
        if kind in ("ap.ssid.roaming", "ap.ssid.accounting"):
            import re as _re
            if not isinstance(op.get("ssid"), str) or not _re.fullmatch(_NAME, op["ssid"]):
                raise PolicyViolation("An existing SSID profile name is required (1–32 safe characters).")
            if kind == "ap.ssid.roaming":
                if any(op.get(k) not in ("enabled", "disabled") for k in ("dot11k", "dot11v", "dot11r")):
                    raise PolicyViolation("Specify enabled or disabled for each of 802.11k, 802.11v and 802.11r.")
            else:
                if op.get("value") not in ("enabled", "disabled"):
                    raise PolicyViolation("RADIUS accounting must be enabled or disabled.")
                interval = op.get("interval_minutes")
                if op["value"] == "enabled" and (type(interval) is not int or not 0 <= interval <= 60):
                    raise PolicyViolation("Enabled accounting requires an interim interval of 0–60 minutes.")
                if op["value"] == "disabled" and interval is not None:
                    raise PolicyViolation("Do not specify an interim interval when disabling accounting.")
        if kind == "ap.radio.width":
            if op.get("value") not in ALLOWED_WIDTHS_MHZ:
                raise PolicyViolation("Channel width must be 20 or 40 MHz; 80/160 MHz are refused.")
        if kind == "ap.radio.channel":
            ch, band = op.get("value"), op.get("band")
            if band not in ("2.4", "5"):
                raise PolicyViolation("Radio band must be 2.4 or 5.")
            if op.get("arm_managed") is True:
                if ch != 0 or op.get("power") != 0:
                    raise PolicyViolation("Native ARM assignment requires channel=0 and power=0.")
                return dict(op)
            if not isinstance(ch, int) or ch not in (VALID_2G if band == "2.4" else VALID_5G):
                raise PolicyViolation(f"Channel {ch} is not valid for {band} GHz.")
            if ch in excluded_channels:
                raise PolicyViolation(f"Channel {ch} is excluded by site policy.")
            if band == "5" and ch in DFS_5G:
                if not allow_dfs:
                    raise PolicyViolation(f"DFS channel {ch} requires an approved site compatibility profile.")
                return {**op, "allow_dfs": True}
        if kind == "ap.radio.tx_power":
            lo, hi = op.get("min_dbm"), op.get("max_dbm")
            if not (isinstance(lo, int) and isinstance(hi, int) and 3 <= lo <= hi <= 30):
                raise PolicyViolation("Transmit power bounds must satisfy 3 <= min <= max <= 30 dBm.")
        if kind == "ap.ssid.vlan":
            if not (isinstance(op.get("value"), int) and 1 <= op["value"] <= 4094):
                raise PolicyViolation("SSID VLAN must be 1..4094.")
            if not isinstance(op.get("ssid"), str) or not op["ssid"].strip():
                raise PolicyViolation("SSID VLAN mapping needs the SSID profile name.")
        if kind == "ap.client_match" and op.get("value") not in ("enabled", "disabled"):
            raise PolicyViolation("ClientMatch must be enabled or disabled.")
        if kind == "ap.band_steering" and op.get("value") not in ("prefer-5ghz", "force-5ghz", "balance-bands", "disabled"):
            raise PolicyViolation("Invalid band steering mode.")
        if kind == "ap.airtime_fairness" and op.get("value") not in ("default-access", "fair-access", "preferred-access"):
            raise PolicyViolation("Invalid airtime fairness mode.")
        if kind in ("ap.ssid.create", "ap.ssid.enterprise"):
            import re as _re
            if not _re.match(_NAME, str(op.get("ssid", ""))):
                raise PolicyViolation("SSID profile name must be 1-32 letters, digits, '_', '.', '-'.")
            if op.get("essid") is not None and not (isinstance(op["essid"], str) and 1 <= len(op["essid"]) <= 32
                                                    and op["essid"].isprintable()):
                raise PolicyViolation("ESSID must be 1-32 printable characters.")
            if not (isinstance(op.get("vlan"), int) and 1 <= op["vlan"] <= 4094):
                raise PolicyViolation("SSID VLAN must be 1..4094.")
            if any(k in op for k in ("passphrase", "secret", "key", "_secret")):
                raise PolicyViolation("Secrets are never placed in a change plan; reference a credential profile "
                                      "with secret_ref.")
        if kind == "ap.ssid.create":
            sec = op.get("security", "wpa2-psk-aes")
            if sec not in ("wpa2-psk-aes", "wpa3-sae-aes", "opensystem"):
                raise PolicyViolation("Security must be wpa2-psk-aes, wpa3-sae-aes or opensystem.")
            if sec != "opensystem" and not op.get("secret_ref"):
                raise PolicyViolation("A PSK SSID needs secret_ref (credential profile holding the passphrase).")
        if kind == "ap.ssid.enterprise":
            import re as _re
            if op.get("security", "wpa2-enterprise") not in ("wpa2-enterprise", "wpa3-enterprise"):
                raise PolicyViolation("Enterprise security must be wpa2-enterprise or wpa3-enterprise.")
            if not _re.match(_NAME, str(op.get("radius_name", ""))):
                raise PolicyViolation("RADIUS server name must be 1-32 letters, digits, '_', '.', '-'.")
            if not _ipv4(op.get("radius_ip")):
                raise PolicyViolation("RADIUS server must be an IPv4 address.")
            for k in ("radius_port", "radius_acct_port"):
                if op.get(k) is not None and not (isinstance(op[k], int) and 1 <= op[k] <= 65535):
                    raise PolicyViolation(f"{k} must be 1..65535.")
            if not op.get("secret_ref"):
                raise PolicyViolation("802.1X SSID needs secret_ref (credential profile holding the RADIUS secret).")
    elif family == "switch":
        if kind not in SWITCH_OPERATIONS:
            raise PolicyViolation(f"Operation {kind!r} is not a switch operation.")
        port = op.get("port")
        if kind.startswith("switch.port") and not port:
            raise PolicyViolation("Port operations require a port name.")
        if port and uplink_ports and port in uplink_ports and kind in (
                "switch.port.admin_state", "switch.port.access_vlan"):
            raise PolicyViolation(f"Port {port} is a protected uplink; refusing {kind}.")
        if kind == "switch.vlan.voice":
            if type(op.get("vlan")) is not int or not 2 <= op["vlan"] <= 4094:
                raise PolicyViolation("Select an existing non-default VLAN (2–4094).")
            if op.get("value") not in ("enabled", "disabled") or op.get("acknowledge_voice_change") is not True:
                raise PolicyViolation("Explicitly acknowledge the voice VLAN advertisement change.")
        if kind in ("switch.port.auth_dot1x", "switch.port.auth_mab"):
            import re
            if not isinstance(port, str) or not re.fullmatch(r"[1-9][0-9]?/[1-9][0-9]?/[1-9][0-9]?", port):
                raise PolicyViolation("Select a physical access port.")
            if uplink_ports and port in uplink_ports:
                raise PolicyViolation("Port authentication is prohibited on protected uplinks.")
            if op.get("method") != ("dot1x" if kind == "switch.port.auth_dot1x" else "mac-auth") or op.get("acknowledge_auth_change") is not True:
                raise PolicyViolation("Select 802.1X or MAC authentication and acknowledge the access-policy change.")
            if op.get("value") != "enabled":
                raise PolicyViolation("Only enabling an existing authentication method is supported; disabling it can bypass NAC.")
        if kind in ("switch.vlan.define", "switch.port.access_vlan"):
            v = op.get("value")
            if not (isinstance(v, int) and 1 <= v <= 4094):
                raise PolicyViolation("VLAN must be 1..4094.")
        if kind == "switch.port.trunk_vlans":
            vs = op.get("value")
            if not (isinstance(vs, list) and vs and all(isinstance(x, int) and 1 <= x <= 4094 for x in vs)):
                raise PolicyViolation("Trunk VLANs must be a non-empty list of 1..4094.")
            if len(set(vs)) != len(vs):
                raise PolicyViolation("Duplicate trunk VLAN.")
        if kind == "switch.port.admin_state" and op.get("value") not in ("up", "down"):
            raise PolicyViolation("Admin state must be up or down.")
        if kind in ("switch.port.poe", "switch.lldp") and op.get("value") not in ("enabled", "disabled"):
            raise PolicyViolation("Value must be enabled or disabled.")
        if kind == "switch.port.description":
            v = op.get("value")
            if not (isinstance(v, str) and len(v) <= 64 and v.isprintable()):
                raise PolicyViolation("Description must be at most 64 printable characters.")
        if kind == "switch.port.stp_bpdu_guard" and op.get("value") not in ("enabled", "disabled"):
            raise PolicyViolation("BPDU guard must be enabled or disabled.")
        if kind == "switch.port.stp_bpdu_guard" and op.get("value") == "enabled" and uplink_ports and port in uplink_ports:
            raise PolicyViolation(f"Port {port} is a protected uplink; BPDU guard would err-disable it.")
        if kind == "switch.port.storm_control":
            ks = [k for k in ("broadcast_kbps", "multicast_kbps", "unknown_unicast_kbps") if op.get(k) is not None]
            if not ks:
                raise PolicyViolation("Storm control needs at least one of broadcast/multicast/unknown_unicast _kbps.")
            for k in ks:
                if not (isinstance(op[k], int) and 64 <= op[k] <= 10_000_000):
                    raise PolicyViolation(f"{k} must be 64..10000000 kbps.")
        if kind == "switch.port.qos_trust" and op.get("value") not in ("none", "cos", "dscp"):
            raise PolicyViolation("QoS trust must be none, cos or dscp.")
        if kind in ("switch.system.ntp", "switch.system.syslog"):
            if not _ipv4(op.get("value")):
                raise PolicyViolation("Server must be an IPv4 address.")
            if kind == "switch.system.syslog" and op.get("severity", "info") not in (
                    "emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"):
                raise PolicyViolation("Invalid syslog severity.")
        if kind == "switch.system.dns":
            vs = op.get("value")
            if not (isinstance(vs, list) and 1 <= len(vs) <= 3 and all(_ipv4(x) for x in vs)):
                raise PolicyViolation("DNS needs 1-3 IPv4 name servers.")
        if kind.startswith("switch.system.") and op.get("vrf", "mgmt") not in ("mgmt", "default"):
            raise PolicyViolation("VRF must be mgmt or default.")
        if kind == "switch.lag.members":
            import re as _re
            if not _re.match(r"^lag\d{1,3}$", str(op.get("lag", ""))):
                raise PolicyViolation("LAG must be named lagN.")
            ports = op.get("value")
            if not (isinstance(ports, list) and 2 <= len(ports) <= 8 and len(set(ports)) == len(ports)):
                raise PolicyViolation("A LAG needs 2-8 distinct member ports.")
            if uplink_ports and set(ports) & uplink_ports:
                raise PolicyViolation("LAG members include a protected uplink.")
        if kind in _RC17_SWITCH_OPS:
            _validate_rc17_switch(op, uplink_ports or set())
        if kind in _RC18_SWITCH_OPS:
            return _validate_rc18_switch(op, uplink_ports or set())
    else:
        raise PolicyViolation("Unknown device family.")
    return dict(op)


# ---- rc17 switch operations: ACLs, static routes, DSCP QoS policy, VSF member provisioning ---------------------
_RC17_SWITCH_OPS = {"switch.acl.define", "switch.acl.apply", "switch.route.static", "switch.qos.dscp_policy",
                    "switch.vsf.member"}
_ACL_NAME = r"^[A-Za-z][A-Za-z0-9_-]{0,31}$"
_PORT_RE = r"^[1-9][0-9]?/[1-9][0-9]?/[1-9][0-9]?$"


def _prefix(v) -> bool:
    import ipaddress
    if v == "any":
        return True
    try:
        ipaddress.IPv4Network(v, strict=True)
        return True
    except (ValueError, TypeError):
        return False


def _validate_rc17_switch(op: dict, uplink_ports: set[str]) -> None:
    import ipaddress
    import re
    kind = op["op"]
    if kind == "switch.acl.define":
        if not re.fullmatch(_ACL_NAME, str(op.get("name", ""))):
            raise PolicyViolation("ACL name: letter first, up to 32 letters, digits, - or _.")
        entries = op.get("entries")
        if not (isinstance(entries, list) and 1 <= len(entries) <= 64):
            raise PolicyViolation("An ACL needs 1–64 entries.")
        seqs = set()
        for e in entries:
            if not isinstance(e, dict):
                raise PolicyViolation("Each ACL entry must be an object.")
            seq = e.get("seq")
            if not (isinstance(seq, int) and 1 <= seq <= 4294967295) or seq in seqs:
                raise PolicyViolation("ACL sequence numbers must be unique positive integers.")
            seqs.add(seq)
            if e.get("action") not in ("permit", "deny"):
                raise PolicyViolation("ACL action must be permit or deny.")
            if e.get("protocol", "any") not in ("any", "tcp", "udp", "icmp"):
                raise PolicyViolation("ACL protocol must be any, tcp, udp or icmp.")
            if not (_prefix(e.get("src", "any")) and _prefix(e.get("dst", "any"))):
                raise PolicyViolation("ACL source/destination must be 'any' or an IPv4 network such as 10.1.0.0/16.")
            port = e.get("dst_port")
            if port is not None and (e.get("protocol") not in ("tcp", "udp") or not (isinstance(port, int) and 1 <= port <= 65535)):
                raise PolicyViolation("A destination port (1–65535) needs protocol tcp or udp.")
        if not any(e["action"] == "permit" for e in entries) and op.get("acknowledge_deny_all") is not True:
            raise PolicyViolation("An ACL with no permit entry blocks all traffic; acknowledge_deny_all=true is required.")
    elif kind == "switch.acl.apply":
        if not re.fullmatch(_PORT_RE, str(op.get("port", ""))):
            raise PolicyViolation("Select a physical port.")
        if op.get("port") in uplink_ports:
            raise PolicyViolation("Applying an ACL to a protected uplink is refused (risk of losing management).")
        if op.get("value") is not None and not re.fullmatch(_ACL_NAME, str(op.get("value"))):
            raise PolicyViolation("value must name an existing ACL, or null to remove the ingress ACL.")
        if op.get("direction", "in") != "in":
            raise PolicyViolation("Only ingress IPv4 ACL application is supported.")
    elif kind == "switch.route.static":
        if not _prefix(op.get("prefix")) or op.get("prefix") == "any":
            raise PolicyViolation("Route prefix must be an IPv4 network such as 10.20.0.0/16.")
        if ipaddress.IPv4Network(op["prefix"]).prefixlen == 0 and op.get("acknowledge_default_route") is not True:
            raise PolicyViolation("Changing the default route needs acknowledge_default_route=true.")
        try:
            hop = ipaddress.IPv4Address(op.get("next_hop"))
        except (ValueError, TypeError):
            raise PolicyViolation("Next hop must be an IPv4 address.") from None
        if hop.is_unspecified or hop.is_multicast or hop.is_loopback:
            raise PolicyViolation("Next hop must be a unicast address.")
        if op.get("vrf", "default") not in ("default",):
            raise PolicyViolation("Static routes are supported in the default VRF only (management VRF changes refused).")
        dist = op.get("distance", 1)
        if not (isinstance(dist, int) and 1 <= dist <= 255):
            raise PolicyViolation("Administrative distance must be 1–255.")
    elif kind == "switch.qos.dscp_policy":
        if not re.fullmatch(_ACL_NAME, str(op.get("name", ""))):
            raise PolicyViolation("Policy name: letter first, up to 32 letters, digits, - or _.")
        if op.get("protocol") not in ("tcp", "udp"):
            raise PolicyViolation("Classify by tcp or udp destination port.")
        if not (isinstance(op.get("dst_port"), int) and 1 <= op["dst_port"] <= 65535):
            raise PolicyViolation("Destination port must be 1–65535.")
        if not (isinstance(op.get("dscp"), int) and 0 <= op["dscp"] <= 63):
            raise PolicyViolation("DSCP must be 0–63.")
        ports = op.get("ports", [])
        if not (isinstance(ports, list) and len(ports) <= 52 and all(re.fullmatch(_PORT_RE, str(p)) for p in ports)):
            raise PolicyViolation("Apply-to ports must be physical ports (0–52).")
    elif kind == "switch.vsf.member":
        if op.get("model_family") != "CX 6200":
            raise PolicyViolation("VSF is supported on the CX 6200 family only; CX 6100 does not support VSF.")
        if not (isinstance(op.get("member"), int) and 2 <= op["member"] <= 8):
            raise PolicyViolation("VSF member number must be 2–8 (member 1 is the conductor).")
        if not re.fullmatch(r"^[A-Za-z0-9-]{3,32}$", str(op.get("member_type", ""))):
            raise PolicyViolation("Give the member's exact part number (e.g. JL724A).")
        links = op.get("links")
        if not (isinstance(links, dict) and set(links) <= {"1", "2"} and links and
                all(isinstance(v, list) and 1 <= len(v) <= 4 and all(re.fullmatch(_PORT_RE, str(p)) for p in v)
                    for v in links.values())):
            raise PolicyViolation("VSF links: {'1': [ports], '2': [ports]} with 1–4 physical ports each.")
        if set(p for v in links.values() for p in v) & uplink_ports:
            raise PolicyViolation("VSF link ports include a protected uplink.")
        if op.get("acknowledge_stack_change") is not True:
            raise PolicyViolation("VSF provisioning changes stack topology; acknowledge_stack_change=true is required.")


def capability_check(operations: list[dict], capabilities: dict[str, str]) -> list[dict]:
    """Every operation reports its capability state; missing = UNSUPPORTED (never silently skipped)."""
    return [{"op": o["op"], "capability": capabilities.get(o["op"], "UNSUPPORTED")} for o in operations]


def plan_hash(device_id: str, operations: list[dict], expected_config_hash: str | None) -> str:
    body = json.dumps({"d": device_id, "ops": operations, "base": expected_config_hash},
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def check_approval(*, requester: str, approver: str, approver_role: str) -> None:
    if not approver or requester.casefold() == approver.casefold():
        raise PolicyViolation("Requester cannot approve their own change (two-person rule).")
    if approver_role not in ("approver", "admin"):
        raise PolicyViolation("Approver role required.")


def approval_valid(*, approved_at: datetime | None, now: datetime, approved_config_hash: str | None,
                   current_config_hash: str | None, approved_firmware: str | None,
                   current_firmware: str | None) -> tuple[bool, str | None]:
    if approved_at is None:
        return False, "Not approved."
    if approved_at.tzinfo is None:
        approved_at = approved_at.replace(tzinfo=timezone.utc)
    if now - approved_at > APPROVAL_TTL:
        return False, "Approval expired."
    if approved_config_hash != current_config_hash:
        return False, "Device configuration drifted since approval."
    if approved_firmware != current_firmware:
        return False, "Device firmware changed since approval."
    return True, None


def execution_gate(*, write_lock: bool, emergency_stop: bool, plan_state: str,
                   approval_ok: bool, capabilities: list[dict], pre_backup_verified: bool,
                   device_lease_held: bool, executor_authenticated: bool) -> dict:
    """Server-side, execution-time decision. Returns ALLOW only when every guard passes."""
    guards = [
        ("global_write_lock_off", not write_lock),
        ("emergency_stop_clear", not emergency_stop),
        ("plan_approved", plan_state == "APPROVED"),
        ("approval_still_valid", approval_ok),
        ("adapter_certified_for_every_op",
         bool(capabilities) and all(c["capability"] == "CERTIFIED_WRITE" for c in capabilities)),
        ("pre_change_backup_verified", pre_backup_verified),
        ("device_lease_held", device_lease_held),
        ("executor_authenticated", executor_authenticated),
    ]
    failed = [name for name, ok in guards if not ok]
    return {"decision": "DENY" if failed else "ALLOW",
            "guards": [{"guard": n, "pass": ok} for n, ok in guards],
            "denial_reasons": failed}


_HOST = r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*$"


def _validate_rc17_wlan(kind: str, op: dict) -> dict:
    """rc17 Instant WLAN security objects. Creation only (no overwrite); rollback deletes what was created."""
    import re as _re
    if kind == "ap.role.define":
        if not _re.fullmatch(_NAME, str(op.get("role", ""))):
            raise PolicyViolation("Role name must be 1-32 letters, digits, '_', '.', '-'.")
        rules = op.get("rules")
        if not isinstance(rules, list) or not 1 <= len(rules) <= 64:
            raise PolicyViolation("A role needs 1-64 access rules.")
        clean = []
        for r in rules:
            dest, proto, action = r.get("dest", "any"), r.get("protocol", "any"), r.get("action")
            if action not in ("permit", "deny"):
                raise PolicyViolation("Each rule action must be permit or deny.")
            if proto not in ("any", "tcp", "udp"):
                raise PolicyViolation("Rule protocol must be any, tcp or udp.")
            if dest != "any":
                if not (isinstance(dest, dict) and _ipv4(dest.get("ip")) and _ipv4(dest.get("mask"))):
                    raise PolicyViolation("Rule destination must be any or {ip, mask} IPv4.")
                dest = {"ip": dest["ip"], "mask": dest["mask"]}
            ports = r.get("ports")
            if proto == "any":
                if ports not in (None, "any"):
                    raise PolicyViolation("Protocol any takes no port range.")
                ports = None
            else:
                if not (isinstance(ports, list) and len(ports) == 2 and all(type(x) is int and 1 <= x <= 65535 for x in ports)
                        and ports[0] <= ports[1]):
                    raise PolicyViolation("TCP/UDP rules need a port range [start, end] within 1-65535.")
            clean.append({"dest": dest, "protocol": proto, "ports": ports, "action": action})
        return {"op": kind, "role": op["role"], "rules": clean}
    if kind == "ap.captive_portal.external":
        if not _re.fullmatch(_NAME, str(op.get("profile", ""))):
            raise PolicyViolation("Portal profile name must be 1-32 letters, digits, '_', '.', '-'.")
        if not (_ipv4(op.get("server")) or _re.fullmatch(_HOST, str(op.get("server", "")))):
            raise PolicyViolation("Portal server must be an IPv4 address or DNS name.")
        port = op.get("port", 443)
        if not (type(port) is int and 1 <= port <= 65535):
            raise PolicyViolation("Portal port must be 1-65535.")
        url = op.get("url", "/")
        if not (isinstance(url, str) and _re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{0,254}", url)):
            raise PolicyViolation("Portal URL path must start with / and contain no spaces or quotes.")
        redirect = op.get("redirect_url")
        if redirect is not None and not (isinstance(redirect, str) and
                                         _re.fullmatch(r"https://[A-Za-z0-9._~!$&'()*+,;=:@%/?-]{1,247}", redirect)):
            raise PolicyViolation("Redirect URL must be an https:// URL without spaces or quotes.")
        if op.get("fail_through", False) is not False:
            raise PolicyViolation("Portal fail-through (open access when the portal is down) is refused.")
        https = op.get("https", True)
        if https is not True and op.get("acknowledge_plain_http") is not True:
            raise PolicyViolation("Plain-HTTP portals need an explicit acknowledgement.")
        return {"op": kind, "profile": op["profile"], "server": op["server"], "port": port, "url": url,
                "https": https is True, "redirect_url": redirect, "fail_through": False}
    # ap.ssid.multi_auth: MAC authentication + 802.1X on an existing enterprise SSID
    if not _re.fullmatch(_NAME, str(op.get("ssid", ""))):
        raise PolicyViolation("An existing SSID profile name is required.")
    if op.get("value") not in ("enabled", "disabled"):
        raise PolicyViolation("Multi-authentication must be enabled or disabled.")
    if op["value"] == "enabled" and not (isinstance(op.get("mac_role"), str) and _re.fullmatch(_NAME, op["mac_role"])):
        raise PolicyViolation("Enabling MAC + 802.1X needs the existing role for MAC-authenticated clients.")
    if op.get("acknowledge_auth_change") is not True:
        raise PolicyViolation("Explicitly acknowledge the authentication change.")
    return {"op": kind, "ssid": op["ssid"], "value": op["value"],
            "mac_role": op.get("mac_role") if op["value"] == "enabled" else None, "acknowledge_auth_change": True}


# ---- rc18 switch operations: RADIUS servers, server groups, port-access accounting, port authentication policy,
# single-area ("Access") OSPF on CX 6200. Bodies follow the documented AOS-CX 10.x REST resources:
# /system/vrfs/{vrf}/radius_servers/{address},{port},{port_type}, /system/aaa_server_groups/{group_name},
# /system/aaa_accounting_attributes/port-access, /system/interfaces/{port}/port_access_auth_configurations/{method},
# /system/vrfs/{vrf}/ospf_routers/{instance_tag}(/areas/{area_id}).
_RC18_SWITCH_OPS = {"switch.aaa.radius_server", "switch.aaa.server_group", "switch.aaa.accounting",
                    "switch.port.auth_policy", "switch.route.ospf"}
_GROUP_RE = r"^[A-Za-z][A-Za-z0-9_.-]{0,31}$"
_BUILTIN_GROUPS = {"radius", "tacacs", "local", "none"}
_HOST_RE = r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*$"


def _int(v, lo, hi) -> bool:
    return type(v) is int and lo <= v <= hi


def _server_ref(r: dict) -> dict:
    import re
    addr = r.get("address")
    if not (_ipv4(addr) or (isinstance(addr, str) and re.fullmatch(_HOST_RE, addr))):
        raise PolicyViolation("RADIUS server address must be an IPv4 address or DNS name.")
    port = r.get("port", 1812)
    if not _int(port, 1, 65535):
        raise PolicyViolation("RADIUS port must be 1-65535.")
    if r.get("port_type", "udp") != "udp":
        raise PolicyViolation("Only RADIUS over UDP is managed; RadSec (TCP/TLS) needs certificate management first.")
    vrf = r.get("vrf", "default")
    if vrf not in ("default", "mgmt"):
        raise PolicyViolation("RADIUS VRF must be default or mgmt.")
    return {"address": addr, "port": port, "port_type": "udp", "vrf": vrf}


def _validate_rc18_switch(op: dict, uplink_ports: set[str]) -> dict:
    import ipaddress
    import re
    kind, action = op["op"], op.get("action", "create")
    if kind != "switch.aaa.accounting" and action not in ("create", "modify", "delete"):
        raise PolicyViolation("action must be create, modify or delete.")
    if any(k in op for k in ("passkey", "secret", "key")):
        raise PolicyViolation("Secrets are never placed in a change plan; reference a credential profile with secret_ref.")
    if kind == "switch.aaa.radius_server":
        out = {"op": kind, "action": action, **_server_ref(op)}
        if action == "create" and not op.get("secret_ref"):
            raise PolicyViolation("A new RADIUS server needs secret_ref (credential profile holding the shared secret).")
        if action != "delete":
            for f, lo, hi in (("timeout", 1, 60), ("retries", 0, 5), ("accounting_udp_port", 1, 65535)):
                if op.get(f) is not None:
                    if not _int(op[f], lo, hi):
                        raise PolicyViolation(f"{f} must be {lo}-{hi}.")
                    out[f] = op[f]
            if op.get("auth_type") is not None:
                if op["auth_type"] not in ("pap", "chap"):
                    raise PolicyViolation("auth_type must be pap or chap.")
                out["auth_type"] = op["auth_type"]
            if op.get("tracking_enable") is not None:
                if type(op["tracking_enable"]) is not bool:
                    raise PolicyViolation("tracking_enable must be true or false.")
                out["tracking_enable"] = op["tracking_enable"]
            if op.get("secret_ref"):
                out["secret_ref"] = op["secret_ref"]
        if action == "modify" and len(out) <= 6:
            raise PolicyViolation("Nothing to modify.")
        return out
    if kind == "switch.aaa.server_group":
        name = str(op.get("group_name", ""))
        if not re.fullmatch(_GROUP_RE, name) or name.lower() in _BUILTIN_GROUPS:
            raise PolicyViolation("Group name must be 1-32 safe characters and not a built-in group.")
        out = {"op": kind, "action": action, "group_name": name}
        if action != "delete":
            lbm = op.get("load_balancing_mode", "priority-based")
            if lbm not in ("priority-based", "round-robin"):
                raise PolicyViolation("load_balancing_mode must be priority-based or round-robin.")
            servers = op.get("servers")
            if not isinstance(servers, list) or not 1 <= len(servers) <= 8:
                raise PolicyViolation("A server group needs 1-8 servers in priority order (first = primary).")
            refs = [_server_ref(x) for x in servers]
            if len({(r["address"], r["port"], r["vrf"]) for r in refs}) != len(refs):
                raise PolicyViolation("A server appears twice in the group.")
            out |= {"load_balancing_mode": lbm, "servers": refs}
        return out
    if kind == "switch.aaa.accounting":
        mode = op.get("accounting_mode")
        if mode not in ("start-stop", "stop-only", "disabled"):
            raise PolicyViolation("accounting_mode must be start-stop, stop-only or disabled.")
        out = {"op": kind, "session_type": "port-access", "accounting_mode": mode}
        if mode == "start-stop" and op.get("interim_update_interval") is not None:
            if not _int(op["interim_update_interval"], 1, 525600):
                raise PolicyViolation("interim_update_interval must be 1-525600 minutes.")
            out["interim_update_interval"] = op["interim_update_interval"]
        return out
    if kind == "switch.port.auth_policy":
        port = op.get("port")
        if not re.fullmatch(_PORT_RE, str(port or "")):
            raise PolicyViolation("A front-panel port such as 1/1/10 is required.")
        if port in uplink_ports:
            raise PolicyViolation(f"Port {port} is a protected uplink; port authentication is refused there.")
        method = op.get("method")
        if method not in ("dot1x", "mac-auth"):
            raise PolicyViolation("method must be dot1x or mac-auth.")
        if op.get("acknowledge_auth_change") is not True:
            raise PolicyViolation("Explicitly acknowledge the port authentication change.")
        out = {"op": kind, "action": action, "port": port, "method": method, "acknowledge_auth_change": True}
        if action == "delete":
            return out
        spec = {"auth_enable": bool, "reauth_enable": bool, "cached_reauth_enable": bool,
                "reauth_period": (1, 86400), "quiet_period": (0, 65535), "max_retries": (1, 10)}
        if method == "dot1x":
            spec |= {"max_requests": (1, 10), "eapol_timeout": (1, 65535), "discovery_period": (1, 65535)}
        for f, rule in spec.items():
            if f not in op or op[f] is None:
                continue
            if rule is bool:
                if type(op[f]) is not bool:
                    raise PolicyViolation(f"{f} must be true or false.")
            elif not _int(op[f], *rule):
                raise PolicyViolation(f"{f} must be {rule[0]}-{rule[1]}.")
            out[f] = op[f]
        if op.get("radius_server_group") is not None:
            g = str(op["radius_server_group"])
            if not re.fullmatch(_GROUP_RE, g):
                raise PolicyViolation("radius_server_group must name an existing server group.")
            out["radius_server_group"] = g
        if action == "create" and "auth_enable" not in out:
            raise PolicyViolation("State auth_enable explicitly when creating a port authentication policy.")
        unknown = set(op) - set(out) - {"op", "action", "model_family", "acknowledge_auth_change"} - set(spec) - {"radius_server_group"}
        if unknown:
            raise PolicyViolation(f"Unsupported attribute(s) for {method}: {sorted(unknown)}")
        return out
    # switch.route.ospf — CX 6200 "Access OSPF": exactly one area, no redistribution of BGP/RIP
    tag = op.get("instance_tag", 1)
    if not _int(tag, 1, 65535):
        raise PolicyViolation("instance_tag must be 1-65535.")
    out = {"op": kind, "action": action, "instance_tag": tag, "vrf": op.get("vrf", "default")}
    if out["vrf"] != "default":
        raise PolicyViolation("OSPF is managed in the default VRF only.")
    if op.get("acknowledge_routing_change") is not True:
        raise PolicyViolation("Explicitly acknowledge the dynamic routing change.")
    out["acknowledge_routing_change"] = True
    if action == "delete":
        return out
    try:
        rid = str(ipaddress.IPv4Address(op.get("router_id")))
        area = str(ipaddress.IPv4Address(op.get("area", "0.0.0.0")))
    except (ValueError, TypeError):
        raise PolicyViolation("router_id and area must be dotted IPv4 values.") from None
    area_type = op.get("area_type", "default")
    if area_type not in ("default", "stub", "stub_no_summary", "nssa", "nssa_no_summary"):
        raise PolicyViolation("Invalid area_type.")
    redist = op.get("redistribute", [])
    if not isinstance(redist, list) or set(redist) - {"connected", "static"}:
        raise PolicyViolation("Only connected and static routes may be redistributed.")
    ifaces = op.get("interfaces", [])
    if not isinstance(ifaces, list) or not 1 <= len(ifaces) <= 32 or \
            not all(isinstance(i, str) and re.fullmatch(r"^(vlan[1-9][0-9]{0,3}|[1-9][0-9]?/[1-9][0-9]?/[1-9][0-9]?)$", i) for i in ifaces):
        raise PolicyViolation("List 1-32 routed interfaces (e.g. vlan20 or 1/1/49) to activate in the area.")
    paths = op.get("maximum_paths", 4)
    if not _int(paths, 1, 8):
        raise PolicyViolation("maximum_paths must be 1-8.")
    return out | {"router_id": rid, "area": area, "area_type": area_type, "redistribute": sorted(set(redist)),
                  "interfaces": ifaces, "passive_interface_default": bool(op.get("passive_interface_default", True)),
                  "maximum_paths": paths}


def _validate_rc18_wlan(kind: str, op: dict) -> dict:
    """rc18 Instant RADIUS management. Instant has no named server-group object: an SSID references up to TWO
    auth servers (first = primary, second = failover) with optional load balancing; that IS the group model."""
    import re
    if any(k in op for k in ("passkey", "secret", "key")):
        raise PolicyViolation("Secrets are never placed in a change plan; reference a credential profile with secret_ref.")
    if kind == "ap.radius.server":
        action = op.get("action", "create")
        if action not in ("create", "modify", "delete"):
            raise PolicyViolation("action must be create, modify or delete.")
        if not re.fullmatch(_NAME, str(op.get("name", ""))):
            raise PolicyViolation("RADIUS server name must be 1-32 letters, digits, '_', '.', '-'.")
        out = {"op": kind, "action": action, "name": op["name"]}
        if action == "delete":
            if not op.get("secret_ref"):
                raise PolicyViolation("Deleting a server needs its secret_ref so rollback can recreate it.")
            return out | {"secret_ref": op["secret_ref"]}
        if action == "create" or op.get("ip") is not None:
            if not _ipv4(op.get("ip")):
                raise PolicyViolation("RADIUS server must be an IPv4 address.")
            out["ip"] = op["ip"]
        for f, lo, hi, default in (("port", 1, 65535, 1812), ("acct_port", 1, 65535, 1813), ("timeout", 1, 30, None),
                                   ("retry_count", 0, 5, None)):
            val = op.get(f, default if action == "create" else None)
            if val is not None:
                if not (type(val) is int and lo <= val <= hi):
                    raise PolicyViolation(f"{f} must be {lo}-{hi}.")
                out[f] = val
        if action == "create" and not op.get("secret_ref"):
            raise PolicyViolation("A new RADIUS server needs secret_ref (credential profile holding the shared secret).")
        if op.get("secret_ref"):
            out["secret_ref"] = op["secret_ref"]
            if action == "modify":
                if not op.get("previous_secret_ref"):
                    raise PolicyViolation("Rotating a shared secret needs previous_secret_ref so rollback can restore it.")
                out["previous_secret_ref"] = op["previous_secret_ref"]
        if action == "modify" and len(out) <= 3:
            raise PolicyViolation("Nothing to modify.")
        return out
    if not re.fullmatch(_NAME, str(op.get("ssid", ""))):
        raise PolicyViolation("An existing enterprise SSID profile name is required.")
    servers = op.get("servers")
    if not isinstance(servers, list) or not 1 <= len(servers) <= 2 or len(set(servers)) != len(servers) or \
            not all(isinstance(x, str) and re.fullmatch(_NAME, x) for x in servers):
        raise PolicyViolation("Assign one or two distinct RADIUS servers (first = primary, second = failover).")
    lb = op.get("load_balancing", False)
    if type(lb) is not bool or (lb and len(servers) < 2):
        raise PolicyViolation("Load balancing needs two servers.")
    acct = op.get("accounting", "unchanged")
    if acct not in ("enabled", "disabled", "unchanged"):
        raise PolicyViolation("accounting must be enabled, disabled or unchanged.")
    out = {"op": kind, "ssid": op["ssid"], "servers": servers, "load_balancing": lb, "accounting": acct}
    if acct == "enabled":
        iv = op.get("interval_minutes")
        if not (type(iv) is int and 0 <= iv <= 60):
            raise PolicyViolation("Enabled accounting needs an interim interval of 0-60 minutes.")
        out["interval_minutes"] = iv
    if op.get("acknowledge_auth_change") is not True:
        raise PolicyViolation("Explicitly acknowledge the authentication server change.")
    return out | {"acknowledge_auth_change": True}
