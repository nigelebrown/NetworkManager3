"""Allowlisted read-only device diagnostics. Never accept a CLI command from a user."""
from __future__ import annotations

from ..infrastructure.device_audit import sanitize

# These AOS-CX 10.14 AnyCLI show commands are documented by HPE Aruba.
# /cli/commands is checked for support before any /cli POST; model-specific
# unsupported commands are returned as UNSUPPORTED, not guessed or retried.
CX_SHOW: dict[str, str] = {
    "port_auth": "show port-access clients",
    "vsf": "show vsf detail",
    "poe": "show power-over-ethernet",
    "qos": "show qos trust",
    "lacp": "show lacp interfaces",
    "transceivers": "show interface transceiver detail",
    "optical_dom": "show interface dom",
}
# Only preexisting read-only Instant REST show commands (or log category) are used.
INSTANT_SHOW: dict[str, str] = {
    "rf": "show ap arm rf-summary",
    "clients": "show clients",
    "system": "show ap debug system-status",
    "wireless": "show log wireless",
}
MAX_DIAGNOSTIC_BYTES = 48_000


def safe_output(text: object) -> str:
    """Strip secrets and cap diagnostic output; a CLI response is untrusted."""
    if isinstance(text, (dict, list)):
        import json
        text = json.dumps(text, default=str, ensure_ascii=False)
    rendered = str(text or "")
    if len(rendered) > MAX_DIAGNOSTIC_BYTES:
        rendered = rendered[:MAX_DIAGNOSTIC_BYTES] + "\n[TRUNCATED: device output exceeded size limit]"
    # The log sanitizer bounds at 1200 characters per item; process in chunks so
    # diagnostics can display several pages while preserving redaction.
    return "\n".join(sanitize(line) for line in rendered.splitlines()[:200])


# HPE AOS-CX 10.14 Fundamentals, 6200 Series, "show interface dom".
# https://www.arubanetworks.com/techdocs/AOS-CX/10.14/PDF/fundamentals_6200.pdf
# Preserve missing readings (n/a) and measured units, never guess alarm thresholds.
def parse_optical_dom(text: str) -> dict:
    import re
    import math
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_DIAGNOSTIC_BYTES:
        return {"state": "UNRECOGNIZED", "ports": {}, "reason": "Invalid or oversized DOM response."}
    # Restrict to the documented single-channel SFP/SFP+ table; multi-lane QSFP
    # output is explicitly reported as partial instead of misattributing lanes.
    if not all(x in text for x in ("Temperature", "Voltage", "Tx Bias", "Rx Power", "Tx Power")):
        return {"state": "UNRECOGNIZED", "ports": {}, "reason": "DOM table headings not present."}
    number = r"(?:-?[0-9]+(?:\.[0-9]+)?|n/a)"
    power = rf"(?:({number}),\s*({number})|n/a)"
    row = re.compile(rf"^(\d+/\d+/\d+)\s+(SFP[+A-Za-z0-9_.-]*)\s+({number})\s+({number})\s+({number})\s+{power}\s+{power}$", re.I)
    def numeric(value):
        if value is None or value.lower() == "n/a":
            return None
        v = float(value)
        return v if math.isfinite(v) else None
    ports, unknown = {}, 0
    for line in text.splitlines():
        line = line.strip()
        if not re.match(r"^\d+/\d+/\d+\s", line):
            continue
        m = row.fullmatch(line)
        if not m:
            unknown += 1
            continue
        port, kind, temp, volt, bias, rxmw, rxdbm, txmw, txdbm = m.groups()
        if port in ports:
            unknown += 1
            continue
        ports[port] = {"type": kind, "temperature_c": numeric(temp), "voltage_v": numeric(volt),
                       "tx_bias_ma": numeric(bias), "rx_power_mw": numeric(rxmw), "rx_power_dbm": numeric(rxdbm),
                       "tx_power_mw": numeric(txmw), "tx_power_dbm": numeric(txdbm)}
    return {"state": "PARTIAL" if unknown else "COLLECTED", "ports": ports,
            "unparsed_port_rows": unknown, "source": "CX_REST_ANYCLI_DOM"}



