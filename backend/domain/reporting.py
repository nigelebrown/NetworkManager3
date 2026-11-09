"""WP-11 / GAP-16 — executive summaries and signed, scoped exports (pure).

Do not invent KPIs and do not fill gaps with zeros: an unmeasured KPI is None with a reason.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from collections import Counter

SECRET_KEYS = re.compile(r"(pass(word)?|secret|token|psk|key|community|credential)", re.I)


def executive_summary(devices: list[dict], freshness: dict[str, str], recs: list[dict],
                      attributions: list[dict], vault: list[dict], sources: tuple[str, ...] = ("REAL",)) -> dict:
    """`sources` decides which provenance is counted (REAL, plus DEMO while a demo dataset is loaded)."""
    real = [d for d in devices if d.get("source_type") in sources]
    by_site: dict[str, Counter] = {}
    for d in real:
        c = by_site.setdefault(d.get("site") or "Unassigned", Counter())
        f = freshness.get(d["id"], "NEVER")
        c["total"] += 1
        c[{"FRESH": "reachable", "STALE": "stale", "GAP": "unreachable_or_gap"}.get(f, "never_polled")] += 1
    polled = [d for d in real if freshness.get(d["id"]) in ("FRESH", "STALE", "GAP")]
    poll_cov = round(100 * sum(freshness.get(d["id"]) == "FRESH" for d in real) / len(real), 1) if real else None
    reviewed = [r for r in recs if r.get("approval_state") not in (None, "ADVISORY")]
    return {
        "devices_real": sum(1 for d in devices if d.get("source_type") == "REAL"),
        "devices_counted": len(real), "sources_counted": list(sources),
        "by_site": {k: dict(v) for k, v in sorted(by_site.items())},
        "by_model": dict(Counter(d.get("model") or "unknown" for d in real)),
        "poll_coverage_percent": poll_cov,
        "poll_coverage_reason": None if real else "No real devices registered.",
        "devices_never_polled": len(real) - len(polled),
        "recommendations": {"total": len(recs), "reviewed": len(reviewed),
                            "by_state": dict(Counter(r.get("approval_state") for r in recs))},
        "attribution": dict(Counter(a.get("level") for a in attributions)),
        "vault": {"artifacts": len(vault),
                  "restore_verified": sum(1 for v in vault if v.get("restore_state") == "RESTORE_VERIFIED")},
        "kpis_not_measured": ["latency_ms", "loss_percent"],
    }


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("***" if SECRET_KEYS.search(str(k)) else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def mask_user(name: str | None, authorized: bool) -> str | None:
    if name is None or authorized:
        return name
    return name[:1] + "***"


def build_export(*, rows: list[dict], columns: list[str], meta: dict, signing_key: bytes) -> dict:
    """CSV body + checksum + HMAC signature over (meta, checksum)."""
    rows = redact(rows)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: ("" if r.get(c) is None else r.get(c)) for c in columns})
    body = buf.getvalue()
    checksum = hashlib.sha256(body.encode()).hexdigest()
    signed = json.dumps({"meta": meta, "sha256": checksum}, sort_keys=True).encode()
    sig = hmac.new(signing_key, signed, hashlib.sha256).hexdigest()
    return {"body": body, "sha256": checksum, "signature": sig, "meta": meta, "row_count": len(rows)}


def verify_export(export: dict, signing_key: bytes) -> bool:
    if hashlib.sha256(export["body"].encode()).hexdigest() != export["sha256"]:
        return False
    signed = json.dumps({"meta": export["meta"], "sha256": export["sha256"]}, sort_keys=True).encode()
    return hmac.compare_digest(hmac.new(signing_key, signed, hashlib.sha256).hexdigest(), export["signature"])
