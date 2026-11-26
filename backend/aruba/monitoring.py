"""Read-only data collection from one Instant cluster."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings
from . import parsers, telemetry
from .client import ArubaClient
from .errors import ArubaCommandError

log = logging.getLogger("sims.aruba.monitoring")


@dataclass
class SiteSnapshot:
    aps: list[dict[str, Any]] = field(default_factory=list)
    clients: list[dict[str, Any]] = field(default_factory=list)
    firmware: str | None = None
    clients_ok: bool = True  # False when the client list could not be read (do not treat clients as gone)
    warnings: list[str] = field(default_factory=list)
    aps_ok: bool = True


def collect_snapshot(client: ArubaClient) -> SiteSnapshot:
    """Poll AP inventory, clients and firmware version.

    `show aps` is mandatory (failure raises). `show clients` and `show version` are
    best-effort: failures become warnings so a partial snapshot is still stored.
    """
    snap = SiteSnapshot()

    resp = client.show("show aps")
    rows = parsers.extract_rows(resp, "show aps")
    snap.aps = parsers.normalize_aps(rows)
    if not snap.aps or len(rows) != len(snap.aps):
        snap.aps_ok = False
        snap.warnings.append("`show aps` returned incomplete or unrecognized AP inventory; absence is not offline evidence.")

    try:
        resp = client.show("show clients")
        rows = parsers.extract_rows(resp, "show clients")
        snap.clients = parsers.normalize_clients(rows)
        raw = parsers.output_text(resp) or ""
        explicit_empty = resp.get("show clients") == [] or bool(re.search(r"(?im)^\s*(?:0 clients?|total(?: number of)? clients\s*[:=]?\s*0|num(?:ber of)? clients\s*[:=]?\s*0|no clients(?: found)?)\s*$", raw))
        if len(rows) != len(snap.clients) or (not snap.clients and not explicit_empty):
            snap.clients_ok = False
            snap.warnings.append("Client inventory is incomplete or unrecognized; disconnect inference suppressed.")
    except ArubaCommandError as exc:
        snap.clients_ok = False
        snap.warnings.append(f"Client list unavailable: {exc}")

    try:
        snap.firmware = parsers.parse_version(client.show("show version"))
        if snap.firmware is None:
            snap.warnings.append("Firmware version could not be parsed from `show version`.")
    except ArubaCommandError as exc:
        snap.warnings.append(f"Firmware version unavailable: {exc}")

    if get_settings().telemetry_enabled and snap.aps:
        enrich_with_telemetry(client, snap)
    return snap


MAX_CONSECUTIVE_TELEMETRY_FAILURES = 3


def enrich_with_telemetry(client: ArubaClient, snap: SiteSnapshot) -> None:
    """Add uptime, CPU, memory, radio channel/power/noise and channel utilization to each AP.

    Three read-only commands per AP, each targeted at that AP. Anything that cannot be read stays None
    (shown as unavailable); a failure for one AP never affects the others or the rest of the poll. After
    several failures in a row the remaining APs are skipped so a cluster that does not support targeting
    is not hammered every poll.
    """
    failed: list[str] = []
    streak = 0
    for d in snap.aps:
        ip = d.get("ip")
        if not ip or d.get("status", "up") != "up":
            continue
        if streak >= MAX_CONSECUTIVE_TELEMETRY_FAILURES:
            failed.append(d.get("name") or ip)
            continue
        got_any = False
        try:
            rf = telemetry.parse_rf_summary(parsers.output_text(client.show("show ap arm rf-summary", iap_ip=ip)))
            if rf:
                got_any = True
                d["radios"] = rf["radios"]
                d["rf_channels"] = rf["channels"]
                for r in rf["radios"]:
                    key = {"5GHz": "util_5g_percent", "2.4GHz": "util_2g_percent"}.get(r.get("band"))
                    if key and r.get("util_percent") is not None:
                        d[key] = r["util_percent"]
        except ArubaCommandError as exc:
            log.info("rf-summary unavailable for %s: %s", ip, exc)
        try:
            radio_stats = telemetry.parse_radio_stats(
                parsers.output_text(client.show("show ap debug radio-stats", iap_ip=ip))
            )
            if radio_stats:
                got_any = True
                # Compact typed measurements only; cumulative counters need delta samples.
                d["radio_stats"] = radio_stats
        except ArubaCommandError as exc:
            log.info("radio-stats unavailable for %s: %s", ip, exc)
        obs: dict = {"gaps": [], "uplink_counters": None,
                     "memory": {"memory_free_mib": None, "reason": "system-status not collected"}}
        try:
            raw = parsers.output_text(client.show("show ap debug system-status", iap_ip=ip))
            st = telemetry.parse_system_status(raw)
            if st:
                got_any = True
                for k, v in st.items():
                    if v is not None:
                        d[k] = v
            mem = telemetry.parse_memory_free_mib(raw)
            up = telemetry.parse_uplink_counters(raw)
            obs["memory"] = mem or {"memory_free_mib": None, "reason": "no MemAvailable/MemFree line in system-status"}
            obs["uplink_counters"] = up
        except ArubaCommandError as exc:
            log.info("system-status unavailable for %s: %s", ip, exc)
            obs["gaps"].append(f"system-status unavailable ({type(exc).__name__})")
        if obs["uplink_counters"] is None:
            try:
                obs["uplink_counters"] = telemetry.parse_uplink_counters(
                    parsers.output_text(client.show("show interface counters", iap_ip=ip)))
            except ArubaCommandError as _exc:
                log.warning("uplink counters unavailable; reported as not measured: %s", type(_exc).__name__, exc_info=True)
        if obs["uplink_counters"] is None:
            obs["gaps"].append("No identifiable uplink byte counters returned by the AP")
        for cmd, kind in (("show ap monitor ap-list", "ap"), ("show ap monitor sta-list", "client")):
            try:
                res = telemetry.parse_monitor_list(parsers.output_text(client.show(cmd, iap_ip=ip)), kind)
                obs[f"monitor_{kind}s"] = res or {"total": None, "reason": f"'{cmd}' returned no recognisable table"}
            except ArubaCommandError as exc:
                obs[f"monitor_{kind}s"] = {"total": None, "reason": f"'{cmd}' rejected ({type(exc).__name__})"}
        obs["client_match"] = {"state": "NOT_COLLECTED",
                               "reason": "Client Match status is unavailable on the tested firmware."}
        d["observations"] = obs
        if got_any:
            streak = 0
        else:
            streak += 1
            failed.append(d.get("name") or ip)
    if failed:
        snap.warnings.append(f"RF/system telemetry unavailable for {len(failed)} AP(s): {', '.join(failed[:5])}"
                             f"{'…' if len(failed) > 5 else ''}.")