# ---- device-initiated (active) diagnostics, rc17 ------------------------------------------------------------
# Fixed command templates; arguments are validated values only. Both are documented operational commands on
# Instant 8.x and AOS-CX 10.x. Cable test and packet capture are deliberately UNSUPPORTED (see ACTIVE_UNSUPPORTED).
ACTIVE_KINDS = ("ping", "traceroute")
ACTIVE_UNSUPPORTED = {
    "cable_test": "Not offered: the per-model AOS-CX/Instant cable-diagnostic command and its effect on the link "
                  "(it can interrupt the port) are not verified for CX 6100/6200; validate on hardware first.",
    "packet_capture": "Not offered: on-device capture needs a mirror/session configuration change and captures user "
                      "traffic (privacy). Use an approved mirror port and an external analyser.",
}
_ACTIVE_LOCK = __import__("threading").Lock()
_BUSY: set[str] = set()


def active_command(family: str, kind: str, target: str, *, vrf: str = "default", count: int = 4) -> str:
    import ipaddress
    ip = ipaddress.IPv4Address(target)                     # raises ValueError for anything but an IPv4 address
    if ip.is_unspecified or ip.is_multicast:
        raise ValueError("Target must be a unicast IPv4 address.")
    if not 1 <= int(count) <= 10:
        raise ValueError("count must be 1–10.")
    if family == "switch":
        if vrf not in ("default", "mgmt"):
            raise ValueError("VRF must be default or mgmt.")
        return f"ping {ip} repetitions {int(count)} vrf {vrf}" if kind == "ping" else f"traceroute {ip} vrf {vrf}"
    if family == "access_point":
        return f"ping {ip}" if kind == "ping" else f"traceroute {ip}"
    raise ValueError("Unsupported device family.")


def run_active(db, device, kind: str, target: str, *, vrf: str = "default", count: int = 4) -> dict:
    """Run one bounded diagnostic from the device. Raises WriterError categories like the read path."""
    from .change_executor import writer_for
    from .writers import WriterError
    if kind in ACTIVE_UNSUPPORTED:
        raise WriterError("UNSUPPORTED", ACTIVE_UNSUPPORTED[kind])
    if kind not in ACTIVE_KINDS:
        raise WriterError("UNSUPPORTED", "Unknown diagnostic.")
    try:
        command = active_command(device.family, kind, target, vrf=vrf, count=count)
    except ValueError as exc:
        raise WriterError("INVALID", str(exc)) from None
    with _ACTIVE_LOCK:
        if device.id in _BUSY:
            raise WriterError("DEVICE_BUSY", "A diagnostic is already running on this device.")
        _BUSY.add(device.id)
    try:
        writer, env = writer_for(db, device)
        if env == "EMULATED":
            out = (f"EMULATED {kind} from {device.hostname or device.serial_number} to {target}: "
                   f"{'4 packets transmitted, 4 received, 0% loss' if kind == 'ping' else '1  ' + target + '  1.0 ms'}")
            return {"kind": kind, "command": command, "output": out, "environment": env, "source": "EMULATOR"}
        from ..aruba import ssh
        from ..aruba.errors import ArubaError
        from ..database.models_phases import DeviceAccess
        access = db.get(DeviceAccess, device.id)
        creds = getattr(writer, "creds", None)
        fp = (access.ssh_fingerprint if access else None) or (creds.ssh_fingerprint if creds else None)
        if creds is None or not fp:
            raise WriterError("PRECONDITION", "Approve this device's SSH host key before running device diagnostics.")
        try:
            raw = ssh.run_exec_command(creds.host, creds.username, creds.password, fp, command,
                                       timeout=90.0 if kind == "traceroute" else 45.0)
        except ArubaError as exc:
            raise WriterError("PRECONDITION" if "fingerprint" in str(exc) else "CONNECTION_LOST", str(exc)) from None
        return {"kind": kind, "command": command, "output": safe_output(raw), "environment": env, "source": "SSH_CLI"}
    finally:
        with _ACTIVE_LOCK:
            _BUSY.discard(device.id)
