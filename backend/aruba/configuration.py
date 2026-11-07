"""Configuration read/parse/payload helpers for Aruba Instant (pure functions + one reader).

Write transport: Aruba Instant 8.10 REST API Guide documents
    POST /rest/ssid      {"ssid-profile": {"action": "create", "ssid-profile": <name>, "essid": {...}, "vlan": {...}, ...}}
    POST /rest/hostname  {"iap_ip_addr": <ap>, "hostname_info": {"hostname": <name>}}
on the MASTER AP only. STATUS: documented, but NOT yet verified on your AP-505. In particular
the guide describes /rest/ssid as "create or modify" without defining how omitted fields behave,
so every apply is followed by a read-back check and the UI recommends a throw-away test SSID first.

Reading: `show running-config` via show-cmd (format PENDING verification; parser is tolerant).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..config import get_settings
from . import parsers
from .client import ArubaClient
from .errors import ArubaCommandError


def config_changes_enabled() -> bool:
    return get_settings().device_writes_permitted


# ---- reading --------------------------------------------------------------------------
_SECRET_LINE = re.compile(
    r"(?im)^(\s*(?:wpa-passphrase|passphrase|key|password|secret|mgmt-user|snmp-server community|"
    r"radius-key|shared-secret|enable-password)\b)\s+.*$"
)


def redact(text: str) -> str:
    """Remove secret-bearing values before a config text is stored or displayed."""
    return _SECRET_LINE.sub(r"\1 <redacted>", text)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def fetch_running_config(client: ArubaClient) -> str:
    resp = client.show("show running-config")
    text = parsers.output_text(resp)
    if not text:
        raise ArubaCommandError("`show running-config` returned no text (format pending verification).")
    return redact(text)


def _unquote(v: str) -> str:
    v = v.strip()
    return v[1:-1] if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'" else v


def parse_running_config(text: str) -> dict[str, Any]:
    """Extract SSID profiles (and the VLANs they use) from Instant CLI-style config text."""
    ssids: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for raw in text.replace("\r", "").split("\n"):
        if not raw.strip():
            continue
        m = re.match(r"^wlan ssid-profile\s+(.+?)\s*$", raw)
        if m:
            cur = {"profile": _unquote(m.group(1)), "essid": None, "vlan": None, "opmode": None,
                   "rf_band": None, "type": None, "enabled": True}
            ssids.append(cur)
            continue
        if cur is not None and raw[:1] in (" ", "\t"):
            parts = raw.strip().split(None, 1)
            key, val = parts[0], (parts[1] if len(parts) > 1 else "")
            if key == "essid":
                cur["essid"] = _unquote(val)
            elif key == "vlan":
                cur["vlan"] = int(val) if val.strip().isdigit() else val.strip() or None
            elif key == "opmode":
                cur["opmode"] = val.strip()
            elif key == "rf-band":
                cur["rf_band"] = val.strip()
            elif key == "type":
                cur["type"] = val.strip()
            elif key == "disable":
                cur["enabled"] = False
        else:
            cur = None  # left the block
    vlans = sorted({s["vlan"] for s in ssids if isinstance(s["vlan"], int)})
    return {"ssids": ssids, "vlans": vlans}


# ---- validation + payloads ------------------------------------------------------------
PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,31}$")
HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,31}$")
PSK_MODES = ("wpa2-psk-aes", "wpa3-sae-aes")
OPMODES = ("opensystem",) + PSK_MODES
Opmode = Literal["opensystem", "wpa2-psk-aes", "wpa3-sae-aes"]


def _printable(v: str, what: str) -> str:
    if any(ord(c) < 32 or ord(c) > 126 for c in v):
        raise ValueError(f"{what} may only contain printable ASCII characters")
    if '"' in v or "\\" in v or "'" in v:
        raise ValueError(f"{what} may not contain quotes or backslashes")
    return v


class SsidChange(BaseModel):
    profile: str
    essid: str | None = Field(default=None, min_length=1, max_length=32)
    vlan: int | None = Field(default=None, ge=1, le=4094)
    opmode: Opmode | None = None
    passphrase: str | None = Field(default=None, min_length=8, max_length=63)
    rf_band: Literal["2.4", "5.0", "all"] | None = None
    enabled: bool | None = None

    @field_validator("profile")
    @classmethod
    def _profile(cls, v: str) -> str:
        if not PROFILE_RE.match(v):
            raise ValueError("profile must be 1-32 chars: letters, digits, '_', '.', '-' (start alphanumeric)")
        return v

    @field_validator("essid")
    @classmethod
    def _essid(cls, v: str | None) -> str | None:
        return _printable(v, "ESSID") if v is not None else v

    @field_validator("passphrase")
    @classmethod
    def _pass(cls, v: str | None) -> str | None:
        return _printable(v, "passphrase") if v is not None else v

    @model_validator(mode="after")
    def _combo(self):
        if self.passphrase and self.opmode == "opensystem":
            raise ValueError("an open SSID cannot have a passphrase")
        return self


def build_ssid_payload(ch: SsidChange, *, creating: bool) -> dict[str, Any]:
    p: dict[str, Any] = {"action": "create", "ssid-profile": ch.profile}
    if creating:
        p["type"] = "employee"
    if ch.essid:
        p["essid"] = {"action": "create", "value": ch.essid}
    if ch.vlan is not None:
        p["vlan"] = {"action": "create", "value": str(ch.vlan)}
    if ch.opmode:
        p["opmode"] = ch.opmode
    if ch.passphrase:
        p["wpa-passphrase"] = ch.passphrase
    if ch.rf_band:
        p["rf-band"] = ch.rf_band
    if ch.enabled is True:
        p["enable"] = "yes"
    elif ch.enabled is False:
        p["disable"] = "yes"
    return {"ssid-profile": p}


def mask_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Display-safe copy of a request body."""
    import copy
    out = copy.deepcopy(payload)
    inner = out.get("ssid-profile")
    if isinstance(inner, dict) and "wpa-passphrase" in inner:
        inner["wpa-passphrase"] = "********"
    return out


def build_hostname_payload(ap_ip: str, hostname: str) -> dict[str, Any]:
    return {"iap_ip_addr": ap_ip, "hostname_info": {"hostname": hostname}}


def verify_ssid(parsed: dict[str, Any], ch: SsidChange) -> list[str]:
    """Compare read-back config with what we asked for. Returns a list of problems (empty = verified)."""
    found = next((s for s in parsed.get("ssids", []) if s["profile"] == ch.profile), None)
    if found is None:
        return [f"Profile '{ch.profile}' was not found in the configuration read back from the device."]
    problems = []
    for label, want, got in (("ESSID", ch.essid, found["essid"]), ("VLAN", ch.vlan, found["vlan"]),
                             ("security mode", ch.opmode, found["opmode"]),
                             ("RF band", ch.rf_band, found["rf_band"]),
                             ("enabled", ch.enabled, found["enabled"])):
        if want is not None and got is not None and want != got:
            problems.append(f"{label}: requested {want!r}, device reports {got!r}")
        elif want is not None and got is None:
            problems.append(f"{label}: could not be read back (format pending verification)")
    return problems
