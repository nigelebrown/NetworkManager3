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
                 "ap.band_steering", "ap.client_match", "ap.airtime_fairness", "ap.ssid.create", "ap.ssid.enterprise"}
SWITCH_OPERATIONS = {"switch.vlan.define", "switch.port.access_vlan", "switch.port.trunk_vlans",
                     "switch.port.admin_state", "switch.port.poe", "switch.lag.members",
                     "switch.lldp", "config.restore", "switch.central.disable", "switch.port.description",
                     "switch.port.stp_bpdu_guard", "switch.port.storm_control", "switch.port.qos_trust",
                     "switch.system.ntp", "switch.system.dns", "switch.system.syslog"}
# Operations whose secret (PSK / RADIUS shared secret) is referenced by an encrypted credential profile id.
# Plans never contain the secret itself; the executor resolves it only at apply time.
SECRET_REF_OPS = {"ap.ssid.create", "ap.ssid.enterprise"}
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
        if kind == "ap.radio.width":
            if op.get("value") not in ALLOWED_WIDTHS_MHZ:
                raise PolicyViolation("Channel width must be 20 or 40 MHz; 80/160 MHz are refused.")
        if kind == "ap.radio.channel":
            ch, band = op.get("value"), op.get("band")
            if band not in ("2.4", "5"):
                raise PolicyViolation("Radio band must be 2.4 or 5.")
            if not isinstance(ch, int) or ch not in (VALID_2G if band == "2.4" else VALID_5G):
                raise PolicyViolation(f"Channel {ch} is not valid for {band} GHz.")
            if ch in excluded_channels:
                raise PolicyViolation(f"Channel {ch} is excluded by site policy.")
            if band == "5" and ch in DFS_5G and not allow_dfs:
                raise PolicyViolation(f"DFS channel {ch} requires an approved site compatibility profile.")
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
    else:
        raise PolicyViolation("Unknown device family.")
    return dict(op)


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
