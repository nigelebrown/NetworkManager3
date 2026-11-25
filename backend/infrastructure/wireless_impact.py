"""Read-only ARM + measured client-experience change review. Never executes device changes."""
from __future__ import annotations
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, or_
from ..database.models import AppSetting, Site, SiteSample, ClientEvent
from ..database.models_inm import InmDevice, InmSite, RecommendationRecord
from ..database.models_phases import ChangeExecution, ChangePlan
from ..experience.config import load_config
from . import arm_measurements as AM

PREFIX = "wireless-impact:"
MODEL = "wireless-link-assessment-v1"
UNKNOWN = ["association/authentication success and latency", "DHCP/DNS success and latency",
           "client packet loss and application RTT/jitter", "measured application throughput"]
LIMIT = 5000

def utc(value):
    if value is None: return None
    if isinstance(value,str): value=datetime.fromisoformat(value.replace("Z","+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

def number(value, low=None, high=None):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value): return None
    return value if (low is None or value>=low) and (high is None or value<=high) else None

def mean(values):
    got=[v for v in values if number(v) is not None]
    return round(sum(got)/len(got),2) if got else None

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()

def policy_hash(db):
    return digest({"model":MODEL,"configuration":load_config(db).model_dump()})

def operations(cfg, ref):
    ops=[]
    if cfg.get("allowed_channels",{}).get("5") != ref.get("allowed_channels",{}).get("5"):
        ops.append({"op":"ap.radio.allowed_channels","value":[str(c) for c in ref["allowed_channels"]["5"]]})
    if (cfg.get("Minimum Transmit Power"),cfg.get("Maximum Transmit Power")) != (ref.get("Minimum Transmit Power"),ref.get("Maximum Transmit Power")):
        ops.append({"op":"ap.radio.tx_power","min_dbm":ref["Minimum Transmit Power"],"max_dbm":ref["Maximum Transmit Power"]})
    if cfg.get("Band Steering Mode") != ref.get("Band Steering Mode"):
        ops.append({"op":"ap.band_steering","value":ref["Band Steering Mode"]})
    if cfg.get("Client Match") != ref.get("Client Match"):
        ops.append({"op":"ap.client_match","value":"enabled" if ref.get("Client Match")=="enable" else "disabled"})
    # User selected the current 20/40 MHz policy, not the reference's 80 MHz capability.
    if cfg.get("Wide Channel Bands")!="5ghz" or cfg.get("80Mhz Support")!="disable":
        ops.append({"op":"ap.radio.width","value":40})
    return ops

def groups(db, allowed_sites):
    reference=db.get(AppSetting,"arm-reference:5g")
    if reference is None: return []
    ref=reference.value["config"]
    sites={s.id:s for s in db.scalars(select(Site).where(Site.id.in_(allowed_sites or [-1]),Site.simulated==False))}
    physical={s.id:s.name for s in db.scalars(select(InmSite))}
    gathered=defaultdict(list)
    for d in db.scalars(select(InmDevice).where(InmDevice.source_type=="REAL",InmDevice.family=="access_point",
                       InmDevice.lifecycle.not_in(("merged","retired","decommissioned")),InmDevice.legacy_site_id.in_(list(sites) or [-1]))):
        s=sites[d.legacy_site_id]
        gathered[(s.vc_host,s.vc_port)].append((d,AM.snapshot(db,d.id),s))
    result=[]
    for (host,port),members in sorted(gathered.items(),key=lambda x:str(x[0])):
        fresh=[m for m in members if m[1].get("freshness")=="FRESH" and m[1].get("state")=="MEASURED"]
        chosen=next((m for m in fresh if host in (m[0].mgmt_addresses or [])),fresh[0] if fresh else None)
        cfg=chosen[1].get("config",{}) if chosen else {}
        conflicts=len({digest(m[1].get("config",{})) for m in fresh})>1
        ops=operations(cfg,ref) if chosen else []
        missing={k:{"current":cfg.get(k),"target":ref[k]} for k in
                 ("Scanning","Client Aware","Channel Quality Aware","160Mhz Support",
                  "Channel Quality Threshold","Channel Quality Wait Time","Error Rate Threshold","Error Rate Wait Time")
                 if chosen and k in ref and cfg.get(k)!=ref[k]}
        ids=sorted({m[2].id for m in members})
        aps=[]
        for d,arm,site in members:
            a=next((a for a in arm.get("assignments",[]) if a.get("band")=="5"),{})
            radio=next((r for r in arm.get("channels",[]) if r.get("band")=="5" and r.get("channel")==a.get("channel")),{})
            cm=db.get(AppSetting,"cm-history:"+d.id)
            cmv=cm.value if cm and cm.value.get("legacy_site_id")==d.legacy_site_id else {}
            aps.append({"native_clientmatch":{"state":cmv.get("state","NOT_COLLECTED"),
                        "record_count":len(cmv.get("records",[])),"clock_alignment":cmv.get("clock_alignment"),
                        "last_success_at":cmv.get("last_success_at")},"id":d.id,"name":d.hostname,"serial":d.serial_number,"model":d.model,"firmware":d.firmware,
                        "ip":(d.mgmt_addresses or [None])[0],"location":physical.get(d.site_id) or None,
                        "site_id":site.id,"arm_freshness":arm.get("freshness"),"observed_at":arm.get("observed_at"),
                        "channel":a.get("channel"),"quality_percent":radio.get("quality_percent"),
                        "util_percent":radio.get("util_percent"),"retry_percent":radio.get("retry_percent")})
        result.append({"key":digest([host,port,ids])[:24],"controller":host,"port":port,"site_ids":ids,
            "sites":sorted({m[2].name for m in members}),"aps":aps,"fresh_aps":len(fresh),
            "config":cfg,"operations":ops,"unsupported_changes":missing,
            "membership_hash":digest(sorted((a["id"],a["serial"],a["firmware"],a["site_id"]) for a in aps)),
            "blockers":(["No fresh native ARM configuration"] if not chosen else [])+
                (["Fresh member configurations disagree"] if conflicts else [])+
                (["Additional typed operations and hardware validation required"] if missing else []),
            "scope_note":"Shared cluster settings affect every member. Power bounds can affect both bands; the width operation enables wide channels only on 5 GHz. Revalidate live membership before execution."})
    return result

def _native(db, group, start, end):
    values=[];seen=set();covered=set();times_by_ap=defaultdict(set)
    for a in group["aps"]:
        s=AM.snapshot(db,a["id"])
        history=list(s.get("recent",[]))
        if s.get("state")=="MEASURED":history.append(s)
        for h in history:
            at=utc(h.get("observed_at"))
            if at is None or not start<=at<=end or (a["id"],at) in seen:continue
            assignment=next((x for x in h.get("assignments",[]) if x.get("band")=="5"),{})
            r=next((x for x in h.get("channels",[]) if x.get("band")=="5" and x.get("channel")==assignment.get("channel")),{})
            if not r:continue
            seen.add((a["id"],at));covered.add(a["id"]);times_by_ap[a["id"]].add(at);values.append(r)
    return {"source":"ARUBA_ARM","sample_count":len(values),"covered_aps":len(covered),
            "coverage":len(covered)/len(group["aps"]) if group["aps"] else 0,
            "repeated_coverage":sum(len(t)>=2 and (max(t)-min(t)).total_seconds()>=600 for t in times_by_ap.values())/len(group["aps"]) if group["aps"] else 0,
            **{k:mean([number(r.get(k),0,100) for r in values]) for k in ("util_percent","quality_percent","retry_percent")}}

def history(db, group, start, end):
    rows=list(db.scalars(select(SiteSample).where(SiteSample.site_id.in_(group["site_ids"]),SiteSample.simulated==False,
                         SiteSample.ts>=start,SiteSample.ts<=end).order_by(SiteSample.ts).limit(LIMIT+1)))
    truncated=len(rows)>LIMIT;rows=rows[:LIMIT]
    valid=[];weighted=[];poor=0;scored=0
    for r in rows:
        total=number(r.clients_total,0)
        counts=[number(x,0) for x in (r.exp_good,r.exp_fair,r.exp_poor)]
        n=sum(counts) if all(x is not None for x in counts) else None
        score=number(r.exp_score,0,100)
        if total is not None and n is not None and 0<n<=total and score is not None:
            weighted.append(score*n);scored+=n;poor+=r.exp_poor;valid.append(r)
    times=sorted(set(utc(r.ts) for r in rows))
    site_coverage=len({r.site_id for r in valid})/len(group["site_ids"]) if group["site_ids"] else 0
    observed=sum(number(r.clients_total,0) or 0 for r in rows)
    events={k:db.scalar(select(func.count()).select_from(ClientEvent).where(ClientEvent.site_id.in_(group["site_ids"]),
             ClientEvent.simulated==False,ClientEvent.kind==k,ClientEvent.ts>=start,ClientEvent.ts<=end)) or 0 for k in ("disconnect","roam")}
    findings=db.scalar(select(func.count()).select_from(RecommendationRecord).where(
        RecommendationRecord.legacy_site_id.in_(group["site_ids"]),RecommendationRecord.source_type=="REAL",
        RecommendationRecord.last_seen_at>=start,RecommendationRecord.last_seen_at<=end)) or 0
    return {"start":start.isoformat(),"end":end.isoformat(),"samples":len(rows),
        "distinct_times":len(times),"span_minutes":(times[-1]-times[0]).total_seconds()/60 if len(times)>1 else 0,
        "truncated":truncated,"site_coverage":site_coverage,
        "mean_clients":mean([number(r.clients_total,0) for r in rows]),
        "client_measurement_coverage":scored/observed if observed else None,"scored_client_observations":scored,
        "link_score":round(sum(weighted)/scored,2) if scored else None,
        "poor_percent":round(poor/scored*100,2) if scored else None,
        "snr_db":mean([number(r.avg_snr_db,-30,100) for r in rows]),
        "rssi_dbm":mean([number(r.avg_rssi_dbm,-130,-1) for r in rows]),
        "inferred_events":events,"intelligence_findings_seen":findings,"arm":_native(db,group,start,end),
        "end_to_end_score":None,"unmeasured":UNKNOWN,"score_kind":"PARTIAL_WIRELESS_LINK",
        "note":"Client-observation weighted link score, not a unique-client success rate or application throughput. Events are inferred from polls."}

def compare(before, after):
    reasons=[]
    for name,w in (("before",before),("after",after)):
        if w.get("truncated") or w.get("distinct_times",0)<3 or w.get("span_minutes",0)<15:reasons.append(name+": insufficient history")
        if w.get("link_score") is None or (w.get("client_measurement_coverage") or 0)<.8 or w.get("site_coverage",0)<1:reasons.append(name+": insufficient client coverage")
        if w.get("arm",{}).get("repeated_coverage",0)<.8:reasons.append(name+": insufficient ARM coverage")
    b,a=before.get("mean_clients"),after.get("mean_clients")
    if b is None or a is None or b<=0 or a<=0 or abs(a-b)>max(2,.2*b):reasons.append("Client load is absent or not comparable")
    if reasons:return {"state":"INCONCLUSIVE","reasons":reasons}
    delta=round(after["link_score"]-before["link_score"],2)
    poor_delta=round(after["poor_percent"]-before["poor_percent"],2)
    state="NO_MATERIAL_LINK_CHANGE"
    if delta<=-5 or poor_delta>=5:state="OBSERVED_LINK_DEGRADATION"
    elif delta>=5 and poor_delta<=0:state="OBSERVED_LINK_IMPROVEMENT"
    elif delta>=5:state="MIXED_LINK_RESULTS"
    return {"state":state,"link_score_delta":delta,"poor_client_percentage_point_delta":poor_delta,
            "causal_claim":False,"end_to_end_verdict":"UNMEASURED",
            "rule":"Application review thresholds: five link-score points or five poor-client percentage points; not a vendor certification."}

def details(db, record, current_groups, now):
    result={**record,"clusters":[]}
    current={g["key"]:g for g in current_groups}
    for saved in record["clusters"]:
        frozen=record.get("completed",{}).get(saved["group"]["key"])
        if frozen:
            result["clusters"].append(frozen);continue
        g=current.get(saved["group"]["key"]);row={"group":saved["group"],"before":saved["before"],"after":None,"comparison":None}
        if not g or g["membership_hash"]!=saved["group"]["membership_hash"]:
            row["state"]="SCOPE_CHANGED_REVIEW_REQUIRED";result["clusters"].append(row);continue
        ids=[a["id"] for a in g["aps"]]
        baseline_start=utc(saved["before"]["start"])
        exs=list(db.scalars(select(ChangeExecution).where(ChangeExecution.device_id.in_(ids),
             ChangeExecution.environment=="HARDWARE",or_(ChangeExecution.finished_at==None,ChangeExecution.finished_at>=baseline_start)).order_by(ChangeExecution.finished_at).limit(101)))
        # Match the frozen proposal, not the recomputed target after it has been applied.
        matching=[]
        for ex in exs:
            plan=db.get(ChangePlan,ex.plan_id)
            if plan and ex.outcome=="APPLIED_VERIFIED" and ex.finished_at and utc(ex.finished_at)>=utc(record["created_at"]) and digest(sorted(plan.operations,key=lambda o:o["op"]))==digest(sorted(saved["group"]["operations"],key=lambda o:o["op"])):
                matching.append(ex)
        if not matching:
            row["state"]="BASELINE_EXPIRED" if now-utc(record["created_at"])>timedelta(minutes=10) else "NOT_APPLIED";result["clusters"].append(row);continue
        ex=matching[0];row["execution_id"]=ex.id
        end=utc(ex.finished_at)+timedelta(minutes=10+record["window_minutes"])
        start=end-timedelta(minutes=record["window_minutes"])
        if len(exs)>100 or len(matching)>1 or any(e.id!=ex.id and utc(e.started_at or e.finished_at) and utc(e.started_at or e.finished_at)<=end for e in exs) or policy_hash(db)!=record["scoring_policy_hash"]:
            row["state"]="CONFOUNDED";result["clusters"].append(row);continue
        if utc(ex.started_at) is None or utc(ex.started_at)<utc(record["created_at"]):
            row["state"]="EXECUTION_TIMING_UNVERIFIED";result["clusters"].append(row);continue
        row["after"]=history(db,g,start,min(now,end)) if now>start else None
        if now<end:row["state"]="OBSERVING";result["clusters"].append(row);continue
        row["comparison"]=compare(row["before"],row["after"])
        if utc(ex.started_at)-utc(record["created_at"])>timedelta(minutes=10):
            row["comparison"]={"state":"INCONCLUSIVE","reasons":["Saved baseline no longer immediately precedes the change. Capture a new review."]}
        row["state"]=row["comparison"]["state"];result["clusters"].append(row)
    return result


def tick(session_factory, owner):
    """Freeze completed evidence while native ARM's bounded history is still available."""
    from .change_executor import _lease, _release
    holder=owner+"-impact"
    with session_factory() as db:
        if not _lease(db,"wireless-impact-review",holder,ttl=120):return {"busy":True}
    try:
        with session_factory() as db:
            rows=list(db.scalars(select(AppSetting).where(AppSetting.key.startswith(PREFIX)).order_by(AppSetting.updated_at).limit(100)))
            active=[r for r in rows if not r.value.get("complete")][:2]
            if not active:return {"updated":0}
            site_ids=list(db.scalars(select(Site.id).where(Site.simulated==False)))
            current=groups(db,site_ids);now=datetime.now(timezone.utc);updated=0
            for setting in active:
                record=dict(setting.value)
                if now-utc(record["created_at"])>timedelta(days=7):
                    record["complete"]=True;record["expired"]=True
                else:
                    out=details(db,record,current,now)
                    completed=dict(record.get("completed",{}))
                    for row in out["clusters"]:
                        if row["state"] not in ("NOT_APPLIED","OBSERVING"):
                            completed[row["group"]["key"]]=row
                    record["completed"]=completed
                    record["complete"]=len(completed)==len(record["clusters"])
                setting.value=record;setting.updated_at=now;updated+=1
            db.commit()
            return {"updated":updated}
    finally:
        with session_factory() as db:
            _release(db,"wireless-impact-review",holder);db.commit()
