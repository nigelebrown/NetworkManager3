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

PARSER_VERSION = "syslog-norm-1.1"   # 1.1 (rc18): Cisco IOS %FAC-SEV-MNEMONIC events
CORRELATION_WINDOW = timedelta(minutes=10)

# RFC3164/5424 priority prefix
_PRI = re.compile(r"^<(?P<pri>\d{1,3})>")
# Aruba CX style: "... hpe-config[1234]: Event|6801|LOG_INFO|AMM|-|User admin changed configuration ..."
_CX_EVENT = re.compile(r"Event\|(?P<code>\d+)\|(?P<sev>LOG_[A-Z]+)\|[^|]*\|[^|]*\|(?P<msg>.*)$")
# Aruba Instant style: "... cli[123]: <341004> <NOTI> ... User admin config change ..."
_IAP_EVENT = re.compile(r"<(?P<code>\d{6})>\s*<(?P<sev>[A-Z]+)>\s*(?P<msg>.*)$")
# Cisco IOS / IOS-XE (rc18, P10-02): "%SYS-5-CONFIG_I: Configured from console by admin on vty0 (10.1.1.1)"
_IOS_EVENT = re.compile(r"%(?P<fac>[A-Z0-9_]+)-(?P<sevn>[0-7])-(?P<mnem>[A-Z0-9_]+):\s*(?P<msg>.*)$")
_IOS_CONFIG_BY = re.compile(r"\bConfigured from \S+ by (?P<user>[A-Za-z0-9._@\-]{1,64})")
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
    ios = None
    if code is None:
        ios = _IOS_EVENT.search(line)
        if ios:
            code = f"{ios.group('fac')}-{ios.group('sevn')}-{ios.group('mnem')}"[:16]
            msg = ios.group("msg").strip()
            sev = SEVERITIES[int(ios.group("sevn"))]
    u = _USER.search(msg)
    if ios:
        by = _IOS_CONFIG_BY.search(msg)
        u = by or u
    is_change = bool(_CONFIG_WORDS.search(msg)) or bool(ios and ios.group("mnem") == "CONFIG_I")
    if transport not in ("TLS_MUTUAL", "TLS", "UDP_ALLOWLISTED"):
        raise ValueError("Unknown syslog transport")
    return NormalizedEvent(
        device_serial=device_serial, source_ip=source_ip, vendor=vendor, event_code=code,
        severity=sev, message=msg[:1000], user=u.group("user") if u else None,
        is_config_change=is_change,
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
              app_requester: str | None = None, clock_offset_s: float | None = None,
              clock_tolerance_s: float = 120.0) -> dict:
    """Return attribution with level, actor (or None) and the evidence used.

    clock_offset_s: measured device clock minus server clock. A device-timestamped change time is corrected by the
    offset; beyond the tolerance the result can never be VERIFIED (timestamps are not reliable evidence)."""
    change_at = _utc(change_at)
    drift = clock_offset_s is not None and abs(clock_offset_s) > clock_tolerance_s
    if clock_offset_s is not None and not app_change_id:
        change_at = change_at - timedelta(seconds=clock_offset_s)
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
    if clock_offset_s is not None:
        evidence["device_clock_offset_s"] = clock_offset_s
    if len(names) == 1 and device_event_user and device_event_user == names[0] and not drift:
        strict = [s for s in active if _utc(s.start) <= change_at
                  and (s.end is None or change_at <= _utc(s.end))]
        if len(strict) == 1:
            return {"level": "VERIFIED", "actor": names[0], "change_type": "DIRECT_DEVICE",
                    "evidence": evidence, "candidates": names}
    if names:
        return {"level": "CORRELATED", "actor": None, "change_type": "DIRECT_DEVICE",
                "evidence": evidence, "candidates": names,
                "uncertainty": (f"Device clock offset {clock_offset_s:+.0f}s exceeds tolerance; timestamps are "
                                "corrected but not proof of identity." if drift else
                                "Candidate session(s) found; identity not uniquely proven.")}
    return {"level": "UNKNOWN", "actor": None, "change_type": "DIRECT_DEVICE",
            "evidence": evidence, "candidates": [],
            "uncertainty": "No AAA/accounting evidence for this device at this time."}


def coverage(sources_seen: set[str]) -> dict:
    """Correlation coverage — absent sources are named, not hidden."""
    expected = ("syslog", "aaa", "app_audit")
    missing = [s for s in expected if s not in sources_seen]
    return {"present": sorted(sources_seen & set(expected)), "missing": missing,
            "complete": not missing}
