"""Alert rules evaluated after each poll. Every rule works only from data that was actually collected:
if its input is unavailable (e.g. uptime or channel utilization before those collectors exist) it does nothing.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AccessPoint, ApSample, ClientRecord, Site
from ..experience.config import ExperienceConfig
from ..security.privacy import mask_mac
from ..firmware import compliance as fw
from .alerts import raise_alert, resolve_alerts

REBOOT_UPTIME_DROP_SECONDS = 60  # uptime must go DOWN by more than this to count as a reboot (jitter guard)


def evaluate(db: Session, site: Site, *, aps: list[AccessPoint], prev_uptime: dict[int, int | None],
             clients: list[ClientRecord], disconnects: dict[str, int], cfg: ExperienceConfig, now: datetime) -> None:
    sim, a = site.simulated, cfg.alerts

    for ap in aps:
        if ap.status != "up":
            continue
        # AP reboot: uptime went backwards between two polls.
        old, new = prev_uptime.get(ap.id), ap.uptime_seconds
        if old is not None and new is not None and new < old - REBOOT_UPTIME_DROP_SECONDS:
            raise_alert(db, site_id=site.id, ap_id=ap.id, kind="ap_reboot", severity="warning", simulated=sim,
                        message=f"AP {ap.name} rebooted (uptime reset from {old // 60} to {new // 60} min)")
        # High channel utilization: every one of the last N samples at/above the threshold.
        rows = db.scalars(select(ApSample).where(ApSample.ap_id == ap.id).order_by(ApSample.ts.desc())
                          .limit(a.utilization_polls)).all()
        utils = [max((u for u in (r.util_2g_percent, r.util_5g_percent) if u is not None), default=None) for r in rows]
        if len(utils) >= a.utilization_polls and all(u is not None and u >= a.utilization_pct for u in utils):
            raise_alert(db, site_id=site.id, ap_id=ap.id, kind="high_utilization", severity="warning", simulated=sim,
                        message=f"AP {ap.name}: channel utilization at or above {a.utilization_pct:g}% for {a.utilization_polls} polls")
        elif utils and utils[0] is not None and utils[0] < a.utilization_pct:
            resolve_alerts(db, site_id=site.id, ap_id=ap.id, kind="high_utilization")

    # Firmware consistency inside the cluster (needs per-AP firmware; with one version everywhere it never fires).
    versions = sorted({ap.firmware for ap in aps if ap.status == "up" and ap.firmware})
    if len(versions) > 1:
        raise_alert(db, site_id=site.id, ap_id=None, kind="firmware_mismatch", severity="warning", simulated=sim,
                    message=f"Site {site.name}: APs run different firmware versions ({', '.join(versions)})")
    else:
        resolve_alerts(db, site_id=site.id, ap_id=None, kind="firmware_mismatch")

    # Firmware compliance against the admin's target (only when a target is set and the version is known).
    report = fw.site_report(db, site)
    if report["status"] in ("outdated", "newer"):
        raise_alert(db, site_id=site.id, ap_id=None, kind="firmware_noncompliant", severity="warning", simulated=sim,
                    message=f"Site {site.name} runs {report['running_parsed']} but the target is {report['target']} "
                            f"({'upgrade needed' if report['status'] == 'outdated' else 'newer than the exact target'})")
    else:
        resolve_alerts(db, site_id=site.id, ap_id=None, kind="firmware_noncompliant")

    for c in clients:
        who = mask_mac(c.mac)
        # Sustained poor experience (a single bad poll is ignored).
        if c.exp_band == "poor" and (c.poor_streak or 0) >= a.poor_polls:
            raise_alert(db, site_id=site.id, ap_id=None, kind="poor_experience", severity="warning", simulated=sim,
                        subject=c.mac, message=f"Client {who} has had poor Wi-Fi experience for {c.poor_streak} polls "
                                               f"(score {c.exp_score}, AP {c.ap_name or 'unknown'})")
        elif c.exp_band != "poor":
            resolve_alerts(db, site_id=site.id, ap_id=None, kind="poor_experience", subject=c.mac)
        # Excessive disconnects (inferred from successive polls).
        n = disconnects.get(c.mac, 0)
        if n >= a.disconnects_threshold:
            raise_alert(db, site_id=site.id, ap_id=None, kind="excessive_disconnects", severity="warning", simulated=sim,
                        subject=c.mac, message=f"Client {who}: {n} disconnects in the last "
                                               f"{a.disconnects_window_minutes} min (inferred from polling)")
        else:
            resolve_alerts(db, site_id=site.id, ap_id=None, kind="excessive_disconnects", subject=c.mac)
