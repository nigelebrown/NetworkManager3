"""Root-cause diagnosis for poor Wi-Fi: "why is this AP / client performing poorly and what do I change?"

Pure, deterministic rules over MEASURED inputs only. Every finding carries the evidence numbers it used, a
confidence, who owns the fix (RF plan / AP / wired / client device), concrete actions, and — where the fix is an AP
radio setting the platform can manage — a change PROPOSAL (never executed here; it goes through change control or
the per-device optimization enrollment). Channel widths stay within the 20/40 MHz policy.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

NON_DFS_5G = (36, 40, 44, 48, 149, 153, 157, 161, 165)
CHANNELS_2G = (1, 6, 11)
SEV_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _ch(v: Any) -> int | None:
    try:
        return int(str(v).rstrip("+-E").split("/")[0].split(",")[0])
    except (TypeError, ValueError):
        return None


def _f(sev, fid, title, why, actions, owner, confidence, proposal=None, evidence=None):
    return {"id": fid, "severity": sev, "title": title, "why": why, "actions": actions, "owner": owner,
            "confidence": confidence, "proposal": proposal, "evidence": evidence or {}}


def best_channel(band: str, used: list[int], current: int | None, allowed: tuple[int, ...] | None = None) -> int | None:
    """Least-used allowed channel among neighbours (ties → lowest); None when the current one is already best."""
    pool = allowed or (NON_DFS_5G if band == "5" else CHANNELS_2G)
    load = Counter(c for c in used if c in pool)
    best = min(pool, key=lambda c: (load[c], c))
    if current in pool and load[current] <= load[best]:
        return None
    return best


def diagnose_ap(ap: dict[str, Any], neighbours: list[dict[str, Any]], clients: list[dict[str, Any]],
                events: dict[str, Any] | None = None) -> dict[str, Any]:
    """ap / neighbours: fleet rows (radio_2g / radio_5g with channel, util_percent, noise_floor_dbm), state, cpu…
    clients: connected clients on this AP (snr_db, band, speed_mbps, quality). events: health-report row or None."""
    out: list[dict] = []
    name = ap.get("name")
    state = ap.get("state")
    if state in ("OFFLINE", "UNKNOWN", "STALE"):
        out.append(_f("high" if state == "OFFLINE" else "medium", "not_observed",
                      "AP is not being observed" if state != "OFFLINE" else "AP is offline",
                      ap.get("state_reason") or state, [ap.get("guidance") or "Restore monitoring first."],
                      "Monitoring" if state != "OFFLINE" else "Wired / power", "high"))
        return _wrap(out, ap, measured=False)
    same_cluster = [n for n in neighbours if n.get("device_id") != ap.get("device_id")]
    for band, key, label, hi, mid in (("5", "radio_5g", "5 GHz", 80, 60), ("2", "radio_2g", "2.4 GHz", 70, 50)):
        r = ap.get(key) or {}
        util, noise, ch = r.get("util_percent"), r.get("noise_floor_dbm"), _ch(r.get("channel"))
        co = [n["name"] for n in same_cluster if _ch((n.get(key) or {}).get("channel")) == ch and ch]
        used = [_ch((n.get(key) or {}).get("channel")) for n in same_cluster]
        target = best_channel(band, [u for u in used if u], ch)
        if util is not None and util >= mid:
            sev = "high" if util >= hi else "medium"
            why = f"{label} radio on channel {ch} is {util:g}% busy"
            acts = []
            if co:
                why += f"; {len(co)} other AP(s) in this cluster share channel {ch} ({', '.join(co[:4])}" \
                       f"{'…' if len(co) > 4 else ''}) — channel reuse; RF proximity/interference is not established"
                if target:
                    acts.append(f"Move {label} from channel {ch} to {target} (least used by neighbours).")
            if band == "2":
                acts += ["Keep 2.4 GHz on 1/6/11 only, 20 MHz.",
                         "Reduce 2.4 GHz transmit power or disable the 2.4 GHz radio on densely spaced APs.",
                         "Enable band steering so dual-band clients prefer 5 GHz."]
            else:
                acts += ["Use 20 MHz (or 40 MHz where neighbours allow) — never 80 MHz on this estate.",
                         "Check client count and application load at peak; add/shift coverage for dense rooms."]
            out.append(_f(sev, f"util_{band}", f"{label} channel congestion", why + ".", acts, "RF plan",
                          "medium" if co else "low",
                          {"operation": "ap.radio.channel", "band": label, "from": ch, "to": target} if target else None,
                          {"util_percent": util, "channel": ch, "co_channel_aps": co}))
        elif ch and target and band == "2" and ch not in CHANNELS_2G:
            out.append(_f("medium", "ch_2g_overlap", "2.4 GHz on an overlapping channel",
                          f"2.4 GHz is on channel {ch}, which overlaps 1/6/11 neighbours.",
                          [f"Move to channel {target}."], "RF plan", "high",
                          {"operation": "ap.radio.channel", "band": label, "from": ch, "to": target}))
        if noise is not None and noise > -85:
            out.append(_f("high" if noise > -75 else "medium", f"noise_{band}", f"High {label} noise floor",
                          f"Noise floor {noise:g} dBm on channel {ch} (healthy is below -90 dBm): non-Wi-Fi "
                          "interference or very dense neighbouring networks.",
                          ["Run a spectrum scan / check for microwave ovens, Bluetooth, cameras, DECT, wireless bridges.",
                           f"Move off channel {ch}" + (f" (suggest {target})." if target else "."),
                           "If 2.4 GHz: consider disabling it on this AP when 5 GHz coverage is sufficient."],
                          "RF plan", "medium",
                          {"operation": "ap.radio.channel", "band": label, "from": ch, "to": target} if target else None,
                          {"noise_floor_dbm": noise}))
    n_cl = ap.get("clients") or len(clients)
    if n_cl and n_cl >= 40:
        out.append(_f("high" if n_cl >= 60 else "medium", "density", "Client density is high",
                      f"{n_cl} clients on one AP.",
                      ["Check max-client and load-balancing settings; enable ClientMatch / load balancing.",
                       "Add or reposition an AP for this area; consider a dedicated AP for meeting rooms.",
                       "Limit guest SSID airtime/bandwidth per user."], "RF plan / capacity", "high", None,
                      {"clients": n_cl}))
    if clients:
        snr = [c["snr_db"] for c in clients if c.get("snr_db") is not None]
        weak = [s for s in snr if s < 15]
        if len(snr) >= 5 and len(weak) / len(snr) >= 0.3:
            out.append(_f("high" if len(weak) / len(snr) >= 0.5 else "medium", "coverage",
                          "Many clients have weak signal (coverage edge / sticky clients)",
                          f"{len(weak)} of {len(snr)} connected clients have SNR below 15 dB.",
                          ["Enable ClientMatch / sticky-client steering and a minimum-RSSI only after verifying client capabilities and coverage; avoid disconnecting edge clients.",
                           "Check AP placement and mounting (ceiling, not inside cabinets); add coverage where clients sit.",
                           "Avoid raising transmit power to fix this — it increases co-channel interference."],
                          "RF plan", "medium", None, {"weak_clients": len(weak), "clients_with_snr": len(snr)}))
        bands = Counter(c.get("band") for c in clients)
        strong24 = [c for c in clients if c.get("band") == "2.4GHz" and (c.get("snr_db") or 0) >= 25]
        if bands.get("2.4GHz", 0) >= 3 and bands.get("2.4GHz", 0) >= bands.get("5GHz", 0) and strong24:
            out.append(_f("medium", "band_balance", "Clients are crowding 2.4 GHz",
                          f"{bands.get('2.4GHz')} clients on 2.4 GHz vs {bands.get('5GHz', 0)} on 5 GHz; "
                          f"{len(strong24)} of them have strong signal and could use 5 GHz.",
                          ["Enable band steering (prefer 5 GHz) on the SSIDs.",
                           "Check that the 5 GHz radio is enabled and not on a DFS channel that clients avoid."],
                          "RF plan", "medium", None, dict(bands)))
        slow = [c for c in clients if (c.get("speed_mbps") or 999) < 24 and (c.get("snr_db") or 0) >= 25]
        if len(slow) >= 3:
            out.append(_f("low", "legacy_clients", "Low data rates despite good signal",
                          f"{len(slow)} clients negotiate below 24 Mbps with good SNR — legacy (802.11b/g) devices or "
                          "low basic rates drag airtime.",
                          ["Raise minimum basic rates (disable 1/2/5.5/11 Mbps) on the SSID.",
                           "Identify and update legacy client drivers."], "RF plan / client", "medium"))
    for k, label, thr in (("cpu_percent", "CPU", 85), ("memory_percent", "Memory", 90)):
        v = ap.get(k)
        if v is not None and v >= thr:
            out.append(_f("medium", k, f"AP {label} is high", f"{label} at {v:g}%.",
                          ["Check firmware for known issues; schedule a reboot in a maintenance window.",
                           "Review per-AP features (DPI, spectrum monitoring) that add load."], "AP", "medium"))
    if events:
        e = events.get("events") or {}
        if e.get("reject", 0) >= 50:
            out.append(_f("high" if e["reject"] >= 300 else "medium", "rejects", "Association rejects",
                          f"{e['reject']:,} association/reassociation rejects"
                          + (f", mostly on {events['top_reject_ssid_band']}" if events.get("top_reject_ssid_band") else "")
                          + (f"; peak hour {events['reject_hours'][0]['hour']}:00" if events.get("reject_hours") else "") + ".",
                          ["The AP is refusing clients (capacity / max-clients / resource constrained).",
                           "Raise or rebalance capacity for that SSID; add coverage for peak periods."], "Capacity", "high"))
        if e.get("deauth", 0) >= 400:
            top = events.get("top_client")
            share = events.get("top_client_share") or 0
            out.append(_f("medium", "deauth", "High deauthentication / disassociation volume",
                          f"{e['deauth']:,} deauth/disassoc events" +
                          (f"; client {top} causes {round(share * 100)}% of negative events" if top and share >= 0.3 else "") + ".",
                          ([f"Investigate client {top}: update driver, forget and rejoin, check power-save."] if top and share >= 0.3 else []) +
                          ["Check roaming thresholds and overlap with neighbouring APs."],
                          "Client device" if top and share >= 0.5 else "RF plan", "medium"))
        if e.get("dhcp_timeout", 0) >= 3:
            out.append(_f("high" if e["dhcp_timeout"] >= 10 else "medium", "dhcp", "DHCP timeouts",
                          f"{e['dhcp_timeout']} DHCP timeouts against {e.get('dhcp_ack', 0)} acknowledgements.",
                          ["Check the VLAN/DHCP relay/scope for the SSID; check switchport errors on the AP uplink."],
                          "Wired / DHCP", "high"))
        if e.get("auth", 0) >= 25:
            out.append(_f("medium", "auth", "Authentication failures", f"{e['auth']} auth/security events.",
                          ["Check RADIUS reachability/certificates and recent PSK changes."], "Security / AAA", "medium"))
    return _wrap(out, ap, measured=True)


def diagnose_client(c: dict[str, Any], ap: dict[str, Any] | None, cluster_aps: int,
                    score: dict[str, Any] | None = None) -> dict[str, Any]:
    out: list[dict] = []
    snr, band, speed = c.get("snr_db"), c.get("band"), c.get("speed_mbps")
    disc, roams = c.get("disconnects", 0) or 0, c.get("roams", 0) or 0
    if snr is not None and snr < 15:
        sticky = roams == 0 and cluster_aps > 1
        out.append(_f("high", "weak_signal", "Weak signal at the client",
                      f"SNR {snr} dB on {c.get('ap') or 'its AP'} (good is 25 dB or more)."
                      + (" No roam was observed; this alone does not prove a sticky client or client movement." if sticky else ""),
                      (["Check ClientMatch history, client capabilities and measured neighbour SNR before changing steering thresholds."] if sticky else []) +
                      ["Check where the user sits relative to the AP; add/move coverage if it is a desk area.",
                       "Update the client's Wi-Fi driver; disable aggressive power saving."],
                      "RF plan / client", "high" if sticky else "medium"))
    elif snr is not None and snr < 25:
        out.append(_f("low", "fair_signal", "Signal is only fair", f"SNR {snr} dB.",
                      ["Usable, but sensitive to interference; check the AP's channel utilisation."], "RF plan", "medium"))
    if band == "2.4GHz" and (snr or 0) >= 25:
        out.append(_f("medium", "band", "Client is on 2.4 GHz with a strong signal",
                      "This client uses 2.4 GHz. Its 5 GHz capability and RF coverage have not been established.",
                      ["Enable band steering on the SSID; confirm the device supports 5 GHz."], "RF plan / client", "medium"))
    if speed is not None and speed < 24 and (snr or 0) >= 25:
        out.append(_f("medium", "low_rate", "Low data rate despite good signal",
                      f"Negotiated {speed:g} Mbps with SNR {snr} dB — legacy radio, power save or interference.",
                      ["Check the device's Wi-Fi standard and driver; check retries on the AP channel."], "Client device", "medium"))
    if disc >= 5:
        out.append(_f("high", "unstable", "Connection is unstable",
                      f"{disc} disconnects inferred in the window.",
                      ["Check the AP's deauth/DHCP events for this client (AP health report).",
                       "Update the client driver; disable Wi-Fi power saving; check for roaming between two APs."],
                      "Client device / RF plan", "medium"))
    if roams >= 10:
        out.append(_f("medium", "ping_pong", "Excessive roaming (ping-pong)",
                      f"{roams} roams in the window — the client sits between APs with similar signal.",
                      ["Reduce transmit power on the overlapping APs or adjust roaming thresholds (802.11k/v/r)."],
                      "RF plan", "medium"))
    if ap:
        for f in diagnose_ap(ap, [], [], None)["findings"]:
            if f["id"].startswith(("util_", "noise_", "density", "not_observed")):
                f = dict(f, id="ap_" + f["id"], title=f"Its AP ({ap.get('name')}): {f['title']}")
                out.append(f)
    if score and score.get("unavailable"):
        out.append(_f("info", "partial", "Score uses partial inputs",
                      "Not measured: " + ", ".join(str(x.get("input", x)) if isinstance(x, dict) else str(x)
                                                   for x in score["unavailable"]) + ".",
                      ["Treat the score as indicative."], "—", "high"))
    return _wrap(out, c, measured=snr is not None)


def _wrap(findings: list[dict], subject: dict, *, measured: bool) -> dict[str, Any]:
    findings.sort(key=lambda f: (SEV_RANK[f["severity"]], f["id"]))
    top = next((f for f in findings if f["severity"] != "info"), None)
    if top:
        headline = top["title"] + " — " + top["why"]
    elif measured:
        headline = "No measured cause of poor performance; inputs are within healthy ranges."
    else:
        headline = "Not enough measured data to diagnose."
    proposals = [f["proposal"] for f in findings if f.get("proposal")]
    return {"headline": headline, "findings": findings, "proposals": proposals,
            "method": "Deterministic rules over measured inputs; thresholds documented in docs/SCORING.md and "
                      "docs/AP_HEALTH_AND_DIAGNOSIS.md. Proposals are never applied automatically."}
