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
        "cluster":"Measured cluster","tier":"GOLD","state_reason":"persisted real data","guidance":"monitor","radio_5g":{"channel":149,"util_percent":10,"noise_floor_dbm":-92},
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


def test_allow_evaluations_do_not_consume_the_hardware_action_budget(client,admin,arm_device,monkeypatch):
    from backend.database.models_phases import OptimizationEnrollment,ExecutionDecision,ChangeExecution
    from backend.infrastructure import change_executor as X
    did,m=arm_device
    monkeypatch.setenv("INM_AUTOMATIC_OPTIMIZATION","true")
    monkeypatch.setattr(O,"get_settings",lambda:SimpleNamespace(device_write_lock=False,allow_config_changes=True))
    monkeypatch.setattr(X,"certification_map",lambda db,d:{"ap.radio.channel":"CERTIFIED_WRITE"})
    with session_scope() as db:
        d=db.get(InmDevice,did)
        art=X.seal_artifact(db,d,b"test-only complete configuration",trigger="MANUAL",actor="tester",source="DEVICE_READ")
        e=OptimizationEnrollment(device_id=did,immutable_serial=d.serial_number,vendor_model="Aruba AP-505",
            firmware_bound=d.firmware,mode="DEVICE_OPT_IN",allowed_operation_types=["ap.radio.channel"],
            value_constraints={},permitted_windows={},impact_stop_threshold={},cooldown_seconds=3600,max_actions_per_day=2,
            expires_at_utc=datetime.now(timezone.utc)+timedelta(days=1),requester_id="requester",
            independent_approver_id="reviewer",status="ARMED")
        db.add(e);db.flush()
        op={"op":"ap.radio.channel","band":"5","value":0,"power":0,"arm_managed":True}
        first,_=O.evaluate(db,d,op,actor="test")
        second,_=O.evaluate(db,d,op,actor="test")
        assert first["decision"]==second["decision"]=="ALLOW"
        for i in range(2):
            p=ChangePlan(device_id=did,title="Measured adjustment",operations=[op],capability_report=[],
                plan_hash="test-hash",source="AUTO_OPTIMIZER",requester="requester",state="APPLIED_VERIFIED",rollback_plan={})
            db.add(p);db.flush()
            db.add(ChangeExecution(plan_id=p.id,device_id=did,environment="HARDWARE",outcome="APPLIED_VERIFIED",
                steps=[],executed_by="tester",finished_at=datetime.now(timezone.utc)-timedelta(minutes=5)))
        db.flush()
        denied,_=O.evaluate(db,d,op,actor="test")
        assert denied["decision"]=="DENY"
        assert next(g for g in denied["guard_evaluation"] if g["guard"]=="daily_budget")["pass"] is False


@pytest.mark.parametrize("journal_present,execution_outcome,expected",[(True,"APPLIED_VERIFIED",1),(False,"APPLIED_VERIFIED",0),(True,"ABORTED",0)])
def test_degradation_rollback_uses_exact_journal_and_reports_only_verified_restore(client,admin,arm_device,monkeypatch,journal_present,execution_outcome,expected):
    from backend.database.models_phases import OptimizationEnrollment,ChangeExecution
    from backend.infrastructure import change_executor as X,runtime
    did,m=arm_device
    applied=datetime.now(timezone.utc)-timedelta(hours=2)
    class Store:
        def query(self,*,metric_name,**kw):
            rows=[]
            for after in (False,True):
                for minute in range(2,42,2):
                    rows.append({"metric_name":metric_name,"quality":"OBSERVED",
                        "observed_at":applied+(timedelta(minutes=minute) if after else -timedelta(minutes=minute)),
                        "value":5 if metric_name=="client_count" else 60 if after else 90})
            return rows
    factory=lambda:Store()
    factory.cache_clear=lambda:None
    monkeypatch.setattr(runtime,"telemetry_store",factory)
    captured=[]
    def execute(db,p,**kw):
        captured.append(p)
        return {"outcome":execution_outcome}
    monkeypatch.setattr(X,"execute_plan",execute)
    with session_scope() as db:
        d=db.get(InmDevice,did)
        post=X.seal_artifact(db,d,b"exact execution post",trigger="POST_CHANGE",actor="tester",source="DEVICE_READ")
        X.seal_artifact(db,d,b"unrelated newer backup",trigger="MANUAL",actor="tester",source="DEVICE_READ")
        p=ChangePlan(device_id=did,title="Native ARM adjustment",operations=[{"op":"ap.radio.channel","band":"5","value":0,"power":0,"arm_managed":True}],
            capability_report=[],plan_hash="test",source="AUTO_OPTIMIZER",requester="requester",state="APPLIED_VERIFIED",rollback_plan={})
        db.add(p);db.flush()
        ex=ChangeExecution(plan_id=p.id,device_id=did,environment="HARDWARE",outcome="APPLIED_VERIFIED",
            steps=[],executed_by="tester",post_artifact_id=post.id,finished_at=applied)
        db.add(ex);db.flush()
        e=OptimizationEnrollment(device_id=did,immutable_serial=d.serial_number,vendor_model="Aruba AP-505",firmware_bound=d.firmware,
            mode="DEVICE_OPT_IN",allowed_operation_types=["ap.radio.channel"],value_constraints={},permitted_windows={},impact_stop_threshold={},cooldown_seconds=3600,max_actions_per_day=2,
            expires_at_utc=datetime.now(timezone.utc)+timedelta(days=1),requester_id="requester",independent_approver_id="reviewer",
            status="ARMED",last_action_id=p.id)
        db.add(e);db.flush()
        if journal_present:
            X._journal_open(db,ex.id,p.id,did,"HARDWARE","INVERSE_OPERATIONS",
                {"writer_checkpoint":{"radio_overrides":{"5":{"channel":"149","power":"18"}}}},"test")
        count=O._check_outcome(db,d,e,"test")
        assert count==expected and e.status=="PAUSED"
        if captured:
            assert captured[0].operations==[{"op":"ap.radio.channel","band":"5","value":149,"power":18.0}]
            assert captured[0].expected_config_hash==post.plaintext_sha256
        else:
            assert "REQUIRES_REVIEW" in e.status_reason


