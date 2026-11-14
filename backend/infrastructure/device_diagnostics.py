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
