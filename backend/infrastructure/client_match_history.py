"""Device-reported Instant ClientMatch action history, separate from inferred roams.

The vendor table supplies ages, not absolute timestamps, destination APs or a
complete association stream. Keep estimated event times explicitly labelled.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..aruba.parsers import normalize_mac
from ..config import get_settings
from ..database.models import AppSetting
from ..database.models_inm import InmDevice
from .device_audit import sanitize

PREFIX = "cm-history:"
COMMAND = "show ap client-match-history"
MAX_ROWS = 300
MAX_BYTES = 100_000
RETENTION = timedelta(days=7)
_MAC_PATTERN = r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"
_ROW = re.compile(r"^(" + _MAC_PATTERN + r")\s+([A-Za-z0-9_-]{1,32})\s+([A-Za-z0-9_-]{1,32})\s+(.{1,200}?)\s+([0-9]{1,2})\s+([0-9dhms:]+)$")
_AGE = re.compile(r"(?:(\d+)d:)?(?:(\d+)h:)?(?:(\d+)m:)?(\d+)s")


def token(mac: str, site_id: int) -> str:
    normalized = normalize_mac(mac)
    if not normalized:
        raise ValueError("Invalid client identifier")
    return hmac.new(get_settings().jwt_secret.encode(),
                    f"clientmatch-v1:{site_id}:{normalized}".encode(), hashlib.sha256).hexdigest()


def _age(value: str) -> int | None:
    m = _AGE.fullmatch(value)
    if not m:
        return None
    days, hours, minutes, seconds = (int(v or 0) for v in m.groups())
    if minutes > 59 or seconds > 59 or (days and hours > 23):
        return None
    total = ((days * 24 + hours) * 60 + minutes) * 60 + seconds
    return total if 0 <= total <= int(RETENTION.total_seconds()) else None


def parse(text: str, *, site_id: int, collected: datetime) -> dict:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise ValueError("ClientMatch output exceeds limit")
    if not re.search(r"(?im)^\s*Client Match Action Table\s*$", text):
        raise ValueError("Missing documented ClientMatch table header")
    count = re.search(r"(?im)^\s*Total\s+(\d+)\s+Records?\s*$", text)
    if not count:
        raise ValueError("Missing ClientMatch record count")
    declared = int(count.group(1))
    rows, recognized = [], 0
    for raw in text.splitlines():
        m = _ROW.fullmatch(raw.strip())
        if not m:
            continue
        recognized += 1
        mac, old, new, reason, radio, age = m.groups()
        seconds = _age(age)
        if seconds is None:
            continue
        reason = re.sub(_MAC_PATTERN, "[client]", sanitize(reason), flags=re.I)[:160]
        reason = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[address]", reason)
        rows.append({"client_token": token(mac, site_id), "old_state": old, "new_state": new,
                     "reason": reason, "radio": int(radio), "age_seconds_at_capture": seconds,
                     "estimated_at": (collected - timedelta(seconds=seconds)).isoformat(),
                     "collected_at": collected.isoformat()})
    if declared and not recognized:
        raise ValueError("No recognizable ClientMatch action rows")
    rows.sort(key=lambda row: row["estimated_at"], reverse=True)
    return {"state": "COLLECTED" if declared == recognized == len(rows) and len(rows) <= MAX_ROWS else "PARTIAL",
            "declared_records": declared, "recognized_records": recognized,
            "truncated": len(rows) > MAX_ROWS or declared > recognized,
            "records": rows[:MAX_ROWS]}


def _utc(value):
    result = datetime.fromisoformat(value)
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def merge(previous: list[dict], incoming: list[dict], at: datetime) -> list[dict]:
    """Approximate dedupe: identical actions in successive captures within 5 s.

    Never count these as exact roaming events. Keep original capture evidence.
    Separate identical actions within the same capture remain distinct.
    """
    cutoff = at - RETENTION
    retained = [row for row in previous if _utc(row["estimated_at"]) >= cutoff]
    old = list(retained)
    used = set()
    for row in incoming:
        sig = tuple(row[k] for k in ("client_token", "old_state", "new_state", "reason", "radio"))
        when = _utc(row["estimated_at"])
        found = None
        for index, candidate in enumerate(old):
            if index in used:
                continue
            if sig == tuple(candidate[k] for k in ("client_token", "old_state", "new_state", "reason", "radio")) and abs(
                    (_utc(candidate["estimated_at"]) - when).total_seconds()) <= 5:
                found = index
                break
        if found is not None:
            used.add(found)
        elif when >= cutoff:
            retained.append(row)
    return sorted(retained, key=lambda row: row["estimated_at"], reverse=True)[:MAX_ROWS]


def save(db, device, parsed: dict | None, *, collected: datetime, failure: str | None = None):
    key = PREFIX + device.id
    existing = db.get(AppSetting, key)
    value = dict(existing.value or {}) if existing else {}
    # A controller reassignment must not expose an earlier cluster's client evidence.
    old = value.get("records", []) if value.get("legacy_site_id") == device.legacy_site_id else []
    rows = merge(old, parsed["records"] if parsed else [], collected)
    state = {"legacy_site_id": device.legacy_site_id, "records": rows,
             "last_attempt_at": collected.isoformat(),
             "state": parsed["state"] if parsed else "FAILED",
             "reason": failure, "last_success_at": collected.isoformat() if parsed else value.get("last_success_at"),
             "declared_records": parsed["declared_records"] if parsed else None,
             "truncated": parsed["truncated"] if parsed else None}
    if existing is None:
        db.add(AppSetting(key=key, value=state, updated_by="clientmatch-collector"))
    else:
        existing.value, existing.updated_at = state, collected
    db.flush()


def journey(db, client, *, since: datetime, now: datetime) -> dict:
    if client.simulated:
        return {"records": [], "coverage": {"state": "DEMO", "eligible_aps": 0, "fresh_aps": 0},
                "source": "DEVICE_REPORTED_CLIENTMATCH", "note": "Demo does not claim device-reported actions."}
    devices = list(db.scalars(select(InmDevice).where(
        InmDevice.legacy_site_id == client.site_id, InmDevice.family == "access_point",
        InmDevice.source_type == "REAL", InmDevice.lifecycle.not_in(("merged", "retired", "decommissioned")))))
    keys = {PREFIX + device.id: device for device in devices}
    settings = db.scalars(select(AppSetting).where(AppSetting.key.in_(keys))) if keys else []
    try:
        fingerprint = token(client.mac, client.site_id)
    except ValueError:
        return {"records": [], "source": "DEVICE_REPORTED_CLIENTMATCH",
                "coverage": {"state": "INVALID_CLIENT_ID", "eligible_aps": len(devices), "fresh_aps": 0},
                "note": "Client identifier cannot be matched safely."}
    records, fresh, seen = [], 0, 0
    for setting in settings:
        value = setting.value or {}
        if value.get("legacy_site_id") != client.site_id:
            continue
        seen += 1
        last = value.get("last_success_at")
        if last and value.get("state") == "COLLECTED" and now - _utc(last) <= timedelta(minutes=20):
            fresh += 1
        for row in value.get("records", []):
            if row.get("client_token") != fingerprint or not max(since, now - RETENTION) <= _utc(row["estimated_at"]) <= now:
                continue
            records.append({**{k: v for k, v in row.items() if k != "client_token"},
                            "ap": keys[setting.key].hostname or keys[setting.key].serial_number})
    records.sort(key=lambda row: row["estimated_at"], reverse=True)
    state = "NO_DATA" if not seen else "CURRENT" if fresh == len(devices) else "PARTIAL_OR_STALE"
    return {"records": records[:MAX_ROWS], "source": "DEVICE_REPORTED_CLIENTMATCH",
            "coverage": {"state": state, "eligible_aps": len(devices), "fresh_aps": fresh, "observed_aps": seen},
            "note": "AP ClientMatch action table; times estimated from device-reported ages. Actions are not complete or independently verified roam transitions."}
