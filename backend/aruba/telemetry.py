"""Parsers for per-AP RF and system telemetry (Phase 2b).

Formats come from Aruba Instant AP RF outputs, including a physical AP test on 2026-11-09 (`show ap arm rf-summary` and
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
            "ext_channel_util_pct": _pct(_num(util[3])) if len(util) > 3 else None,
            "quality_percent": _pct(_num(util[4])) if len(util) > 4 else None,
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
                                ("quality_percent", "quality_percent"),
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


# Verified on a physical Aruba Instant AP via read-only /rest/show-cmd.
# These counters are cumulative since driver reset; never treat them as rates
# without taking a second timed sample and handling counter resets.
_RADIO_COUNTERS = {
    "tx_frames_transmitted": "Tx Frames Transmitted",
    "tx_frames_dropped": "Tx Frames Dropped",
    "tx_success_with_retry": "Tx Success With Retry",
    "tx_multiple_retries": "Tx Multiple Retries",
    "tx_missed_acks": "Tx Missed ACKs",
    "rx_frames_received": "Rx Frames Received",
    "rx_bad_frames": "Rx Bad Frames",
    "rx_retry_frames": "Rx Retry Frames",
    "rx_crc_errors": "Rx CRC Errors",
    "rx_plcp_errors": "Rx PLCP Errors",
    "rx_phy_events": "Rx PHY Events",
    "rx_radar_events": "Rx RADAR Events",
    "radio_resets": "Total Radio Resets",
    "tx_power_changes": "TX Power Changes",
    "channel_changes": "Channel Changes",
    "failed_channel_changes": "Failed Channel Changes",
}
_RADIO_GAUGES = {
    "channel_busy_1s_percent": "Channel Busy 1s",
    "channel_busy_4s_percent": "Channel Busy 4s",
    "channel_busy_64s_percent": "Channel Busy 64s",
    "cca_own_bss_percent": "CCA percentage of our bss",
    "cca_other_bss_percent": "CCA percentage of other bss",
    "cca_interference_percent": "CCA percentage of interference",
}


def parse_radio_stats(text: str | None) -> dict[str, Any] | None:
    """Parse a compact, typed subset of 'show ap debug radio-stats'.

    Per-radio target selection happens at REST call time. Do not log or persist
    the entire vendor debug response (it can be very large).
    """
    if not text or "RADIO Stats" not in text:
        return None
    wanted = {label.lower(): key for key, label in {**_RADIO_COUNTERS, **_RADIO_GAUGES}.items()}
    wanted.update({
        "current noise floor": "noise_floor_dbm",
        "eirp": "eirp_dbm",
        "max eirp": "max_eirp_dbm",
        "rx last snr": "rx_last_snr_db",
    })
    result: dict[str, Any] = {}
    for line in text.splitlines():
        parts = re.match(r"^\s*(.*?)\s{2,}(-?\d+(?:\.\d+)?)\s*$", line)
        if not parts:
            continue
        key = wanted.get(parts.group(1).strip().lower())
        if key is None:
            continue
        number = float(parts.group(2))
        if key == "noise_floor_dbm":
            result[key] = -abs(number) if number > 0 else number
        elif key.endswith("_percent"):
            value = _pct(number)
            if value is not None:
                result[key] = value
        elif key.endswith("_dbm") or key.endswith("_db"):
            result[key] = number
        elif number >= 0 and number.is_integer():
            result[key] = int(number)
    return result or None


# ---- 0013: memory, uplink counters and over-the-air monitor tables -----------------------------------------
# Format confidence: these parsers accept only unambiguous shapes (Linux meminfo "MemFree: N kB", ifconfig
# "RX bytes:N ... TX bytes:N", /proc/net/dev rows, and header-driven whitespace tables). Values are labelled
# PARSED_FORMAT_UNVERIFIED until a captured hardware fixture for the model/firmware is added to the test suite.
_MEMFREE = re.compile(r"^\s*MemFree:\s*(\d+)\s*kB\s*$", re.I | re.M)
_MEMAVAIL = re.compile(r"^\s*MemAvailable:\s*(\d+)\s*kB\s*$", re.I | re.M)
_IFCONFIG_BLOCK = re.compile(r"^(eth0|bond0|br0)\b.*?(?=^\S|\Z)", re.M | re.S)
_RX_TX = re.compile(r"RX bytes:\s*(\d+).*?TX bytes:\s*(\d+)", re.S)
_PROC_NET_DEV = re.compile(r"^\s*(eth0|bond0):\s*(\d+)(?:\s+\d+){7}\s+(\d+)", re.M)
_MACISH = re.compile(r"^([0-9a-f]{2}[:-]){5}[0-9a-f]{2}$", re.I)


def parse_memory_free_mib(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    m = _MEMAVAIL.search(text) or _MEMFREE.search(text)
    if not m:
        return None
    return {"memory_free_mib": round(int(m.group(1)) / 1024, 1),
            "basis": "MemAvailable" if m.re is _MEMAVAIL else "MemFree", "format": "PARSED_FORMAT_UNVERIFIED"}


def parse_uplink_counters(text: str | None) -> dict[str, Any] | None:
    """Cumulative uplink byte counters. Never a rate: rates need two timed samples (see uplink_rates)."""
    if not text:
        return None
    for block in _IFCONFIG_BLOCK.finditer(text):
        m = _RX_TX.search(block.group(0))
        if m:
            return {"interface": block.group(1), "rx_bytes": int(m.group(1)), "tx_bytes": int(m.group(2)),
                    "format": "PARSED_FORMAT_UNVERIFIED"}
    m = _PROC_NET_DEV.search(text)
    if m:
        return {"interface": m.group(1), "rx_bytes": int(m.group(2)), "tx_bytes": int(m.group(3)),
                "format": "PARSED_FORMAT_UNVERIFIED"}
    return None


def _table(text: str) -> list[dict[str, str]]:
    """Header-driven fixed-width table: column boundaries come from where each header name starts, so values
    containing single spaces (SSIDs) stay intact. The header must contain a 'bssid'/'mac' column."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    for i, line in enumerate(lines):
        heads = [(m.start(), m.group(0).lower()) for m in re.finditer(r"\S+(?: \S+)*", line)]
        names = [h for _, h in heads]
        if len(heads) < 3 or not any(n in ("bssid", "mac", "mac address", "client mac") for n in names):
            continue
        starts = [p for p, _ in heads] + [10 ** 6]
        out = []
        for row in lines[i + 1:]:
            if set(row.strip()) <= set("-= "):
                continue
            rec = {name: row[starts[k]:starts[k + 1]].strip() for k, name in enumerate(names)}
            key = rec.get("bssid") or rec.get("mac") or rec.get("mac address") or rec.get("client mac") or ""
            if not _MACISH.match(key.strip()):
                if out:
                    break        # end of table (e.g. "Num APs:3")
                continue
            out.append(rec)
        return out
    return []


