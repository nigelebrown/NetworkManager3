"""AP health rating — Gold / Silver / Bronze (pure functions, no I/O).

The worst tier across event, condition, and measured client experience evidence wins:

1. Event evidence (syslog / Loki, ingested device log events, or an imported Aruba event export). Each event is
   classified into one category; negative categories are counted per AP and compared with the thresholds below.
   The thresholds are the ones used in the "Wireless AP Health - Action Report" (24 Apr 2026) so that tiers
   produced here match that report for the same counts.
2. Point-in-time condition (the latest measured state of the AP): offline, radio utilisation, CPU, memory,
   client load. Only MEASURED inputs are rated; an input that was not measured is listed as unavailable and
   never treated as healthy.

An AP with neither event evidence nor any measured condition is UNRATED — never Gold by default.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

TIERS = ("BRONZE", "SILVER", "GOLD")  # worst → best
TIER_RANK = {"BRONZE": 0, "SILVER": 1, "GOLD": 2, "UNRATED": 3}

# category ids
REJECT, DHCP_TIMEOUT, DHCP_ACK, AUTH, DEAUTH, ARM, OTHER = (
    "reject", "dhcp_timeout", "dhcp_ack", "auth", "deauth", "arm", "other")
NEGATIVE = (REJECT, DHCP_TIMEOUT, AUTH, DEAUTH, ARM)

# Thresholds (inclusive) — identical to the 24 Apr 2026 report.
SILVER_AT = {REJECT: 50, DHCP_TIMEOUT: 3, AUTH: 25, DEAUTH: 400, "negative": 500, ARM: 2}
BRONZE_AT = {REJECT: 300, DHCP_TIMEOUT: 10, AUTH: 100, DEAUTH: 800, "negative": 1500, ARM: 5}
LABEL = {REJECT: "Rejects", DHCP_TIMEOUT: "DHCP TO", AUTH: "Auth/Sec", DEAUTH: "Deauth/Disassoc",
         "negative": "Negative", ARM: "Radio ARM"}

# Point-in-time condition thresholds (measured values only)
CONDITION_SILVER = {"util_percent": 60.0, "cpu_percent": 80.0, "memory_percent": 85.0, "clients": 40,
                    "noise_floor_dbm": -85.0}
CONDITION_BRONZE = {"util_percent": 80.0, "cpu_percent": 95.0, "memory_percent": 95.0, "clients": 60,
                    "noise_floor_dbm": -75.0}

_MAC = re.compile(r"\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b", re.I)
_RX = {
    DEAUTH: re.compile(r"de-?auth|dis-?assoc", re.I),
    REJECT: re.compile(r"(re)?assoc\w*[^\n]{0,40}(reject|denied|refus|fail)|(reject|denied)[^\n]{0,40}(re)?assoc", re.I),
    DHCP_TIMEOUT: re.compile(r"dhcp[^\n]{0,60}(time\s?-?out|timed out|no (response|offer)|fail)", re.I),
    DHCP_ACK: re.compile(r"dhcp[^\n]{0,30}\back\b|dhcpack", re.I),
    AUTH: re.compile(r"auth\w*[^\n]{0,40}(fail|reject|timeout|denied|invalid)|802\.1x|\beap\b|radius|mic failure|"
                     r"4-?way|key handshake|security|wpa\d?[^\n]{0,20}fail|captive portal[^\n]{0,20}fail", re.I),
    ARM: re.compile(r"\barm\b|channel change|changed channel|power change|radar detect|dfs", re.I),
}
_ORDER = (DEAUTH, REJECT, DHCP_TIMEOUT, DHCP_ACK, AUTH, ARM)


def classify(text: str, category_hint: str | None = None) -> str:
    """Map one event description to a single category. A category column from an export, when present and
    recognisable, is used before the free text."""
    for probe in (category_hint or "", text or ""):
        if not probe:
            continue
        for cat in _ORDER:
            if _RX[cat].search(probe):
                return cat
    return OTHER


def client_mac(text: str, explicit: str | None = None) -> str | None:
    if explicit and _MAC.fullmatch(explicit.strip().lower().replace("-", ":")):
        return explicit.strip().lower().replace("-", ":")
    m = _MAC.search(text or "")
    return m.group(1).lower() if m else None


@dataclass
class ApEvents:
    ap: str
    total: int = 0
    counts: Counter = field(default_factory=Counter)
    clients: set = field(default_factory=set)
    client_negative: Counter = field(default_factory=Counter)
    reject_by_ssid_band: Counter = field(default_factory=Counter)
    reject_by_hour: Counter = field(default_factory=Counter)

    @property
    def negative(self) -> int:
        return sum(self.counts[c] for c in NEGATIVE)


def aggregate(events: Iterable[dict[str, Any]]) -> dict[str, ApEvents]:
    """events: dicts with ap, text, and optional category, client, ssid, band, hour (0-23), count (default 1)."""
    out: dict[str, ApEvents] = {}
    for e in events:
        ap = (e.get("ap") or "").strip()
        if not ap:
            continue
        n = int(e.get("count") or 1)
        row = out.setdefault(ap, ApEvents(ap))
        cat = e.get("category_id") or classify(e.get("text") or "", e.get("category"))
        row.total += n
        row.counts[cat] += n
        mac = client_mac(e.get("text") or "", e.get("client"))
        if mac:
            row.clients.add(mac)
            if cat in NEGATIVE:
                row.client_negative[mac] += n
        if cat == REJECT:
            key = " / ".join(x for x in (e.get("ssid"), e.get("band")) if x)
            if key:
                row.reject_by_ssid_band[key] += n
            if e.get("hour") is not None:
                row.reject_by_hour[int(e["hour"])] += n
    return out


def event_tier(counts: dict[str, int]) -> tuple[str, list[str]]:
    """Return (tier, triggers). Triggers name every threshold met at the deciding tier."""
    vals = {k: int(counts.get(k, 0) or 0) for k in (REJECT, DHCP_TIMEOUT, AUTH, DEAUTH, ARM)}
    vals["negative"] = int(counts.get("negative", sum(vals.values())))
    order = (REJECT, DHCP_TIMEOUT, AUTH, DEAUTH, "negative", ARM)
    bronze = [f"{LABEL[k]} {vals[k]}" for k in order if vals[k] >= BRONZE_AT[k]]
    if bronze:
        return "BRONZE", bronze
    silver = [f"{LABEL[k]} {vals[k]}" for k in order if vals[k] >= SILVER_AT[k]]
    if silver:
        return "SILVER", silver
    return "GOLD", []


def condition_tier(m: dict[str, Any]) -> tuple[str | None, list[str], list[str]]:
    """Rate the latest MEASURED condition. Returns (tier or None when nothing measured, triggers, unavailable)."""
    if m.get("state") == "OFFLINE":
        return "BRONZE", ["AP offline / not answering"], []
    checks = (("util_2g_percent", "util_percent", "2.4 GHz busy", "%", True),
              ("util_5g_percent", "util_percent", "5 GHz busy", "%", True),
              ("cpu_percent", "cpu_percent", "CPU", "%", True),
              ("memory_percent", "memory_percent", "Memory", "%", True),
              ("clients", "clients", "Clients", "", True),
              ("noise_2g_dbm", "noise_floor_dbm", "2.4 GHz noise", " dBm", True),
              ("noise_5g_dbm", "noise_floor_dbm", "5 GHz noise", " dBm", True))
    measured, unavailable, bronze, silver = 0, [], [], []
    for key, th, label, unit, _ in checks:
        v = m.get(key)
        if v is None:
            unavailable.append(label)
            continue
        measured += 1
        v = float(v)
        txt = f"{label} {round(v, 1):g}{unit}"
        if v >= CONDITION_BRONZE[th]:
            bronze.append(txt)
        elif v >= CONDITION_SILVER[th]:
            silver.append(txt)
    if m.get("state") in ("UNKNOWN", "STALE", None) and not measured:
        return None, [], unavailable
    if bronze:
        return "BRONZE", bronze, unavailable
    if silver:
        return "SILVER", silver, unavailable
    return ("GOLD" if measured else None), [], unavailable


def interpretation(row: dict[str, Any]) -> tuple[str, str]:
    """Rule-based interpretation and recommended action (same structure as the action report)."""
    c = row.get("events") or {}
    rej, dto, auth, de, arm = (c.get(k, 0) for k in (REJECT, DHCP_TIMEOUT, AUTH, DEAUTH, ARM))
    top = row.get("top_client")
    top_share = row.get("top_client_share") or 0
    cond = row.get("condition_triggers") or []
    if row.get("state") == "OFFLINE":
        return ("AP is not answering the collector.",
                "Check PoE/switchport and uplink, confirm the AP still belongs to its cluster, then re-poll.")
    if rej >= BRONZE_AT[REJECT] or (rej >= SILVER_AT[REJECT] and rej >= de):
        where = row.get("top_reject_ssid_band")
        return (f"High-density / association-reject condition{(' on ' + where) if where else ''}.",
                "Validate client count and load at peak hours; review max-client/capacity limits; check whether "
                "clients are concentrated on this AP; add or shift coverage for high-density rooms; monitor after changes.")
    if dto >= BRONZE_AT[DHCP_TIMEOUT]:
        return ("Highest DHCP-timeout class; acknowledgements may still be present.",
                "Validate VLAN / DHCP relay / scope health for the mapped WLAN; check switchport errors and DHCP "
                "server reachability; monitor whether timeouts repeat.")
    if de >= SILVER_AT[DEAUTH] and top and top_share >= 0.5:
        return ("Very high deauthentication/disassociation volume, strongly client-driven.",
                f"Identify top client {top}; update driver / forget-and-rejoin / retest that endpoint; then compare "
                "the AP baseline.")
    if de >= SILVER_AT[DEAUTH]:
        return ("High deauthentication/disassociation volume from multiple clients.",
                "Check roaming / sticky-client behaviour and SNR/RSSI distribution; verify channel/power plan with "
                "neighbouring APs; compare 5 GHz / 2.4 GHz client distribution.")
    if auth >= SILVER_AT[AUTH]:
        return ("Elevated authentication/security failures.",
                "Check RADIUS reachability and certificates, PSK changes, and the client mix failing 802.1X.")
    if arm >= SILVER_AT[ARM]:
        return ("Radio ARM changes (channel/power) are frequent.",
                "Check for interference or DFS radar events; review ARM thresholds; keep 20/40 MHz channel policy.")
    if cond:
        return ("Measured radio/system condition breached a threshold: " + "; ".join(cond) + ".",
                "Review channel utilisation and noise around this AP, client load and AP resource usage.")
    if (exp := row.get("client_experience")) and exp["tier"] != "GOLD":
        return ("Connected clients report reduced digital experience.",
                "Review the affected clients' signal, connection rates and disconnect history.")
    if row.get("tier") == "GOLD":
        return ("No threshold breach in this window.", "Keep as baseline; no immediate AP-side corrective action.")
    return ("Not enough evidence to rate.", "Enable syslog to the collector and make sure the AP is in a polled cluster.")


def build_rows(event_map: dict[str, ApEvents], conditions: dict[str, dict[str, Any]],
               aliases: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Combine event evidence (keyed by AP name/host) with conditions (keyed by AP name).
    aliases maps any event key (IP, MAC, serial) onto the AP name used in conditions."""
    aliases = {k.casefold(): v for k, v in (aliases or {}).items()}
    merged: dict[str, ApEvents] = {}
    for key, ev in event_map.items():
        name = aliases.get(key.casefold(), key)
        if name in merged:
            m = merged[name]
            m.total += ev.total
            m.counts.update(ev.counts)
            m.clients |= ev.clients
            m.client_negative.update(ev.client_negative)
            m.reject_by_ssid_band.update(ev.reject_by_ssid_band)
            m.reject_by_hour.update(ev.reject_by_hour)
        else:
            ev.ap = name
            merged[name] = ev
    rows = []
    for name in sorted(set(merged) | set(conditions)):
        ev, cond = merged.get(name), conditions.get(name, {})
        row: dict[str, Any] = {"ap": name, **{k: cond.get(k) for k in (
            "device_id", "site", "cluster", "model", "state", "state_reason", "clients_now", "util_2g_percent",
            "util_5g_percent", "cpu_percent", "memory_percent", "channel_2g", "channel_5g",
            "client_experience")}}
        tiers, triggers = [], []
        if ev:
            counts = {k: ev.counts.get(k, 0) for k in (REJECT, DHCP_TIMEOUT, DHCP_ACK, AUTH, DEAUTH, ARM, OTHER)}
            et, etr = event_tier({**counts, "negative": ev.negative})
            tiers.append(et)
            triggers += etr
            top = ev.client_negative.most_common(1)
            row.update(events=counts, total_events=ev.total, negative=ev.negative, unique_clients=len(ev.clients),
                       event_tier=et, event_trigger="; ".join(etr) or None, top_client=top[0][0] if top else None,
                       top_client_share=round(top[0][1] / ev.negative, 2) if top and ev.negative else None,
                       top_reject_ssid_band=(ev.reject_by_ssid_band.most_common(1) or [(None,)])[0][0],
                       reject_hours=[{"hour": h, "count": n} for h, n in ev.reject_by_hour.most_common(3)])
        else:
            row.update(events=None, total_events=None, negative=None, unique_clients=None, event_tier=None)
        ct, ctr, unavailable = condition_tier(cond) if cond else (None, [], [])
        row.update(condition_tier=ct, condition_triggers=ctr, unavailable_inputs=unavailable)
        if ct:
            tiers.append(ct)
            triggers += ctr
        if exp := cond.get("client_experience"):
            tiers.append(exp["tier"])
            if exp["tier"] != "GOLD":
                triggers.append(exp["detail"])
        row["tier"] = min(tiers, key=lambda t: TIER_RANK[t]) if tiers else "UNRATED"
        row["trigger"] = "; ".join(triggers) if triggers else (
            "No threshold breach" if row["tier"] == "GOLD" else "No evidence in window")
        row["interpretation"], row["action"] = interpretation(row)
        rows.append(row)
    rows.sort(key=lambda r: (r["state"] != "OFFLINE", TIER_RANK[r["tier"]], -(r["negative"] or 0), r["ap"]))
    return rows


