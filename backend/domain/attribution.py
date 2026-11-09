"""WP-03 / GAP-03, GAP-04 — syslog normalization and change attribution (pure).

Attribution levels (C2):
  VERIFIED   — exactly one AAA session for a named user on the same device serial,
               active at the config-commit time, AND the device's own config event names
               that same user.
  CORRELATED — a candidate session exists with recorded evidence but the link is not unique
               or the device event does not name the user.
  UNKNOWN    — no defensible identity. Never inferred from who last logged into the app.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

PARSER_VERSION = "syslog-norm-1.0"
CORRELATION_WINDOW = timedelta(minutes=10)

# RFC3164/5424 priority prefix
_PRI = re.compile(r"^<(?P<pri>\d{1,3})>")
# Aruba CX style: "... hpe-config[1234]: Event|6801|LOG_INFO|AMM|-|User admin changed configuration ..."
_CX_EVENT = re.compile(r"Event\|(?P<code>\d+)\|(?P<sev>LOG_[A-Z]+)\|[^|]*\|[^|]*\|(?P<msg>.*)$")
# Aruba Instant style: "... cli[123]: <341004> <NOTI> ... User admin config change ..."
_IAP_EVENT = re.compile(r"<(?P<code>\d{6})>\s*<(?P<sev>[A-Z]+)>\s*(?P<msg>.*)$")
_USER = re.compile(r"\b[Uu]ser[:= ]+(?P<user>[A-Za-z0-9._@\-]{1,64})")
_CONFIG_WORDS = re.compile(r"config(uration)?\s+(change|commit|saved|modified|updated)|"
                           r"running-config|write memory|copy running", re.I)
SEVERITIES = ("emergency", "alert", "critical", "error", "warning", "notice", "info", "debug")


@dataclass(frozen=True)
class NormalizedEvent:
    device_serial: str | None
    source_ip: str
    vendor: str
    event_code: str | None
    severity: str
    message: str
    user: str | None
    is_config_change: bool
    observed_at: datetime
    parser_version: str
    transport_quality: str      # TLS_MUTUAL / TLS / UDP_ALLOWLISTED
    raw_hash: str


def normalize(raw: str, *, source_ip: str, device_serial: str | None, vendor: str,
              received_at: datetime, transport: str = "UDP_ALLOWLISTED") -> NormalizedEvent:
    """Parse one syslog line. Unknown formats are kept (message preserved), never dropped."""
    line = raw.strip()[:4096]
    sev = "info"
    m = _PRI.match(line)
    if m:
        sev = SEVERITIES[int(m.group("pri")) % 8]
        line = line[m.end():]
    code = None
    msg = line
    for pattern in (_CX_EVENT, _IAP_EVENT):
        e = pattern.search(line)
        if e:
            code = e.group("code")
            msg = e.group("msg").strip()
            s = e.group("sev").lower().replace("log_", "")
            sev = {"noti": "notice", "warn": "warning", "err": "error", "crit": "critical",
                   "emerg": "emergency"}.get(s, s if s in SEVERITIES else sev)
            break
    u = _USER.search(msg)
    if transport not in ("TLS_MUTUAL", "TLS", "UDP_ALLOWLISTED"):
        raise ValueError("Unknown syslog transport")
    return NormalizedEvent(
        device_serial=device_serial, source_ip=source_ip, vendor=vendor, event_code=code,
        severity=sev, message=msg[:1000], user=u.group("user") if u else None,
        is_config_change=bool(_CONFIG_WORDS.search(msg)),
        observed_at=received_at if received_at.tzinfo else received_at.replace(tzinfo=timezone.utc),
        parser_version=PARSER_VERSION, transport_quality=transport,
        raw_hash=hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest())


@dataclass(frozen=True)
class AuthSession:
    id: str
    device_serial: str
    username: str
    start: datetime
    end: datetime | None
    source: str                 # RADIUS / TACACS+ / CX_ACCOUNTING


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def attribute(*, device_serial: str, change_at: datetime, device_event_user: str | None,
              sessions: list[AuthSession], app_change_id: str | None = None,
              app_requester: str | None = None) -> dict:
    """Return attribution with level, actor (or None) and the evidence used."""
    change_at = _utc(change_at)
    if app_change_id:
        # Application-originated change: actor is the audited requester, not an inference.
        return {"level": "VERIFIED", "actor": app_requester, "change_type": "APPLICATION",
                "evidence": {"app_change_id": app_change_id}, "candidates": []}
    active = [s for s in sessions if s.device_serial == device_serial
              and _utc(s.start) - CORRELATION_WINDOW <= change_at
              and (s.end is None or change_at <= _utc(s.end) + CORRELATION_WINDOW)]
    names = sorted({s.username for s in active})
    evidence = {"sessions": [s.id for s in active], "device_event_user": device_event_user,
                "window_minutes": int(CORRELATION_WINDOW.total_seconds() // 60)}
    if len(names) == 1 and device_event_user and device_event_user == names[0]:
        strict = [s for s in active if _utc(s.start) <= change_at
                  and (s.end is None or change_at <= _utc(s.end))]
        if len(strict) == 1:
            return {"level": "VERIFIED", "actor": names[0], "change_type": "DIRECT_DEVICE",
                    "evidence": evidence, "candidates": names}
    if names:
        return {"level": "CORRELATED", "actor": None, "change_type": "DIRECT_DEVICE",
                "evidence": evidence, "candidates": names,
                "uncertainty": "Candidate session(s) found; identity not uniquely proven."}
    return {"level": "UNKNOWN", "actor": None, "change_type": "DIRECT_DEVICE",
            "evidence": evidence, "candidates": [],
            "uncertainty": "No AAA/accounting evidence for this device at this time."}


def coverage(sources_seen: set[str]) -> dict:
    """Correlation coverage — absent sources are named, not hidden."""
    expected = ("syslog", "aaa", "app_audit")
    missing = [s for s in expected if s not in sources_seen]
    return {"present": sorted(sources_seen & set(expected)), "missing": missing,
            "complete": not missing}
