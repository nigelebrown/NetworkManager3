"""Discovery rules — PRTG / SolarWinds-style auto-discovery built from independent, reusable parts.

* **Discovery subnets** are saved on their own (name, CIDR, site, notes) and can be reused by many rules.
* **Credential profiles** already exist on their own (`/api/v1/onboarding/credentials`).
* A **rule** joins them: subnets + an ordered list of credential profiles (tried in order on hosts the first one
  could not log in to) + what to do with what is found (register, bind the credential that worked, turn on read-only
  monitoring, run cluster-aware AP onboarding) + a schedule (manual / daily / weekly).

A run fans out into the existing durable, leased, read-only discovery jobs (≤ /24 each; larger subnets are split into
/24 batches, max /22 per subnet) and is *advanced* by the scheduler: when its jobs finish it retries failed logins with
the next profiles, registers verified identities (never a guessed serial), binds credentials and runs the follow-up
actions. Every step is audited; nothing writes device configuration.
"""
from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database.models import AppSetting, User
from ..database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential, OnboardingDiscovery
from ..database.session import get_db
from ..security.deps import audit, require_role

from ..security.context import get_scope


def require_global_scope():
    """Fleet-wide workspaces are refused to site-scoped users (kept from the production branch)."""
    if get_scope() is not None:
        raise HTTPException(403, "This fleet-wide workspace requires access to all sites.")


router = APIRouter(prefix="/api/v1/discovery", tags=["discovery-rules"], dependencies=[Depends(require_role("viewer")), Depends(require_global_scope)])
SUB, RULE, RUN = "disc-subnet:", "disc-rule:", "disc-run:"
SCHEDULES = {"manual": None, "daily": timedelta(days=1), "weekly": timedelta(days=7)}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _list(db: Session, prefix: str) -> list[dict]:
    return [{"id": r.key[len(prefix):], **r.value} for r in db.scalars(select(AppSetting).where(AppSetting.key.startswith(prefix)))]


def _get(db: Session, prefix: str, oid: str) -> dict:
    row = db.get(AppSetting, prefix + oid)
    if row is None:
        raise HTTPException(404, "Not found")
    return {"id": oid, **row.value}


def _put(db: Session, prefix: str, oid: str, value: dict, actor: str) -> None:
    value = {k: v for k, v in value.items() if k != "id"}
    row = db.get(AppSetting, prefix + oid)
    if row is None:
        db.add(AppSetting(key=prefix + oid, value=value, updated_by=actor, updated_at=_now()))
    else:
        row.value, row.updated_by, row.updated_at = value, actor, _now()
    db.flush()


def _del(db: Session, prefix: str, oid: str) -> None:
    row = db.get(AppSetting, prefix + oid)
    if row is None:
        raise HTTPException(404, "Not found")
    db.delete(row)


def batches(cidr: str) -> list[str]:
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError:
        raise HTTPException(422, "Enter an IPv4 address or CIDR, e.g. 10.20.30.0/24.")
    if not isinstance(net, ipaddress.IPv4Network):
        raise HTTPException(422, "Only IPv4 is supported.")
    if net.prefixlen < 22:
        raise HTTPException(422, "Subnets larger than /22 (1,024 addresses) must be split into several saved subnets.")
    if not net.is_private:
        raise HTTPException(422, "Only private management ranges can be discovered.")
    return [str(s) for s in net.subnets(new_prefix=24)] if net.prefixlen < 24 else [str(net)]


# ------------------------------------------------------------------ subnets
class SubnetIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    cidr: str = Field(min_length=7, max_length=20)
    site_name: str = Field(min_length=1, max_length=100)
    notes: str = Field("", max_length=300)


