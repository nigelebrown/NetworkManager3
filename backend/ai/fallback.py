"""Deterministic assistant fallback used when the on-prem model is disabled, unreachable, busy or times out.

It routes a question to one allow-listed read-only tool by keywords and words the answer from a template.
Nothing here can call a write path; misleading instructions in the question ("set the channel to 80 MHz",
"ignore your rules") are treated as text to route, never as commands.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database.models import AccessPoint, ClientRecord, Site, SiteSample

ROUTES = [  # first match wins; order = most specific first
    (r"\b(spacing|overlap|co-?channel|interferen|coverage|placement|rf\b|optimi[sz])", "rf_placement"),
    (r"\b(disconnect|drop(ped|s)?|kicked)", "disconnects"),
    (r"\broam", "roaming"),
    (r"\b(alert|alarm|incident|warning)s?\b", "active_alerts"),
    (r"\b(ssid|wlan|network name)s?\b", "ssid_breakdown"),
    (r"\b(trend|over time|history|getting (better|worse)|last \d+ ?h)", "experience_trend"),
    (r"\b(worst|poor|lowest|bad|unhappy|struggling)\b.*\bclient|\bclients?\b.*\b(worst|poor|lowest|bad)", "worst_clients"),
    (r"\b(ap|aps|access points?|offline|down)\b", "ap_overview"),
]
WRITE_INTENT = re.compile(r"\b(set|change|configure|apply|enable|disable|reboot|restart|delete|push|write|80 ?mhz|ignore (your|the|all) (rules|instructions))\b", re.I)


def route(question: str) -> str | None:
    q = question.lower()
    for pattern, tool in ROUTES:
        if re.search(pattern, q):
            return tool
    return None


def freshness(db: Session, ids: list[int]) -> dict:
    latest = db.scalar(select(func.max(SiteSample.ts)).where(SiteSample.site_id.in_(ids))) if ids else None
    seen = db.scalar(select(func.max(ClientRecord.last_seen)).where(ClientRecord.site_id.in_(ids))) if ids else None
    t = max([x for x in (latest, seen) if x is not None], default=None)
    if t is not None and t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - t).total_seconds() / 60 if t else None
    return {"last_observation": t.isoformat() if t else None,
            "age_minutes": round(age, 1) if age is not None else None,
            "state": "NO_DATA" if t is None else "FRESH" if age <= 15 else "STALE"}


def citations(db: Session, ids: list[int], tool: str | None) -> list[dict]:
    sites = [s.name for s in db.scalars(select(Site).where(Site.id.in_(ids or [-1])))]
    fr = freshness(db, ids)
    out = [{"source": f"tool:{tool}" if tool else "site_facts", "scope": sites, "as_of": fr["last_observation"],
            "freshness": fr["state"]}]
    links = {"ap_overview": "/aps", "worst_clients": "/experience", "disconnects": "/clients", "roaming": "/clients",
             "active_alerts": "/alerts", "experience_trend": "/experience", "ssid_breakdown": "/clients",
             "rf_placement": "/rf-health"}
    if tool in links:
        out.append({"source": "evidence", "href": links[tool]})
    return out


def _fmt_list(rows, render, empty="none"):
    return "\n".join(f"- {render(r)}" for r in rows) if rows else f"- {empty}"


def answer(tool: str | None, facts: dict[str, Any], question: str) -> str:
    preface = ("Note: this assistant is read-only and cannot change devices; I have only reported data.\n"
               if WRITE_INTENT.search(question) else "")
    if tool is None:
        return preface + "I can't answer that from the collected data."
    if tool == "ap_overview":
        aps = facts.get("aps", [])
        down = [a for a in aps if a.get("status") != "up"]
        return preface + (f"{len(aps)} access points; {len(down)} not up.\n" +
                          _fmt_list(aps[:15], lambda a: f"{a['ap']}: {a['status']}, {a['clients']} clients, score {a['score'] if a['score'] is not None else 'n/a'}"))
    if tool == "worst_clients":
        rows = facts.get("worst_clients", [])
        return preface + "Lowest experience scores:\n" + _fmt_list(rows, lambda c: f"{c.get('client')}: score {c.get('score')} on {c.get('ap')} ({c.get('ssid')})")
    if tool == "disconnects":
        return preface + (f"{facts.get('total_disconnects', 0)} disconnects in {facts.get('window_hours')} h "
                          f"(inferred from polling).\nBy AP:\n" + _fmt_list(facts.get("by_ap", []), lambda x: f"{x[0]}: {x[1]}"))
    if tool == "roaming":
        return preface + (f"{facts.get('total_roams', 0)} roams in {facts.get('window_hours')} h.\nTop paths:\n" +
                          _fmt_list(facts.get("paths", []), lambda x: f"{x[0]}: {x[1]}"))
    if tool == "active_alerts":
        rows = facts.get("alerts", [])
        return preface + f"{len(rows)} open alert(s):\n" + _fmt_list(rows, lambda a: f"{a.get('severity')} {a.get('kind')} ({a.get('status')}) {a.get('ap') or ''}".strip())
    if tool == "experience_trend":
        if not facts.get("samples"):
            return preface + f"No scored experience samples in the last {facts.get('window_hours')} h."
        return preface + (f"Experience over {facts['window_hours']} h ({facts['samples']} samples): first {facts['first']}, "
                          f"latest {facts['last']}, range {facts['min']}–{facts['max']}, average {facts['average']}.")
    if tool == "ssid_breakdown":
        return preface + "Per SSID:\n" + _fmt_list(facts.get("ssids", []), lambda s: f"{s.get('ssid')}: {s.get('clients')} clients, avg score {s.get('average_score')}")
    if tool == "rf_placement":
        return preface + (facts.get("interpretation", "") + "\n" +
                          _fmt_list(facts.get("aps", [])[:10], lambda a: f"{a.get('ap')}: {a.get('findings') or a.get('channel') or 'no finding'}") +
                          "\nNot measured: " + ", ".join(facts.get("not_measured", [])))
    return preface + "Data retrieved; see facts."


def summary_text(facts: dict[str, Any]) -> str:
    aps = facts.get("aps", [])
    down = [a for a in aps if a.get("status") != "up"]
    exp = facts.get("experience") or {}
    return (f"Sites: {', '.join(facts.get('sites', [])) or 'none'}. {len(aps)} APs ({len(down)} not up), "
            f"{facts.get('clients_connected', 0)} connected clients, experience {exp.get('score', 'n/a')} ({exp.get('band', 'unscored')}). "
            f"{len(facts.get('active_alerts', []))} open alerts; {facts.get('disconnects', {}).get('total_disconnects', 0)} disconnects "
            f"in {facts.get('window_hours')} h. Not measured: {', '.join(facts.get('not_measured', []))}.")


def recommend_text(facts: dict[str, Any]) -> str:
    out = []
    down = [a["ap"] for a in facts.get("aps", []) if a.get("status") != "up"]
    if down:
        out.append(f"Check uplink/PoE for offline APs: {', '.join(down[:5])}.")
    if facts.get("active_alerts"):
        out.append(f"Review {len(facts['active_alerts'])} open alert(s).")
    poor = [c for c in facts.get("worst_clients", []) if (c.get("score") or 100) < 60]
    if poor:
        out.append(f"Investigate {len(poor)} poorly scoring client(s), starting with {poor[0].get('client')} on {poor[0].get('ap')}.")
    if not out:
        out.append("No rule-based action stands out in the collected data.")
    return "Rule-based suggestions (no model available):\n" + "\n".join(f"- {x}" for x in out)