def test_first_use_ssh_requires_saved_login_and_matching_aruba_identity(client,admin,arm_device,monkeypatch):
    from backend.infrastructure import automatic_onboarding as AO
    from backend.aruba import ssh
    did,m=arm_device
    monkeypatch.setattr("backend.api.quick_devices._permit_probe",lambda ip:None)
    raw=outputs();closed=[];calls=[]
    class Reader:
        def __init__(self,host,user,password,fp,**kw):
            assert host=="10.9.8.10" and fp=="SHA256:measured"
        def show(self,cmd):
            calls.append(cmd)
            if cmd=="show version": return {"Command output":"ArubaOS (MODEL:505), Version 8.10.0.21"}
            from backend.aruba.arm import COMMANDS
            return {"Command output":raw[next(k for k,v in COMMANDS.items() if v==cmd)]}
        def close(self): closed.append(True)
    monkeypatch.setattr(ssh,"scan_host_key",lambda *a,**kw:{"fingerprint":"SHA256:measured","key_type":"rsa"})
    monkeypatch.setattr(ssh,"SshShowClient",Reader)
    with session_scope() as db:
        AO.save_policy(db,{"enabled":True,"auto_trust_new_hosts":True,"credential_ids":None},"test")
        value=AM.collect(db,db.get(InmDevice,did))
        assert value["state"]=="MEASURED" and value["transport"]=="PINNED_SSH_DIRECT_AP"
        assert AD.pinned_fingerprint(db,"10.9.8.10")=="SHA256:measured"
    assert calls[:2]==["show ap-env","show version"] and closed


def test_optimizer_pauses_when_execution_is_not_verified(client,admin,arm_device,monkeypatch):
    from backend.database.models_phases import OptimizationEnrollment
    from backend.infrastructure import change_executor as X
    did,m=arm_device
    monkeypatch.setenv("INM_AUTOMATIC_OPTIMIZATION","true")
    monkeypatch.setattr(O,"get_settings",lambda:SimpleNamespace(device_write_lock=False,allow_config_changes=True))
    monkeypatch.setattr(X,"certification_map",lambda db,d:{"ap.radio.channel":"CERTIFIED_WRITE"})
    monkeypatch.setattr(X,"execute_plan",lambda *a,**kw:{"decision":"ALLOW","outcome":"ABORTED"})
    with session_scope() as db:
        d=db.get(InmDevice,did)
        X.seal_artifact(db,d,b"test-only full configuration",trigger="MANUAL",actor="tester",source="DEVICE_READ")
        e=OptimizationEnrollment(device_id=did,immutable_serial=d.serial_number,vendor_model="Aruba AP-505",
            firmware_bound=d.firmware,mode="DEVICE_OPT_IN",allowed_operation_types=["ap.radio.channel"],
            value_constraints={},permitted_windows={},impact_stop_threshold={},cooldown_seconds=3600,max_actions_per_day=2,
            expires_at_utc=datetime.now(timezone.utc)+timedelta(days=1),requester_id="requester",
            independent_approver_id="reviewer",status="ARMED",standing_approval=True)
        db.add(e);db.flush();eid=e.id
        result=O.run_optimizer(db,actor="test")
        assert result["executed"]==0 and result["failed"]==0 and result["denied"]==0
        db.refresh(e)
        assert e.status=="PAUSED" and "ABORTED" in e.status_reason


