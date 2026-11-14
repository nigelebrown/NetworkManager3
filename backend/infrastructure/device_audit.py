"""Bounded, secret-redacted device log normalization.

Vendor APIs return firmware-specific dictionaries, not stable audit records.
We expose selected attributes only, never raw device JSON or HTTP headers.
No inferred identity, action, or timestamps are fabricated.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

MAX_DEVICE_ENTRIES = 100
MAX_MESSAGE_CHARS = 1200
MAX_LOG_BYTES = 256_000
_PRIVATE = re.compile(
    r"(?i)\b(password|passwd|passphrase|secret|token|authorization|api[_-]?key|"
    r"radius[_-]?key|sid|session[_-]?id)\b\s*[:=]\s*[^\s,;]+"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+")
_URL_CRED = re.compile(r"(?i)(https?://)([^\s/@:]+):([^\s/@]+)@")
_QUERY_CRED = re.compile(
    r"(?i)([?&](?:sid|password|passwd|token|secret|api_key|key)=)[^&#\s]+"
)
_AP_LINE = re.compile(r"^(?P<date>[A-Z][a-z]{2}\s+\d{1,2}\s+\d\d:\d\d:\d\d)\s+(?P<message>.*)$")


def sanitize(value: Any) -> str:
    """Normalize one log field without leaking credential material."""
    text = str(value or "")[:MAX_MESSAGE_CHARS * 4]
    text = _URL_CRED.sub(r"\1[REDACTED]@", text)
    text = _QUERY_CRED.sub(r"\1[REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _PRIVATE.sub(lambda m: m.group(1) + "=[REDACTED]", text)
    return text[:MAX_MESSAGE_CHARS]


def _row(*, source: str, message: Any, timestamp: Any = None, level: Any = None,
         actor: Any = None, action: Any = None) -> dict:
    clean = sanitize(message)
    stamp = sanitize(timestamp) if timestamp is not None else None
    identity = f"{source}|{stamp}|{clean}"
    return {"id": hashlib.sha256(identity.encode()).hexdigest()[:24], "source": source,
            "timestamp": stamp, "level": sanitize(level) if level is not None else None,
            "actor": sanitize(actor) if actor is not None else None,
            "action": sanitize(action) if action is not None else None,
            "message": clean}


def normalize_cx(payload: Any, *, kind: str = "audit", limit: int = 50) -> list[dict]:
    """Extract AOS-CX /logs/audit or /logs/event records without exposing unknown fields."""
    if kind not in ("audit", "event") or not 1 <= limit <= MAX_DEVICE_ENTRIES:
        raise ValueError("Unsupported kind or unbounded request")
    if isinstance(payload, dict):
        records = payload.get("items") or payload.get("logs") or payload.get("entries")
        if not isinstance(records, (dict, list)):
            records = payload
    else:
        records = payload
    if isinstance(records, dict):
        # Nested API resource dictionaries are ordered as delivered by the switch.
        records = list(records.values())
    if not isinstance(records, list):
        raise ValueError("Unexpected AOS-CX log payload type")
    out = []
    for item in records[:MAX_DEVICE_ENTRIES]:
        if not isinstance(item, dict):
            continue
        msg = next((item[key] for key in ("message", "data", "text", "description")
                    if isinstance(item.get(key), (str, int, float))), None)
        if msg is None:
            continue
        out.append(_row(source="cx." + kind, message=msg,
                        timestamp=item.get("timestamp") or item.get("time") or item.get("created_at"),
                        level=item.get("severity") or item.get("level"),
                        actor=item.get("user") or item.get("username"),
                        action=item.get("service") or item.get("event_id")))
        if len(out) >= limit:
            break
    return out


def normalize_instant(payload: Any, *, kind: str = "system", limit: int = 50) -> list[dict]:
    """Parse a bounded Instant 'show log' response. No fabricated timestamps or actors."""
    if kind not in ("system", "security", "user") or not 1 <= limit <= MAX_DEVICE_ENTRIES:
        raise ValueError("Unsupported kind or unbounded request")
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Aruba Instant show-cmd payload type")
    raw = payload.get("Command output") or payload.get("command output") or payload.get("output")
    if isinstance(raw, list):
        lines = [str(line) for line in raw]
    elif isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_LOG_BYTES:
            raise ValueError("Device log reply is too large")
        lines = raw.splitlines()
    else:
        raise ValueError("No log lines in Instant show-cmd reply")
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        m = _AP_LINE.match(line)
        out.append(_row(source="instant." + kind, message=m.group("message") if m else line,
                        timestamp=m.group("date") if m else None))
    return out
