"""Resolve the sending device of a syslog line when the RFC 3164 header was mis-parsed.

Year-in-header BSD syslog (rc18, checked with real packets through the shipped Alloy pipeline):
  * ``<134>Nov 14 19:31:02 2026 10.103.5.20 stm[3442]: …`` (year after the time): the RFC 3164 parser reports
    host ``2026`` and the message starts with the real host followed by an ``app[pid]:`` tag;
  * ``<134>Nov 14 2026 19:31:02 10.103.5.20 cli[…]`` (year before the time): Alloy's parser rejects the packet
    (counted in loki_source_syslog_parsing_errors_total); a lenient parser leaves the time at the start of the message.
Only those recognisable shapes are corrected; anything else stays as reported, so a device is never mapped by
guesswork. configs/alloy/config.alloy applies the same rule before the line is stored.
"""
from __future__ import annotations

import re

_YEAR = re.compile(r"^(19|20)\d{2}$")
_TIME = re.compile(r"^\d{1,2}:\d{2}:\d{2}$")
_AFTER_TIME = re.compile(r"^\s*\d{1,2}:\d{2}:\d{2}(?:\.\d+)?\s+([A-Za-z0-9][A-Za-z0-9._:-]{0,99})\b")
_TAGGED = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._:-]{0,99})\s+[A-Za-z0-9_./-]+(?:\[\d+\])?:")
_FULL_HEADER = re.compile(r"^(?:<\d{1,3}>)?[A-Z][a-z]{2}\s+\d{1,2}\s+(?:(?:19|20)\d{2}\s+)?\d{1,2}:\d{2}:\d{2}\s+"
                          r"([A-Za-z0-9][A-Za-z0-9._:-]{0,99})\s")


def resolve_host(host: str | None, line: str | None) -> tuple[str | None, str | None]:
    """Returns (host, correction). correction is None when the reported host was used unchanged."""
    h = (host or "").strip()
    text = line or ""
    if h and not (_YEAR.match(h) or _TIME.match(h)):
        return h, None
    m = _AFTER_TIME.match(text) or _FULL_HEADER.match(text) or (_TAGGED.match(text) if _YEAR.match(h) else None)
    if m and not _YEAR.match(m.group(1)):
        return m.group(1), ("YEAR_AS_HOST_CORRECTED" if _YEAR.match(h) else
                            "TIME_AS_HOST_CORRECTED" if _TIME.match(h) else "HOST_FROM_HEADER")
    return (h or None), ("YEAR_AS_HOST_UNRESOLVED" if _YEAR.match(h) else None)