@router.get("/subnets")
def list_subnets(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    out = _list(db, SUB)
    rules = _list(db, RULE)
    for s in out:
        s["used_by"] = [r["name"] for r in rules if s["id"] in r.get("subnet_ids", [])]
        s["batches"] = len(batches(s["cidr"]))
    return sorted(out, key=lambda x: x["name"].casefold())


@router.post("/subnets", status_code=201)
def add_subnet(body: SubnetIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    b = batches(body.cidr)
    sid = uuid.uuid4().hex[:12]
    _put(db, SUB, sid, {**body.model_dump(), "cidr": str(ipaddress.ip_network(body.cidr, strict=False)),
                        "created_by": admin.username, "created_at": _now().isoformat()}, admin.username)
    audit(db, "discovery_subnet_saved", username=admin.username, target=sid, detail=f"{body.cidr} ({len(b)} x /24)", request=request)
    return _get(db, SUB, sid)


@router.put("/subnets/{sid}")
def edit_subnet(sid: str, body: SubnetIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    cur = _get(db, SUB, sid)
    batches(body.cidr)
    _put(db, SUB, sid, {**cur, **body.model_dump(), "cidr": str(ipaddress.ip_network(body.cidr, strict=False))}, admin.username)
    audit(db, "discovery_subnet_updated", username=admin.username, target=sid, detail=body.cidr, request=request)
    return _get(db, SUB, sid)


@router.delete("/subnets/{sid}")
def delete_subnet(sid: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    users = [r["name"] for r in _list(db, RULE) if sid in r.get("subnet_ids", [])]
    if users:
        raise HTTPException(409, f"Used by rule(s): {', '.join(users)}. Remove it from them first.")
    _del(db, SUB, sid)
    audit(db, "discovery_subnet_deleted", username=admin.username, target=sid, request=request)
    return {"deleted": sid}


# ------------------------------------------------------------------ rules
class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    subnet_ids: list[str] = Field(min_length=1, max_length=32)
    credential_ids: list[str] = Field(min_length=1, max_length=6)  # tried in this order
    register_found: bool = True
    enable_monitoring: bool = True
    onboard_ap_clusters: bool = True
    schedule: str = Field("manual", pattern="^(manual|daily|weekly)$")
    enabled: bool = True


def _validate_rule(db: Session, body: RuleIn) -> None:
    for sid in body.subnet_ids:
        _get(db, SUB, sid)
    fams = set()
    for cid in body.credential_ids:
        p = db.get(OnboardingCredential, cid)
        if p is None or not p.enabled:
            raise HTTPException(422, "Every credential profile must exist and be enabled.")
        fams.add(p.device_family)


@router.get("/rules")
def list_rules(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    subs = {s["id"]: s for s in _list(db, SUB)}
    creds = {p.id: p for p in db.scalars(select(OnboardingCredential))}
    out = []
    for r in _list(db, RULE):
        r["subnets"] = [{"id": i, "name": subs[i]["name"], "cidr": subs[i]["cidr"]} for i in r.get("subnet_ids", []) if i in subs]
        r["credentials"] = [{"id": i, "name": creds[i].name, "family": creds[i].device_family, "enabled": creds[i].enabled}
                            for i in r.get("credential_ids", []) if i in creds]
        r["last_run"] = _run_summary(db, r.get("last_run_id")) if r.get("last_run_id") else None
        out.append(r)
    return sorted(out, key=lambda x: x["name"].casefold())


@router.post("/rules", status_code=201)
def add_rule(body: RuleIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _validate_rule(db, body)
    rid = uuid.uuid4().hex[:12]
    nxt = (_now() + SCHEDULES[body.schedule]).isoformat() if SCHEDULES[body.schedule] else None
    _put(db, RULE, rid, {**body.model_dump(), "created_by": admin.username, "created_at": _now().isoformat(),
                         "next_run_at": nxt, "last_run_id": None}, admin.username)
    audit(db, "discovery_rule_saved", username=admin.username, target=rid,
          detail=f"{body.name}: {len(body.subnet_ids)} subnet(s), {len(body.credential_ids)} profile(s), {body.schedule}", request=request)
    return _get(db, RULE, rid)


@router.put("/rules/{rid}")
def edit_rule(rid: str, body: RuleIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    cur = _get(db, RULE, rid)
    _validate_rule(db, body)
    nxt = (_now() + SCHEDULES[body.schedule]).isoformat() if SCHEDULES[body.schedule] else None
    _put(db, RULE, rid, {**cur, **body.model_dump(), "next_run_at": nxt}, admin.username)
    audit(db, "discovery_rule_updated", username=admin.username, target=rid, request=request)
    return _get(db, RULE, rid)


@router.delete("/rules/{rid}")
def delete_rule(rid: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    _del(db, RULE, rid)
    audit(db, "discovery_rule_deleted", username=admin.username, target=rid, request=request)
    return {"deleted": rid}


# ------------------------------------------------------------------ runs
def start_run(db: Session, rule: dict, actor: str, request: Request | None = None) -> dict:
    from .onboarding_workflow import DiscoveryRequest, discover_devices
    subs = {s["id"]: s for s in _list(db, SUB)}
    primary = rule["credential_ids"][0]
    jobs, errors = [], []
    for sid in rule["subnet_ids"]:
        s = subs.get(sid)
        if not s:
            continue
        for scope in batches(s["cidr"]):
            try:
                user = db.scalar(select(User).where(User.username == actor)) or db.scalar(
                    select(User).where(User.role == "admin"))
                out = discover_devices(DiscoveryRequest(credential_id=primary, scope=scope, site_name=s["site_name"]),
                                       request or _FakeRequest(), db, user)
                jobs.append({"job_id": out["id"], "scope": scope, "site_name": s["site_name"], "subnet": s["name"]})
            except HTTPException as exc:
                errors.append({"scope": scope, "subnet": s["name"], "error": str(exc.detail)[:200]})
    run_id = uuid.uuid4().hex[:12]
    run = {"rule_id": rule["id"], "rule_name": rule["name"], "started_at": _now().isoformat(), "started_by": actor,
           "state": "SCANNING" if jobs else "FAILED", "jobs": jobs, "errors": errors, "steps": [], "summary": {}}
    _put(db, RUN, run_id, run, actor)
    nxt = (_now() + SCHEDULES[rule["schedule"]]).isoformat() if SCHEDULES.get(rule["schedule"]) else None
    _put(db, RULE, rule["id"], {**rule, "last_run_id": run_id, "next_run_at": nxt}, actor)
    audit(db, "discovery_rule_run", username=actor, target=rule["id"],
          detail=f"run={run_id} jobs={len(jobs)} errors={len(errors)}")
    return {"id": run_id, **run}


class _FakeRequest:
    """Scheduler-initiated runs have no HTTP request; audit() tolerates missing client details."""
    client = None
    headers: dict = {}


def advance(db: Session, run_id: str, actor: str = "discovery-rules", identify=None) -> dict:
    """Move a run forward once all its discovery jobs are terminal: retry auth failures with the next profiles,
    register verified identities, bind the working credential and run follow-up actions. Idempotent."""
    from ..infrastructure import discovery_jobs as DJ
    run = _get(db, RUN, run_id)
    if run["state"] not in ("SCANNING",):
        return run
    rule_row = db.get(AppSetting, RULE + run["rule_id"])
    if rule_row is None:
        run["state"] = "FAILED"
        run["errors"].append({"error": "rule deleted"})
        _put(db, RUN, run_id, run, actor)
        return run
    rule = {"id": run["rule_id"], **rule_row.value}
    jobs = [db.get(OnboardingDiscovery, j["job_id"]) for j in run["jobs"]]
    if any(j is not None and j.status in DJ.ACTIVE for j in jobs):
        return run
    if identify is None:
        from .onboarding_workflow import _identify as identify
    found, retried, failed = [], 0, 0
    for j, meta in zip(jobs, run["jobs"]):
        if j is None:
            continue
        for r in j.results or []:
            row, cred = dict(r), j.credential_id
            if row.get("status") != "FOUND":
                for alt in rule["credential_ids"][1:]:  # ordered fallback on hosts that answered but did not log in
                    if row.get("status") == "NO_RESPONSE":
                        break
                    retried += 1
                    try:
                        cand = identify(alt, row["management_ip"])
                    except Exception:
                        continue
                    if cand.get("status") == "FOUND":
                        row, cred = cand, alt
                        break
            if row.get("status") == "FOUND" and row.get("enrollable"):
                found.append((row, cred, meta["site_name"], j))
            elif row.get("status") != "NO_RESPONSE":
                failed += 1
    registered, bound = 0, 0
    if rule.get("register_found", True):
        from .quick_devices import QuickDevice, record_observation
        for row, cred, site, j in found:
            prof = db.get(OnboardingCredential, cred)
            if prof is None:
                continue
            res = record_observation(db, QuickDevice(family=prof.device_family, site_name=site, hostname=row["hostname"],
                                                     serial_number=row["serial_number"], model=row.get("model") or "Unknown",
                                                     firmware=row.get("firmware"), management_ip=row["management_ip"],
                                                     status="up", metrics={}),
                                     actor, _FakeRequest(), evidence="PROBED", notes=f"discovery rule {rule['name']}")
            registered += 1
            b = db.get(OnboardingDeviceCredential, res["id"])
            if b is None:
                db.add(OnboardingDeviceCredential(device_id=res["id"], credential_id=cred, bound_by=actor))
                bound += 1
        for j in jobs:
            if j is not None and j.status == "SCANNED":
                j.status = "REGISTERED"
    db.flush()
    steps = [f"Scanned {len(run['jobs'])} /24 batch(es)", f"{len(found)} device(s) identified",
             f"{retried} login retr(y/ies) with fallback profiles", f"{registered} registered, {bound} credential binding(s)"]
    if rule.get("enable_monitoring", True):
        from .guides import AutoPlans, auto_plans
        admin = db.scalar(select(User).where(User.username == actor)) or db.scalar(select(User).where(User.role == "admin"))
        r = auto_plans(AutoPlans(family="switch"), _FakeRequest(), db, admin)
        steps.append(f"Monitoring: {r['created']} switch plan(s) created")
    if rule.get("onboard_ap_clusters", True) and any(db.get(OnboardingCredential, c) and
                                                     db.get(OnboardingCredential, c).device_family == "access_point"
                                                     for c in rule["credential_ids"]):
        from ..infrastructure import ap_autodiscovery as AD
        try:
            AD.start_job(actor, device_ids=None, auto_enable_rest=False)
            steps.append("AP cluster auto-onboarding started")
        except RuntimeError as exc:
            steps.append(f"AP cluster onboarding not started: {exc}")
    run.update(state="COMPLETED", finished_at=_now().isoformat(), steps=steps,
               summary={"identified": len(found), "registered": registered, "bound": bound, "login_failures": failed,
                        "retries": retried})
    _put(db, RUN, run_id, run, actor)
    audit(db, "discovery_rule_run_completed", username=actor, target=run["rule_id"], detail="; ".join(steps))
    return run


def _run_summary(db: Session, run_id: str) -> dict | None:
    row = db.get(AppSetting, RUN + run_id)
    if row is None:
        return None
    v = row.value
    jobs = [db.get(OnboardingDiscovery, j["job_id"]) for j in v.get("jobs", [])]
    from ..infrastructure import discovery_jobs as DJ
    prog = [DJ.progress(j) for j in jobs if j is not None and j.targets]
    return {"id": run_id, "state": v["state"], "started_at": v["started_at"], "finished_at": v.get("finished_at"),
            "summary": v.get("summary"), "steps": v.get("steps"), "errors": v.get("errors"),
            "progress": {"done": sum(p["done"] for p in prog), "total": sum(p["total"] for p in prog),
                         "found": sum(p["found"] for p in prog)} if prog else None,
            "jobs": [{"id": j.id, "status": j.status, "scope": j.scope} for j in jobs if j is not None]}


@router.post("/rules/{rid}/run", status_code=202)
def run_rule(rid: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    rule = _get(db, RULE, rid)
    out = start_run(db, rule, admin.username, request)
    advance(db, out["id"], admin.username)  # small (≤ 8 host) scans finish synchronously
    return _run_summary(db, out["id"])


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), admin: User = Depends(require_role("admin"))):
    advance(db, run_id, admin.username)
    out = _run_summary(db, run_id)
    if out is None:
        raise HTTPException(404, "Run not found")
    return out


def sweep(db: Session) -> dict:
    """Scheduler hook: advance running runs and start due scheduled rules."""
    advanced = started = 0
    for r in _list(db, RUN):
        if r.get("state") == "SCANNING":
            advance(db, r["id"], r.get("started_by") or "discovery-rules")
            advanced += 1
    for rule in _list(db, RULE):
        due = rule.get("next_run_at")
        if rule.get("enabled") and SCHEDULES.get(rule.get("schedule")) and due and datetime.fromisoformat(due) <= _now():
            start_run(db, rule, rule.get("created_by") or "discovery-rules")
            started += 1
    return {"advanced": advanced, "started": started}
