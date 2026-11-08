"""Evidence-based printer metrics. All arithmetic intentionally pure/testable.

Never interchange sheets, sides, impressions or logical pages. No estimates
are presented as measured values. Colour-cartridge cycle deltas are *device
meter deltas*, NOT proof that those pages consumed that colour.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any


def _rounded(value: Decimal, digits: int = 2) -> float:
    quant = Decimal("1").scaleb(-digits)
    return float(value.quantize(quant, rounding=ROUND_HALF_UP))


def meter_change(previous: int | None, current: int | None) -> dict[str, Any]:
    if previous is None or current is None:
        return {"delta": None, "quality": "INSUFFICIENT_DATA", "reason": "Both cumulative readings are required"}
    if previous < 0 or current < 0:
        return {"delta": None, "quality": "INVALID", "reason": "Negative counter"}
    if current < previous:
        return {"delta": None, "quality": "RESET_OR_ROLLOVER", "reason": "Meter declined; cannot infer usage"}
    return {"delta": current - previous, "quality": "CALCULATED", "reason": None}


def cycle_analysis(cycle: Any) -> dict[str, Any]:
    """Accepts a SQLAlchemy cycle or a simple mock with the same attributes."""
    out: dict[str, Any] = {
        "cycle_id": cycle.id,
        "status": cycle.status,
        "meter_kind": cycle.meter_kind,
        "quantity_label": "device counter delta during consumable lifecycle",
        "start_meter": cycle.start_meter,
        "end_meter": cycle.end_meter,
        "impressions_during_cycle": None,
        "cost_per_1000_impressions": None,
        "cost_per_impression": None,
        "declared_yield_ratio_percent": None,
        "measured_ink_ml_per_impression": None,
        "pages_per_ml_added": None,
        "currency": cycle.currency,
        "quality": "INSUFFICIENT_DATA",
        "warnings": [],
    }
    if cycle.status != "closed":
        out["warnings"].append("Open cycle: cost/yield pending removal or subsequent refill reading")
        return out
    result = meter_change(cycle.start_meter, cycle.end_meter)
    out["quality"] = result["quality"]
    if result["delta"] is None:
        out["warnings"].append(result["reason"])
        return out
    count = result["delta"]
    # The name "impressions" is valid only for an impressions meter. Other meters
    # are independently meaningful but never relabelled as impressions.
    out["counter_delta"] = count
    if "impressions" in cycle.meter_kind:
        out["impressions_during_cycle"] = count
    if count <= 0:
        out["warnings"].append("Zero counter delta: per-impression ratios unavailable")
        return out
    if cycle.meter_kind not in ("impressions_total", "bw_impressions", "colour_impressions"):
        out["warnings"].append("The selected meter is not an impression count; cost per impression unavailable")
        return out

    out["quality"] = "CALCULATED_PROXY"
    out["warnings"].append(
        "Counter delta reflects all device impressions for the selected meter; "
        "it does not prove the number that consumed this specific cartridge or colour."
    )
    if getattr(cycle, "partial_refill", False):
        out["warnings"].append("Partial refill: cartridge/replacement yield may not be comparable with ISO yield")
    if cycle.cost_amount is not None and Decimal(cycle.cost_amount) >= 0:
        per = Decimal(cycle.cost_amount) / Decimal(count)
        out["cost_per_impression"] = _rounded(per, 4)
        out["cost_per_1000_impressions"] = _rounded(per * 1000, 2)
    if cycle.declared_yield is not None and cycle.declared_yield > 0:
        out["declared_yield_ratio_percent"] = _rounded(Decimal(count) * 100 / Decimal(cycle.declared_yield), 1)
        out["warnings"].append("Manufacturer ISO yield and actual workload/coverage may differ")
    consumed = getattr(cycle, "measured_consumed_ml", None)
    added = getattr(cycle, "quantity_added_ml", None)
    if consumed is not None and Decimal(consumed) >= 0:
        out["measured_ink_ml_per_impression"] = _rounded(Decimal(consumed) / Decimal(count), 5)
        out["warnings"].append("Measured ink volume divided by device impressions; not document coverage")
    elif added is not None and Decimal(added) > 0:
        out["pages_per_ml_added"] = _rounded(Decimal(count) / Decimal(added), 2)
        out["warnings"].append("Refill-based proxy only; added volume is NOT assumed consumed")
    return out


def low_supply_alert(percent: Decimal | int | float | None, *, threshold: float = 15) -> str | None:
    if percent is None or float(percent) < 0 or float(percent) > 100:
        return None
    if float(percent) <= 5:
        return "CRITICAL"
    if float(percent) <= threshold:
        return "LOW"
    return None


def compare_model_cycles(rows: list[tuple[str, Any]]) -> list[dict]:
    """Evidence-only simple peer comparison. No model rankings without ≥3 valid cycles per model."""
    groups: dict[str, list[float]] = {}
    for model, cycle in rows:
        record = cycle_analysis(cycle)
        val = record["cost_per_1000_impressions"]
        if val is not None and val >= 0:
            groups.setdefault(model, []).append(val)
    return [
        {
            "model": model,
            "verified_cycles": len(values),
            "mean_consumable_cost_per_1000_impressions": round(sum(values) / len(values), 2) if len(values) >= 3 else None,
            "quality": "COMPARABLE_COST_ONLY" if len(values) >= 3 else "INSUFFICIENT_EVIDENCE",
        }
        for model, values in sorted(groups.items())
    ]