def _band(channel: str | None) -> str:
    m = re.match(r"(\d+)", channel or "")
    if not m:
        return "unknown"
    ch = int(m.group(1))
    return "2.4GHz" if ch <= 14 else "5GHz" if ch <= 177 else "unknown"


def parse_monitor_list(text: str | None, kind: str) -> dict[str, Any] | None:
    """Counts of neighbouring APs (kind='ap') or monitored stations (kind='client') heard over the air, by band.
    These are observed radio neighbours — never substituted by same-channel entries from our own inventory."""
    if not text:
        return None
    rows = _table(text)
    if not rows:
        return None
    by_band: dict[str, int] = {}
    keys = set()
    for r in rows:
        key = (r.get("bssid") or r.get("mac") or r.get("mac address") or r.get("client mac")).lower()
        if key in keys:
            continue
        keys.add(key)
        band = _band(r.get("chan") or r.get("channel") or r.get("ch"))
        by_band[band] = by_band.get(band, 0) + 1
    out = {"total": len(keys), "by_band": by_band, "format": "PARSED_FORMAT_UNVERIFIED"}
    if kind == "ap":
        types: dict[str, int] = {}
        for r in rows:
            t = r.get("ap-type") or r.get("ap type") or r.get("type")
            if t:
                types[t.lower()] = types.get(t.lower(), 0) + 1
        if types:
            out["by_type"] = types
    return out


def uplink_rates(prev: dict | None, cur: dict | None, prev_at: str | None, cur_at: str, *, max_gap_s: float = 1800) -> dict:
    """Bits/s from two cumulative samples. Resets, wraps outside 32-bit and long gaps yield no rate (with reason)."""
    from datetime import datetime
    if not cur:
        return {"rx_bps": None, "tx_bps": None, "quality": "NO_COUNTERS"}
    if not prev or not prev_at or prev.get("interface") != cur.get("interface"):
        return {"rx_bps": None, "tx_bps": None, "quality": "FIRST_SAMPLE"}
    dt = (datetime.fromisoformat(cur_at) - datetime.fromisoformat(prev_at)).total_seconds()
    if dt <= 0:
        return {"rx_bps": None, "tx_bps": None, "quality": "INVALID_INTERVAL"}
    if dt > max_gap_s:
        return {"rx_bps": None, "tx_bps": None, "quality": "GAP", "interval_s": round(dt)}
    out = {"interval_s": round(dt, 1), "quality": "VERIFIED_DELTA"}
    for k in ("rx", "tx"):
        a, b = prev[f"{k}_bytes"], cur[f"{k}_bytes"]
        if b >= a:
            d = b - a
        elif a > 0.95 * 2 ** 32 and b < 0.05 * 2 ** 32:
            d, out["quality"] = (2 ** 32 - a) + b, "ROLLOVER_32"
        else:
            out.update({"rx_bps": None, "tx_bps": None, "quality": "COUNTER_RESET"})
            return out
        out[f"{k}_bps"] = round(d * 8 / dt, 1)
    return out
