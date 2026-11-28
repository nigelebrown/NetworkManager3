"""Typed reconciliation for Instant AP clusters (rc17): turn a vault baseline into TYPED restore operations.

Instant has no full-config restore. Instead the baseline running-config and the current running-config are
compared ONLY for settings the platform manages with typed operations; each difference becomes the typed op that
returns it to the baseline. The result is a normal change plan (validation, approval, certification, checkpoint,
readback, rollback) — nothing is applied automatically and no raw CLI is replayed.

Never produced: any 80 MHz width (an 80 MHz or unknown baseline width is reported as UNRESTORABLE), secrets (a
deleted PSK/802.1X SSID needs a new plan with a secret_ref), or deletion of objects created since the baseline.
"""
from __future__ import annotations

import re

ARM_TYPED = {"client-match": "ap.client_match", "band-steering-mode": "ap.band_steering",
             "air-time-fairness-mode": "ap.airtime_fairness"}
SSID_FLAGS = ("dot11k", "dot11v", "dot11r")


def _block(text: str, header: str) -> list[str] | None:
    lines = text.replace("\r", "").split("\n")
    if header not in [x.rstrip() for x in lines]:
        return None
    i = [x.rstrip() for x in lines].index(header)
    out = []
    for ln in lines[i + 1:]:
        if not ln.startswith(" "):
            break
        out.append(ln.strip())
    return out


def _arm(text: str) -> dict:
    out: dict = {}
    for ln in _block(text, "arm") or []:
        neg = ln.startswith("no ")
        key, _, val = (ln[3:] if neg else ln).partition(" ")
        out[key] = (False if neg else (val if val else True))
    return out


def _flag(lines: list[str], key: str) -> str:
    return "enabled" if key in lines else "disabled"


def _ssids(text: str) -> list[str]:
    return re.findall(r"^wlan ssid-profile (\S+)$", text, re.M)


def plan(baseline: str, current: str) -> dict:
    ops: list[dict] = []
    unrestorable: list[dict] = []
    b, c = _arm(baseline), _arm(current)
    for key, op in ARM_TYPED.items():
        if b.get(key) != c.get(key):
            if key == "client-match":
                ops.append({"op": op, "value": "enabled" if b.get(key) is True else "disabled"})
            elif isinstance(b.get(key), str):
                val = "disabled" if (key == "band-steering-mode" and b[key] == "disable") else b[key]
                ops.append({"op": op, "value": val})
            else:
                unrestorable.append({"setting": f"arm {key}", "reason": "baseline value not explicit"})
    if (b.get("min-tx-power"), b.get("max-tx-power")) != (c.get("min-tx-power"), c.get("max-tx-power")):
        try:
            ops.append({"op": "ap.radio.tx_power", "min_dbm": int(b["min-tx-power"]), "max_dbm": int(b["max-tx-power"])})
        except (KeyError, TypeError, ValueError):
            unrestorable.append({"setting": "arm tx-power", "reason": "baseline bounds not explicit"})
    if b.get("wide-bands") != c.get("wide-bands") or b.get("80mhz-support") != c.get("80mhz-support"):
        width = {"none": 20, "5ghz": 40}.get(b.get("wide-bands"))
        if width is None or b.get("80mhz-support") is not False:
            unrestorable.append({"setting": "arm wide-bands", "reason": "baseline width is not an explicit 20/40 MHz "
                                 "policy with 80 MHz disabled; 80 MHz is never configured"})
        else:
            ops.append({"op": "ap.radio.width", "value": width})
    if b.get("a-channels") != c.get("a-channels"):
        if isinstance(b.get("a-channels"), str):
            ops.append({"op": "ap.radio.allowed_channels", "value": b["a-channels"].split(",")})
        else:
            unrestorable.append({"setting": "arm a-channels", "reason": "no explicit baseline channel list"})
    base_ssids, cur_ssids = set(_ssids(baseline)), set(_ssids(current))
    for prof in sorted(base_ssids - cur_ssids):
        unrestorable.append({"setting": f"wlan ssid-profile {prof}", "reason": "deleted since baseline; recreate "
                             "with ap.ssid.create / ap.ssid.enterprise and a credential profile (secret_ref)"})
    for prof in sorted(cur_ssids - base_ssids):
        unrestorable.append({"setting": f"wlan ssid-profile {prof}", "reason": "created since baseline; SSID "
                             "deletion is not a typed operation — remove it through its original plan's rollback"})
    for prof in sorted(base_ssids & cur_ssids):
        bb, cb = _block(baseline, f"wlan ssid-profile {prof}") or [], _block(current, f"wlan ssid-profile {prof}") or []
        bv = next((x.split(" ", 1)[1] for x in bb if x.startswith("vlan ")), None)
        cv = next((x.split(" ", 1)[1] for x in cb if x.startswith("vlan ")), None)
        if bv != cv:
            if bv is not None and bv.isdigit():
                ops.append({"op": "ap.ssid.vlan", "ssid": prof, "value": int(bv)})
            else:
                unrestorable.append({"setting": f"{prof} vlan", "reason": "baseline VLAN is not a numeric id"})
        if any(_flag(bb, k) != _flag(cb, k) for k in SSID_FLAGS):
            ops.append({"op": "ap.ssid.roaming", "ssid": prof, **{k: _flag(bb, k) for k in SSID_FLAGS}})
        if _flag(bb, "radius-accounting") != _flag(cb, "radius-accounting") or \
                [x for x in bb if x.startswith("radius-interim")] != [x for x in cb if x.startswith("radius-interim")]:
            if _flag(bb, "radius-accounting") == "enabled":
                iv = next((x.split()[1] for x in bb if x.startswith("radius-interim-accounting-interval ")), None)
                if iv is None or not iv.isdigit():
                    unrestorable.append({"setting": f"{prof} accounting", "reason": "baseline interval not explicit"})
                else:
                    ops.append({"op": "ap.ssid.accounting", "ssid": prof, "value": "enabled", "interval_minutes": int(iv)})
            else:
                ops.append({"op": "ap.ssid.accounting", "ssid": prof, "value": "disabled"})
    return {"operations": ops, "unrestorable": unrestorable,
            "in_sync": not ops and not unrestorable,
            "note": "Typed settings only; untyped differences are not replayed. Review in the change plan."}
