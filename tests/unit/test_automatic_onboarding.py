"""Automatic onboarding: fake network I/O, real persisted cursors and credentials."""
import pytest
from sqlalchemy import select

from backend.database.session import create_schema, session_scope
from backend.database.models import AccessPoint, AppSetting, Site
from backend.database.models_inm import InmDevice, JobLease
from backend.database.models_onboarding import OnboardingCredential, OnboardingDeviceCredential
from backend.database.models_phases import CollectionPlan
from backend.infrastructure import ap_autodiscovery as AD, automatic_onboarding as AO
from backend.security.crypto import encrypt_secret


@pytest.fixture
def env(app_env):
    app_env.setenv("SIMS_MODE", "live")
    app_env.setenv("INM_READONLY_DEVICE_CIDRS", "10.9.8.0/24")
    from backend.config import get_settings
    get_settings.cache_clear()
    create_schema()
    with session_scope() as db:
        ps = []
        for name, family in (("bad", "access_point"), ("good", "access_point"), ("switch", "switch")):
            p = OnboardingCredential(name=name, device_family=family,
                protocol="aruba_instant_rest" if family=="access_point" else "aruba_cx_rest",
                username=name, password_enc=encrypt_secret("test-only"), created_by="tester")
            db.add(p); db.flush(); ps.append(p.id)
        ids = []
        for i, family in enumerate(("access_point", "access_point", "switch")):
            d = InmDevice(source_type="REAL", environment="lab", vendor="Aruba", family=family,
                model="AP-505" if family=="access_point" else "CX 6200F",
                serial_number=f"S{i}", hostname=f"Device-{i}", adapter="manual_snapshot",
                mgmt_addresses=[f"10.9.8.{i+10}"])
            db.add(d); db.flush(); ids.append(d.id)
    return ids, ps


def rest_off(*args):
    from backend.aruba.errors import ArubaRestDisabledError
    raise ArubaRestDisabledError("off")


def members():
    return [{"name":"Device-0","ip":"10.9.8.10","serial":"S0","is_master":True,"mac":"aa:aa:aa:aa:aa:01"}]


def poll(db, site):
    site.last_poll_ok = True
    if not db.scalar(select(AccessPoint).where(AccessPoint.site_id==site.id)):
        db.add(AccessPoint(site_id=site.id,name="Device-0",mac="aa:aa:aa:aa:aa:01",
                           ip="10.9.8.10",serial="S0"))
    return True


def test_first_use_pinned_only_after_working_saved_credential(env):
    ids, profiles = env
    attempts = []
    def ssh(ip, user, password, fp):
        attempts.append(user)
        from backend.aruba.errors import ArubaAuthError
        if user=="bad":
            raise ArubaAuthError("rejected")
        return members(), {"transport":"ssh_cli","ssh_host":ip,"fingerprint":fp}
    with session_scope() as db:
        db.add(OnboardingDeviceCredential(device_id=ids[0], credential_id=profiles[0], bound_by="t"))
        db.flush()
        out=AD.discover(db,"t",device_ids=[ids[0]],rest_members=rest_off,ssh_members=ssh,poll=poll,
            auto_trust_new_hosts=True,key_scanner=lambda ip:{"fingerprint":"SHA256:first","key_type":"rsa"})
        assert out["counts"]=={"CLUSTER_REGISTERED":1}
        assert attempts==["bad","good"]
        assert AD.pinned_fingerprint(db,"10.9.8.10")=="SHA256:first"
        assert db.get(OnboardingDeviceCredential,ids[0]).credential_id==profiles[1]


def test_failed_login_never_pins_first_key(env):
    ids, profiles=env
    def ssh(*args):
        from backend.aruba.errors import ArubaAuthError
        raise ArubaAuthError("rejected")
    with session_scope() as db:
        out=AD.discover(db,"t",device_ids=[ids[0]],rest_members=rest_off,ssh_members=ssh,poll=poll,
            auto_trust_new_hosts=True,key_scanner=lambda ip:{"fingerprint":"SHA256:untrusted"})
        assert "CLUSTER_REGISTERED" not in out["counts"]
        assert AD.pinned_fingerprint(db,"10.9.8.10") is None
        assert db.get(OnboardingDeviceCredential,ids[0]) is None


def test_changed_known_key_never_gets_credentials(env):
    ids, profiles=env
    def no_ssh(*args):
        raise AssertionError("credentials must not be sent to a changed key")
    with session_scope() as db:
        AD._put(db,"ssh-hostkey:10.9.8.10",{"fingerprint":"SHA256:old"},"t")
        out=AD.discover(db,"t",device_ids=[ids[0]],rest_members=rest_off,ssh_members=no_ssh,poll=poll,
            auto_trust_new_hosts=True,key_scanner=lambda ip:{"fingerprint":"SHA256:new"})
        assert out["counts"]=={"SSH_HOST_KEY_CHANGED":1}
        assert AD.pinned_fingerprint(db,"10.9.8.10")=="SHA256:old"


