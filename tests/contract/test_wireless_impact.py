"""Real evidence windows and hardware provenance; no network operations are permitted."""
from datetime import datetime,timedelta,timezone
from copy import deepcopy
import math
import pytest
from sqlalchemy import select
from backend.database.models import AppSetting,Site,SiteSample
from backend.database.models_inm import InmDevice
from backend.database.models_phases import ChangePlan,ChangeExecution
from backend.database.session import session_scope
from backend.infrastructure import wireless_impact as W,ap_autodiscovery as AD
from backend.experience.scoring import ClientMetrics,score_client
from backend.experience.config import ExperienceConfig
from tests.contract.test_arm_workflow import arm_device

def window(score=60,poor=30,clients=10):
    return {"distinct_times":4,"span_minutes":20,"truncated":False,"site_coverage":1,
      "link_score":score,"poor_percent":poor,"mean_clients":clients,"client_measurement_coverage":1,
      "arm":{"coverage":1,"repeated_coverage":1}}

def test_observed_improvement_is_not_an_end_to_end_or_causal_claim():
    r=W.compare(window(),window(80,10))
    assert r["state"]=="OBSERVED_LINK_IMPROVEMENT"
    assert r["causal_claim"] is False and r["end_to_end_verdict"]=="UNMEASURED"

@pytest.mark.parametrize("field,value",[("link_score",None),("client_measurement_coverage",.5),("distinct_times",1),("span_minutes",2),("truncated",True),("mean_clients",30)])
def test_bad_coverage_or_different_load_cannot_claim_improvement(field,value):
    after=window(95,0);after[field]=value
    assert W.compare(window(),after)["state"]=="INCONCLUSIVE"

def test_a_single_arm_snapshot_is_not_a_sustained_measurement():
    after=window(95,0);after["arm"]["repeated_coverage"]=0
    assert W.compare(window(),after)["state"]=="INCONCLUSIVE"

def test_more_poor_clients_prevents_an_unqualified_improvement():
    assert W.compare(window(),window(80,34))["state"]=="MIXED_LINK_RESULTS"
    assert W.compare(window(),window(80,40))["state"]=="OBSERVED_LINK_DEGRADATION"

@pytest.mark.parametrize("bad",[float("nan"),float("inf"),True,5000])
def test_invalid_signal_never_becomes_perfect_experience(bad):
    r=score_client(ClientMetrics(snr_db=bad),ExperienceConfig())
    assert r["score"] is None and r["end_to_end_score"] is None

def test_link_rate_and_partial_score_are_labelled():
    r=score_client(ClientMetrics(snr_db=35,speed_mbps=150),ExperienceConfig())
    assert r["score"]==100 and r["score_kind"]=="PARTIAL_WIRELESS_LINK"
    assert r["end_to_end_score"] is None and r["service_assurance"]["state"]=="UNMEASURED"
    assert "not measured throughput" in r["inputs"]["rate"]["meaning"]

@pytest.fixture
def impact(client,admin,arm_device):
    did,m=arm_device;now=datetime.now(timezone.utc)
    with session_scope() as db:
        s=Site(name="Impact real cluster",vc_host="10.9.8.10",simulated=False,enabled=True)
        db.add(s);db.flush();sid=s.id
        db.get(InmDevice,did).legacy_site_id=sid
        AD._put(db,"arm-reference:5g",{"config":m["config"],"source_name":"Initial reference"},"test")
        snap=deepcopy(m)
        snap["recent"]=[{"observed_at":(now-timedelta(minutes=n)).isoformat(),"channels":m["channels"],"assignments":m["assignments"]} for n in (25,15,5)]
        AD._put(db,"arm-rf:"+did,snap,"test")
        for n in (25,15,5):
            db.add(SiteSample(site_id=sid,ts=now-timedelta(minutes=n),aps_total=1,aps_up=1,
               clients_total=10,exp_score=60,exp_good=6,exp_fair=0,exp_poor=4,simulated=False))
    preview=client.get("/api/v1/intelligence/wireless-impact",headers=admin)
    assert preview.status_code==200,preview.text
    g=next(g for g in preview.json()["groups"] if any(a["id"]==did for a in g["aps"]))
    return did,sid,g,m

def capture(client,admin,g):
    r=client.post("/api/v1/intelligence/wireless-impact/reviews",headers=admin,json={"cluster_keys":[g["key"]]})
    assert r.status_code==201,r.text
    return r.json()