def summarize(rows: list[dict[str, Any]], event_map: dict[str, ApEvents]) -> dict[str, Any]:
    totals: Counter = Counter()
    clients: set = set()
    for ev in event_map.values():
        totals.update(ev.counts)
        totals["total"] += ev.total
        clients |= ev.clients
    tiers = Counter(r["tier"] for r in rows)
    bullets = []
    if totals[DHCP_TIMEOUT] or totals[DHCP_ACK]:
        ok = totals[DHCP_ACK] >= 20 * max(totals[DHCP_TIMEOUT], 1)
        bullets.append(f"DHCP: {totals[DHCP_TIMEOUT]:,} timeouts against {totals[DHCP_ACK]:,} acknowledgements — "
                       + ("no group-wide DHCP outage indicated." if ok else "investigate DHCP/relay health."))
    worst = next((r for r in rows if r["tier"] == "BRONZE"), None)
    if worst:
        bullets.append(f"Primary issue: {worst['ap']} — {worst['trigger']}.")
    bullets.append(f"{tiers.get('GOLD', 0)} Gold, {tiers.get('SILVER', 0)} Silver, {tiers.get('BRONZE', 0)} Bronze"
                   + (f", {tiers['UNRATED']} unrated (no evidence)" if tiers.get("UNRATED") else "") + " APs.")
    bullets.append("Bronze APs require direct corrective action; Silver APs require monitoring/tuning; Gold APs are "
                   "stable in this window.")
    return {"tiers": {t: tiers.get(t, 0) for t in ("GOLD", "SILVER", "BRONZE", "UNRATED")},
            "metrics": {"total_events": totals["total"], "aps_observed": len(event_map), "unique_clients": len(clients),
                        "dhcp_timeouts": totals[DHCP_TIMEOUT], "dhcp_acks": totals[DHCP_ACK],
                        "rejects": totals[REJECT], "deauth_disassoc": totals[DEAUTH], "auth_security": totals[AUTH],
                        "radio_arm": totals[ARM]},
            "executive_summary": bullets}