def test_only_selected_profiles_are_used(env):
    ids, profiles=env
    attempts=[]
    def ssh(ip,user,password,fp):
        attempts.append(user)
        return members(),{"transport":"ssh_cli","ssh_host":ip,"fingerprint":fp}
    with session_scope() as db:
        out=AD.discover(db,"t",device_ids=[ids[0]],credential_ids=[profiles[1]],
            auto_trust_new_hosts=True,rest_members=rest_off,ssh_members=ssh,poll=poll,
            key_scanner=lambda ip:{"fingerprint":"SHA256:first"})
        assert attempts==["good"] and out["counts"]=={"CLUSTER_REGISTERED":1}


def test_api_queue_survives_status_reads_and_worker_restart(env):
    ids, _=env
    with session_scope() as db:
        AO.queue(db,"t",device_ids=ids[:2])
        assert AD.job_status(db)["state"]=="QUEUED"
    seen=[]
    def process(db,d,actor,options):
        seen.append(d.id)
        return {"device_id":d.id,"result":"LINKED"}
    AO.tick(session_scope,"worker-one",batch_size=1,process=process)
    with session_scope() as db:
        status=AD.job_status(db)
        assert status["state"]=="RUNNING" and status["done"]==1
    AO.tick(session_scope,"replacement-worker",batch_size=1,process=process)
    with session_scope() as db:
        status=AD.job_status(db)
        assert status["state"]=="SUCCEEDED" and status["done"]==2
        assert status["counts"]=={"LINKED":2}
    assert seen==ids[:2]


def test_one_device_failure_does_not_abort_remaining_devices(env):
    ids,_=env
    with session_scope() as db:
        AO.queue(db,"t",device_ids=ids[:2])
    def process(db,d,actor,options):
        if d.id==ids[0]:
            raise RuntimeError("no credentials in errors")
        return {"device_id":d.id,"result":"LINKED"}
    AO.tick(session_scope,"worker",batch_size=2,process=process)
    with session_scope() as db:
        status=AD.job_status(db)
        assert status["done"]==2 and status["counts"]=={"FAILED":1,"LINKED":1}
        assert status["results"][0]["detail"]=="RuntimeError"


def test_exclusive_worker_lease_prevents_duplicate_processing(env):
    ids,_=env
    from backend.infrastructure.change_executor import _lease,_release
    with session_scope() as db:
        AO.queue(db,"t",device_ids=ids[:1])
        assert _lease(db,AO.LEASE_DEVICE,"other-worker",ttl=300)
    assert AO.tick(session_scope,"worker")["busy"]
    with session_scope() as db:
        assert AD.job_status(db)["done"]==0
        _release(db,AO.LEASE_DEVICE,"other-worker")


def test_switch_success_creates_one_active_monitoring_plan(env):
    ids,profiles=env
    with session_scope() as db:
        d=db.get(InmDevice,ids[2])
        observed={"serial_number":d.serial_number,"hostname":"Verified-CX","model":"CX 6200F","firmware":"10.14.0001"}
        out=AO._switch(db,d,"t",{"credential_ids":[profiles[2]]},probe=lambda *a:observed)
        assert out["result"]=="SWITCH_MONITORING_ENABLED"
        assert db.get(OnboardingDeviceCredential,d.id).credential_id==profiles[2]
        AO._switch(db,d,"t",{"credential_ids":[profiles[2]]},probe=lambda *a:observed)
        assert len(list(db.scalars(select(CollectionPlan))))==1
        assert db.scalar(select(CollectionPlan)).state=="ACTIVE"


def test_switch_identity_mismatch_is_not_enrolled(env):
    ids,profiles=env
    with session_scope() as db:
        d=db.get(InmDevice,ids[2])
        out=AO._switch(db,d,"t",{},probe=lambda *a:{"serial_number":"OTHER"})
        assert out["result"]=="IDENTITY_MISMATCH"
        assert db.get(OnboardingDeviceCredential,d.id) is None
        assert db.scalar(select(CollectionPlan)) is None


def test_enabled_policy_picks_up_imported_devices_without_api_request(env):
    ids,profiles=env
    with session_scope() as db:
        AO.save_policy(db,{"enabled":True,"credential_ids":[profiles[1],profiles[2]]},"t")
    seen=[]
    def process(db,d,actor,options):
        seen.append(d.id)
        assert options["credential_ids"]==[profiles[1],profiles[2]]
        return {"device_id":d.id,"result":"LINKED"}
    AO.tick(session_scope,"worker",batch_size=3,process=process)
    assert set(seen)==set(ids)
    with session_scope() as db:
        assert AD.job_status(db)["state"]=="SUCCEEDED"


def test_legacy_interrupted_run_is_requeued_without_losing_saved_clusters(env):
    ids,_=env
    with session_scope() as db:
        AD._put(db,AD.JOB_KEY,{"state":"RUNNING","done":10,"total":50,"clusters_saved":6,"by":"t"},"t")
    AO.tick(session_scope,"new-worker",batch_size=1,
            process=lambda db,d,*args:{"device_id":d.id,"result":"ALREADY_MONITORED"})
    with session_scope() as db:
        status=AD.job_status(db)
        assert status["done"]==1 and "device_ids" in status
        assert status["device_ids"]==ids[:2]
