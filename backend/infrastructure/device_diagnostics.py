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
