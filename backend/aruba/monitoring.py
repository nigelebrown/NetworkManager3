"""Read-only data collection from one Instant cluster."""
from __future__ import annotations

import logging
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


def collect_snapshot(client: ArubaClient) -> SiteSnapshot:
    """Poll AP inventory, clients and firmware version.

    `show aps` is mandatory (failure raises). `show clients` and `show version` are
    best-effort: failures become warnings so a partial snapshot is still stored.
    """
    snap = SiteSnapshot()

    resp = client.show("show aps")
    snap.aps = parsers.normalize_aps(parsers.extract_rows(resp, "show aps"))
    if not snap.aps:
        snap.warnings.append("`show aps` returned no recognizable AP rows (format pending verification).")

    try:
        resp = client.show("show clients")
        snap.clients = parsers.normalize_clients(parsers.extract_rows(resp, "show clients"))
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
        try:
            st = telemetry.parse_system_status(parsers.output_text(client.show("show ap debug system-status", iap_ip=ip)))
            if st:
                got_any = True
                for k, v in st.items():
                    if v is not None:
                        d[k] = v
        except ArubaCommandError as exc:
            log.info("system-status unavailable for %s: %s", ip, exc)
        if got_any:
            streak = 0
        else:
            streak += 1
            failed.append(d.get("name") or ip)
    if failed:
        snap.warnings.append(f"RF/system telemetry unavailable for {len(failed)} AP(s): {', '.join(failed[:5])}"
                             f"{'…' if len(failed) > 5 else ''}.")