def test_capture_has_scope_and_measured_before_but_no_fake_after(client,admin,impact,monkeypatch):
    monkeypatch.setattr("backend.infrastructure.change_executor.writer_for",lambda *a:pytest.fail("Review must not instantiate a device writer"))
    did,sid,g,m=impact
    r=capture(client,admin,g)
    row=r["clusters"][0]
    assert row["state"]=="NOT_APPLIED" and row["after"] is None
    assert row["before"]["link_score"]==60 and row["before"]["arm"]["coverage"]==1
    assert row["before"]["end_to_end_score"] is None
    assert row["group"]["aps"][0]["id"]==did
    assert "password" not in str(r).lower()

def test_zero_clients_do_not_become_a_healthy_sample(client,admin,impact):
    did,sid,g,m=impact
    with session_scope() as db:
        for row in db.scalars(select(SiteSample).where(SiteSample.site_id==sid)):
            row.clients_total=0;row.exp_score=100;row.exp_good=0;row.exp_fair=0;row.exp_poor=0
    r=capture(client,admin,g)["clusters"][0]["before"]
    assert r["link_score"] is None and r["poor_percent"] is None

def test_demo_samples_and_stale_arm_do_not_supply_real_baselines(client,admin,impact):
    did,sid,g,m=impact
    with session_scope() as db:
        for row in db.scalars(select(SiteSample).where(SiteSample.site_id==sid)):row.simulated=True
    assert capture(client,admin,g)["clusters"][0]["before"]["samples"]==0
    with session_scope() as db:
        v=db.get(AppSetting,"arm-rf:"+did);v.value={**v.value,"observed_at":"2000-01-01T00:00:00+00:00"}
    r=client.post("/api/v1/intelligence/wireless-impact/reviews",headers=admin,json={"cluster_keys":[g["key"]]})
    assert r.status_code==409

def test_site_scope_cannot_leak_review_membership(client,admin,impact,monkeypatch):
    did,sid,g,m=impact;r=capture(client,admin,g)
    monkeypatch.setattr("backend.api.intelligence._in_scope",lambda site:False)
    assert client.get("/api/v1/intelligence/wireless-impact",headers=admin).json()["groups"]==[]
    assert client.get("/api/v1/intelligence/wireless-impact/reviews/"+r["id"],headers=admin).status_code==404

@pytest.mark.parametrize("environment,outcome,state",[("EMULATED","APPLIED_VERIFIED","NOT_APPLIED"),("HARDWARE","ABORTED","NOT_APPLIED"),("HARDWARE","APPLIED_VERIFIED","OBSERVED_LINK_IMPROVEMENT")])
def test_after_requires_exact_hardware_execution_and_completed_real_windows(client,admin,impact,monkeypatch,environment,outcome,state):
    from backend.api import intelligence as API
    did,sid,g,m=impact;r=capture(client,admin,g);created=W.utc(r["created_at"])
    with session_scope() as db:
        p=ChangePlan(device_id=did,title="Reviewed ARM target",operations=g["operations"],capability_report=[],
             plan_hash="test",source="OPERATOR",requester="operator",state="APPLIED_VERIFIED",rollback_plan={})
        db.add(p);db.flush()
        db.add(ChangeExecution(plan_id=p.id,device_id=did,environment=environment,outcome=outcome,steps=[],
             executed_by="executor",started_at=created+timedelta(minutes=1),finished_at=created+timedelta(minutes=2)))
        for n in (15,25,35):
            db.add(SiteSample(site_id=sid,ts=created+timedelta(minutes=n),aps_total=1,aps_up=1,
                clients_total=10,exp_score=80,exp_good=9,exp_fair=0,exp_poor=1,simulated=False))
        snap=deepcopy(m)
        snap["observed_at"]=(created+timedelta(minutes=35)).isoformat()
        snap["recent"]=[{"observed_at":(created+timedelta(minutes=n)).isoformat(),"channels":m["channels"],"assignments":m["assignments"]} for n in (15,25)]
        AD._put(db,"arm-rf:"+did,snap,"test")
    monkeypatch.setattr(API,"_now",lambda:created+timedelta(minutes=45))
    out=client.get("/api/v1/intelligence/wireless-impact/reviews/"+r["id"],headers=admin)
    assert out.status_code==200,out.text
    row=out.json()["clusters"][0]
    if state=="NOT_APPLIED":
        assert row["state"] in ("NOT_APPLIED","BASELINE_EXPIRED") and row["after"] is None
    else:
        assert row["state"]==state,row
        assert row["after"]["link_score"]==80 and row["comparison"]["causal_claim"] is False
