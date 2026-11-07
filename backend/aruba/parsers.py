"""Tolerant parsers for Aruba Instant `show` output.

Instant REST responses carry either structured JSON (list of dicts) or a text table in a
"Command output" field, depending on firmware and command. We accept both. Anything we
cannot find is returned as None ("unavailable") - never guessed.

PENDING VERIFICATION: exact column names on 8.10.0.21 for the AP-505. Run
`python -m backend.verify_live` to capture real output and compare with these aliases.
"""
from __future__ import annotations

import re
from typing import Any

_SPLIT = re.compile(r"\s{2,}")
_DASHES = re.compile(r"^-{3,}\s*$")
_MAC = re.compile(r"^([0-9a-f]{2}[:\-]){5}[0-9a-f]{2}$", re.IGNORECASE)


# ---------- generic extraction ----------------------------------------------------------
def _output_text(resp: dict[str, Any]) -> str | None:
    for key in ("Command output", "Command Output", "command output", "output"):
        v = resp.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None


def parse_text_table(text: str) -> list[dict[str, str]]:
    """Parse the first Aruba text table: title / ---- / header / rows (columns split on 2+ spaces)."""
    lines = text.replace("\r", "").split("\n")
    for i, line in enumerate(lines):
        if _DASHES.match(line.strip()) and i + 1 < len(lines):
            header_line = lines[i + 1]
            if not header_line.strip():
                continue
            header = [h.strip() for h in _SPLIT.split(header_line.strip())]
            if len(header) < 2:
                continue
            rows: list[dict[str, str]] = []
            for raw in lines[i + 2:]:
                if not raw.strip():
                    break
                if re.fullmatch(r"[-\s]+", raw):
                    continue  # the underline row beneath the column headers is not data
                if raw.lower().startswith(("num ", "total", "flags:", "legend")):
                    break
                cells = [c.strip() for c in _SPLIT.split(raw.strip())]
                rows.append({h: (cells[j] if j < len(cells) else "") for j, h in enumerate(header)})
            return rows
    return []


def extract_rows(resp: dict[str, Any], cmd: str) -> list[dict[str, str]]:
    """Return a list of row dicts from a show-cmd response, whichever shape it has."""
    candidates = [resp.get(cmd)] + list(resp.values())
    for v in candidates:
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            return [{str(k): "" if val is None else str(val) for k, val in x.items()} for x in v]
    text = _output_text(resp)
    if text:
        return parse_text_table(text)
    return []


def _get(row: dict[str, str], *aliases: str) -> str | None:
    lowered = {k.strip().lower(): v for k, v in row.items()}
    for a in aliases:
        v = lowered.get(a)
        if v is not None and v.strip() not in ("", "-", "--", "N/A", "n/a"):
            return v.strip()
    return None


def _int(s: str | None) -> int | None:
    if s is None:
        return None
    m = re.match(r"^-?\d+", s.strip())
    return int(m.group()) if m else None


def _float(s: str | None) -> float | None:
    if s is None:
        return None
    m = re.match(r"^-?\d+(\.\d+)?", s.strip())
    return float(m.group()) if m else None


def normalize_mac(mac: str | None) -> str | None:
    if not mac:
        return None
    mac = mac.strip().lower().replace("-", ":")
    return mac if _MAC.match(mac) else None


def parse_uptime(s: str | None) -> int | None:
    """'1d:2h:3m:4s', '2h 3m', '45m:10s' -> seconds."""
    if not s:
        return None
    total, found = 0, False
    for num, unit in re.findall(r"(\d+)\s*([dhms])", s.lower()):
        total += int(num) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        found = True
    return total if found else None


def band_from_channel(channel: str | None) -> str | None:
    """Derive band from the channel number the AP reported (e.g. '6', '36E', '149+')."""
    n = _int(channel)
    if n is None or n <= 0:
        return None
    return "2.4GHz" if n <= 14 else "5GHz"