def movement(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tier movement between two reports (validation after corrective action)."""
    before = {r["ap"]: r for r in previous}
    out = []
    for r in current:
        p = before.get(r["ap"])
        if p is None or p["tier"] == r["tier"]:
            continue
        if (p.get("events") is None) != (r.get("events") is None):
            # one report had event evidence for this AP and the other did not (e.g. an imported export vs live
            # syslog): a tier change here says nothing about the network, so it is not called an improvement.
            out.append({"ap": r["ap"], "from": p["tier"], "to": r["tier"], "direction": "not comparable"})
            continue
        better = TIER_RANK[r["tier"]] > TIER_RANK[p["tier"]]
        out.append({"ap": r["ap"], "from": p["tier"], "to": r["tier"], "direction": "improved" if better else "worse"})
    return out


METHOD = {
    "negative_categories": ["association/reassociation rejects", "DHCP timeouts", "authentication/security events",
                            "deauthentication/disassociation events", "Radio ARM events"],
    "silver": {LABEL[k]: v for k, v in SILVER_AT.items()},
    "bronze": {LABEL[k]: v for k, v in BRONZE_AT.items()},
    "condition_silver": CONDITION_SILVER, "condition_bronze": CONDITION_BRONZE,
}


def by_hour_counter(rows: Iterable[tuple[str, int]]) -> dict[str, int]:
    acc: dict[str, int] = defaultdict(int)
    for k, n in rows:
        acc[k] += n
    return dict(acc)
