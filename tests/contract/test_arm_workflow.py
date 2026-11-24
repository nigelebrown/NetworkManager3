"""API contracts for ARM review, provenance and idempotent change creation; network I/O is replaced."""
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from backend.database.session import session_scope
from backend.database.models_inm import InmDevice
from backend.database.models_onboarding import OnboardingCredential,OnboardingDeviceCredential
from backend.database.models_phases import ChangePlan
from backend.infrastructure import ap_autodiscovery as AD,arm_measurements as AM,optimizer as O
from backend.security.crypto import encrypt_secret
from tests.unit.test_arm_intelligence import measured,outputs

@pytest.fixture
def arm_device(client,admin,monkeypatch):
    from backend.api import ap_operations as API
    with session_scope() as db:
        d=InmDevice(source_type="REAL",environment="production",vendor="Aruba",family="access_point",
             model="AP-505",firmware="8.10.0.21",serial_number="REAL-REFERENCE-1",
             hostname="Measured-AP",adapter="aruba_instant",mgmt_addresses=["10.9.8.10"])
        db.add(d);db.flush();did=d.id
        p=OnboardingCredential(name="ARM-read",device_family="access_point",protocol="aruba_instant_rest",
             username="reader",password_enc=encrypt_secret("private-test-value"),created_by="admin")
        db.add(p);db.flush()
        db.add(OnboardingDeviceCredential(device_id=did,credential_id=p.id,bound_by="admin"))
        m=measured();m["overrides"]["5"]={"chan":149,"tx_power":18}
        AD._put(db,"arm-rf:"+did,m,"test")
    ap={"device_id":did,"name":"Measured-AP","state":"ONLINE","cluster_key":"unlinked","clients":1,
        "state_reason":"persisted real data","guidance":"monitor","radio_5g":{"channel":149,"util_percent":10,"noise_floor_dbm":-92},
        "radio_2g":{"channel":11,"util_percent":20,"noise_floor_dbm":-96},
        "cpu_percent":20,"memory_percent":30}
    monkeypatch.setattr(API,"build_fleet",lambda db:{"aps":[ap]})
    return did,m

def test_diagnosis_to_real_review_plan_is_idempotent_and_carries_exact_operation(client,admin,arm_device,monkeypatch):
    did,m=arm_device
    monkeypatch.setattr("backend.infrastructure.change_executor.writer_for",lambda *a:pytest.fail("No adapter may run during plan creation"))
    d=client.get(f"/api/v1/wireless/diagnose/ap/{did}",headers=admin)
    assert d.status_code==200,d.text
    assert d.json()["arm"]["source"]=="ARUBA_ARM"
    b={"finding_id":"arm_override_5","observed_at":m["observed_at"]}
    url=f"/api/v1/wireless/diagnose/ap/{did}/change-plan"
    p=client.post(url,headers=admin,json=b)
    assert p.status_code==201,p.text
    assert p.json()["state"]=="PENDING_APPROVAL"
    assert p.json()["operations"]==[{"op":"ap.radio.channel","band":"5","value":0,"power":0,"arm_managed":True}]
    assert client.post(url,headers=admin,json=b).json()["id"]==p.json()["id"]
    with session_scope() as db:
        rows=list(db.scalars(select(ChangePlan).where(ChangePlan.device_id==did)))
        assert len(rows)==1 and rows[0].source=="ARM_DIAGNOSIS"

def test_stale_or_replaced_evidence_cannot_submit_an_adjustment(client,admin,arm_device):
    did,m=arm_device
    url=f"/api/v1/wireless/diagnose/ap/{did}/change-plan"
    b={"finding_id":"arm_override_5","observed_at":"2000-01-01T00:00:00+00:00"}
    assert client.post(url,headers=admin,json=b).status_code==409
    old=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
    with session_scope() as db:
        AD._put(db,"arm-rf:"+did,{**m,"observed_at":old},"test")
    b["observed_at"]=old
    assert client.post(url,headers=admin,json=b).status_code==409

def test_baseline_saves_measured_reference_without_changing_hardware(client,admin,arm_device):
    did,m=arm_device
    r=client.put("/api/v1/wireless/arm-reference/5g",headers=admin,
        json={"source_device_id":did,"label":"Commissioners Office initial 5 GHz baseline"})
    assert r.status_code==200,r.text
    assert not r.json()["auto_apply"]
    d=client.get(f"/api/v1/wireless/diagnose/ap/{did}",headers=admin).json()
    assert d["baseline"]["available"] and not d["baseline"]["differences"]

def test_measurement_collection_uses_fixed_show_only_commands_and_never_persists_secrets(client,admin,arm_device,monkeypatch):
    from backend.aruba.arm import COMMANDS
    did,m=arm_device
    monkeypatch.setattr("backend.api.quick_devices._permit_probe",lambda ip:None)
    raw=outputs();calls=[]
    class Reader:
        def show(self,cmd):
            calls.append(cmd)
            return {"Command output":raw[next(k for k,v in COMMANDS.items() if v==cmd)]}
    with session_scope() as db:
        v=AM.collect(db,db.get(InmDevice,did),client=Reader())
        assert v["state"]=="MEASURED"
        assert "private-test-value" not in str(v)
    assert calls==list(COMMANDS.values()) and all(c.startswith("show ") for c in calls)

def test_optimizer_respects_native_arm_and_manual_override_is_not_default(client,admin,arm_device):
    did,m=arm_device
    with session_scope() as db:
        d=db.get(InmDevice,did)
        e=SimpleNamespace(allowed_operation_types=["ap.radio.channel"],value_constraints={})
        p=O.propose(db,d,e)
        assert p["arm_managed"] and p["value"]==0
        AD._put(db,"arm-rf:"+did,measured(),"test")
        assert O.propose(db,d,e) is None

def test_native_arm_readback_checks_configured_override_not_dynamic_channel():
    from backend.infrastructure.writers.aruba_instant import ArubaInstantWriter
    from backend.infrastructure.writers import Credentials
    class Reader:
        def show(self,cmd,**kw):
            assert cmd=="show ap-env"
            return {"Command output":"name:Measured-AP\n"}
    w=ArubaInstantWriter(Credentials(host="10.9.8.10",username="u",password="p"),client=Reader())
    ok,seen=w.readback({"op":"ap.radio.channel","band":"5","value":0,"power":0,"arm_managed":True})
    assert ok and seen["native_arm"]

def test_scope_and_role_are_enforced_for_arm_configuration(client,admin,arm_device):
    from tests.conftest import make_user
    did,m=arm_device
    viewer=make_user(client,admin,"arm.viewer","viewer")
    assert client.post(f"/api/v1/wireless/arm/{did}/collect",headers=viewer).status_code==403
    assert client.put("/api/v1/wireless/arm-reference/5g",headers=viewer,
        json={"source_device_id":did,"label":"Reference"}).status_code==403