def normalize_model(raw: str | None) -> str | None:
    """'505(indoor)' -> 'AP-505'. Anything not matching that pattern is kept as reported."""
    if not raw:
        return None
    m = re.fullmatch(r"(\d{2,3}[A-Za-z]?)\s*(\(.*\))?", raw.strip())
    return f"AP-{m.group(1)}" if m else raw


# ---------- normalizers ----------------------------------------------------------------
def normalize_aps(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    aps = []
    for r in rows:
        mac = normalize_mac(_get(r, "mac address", "mac", "base mac", "wired mac address"))
        name = _get(r, "name", "ap name")
        ip = _get(r, "ip address", "ip")
        # Instant marks the Virtual Controller (master) AP with a trailing '*' on its IP.
        is_master = None
        if ip and ip.endswith("*"):
            ip, is_master = ip.rstrip("*").strip(), True
        # Instant names an AP after its MAC until it is renamed.
        if mac is None and name:
            mac = normalize_mac(name)
        if not (mac or name or ip):
            continue
        radios = []
        for idx in (0, 1, 2):
            ch = _get(r, f"radio {idx} channel")
            if ch:
                radios.append({
                    "index": idx,
                    "channel": ch,
                    "band": band_from_channel(ch),
                    "tx_power_dbm": None,  # not exposed by `show aps`; Phase 2 probe
                })
        pl = _get(r, "power level")
        aps.append({
            "name": name or ip or mac,
            "mac": mac,
            "ip": ip,
            "model": normalize_model(_get(r, "type", "ap type", "model")),
            "is_master": is_master,
            "serial": _get(r, "serial #", "serial number", "serial"),
            "client_count": _int(_get(r, "clients", "client count")),
            "uptime_seconds": parse_uptime(_get(r, "uptime")),
            "radios": radios or None,
            "power_level": pl,
            "mode": _get(r, "mode"),
        })
    return aps


def _quality_from_label(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"\((good|fair|poor|bad)\)", s.lower())
    if not m:
        return None
    return {"bad": "poor"}.get(m.group(1), m.group(1))


def quality_from_snr(snr: int | None) -> str:
    """Documented thresholds (see docs/SCORING.md): >=25 good, >=15 fair, else poor."""
    if snr is None:
        return "unavailable"
    return "good" if snr >= 25 else "fair" if snr >= 15 else "poor"


def normalize_clients(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        mac = normalize_mac(_get(r, "mac address", "mac"))
        if not mac:
            continue
        signal = _get(r, "signal", "snr")
        snr = _int(signal)
        label = _quality_from_label(signal)
        channel = _get(r, "channel")
        out.append({
            "mac": mac,
            "hostname": _get(r, "name", "hostname"),
            "ip": _get(r, "ip address", "ip"),
            "ssid": _get(r, "essid", "ssid"),
            "ap_name": _get(r, "access point", "ap name", "ap"),
            "channel": channel,
            "band": band_from_channel(channel),
            # Instant's "Signal" column is an SNR-style value in dB. RSSI needs a noise
            # floor, so we leave it unavailable unless a column reports it explicitly.
            "snr_db": snr,
            "rssi_dbm": _int(_get(r, "rssi")),
            "speed_mbps": _float(_get(r, "speed (mbps)", "speed")),
            "quality": label or quality_from_snr(snr),
            "quality_source": "ap-label" if label else ("derived-from-snr" if snr is not None else "none"),
            "auth_state": None,
            "vlan": _int(_get(r, "vlan")),
        })
    return out


_VERSION = re.compile(r"Version\s+([0-9]+(?:\.[0-9]+){2,}(?:[-_][0-9A-Za-z.]+)?)")


def parse_version(resp: dict[str, Any]) -> str | None:
    text = _output_text(resp) or ""
    m = _VERSION.search(text)
    if m:
        return m.group(1)
    for v in resp.values():  # structured fallback
        if isinstance(v, str):
            m = _VERSION.search(v)
            if m:
                return m.group(1)
    return None


def output_text(resp: dict[str, Any]) -> str | None:
    """Public accessor for the text body of a show-cmd response."""
    return _output_text(resp)
