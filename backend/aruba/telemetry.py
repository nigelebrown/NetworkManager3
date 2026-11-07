"""Parsers for per-AP RF and system telemetry (Phase 2b).

Formats come from a real AP-505/AP-535 cluster on Instant 8.13 (`show ap arm rf-summary` and
`show ap debug system-status`). Rules for every parser here:

* Return None / omit a key when the value is not present. Never estimate or default to 0.
* Tolerate extra whitespace, missing sections and unknown lines; never raise on odd input.
"""
from __future__ import annotations

import re
from typing import Any

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+(\d{1,3})\s+(\d{1,3})\s*$")
_UPTIME_PART = re.compile(r"(\d+)\s*(day|hour|min|sec)", re.IGNORECASE)
_UPTIME_UNIT = {"day": 86400, "hour": 3600, "min": 60, "sec": 1}
_BAND = {"5ghz": "5GHz", "2.4ghz": "2.4GHz", "6ghz": "6GHz"}


def _num(s: str) -> float | None:
    m = re.match(r"^-?\d+(\.\d+)?", s.strip())
    return float(m.group()) if m else None


def _pct(v: float | None) -> float | None:
    return v if v is not None and 0 <= v <= 100 else None


def parse_channel_summary(text: str) -> list[dict[str, Any]]:
    """Rows of the 'Channel Summary' table in `show ap arm rf-summary`.

    Columns: phy-type channel retry phy-err mac-err noise util(Qual) cov-idx(Total) intf_idx(Total)
    where util(Qual) = ch-util/rx/tx/ext-ch-util/quality. Noise is reported as a positive magnitude
    (92 means -92 dBm); a trailing '*' marks a split-scan and is ignored.
    """
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 7 or parts[0].lower() not in _BAND:
            continue
        util = parts[6].split("/")
        if len(util) < 3 or not parts[1].rstrip("*^").isdigit():
            continue
        noise = _num(parts[5].rstrip("*"))
        rows.append({
            "band": _BAND[parts[0].lower()],
            "channel": int(parts[1].rstrip("*^")),
            "retry": _num(parts[2]),
            "phy_err": _num(parts[3]),
            "mac_err": _num(parts[4]),
            "noise_floor_dbm": -abs(noise) if noise is not None else None,
            "util_pct": _pct(_num(util[0])),
            "rx_pct": _pct(_num(util[1])),
            "tx_pct": _pct(_num(util[2])),
        })
    return rows


def parse_interfaces(text: str) -> list[dict[str, Any]]:
    """'Interface Name :wifi0 / Phy-Type :5GHz / Current ARM Assignment :165/18.0' blocks.

    Returns one dict per radio: index, band, channel (current ARM channel) and tx_power_dbm (EIRP).
    """
    out: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "interface name":
            m = re.match(r"^wifi(\d+)$", val.lower())
            cur = {"index": int(m.group(1))} if m else None
            if cur is not None:
                out.append(cur)
        elif cur is not None and key == "phy-type":
            cur["band"] = _BAND.get(val.lower())
        elif cur is not None and key == "current arm assignment":
            m = re.match(r"^(\d+)[^/]*/(-?\d+(?:\.\d+)?)$", val)
            if m:
                cur["channel"] = m.group(1)
                cur["tx_power_dbm"] = float(m.group(2))
    return [r for r in out if r.get("band")]


def parse_rf_summary(text: str | None) -> dict[str, Any] | None:
    """Per-radio view of `show ap arm rf-summary`, or None when nothing usable is present.

    Each radio gets its own current channel/power plus the noise, utilization and retry measured ON that
    channel. If the current channel has no row in the summary, those fields are simply absent.
    """
    if not text:
        return None
    channels = parse_channel_summary(text)
    radios = parse_interfaces(text)
    if not radios:
        return None
    for r in radios:
        row = next((c for c in channels if c["band"] == r["band"] and str(c["channel"]) == str(r.get("channel"))), None)
        if row:
            for k_out, k_in in (("noise_floor_dbm", "noise_floor_dbm"), ("util_percent", "util_pct"),
                                ("rx_percent", "rx_pct"), ("tx_percent", "tx_pct"),
                                ("retry", "retry"), ("phy_err", "phy_err"), ("mac_err", "mac_err")):
                if row[k_in] is not None:
                    r[k_out] = row[k_in]
    return {"radios": radios, "channels": channels}


def parse_uptime(text: str) -> int | None:
    """'AP Uptime' section: '6 days 5 hours 54 mins 51 secs' -> seconds."""
    m = re.search(r"AP Uptime\s*\n-+\s*\n([^\n]+)", text)
    if not m:
        return None
    parts = _UPTIME_PART.findall(m.group(1))
    if not parts:
        return None
    return sum(int(n) * _UPTIME_UNIT[u.lower()] for n, u in parts)


def parse_cpu_memory(text: str) -> tuple[float | None, float | None]:
    """Latest sample of the 'CPU and Memory Usage' table: (cpu %, memory %)."""
    start = text.find("CPU and Memory Usage")
    if start < 0:
        return None, None
    end = text.find("Peak CPU", start)
    section = text[start:end if end > 0 else None]
    rows = [m for m in (_TS.match(l.strip()) for l in section.splitlines()) if m]
    if not rows:
        return None, None
    latest = max(rows, key=lambda m: m.group(1))
    return _pct(float(latest.group(2))), _pct(float(latest.group(3)))


def parse_system_status(text: str | None) -> dict[str, Any] | None:
    """uptime_seconds, cpu_percent, memory_percent (each None if absent) from `show ap debug system-status`."""
    if not text:
        return None
    cpu, mem = parse_cpu_memory(text)
    out = {"uptime_seconds": parse_uptime(text), "cpu_percent": cpu, "memory_percent": mem}
    return out if any(v is not None for v in out.values()) else None
