"""WP-09 / GAP-13 — per-device optimization policy (pure).

Owner directive (2026-11-09, production read/write): automatic optimization is a Phase 9 capability,
DEFAULT OFF. A device can act automatically only when ALL of these hold: the owner feature flag
INM_AUTOMATIC_OPTIMIZATION=true (a restriction, never an enabler of any device), an explicit per-device
DEVICE_OPT_IN enrollment with an independent approver, expiry, allowlisted operations and limits,
hardware certification of the operation, a verified backup, fresh metrics, cooldown/budget, and no global
lock or emergency stop. Nothing is inherited from site, model, template or group.

* Every device is RECOMMEND_ONLY by default. Nothing is inherited from site/model/template/group.
* Modes: RECOMMEND_ONLY, SHADOW, DEVICE_OPT_IN. DEVICE_OPT_IN is an intent, not a bypass.
* Global write lock and emergency stop are evaluated FIRST and deny every device.
* Authorization is evaluated at execution time; any missing evidence = DENY.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .change_control import PolicyViolation, validate_operation

MODES = ("RECOMMEND_ONLY", "SHADOW", "DEVICE_OPT_IN")
STATUSES = ("OFF", "PENDING_APPROVAL", "ARMED", "PAUSED", "REVOKED", "EXPIRED")
AUTOMATABLE_OPS = {"ap.radio.channel", "ap.radio.width", "ap.radio.tx_power"}
MAX_POLICY_DAYS = 90
REVOKE_TRIGGERS = ("EXPIRED", "DEVICE_REPLACED", "FIRMWARE_CHANGED", "CERTIFICATION_LOST",
                   "SITE_TRANSFERRED", "CONFIG_DRIFT", "POLICY_EDITED", "QUALITY_DEGRADED",
                   "BACKUP_FAILED", "EMERGENCY_STOP", "READBACK_FAILED", "AUDIT_INTEGRITY_LOST")


def default_state(device_id: str) -> dict:
    """What every device — including newly discovered, transferred and replacement devices — gets."""
    return {"device_id": device_id, "mode": "RECOMMEND_ONLY", "status": "OFF", "badge": "AUTO OFF",
            "inherited": False}


def validate_enrollment_request(req: dict, *, device: dict, now: datetime) -> dict:
    """Validate an explicit per-device enrollment request. No 'all settings', no 'all devices'."""
    if req.get("device_id") != device["id"] or req.get("serial") != device["serial"]:
        raise PolicyViolation("Device ID and serial must exactly match the selected device.")
    if req.get("mode") not in ("SHADOW", "DEVICE_OPT_IN"):
        raise PolicyViolation("Enrollment mode must be SHADOW or DEVICE_OPT_IN.")
    ops = req.get("allowed_operation_types") or []
    if not ops or "*" in ops or "all" in [str(o).lower() for o in ops]:
        raise PolicyViolation("Choose specific operations; there is no 'all settings' option.")
    bad = [o for o in ops if o not in AUTOMATABLE_OPS]
    if bad:
        raise PolicyViolation("Not automatable: " + ", ".join(bad))
    if device.get("family") != "access_point":
        raise PolicyViolation("Only access-point RF operations are defined for automation.")
    c = req.get("value_constraints") or {}
    widths = c.get("allowed_widths_mhz", [20])
    if not widths or any(w not in (20, 40) for w in widths):
        raise PolicyViolation("Allowed widths may only be 20 and/or 40 MHz.")
    expires = req.get("expires_at_utc")
    if not isinstance(expires, datetime):
        raise PolicyViolation("Expiry is required.")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if not now < expires <= now + timedelta(days=MAX_POLICY_DAYS):
        raise PolicyViolation(f"Expiry must be in the future and within {MAX_POLICY_DAYS} days.")
    cd = req.get("cooldown_seconds", 0)
    if not isinstance(cd, int) or cd < 3600:
        raise PolicyViolation("Cooldown must be at least 3600 seconds.")
    mpd = req.get("max_actions_per_day", 0)
    if not isinstance(mpd, int) or not 1 <= mpd <= 4:
        raise PolicyViolation("Max actions per day must be 1..4.")
    return dict(req, expires_at_utc=expires)


def check_independent_approval(requester: str, approver: str) -> None:
    if not approver or requester.casefold() == approver.casefold():
        raise PolicyViolation("Enrollment needs a named independent approver (no self-approval).")


def evaluate_action(*, now: datetime, feature_enabled: bool, global_write_lock: bool, emergency_stop: bool,
                    enrollment: dict | None, device: dict, proposed_op: dict,
                    observation_fresh: bool, backup_verified: bool,
                    adapter_certified: bool, last_action_at: datetime | None,
                    actions_today: int, quality_degraded: bool) -> dict:
    """Execution-time decision for one proposed automated action on one device."""
    guards: list[tuple[str, bool, str]] = []

    def g(name, ok, why=""):
        guards.append((name, bool(ok), why))
    g("automatic_optimization_feature_enabled", feature_enabled,
      "Automatic optimization feature is off (INM_AUTOMATIC_OPTIMIZATION).")
    # Global locks: they dominate every enrollment.
    g("global_write_lock_off", not global_write_lock, "Global device-write lock is on.")
    g("emergency_stop_clear", not emergency_stop, "Emergency stop is active.")
    e = enrollment
    g("enrollment_exists", e is not None, "Device is not enrolled (default AUTO OFF).")
    if e is not None:
        g("enrollment_is_this_device", e.get("device_id") == device["id"]
          and e.get("immutable_serial") == device["serial"], "Enrollment device/serial mismatch.")
        g("mode_device_opt_in", e.get("mode") == "DEVICE_OPT_IN", "Mode is not DEVICE_OPT_IN (simulation only).")
        g("status_armed", e.get("status") == "ARMED", f"Enrollment status is {e.get('status')}.")
        exp = e.get("expires_at_utc")
        if isinstance(exp, datetime) and exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        g("not_expired", isinstance(exp, datetime) and now < exp, "Enrollment expired.")
        g("firmware_bound", e.get("firmware_bound") == device.get("firmware"),
          "Device firmware changed since enrollment.")
        g("independent_approver", bool(e.get("independent_approver_id"))
          and e.get("independent_approver_id") != e.get("requester_id"), "No independent approver.")
        g("operation_allowed", proposed_op.get("op") in (e.get("allowed_operation_types") or []),
          "Operation not in this device's allowlist.")
        try:
            validate_operation(proposed_op, family=device.get("family", ""),
                               excluded_channels=set((e.get("value_constraints") or {})
                                                     .get("excluded_channels", [])))
            width_ok = (proposed_op.get("op") != "ap.radio.width" or proposed_op.get("value") in
                        (e.get("value_constraints") or {}).get("allowed_widths_mhz", [20]))
            g("value_within_policy", width_ok, "Width outside this device's approved set.")
        except PolicyViolation as exc:
            g("value_within_policy", False, str(exc))
        cd = int(e.get("cooldown_seconds") or 0)
        if last_action_at is not None and last_action_at.tzinfo is None:
            last_action_at = last_action_at.replace(tzinfo=timezone.utc)
        g("cooldown_elapsed", last_action_at is None or (now - last_action_at).total_seconds() >= cd,
          "Cooldown active.")
        g("daily_budget", actions_today < int(e.get("max_actions_per_day") or 0),
          "Daily action budget exhausted.")
    g("observations_fresh", observation_fresh, "Metrics stale.")
    g("pre_change_backup_verified", backup_verified, "No verified full-fidelity backup.")
    g("adapter_write_certified", adapter_certified, "Adapter not write-certified for this model/firmware.")
    g("no_quality_degradation", not quality_degraded, "Measured experience degraded; stop, do not oscillate.")
    failed = [(n, why) for n, ok, why in guards if not ok]
    return {"decision": "DENY" if failed else "ALLOW",
            "guard_evaluation": [{"guard": n, "pass": ok} for n, ok, _ in guards],
            "denial_reason": "; ".join(w for _, w in failed) or None,
            "display": ("Configured for future automation — currently locked"
                        if e is not None and e.get("status") == "ARMED" and (global_write_lock or emergency_stop)
                        else None)}


def shadow_simulation(*, proposals: list[dict], cooldown_seconds: int,
                      degrade_after: int | None = None) -> dict:
    """GAP-T11/T24: simulate repeated proposals in SHADOW (no writes). Cooldown suppresses
    oscillation; a degradation event stops the series and proposes rollback."""
    timeline, last, stopped = [], None, False
    for i, p in enumerate(sorted(proposals, key=lambda x: x["at"])):
        at = p["at"] if p["at"].tzinfo else p["at"].replace(tzinfo=timezone.utc)
        if stopped:
            timeline.append({"at": at.isoformat(), "op": p["op"], "result": "STOPPED"}); continue
        if last is not None and (at - last).total_seconds() < cooldown_seconds:
            timeline.append({"at": at.isoformat(), "op": p["op"], "result": "SUPPRESSED_COOLDOWN"}); continue
        timeline.append({"at": at.isoformat(), "op": p["op"], "result": "WOULD_APPLY_SHADOW"})
        last = at
        if degrade_after is not None and i >= degrade_after:
            stopped = True
            timeline.append({"at": at.isoformat(), "op": "rollback_proposal", "result": "STOP_AND_PROPOSE_ROLLBACK"})
    return {"mode": "SHADOW", "device_writes": 0, "timeline": timeline, "stopped": stopped}