def test_optimizer_does_not_report_missing_arm_data_as_healthy(client,admin,arm_device):
    from backend.database.models_phases import OptimizationEnrollment
    did,m=arm_device
    with session_scope() as db:
        d=db.get(InmDevice,did)
        e=OptimizationEnrollment(device_id=did,immutable_serial=d.serial_number,vendor_model="Aruba AP-505",
            firmware_bound=d.firmware,mode="DEVICE_OPT_IN",allowed_operation_types=["ap.radio.channel"],
            value_constraints={},permitted_windows={},impact_stop_threshold={},cooldown_seconds=3600,max_actions_per_day=2,
            expires_at_utc=datetime.now(timezone.utc)+timedelta(days=1),requester_id="requester",
            independent_approver_id="reviewer",status="ARMED")
        db.add(e);db.flush()
        AD._put(db,"arm-rf:"+did,{"state":"UNAVAILABLE"},"test")
        result=O.run_optimizer(db,actor="test")
        assert result["insufficient_evidence"]==1 and result["no_adjustment_needed"]==0 and result["executed"]==0


def test_selected_optimizer_reports_pending_enrollment_without_running_other_ap(client,admin,arm_device,monkeypatch):
    from backend.database.models_phases import OptimizationEnrollment
    did,_ = arm_device
    monkeypatch.setattr(O, "propose", lambda *args: pytest.fail("Pending enrollment cannot reach optimizer proposal"))
    with session_scope() as db:
        d = db.get(InmDevice,did)
        db.add(OptimizationEnrollment(device_id=did,immutable_serial=d.serial_number,vendor_model="Aruba AP-505",
            firmware_bound=d.firmware,mode="DEVICE_OPT_IN",allowed_operation_types=["ap.radio.channel"],
            value_constraints={},permitted_windows={},impact_stop_threshold={},cooldown_seconds=3600,max_actions_per_day=2,
            expires_at_utc=datetime.now(timezone.utc)+timedelta(days=1),requester_id="requester",
            status="PENDING_APPROVAL"))
    result = client.post(f"/api/v1/devices/{did}/optimization/run-now",headers=admin)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["device_id"] == did and data["enrollment_status"] == "PENDING_APPROVAL"
    assert data["selected_for_review"] == 0 and data["skipped_not_armed"] == 1 and data["executed"] == 0


def test_selected_optimizer_counts_stale_measurements_as_evaluated(client,admin,arm_device,monkeypatch):
    from backend.database.models_phases import OptimizationEnrollment
    did,_ = arm_device
    with session_scope() as db:
        d = db.get(InmDevice,did)
        db.add(OptimizationEnrollment(device_id=did,immutable_serial=d.serial_number,vendor_model="Aruba AP-505",
            firmware_bound=d.firmware,mode="DEVICE_OPT_IN",allowed_operation_types=["ap.radio.channel"],
            value_constraints={},permitted_windows={},impact_stop_threshold={},cooldown_seconds=3600,max_actions_per_day=2,
            expires_at_utc=datetime.now(timezone.utc)+timedelta(days=1),requester_id="requester",
            independent_approver_id="reviewer",status="ARMED"))
        AD._put(db,"arm-rf:"+did,{"state":"UNAVAILABLE"},"test")
        # An unrelated armed record must not be selected even if ordered earlier.
        AD._put(db,"optimizer:cursor",{"offset":47},"test")
    original=O.propose
    def only_selected(db,device,enrollment):
        assert device.id == did
        return original(db,device,enrollment)
    monkeypatch.setattr(O,"propose",only_selected)
    result=client.post(f"/api/v1/devices/{did}/optimization/run-now",headers=admin)
    assert result.status_code==200,result.text
    data=result.json()
    assert data["device_id"]==did and data["selected_for_review"]==data["evaluated"]==1
    assert data["insufficient_evidence"]==1 and data["executed"]==0
    with session_scope() as db:
        assert AD._get(db,"optimizer:cursor")["offset"]==47


def test_empty_fleet_optimizer_explains_zero_result(client,admin,arm_device):
    with session_scope() as db:
        result=O.run_optimizer(db,actor="test")
        assert result["selected_for_review"]==0 and result["evaluated"]==0
        assert "No ARMED enrollments" in result["reason"]
