"""Digital-experience scoring (0-100). Pure functions, no I/O. Formula and thresholds: docs/SCORING.md.

Principles
  * Every input is either MEASURED (with a stated source) or UNAVAILABLE. Nothing is estimated.
  * Unavailable inputs are left out and the remaining weights are renormalized, and the result reports
    `coverage` (share of the model's weight that was measurable) and which inputs were missing and why.
  * If too little can be measured (coverage < min_coverage) there is no score at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import ExperienceConfig, Range


def lerp(value: float, r: Range) -> float:
    """Map a value onto 0-100 along r.worst -> r.best, clamped."""
    t = (value - r.worst) / (r.best - r.worst)
    return 100.0 * max(0.0, min(1.0, t))


def band_for(score: float | None, cfg: ExperienceConfig) -> str:
    if score is None:
        return "unavailable"
    return "good" if score >= cfg.bands.good_min else "fair" if score >= cfg.bands.fair_min else "poor"


@dataclass
class _In:
    name: str
    sub: float
    weight: float
    value: Any
    source: str


def _combine(ins: list[_In], missing: dict[str, str], nominal: dict[str, float], cfg: ExperienceConfig) -> dict[str, Any]:
    nominal_total = sum(nominal.values())
    measured_w = sum(i.weight for i in ins)
    coverage = measured_w / nominal_total if nominal_total > 0 else 0.0
    score = None
    if ins and measured_w > 0 and coverage >= cfg.min_coverage:
        score = int(round(sum(i.sub * i.weight for i in ins) / measured_w))
    return {
        "score": score, "band": band_for(score, cfg), "coverage": round(coverage, 2),
        "partial": bool(missing), "unavailable": missing,
        "inputs": {i.name: {"value": i.value, "subscore": round(i.sub), "source": i.source,
                            "weight": round(i.weight / measured_w, 3) if measured_w else 0} for i in ins},
    }


# ---- clients ---------------------------------------------------------------------------------------
@dataclass
class ClientMetrics:
    snr_db: float | None = None
    rssi_dbm: float | None = None
    speed_mbps: float | None = None
    retries_pct: float | None = None       # not collected yet -> always unavailable for now
    disconnects: int | None = None         # inferred from polls, over the configured window
    observed_minutes: float | None = None  # how long SIMS has seen this client


def score_client(m: ClientMetrics, cfg: ExperienceConfig) -> dict[str, Any]:
    c, w = cfg.client, cfg.client.weights
    nominal = {"signal": w.signal, "rate": w.rate, "stability": w.stability, "retries": w.retries}
    ins: list[_In] = []
    missing: dict[str, str] = {}

    if w.signal > 0:
        if m.snr_db is not None:
            ins.append(_In("signal", lerp(m.snr_db, c.snr), w.signal, f"{m.snr_db:g} dB SNR", "AP-reported"))
        elif m.rssi_dbm is not None:
            ins.append(_In("signal", lerp(m.rssi_dbm, c.rssi), w.signal, f"{m.rssi_dbm:g} dBm RSSI", "AP-reported"))
        else:
            missing["signal"] = "the AP did not report SNR or RSSI"
    if w.rate > 0:
        if m.speed_mbps is not None:
            ins.append(_In("rate", lerp(m.speed_mbps, c.rate_mbps), w.rate, f"{m.speed_mbps:g} Mbps", "AP-reported"))
        else:
            missing["rate"] = "the AP did not report a connection speed"
    if w.stability > 0:
        need = c.stability.min_observation_minutes
        if m.disconnects is None or m.observed_minutes is None:
            missing["stability"] = "no connection history"
        elif m.observed_minutes < need:
            missing["stability"] = f"client observed for less than {need} min"
        else:
            sub = lerp(m.disconnects, Range(worst=c.stability.worst_disconnects, best=0))
            ins.append(_In("stability", sub, w.stability,
                           f"{m.disconnects} disconnect(s) in {c.stability.window_hours} h", "inferred from polls"))
    if w.retries > 0:
        if m.retries_pct is not None:
            ins.append(_In("retries", lerp(m.retries_pct, c.retries_pct), w.retries, f"{m.retries_pct:g}% retries", "AP-reported"))
        else:
            missing["retries"] = "retransmission rate is not collected yet"
    return _combine(ins, missing, nominal, cfg)


# ---- access points ------------------------------------------------------------------------------------
@dataclass
class ApMetrics:
    status: str = "up"
    client_scores: list[int] | None = None  # experience scores of the AP's connected clients
    util_pct: float | None = None           # worst of the radios that report utilization
    client_count: int | None = None
    cpu_pct: float | None = None
    memory_pct: float | None = None


def score_ap(m: ApMetrics, cfg: ExperienceConfig) -> dict[str, Any]:
    if m.status != "up":
        return {"score": None, "band": "offline", "coverage": 0.0, "partial": True,
                "unavailable": {"all": "the AP is not up"}, "inputs": {}}
    a, w = cfg.ap, cfg.ap.weights
    nominal = {"clients": w.clients, "utilization": w.utilization, "load": w.load, "resources": w.resources}
    ins: list[_In] = []
    missing: dict[str, str] = {}
    if w.clients > 0:
        if m.client_scores:
            avg = sum(m.client_scores) / len(m.client_scores)
            ins.append(_In("clients", avg, w.clients, f"mean {avg:.0f} over {len(m.client_scores)} client(s)", "derived from client scores"))
        else:
            missing["clients"] = "no scored clients on this AP"
    if w.utilization > 0:
        if m.util_pct is not None:
            ins.append(_In("utilization", lerp(m.util_pct, a.utilization_pct), w.utilization, f"{m.util_pct:g}% busy", "AP-reported"))
        else:
            missing["utilization"] = "channel utilization is not collected yet"
    if w.load > 0:
        if m.client_count is not None:
            ins.append(_In("load", lerp(m.client_count, a.clients_per_ap), w.load, f"{m.client_count} clients", "AP-reported"))
        else:
            missing["load"] = "client count unavailable"
    if w.resources > 0:
        subs = [lerp(v, r) for v, r in ((m.cpu_pct, a.cpu_pct), (m.memory_pct, a.memory_pct)) if v is not None]
        if subs:
            ins.append(_In("resources", min(subs), w.resources, f"CPU {m.cpu_pct}% / mem {m.memory_pct}%", "AP-reported"))
        else:
            missing["resources"] = "CPU/memory not reported"
    return _combine(ins, missing, nominal, cfg)


# ---- aggregates -----------------------------------------------------------------------------------------
def summarize(scores: list[int | None], cfg: ExperienceConfig) -> dict[str, Any]:
    """Site/network summary: mean of the clients that could be scored, plus the good/fair/poor split."""
    got = [s for s in scores if s is not None]
    bands = [band_for(s, cfg) for s in got]
    mean = round(sum(got) / len(got), 1) if got else None
    return {"score": mean, "band": band_for(mean, cfg), "good": bands.count("good"), "fair": bands.count("fair"),
            "poor": bands.count("poor"), "unscored": len(scores) - len(got)}
