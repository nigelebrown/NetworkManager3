"""Collection must work for imported APs and remain attributable to the local hardware."""
from types import SimpleNamespace
from backend.infrastructure import arm_measurements as AM
from backend.database.session import session_scope
from backend.database.models import Site, AppSetting
from backend.database.models_inm import InmDevice
from backend.infrastructure import ap_autodiscovery as AD
from tests.contract.test_arm_workflow import arm_device
from tests.unit.test_arm_intelligence import outputs

def test_default_name_uses_local_serial_not_cluster_name():
    device=SimpleNamespace(hostname="34:8a:12:c8:e5:14",serial_number="PHPBKPP6Y3")
    calls=[]
    class Reader:
        def show(self,cmd,**kw):
            calls.append((cmd,kw))
            return {"Command output":"Name :SetMeUp-C8:DA:EA\nIP Address :10.190.172.111\nSerial Number :PHPBKPP6Y3\n"}
    identity=AM._identity(Reader(),{"environment":"Antenna Type :internal\n"},device)
    assert identity["verified"] and identity["method"]=="LOCAL_SERIAL_NUMBER"
    assert calls==[("show summary",{})]

def test_targeted_summary_cannot_attribute_controller_data_to_a_member():
    device=SimpleNamespace(hostname="default-mac",serial_number="MEMBER-1")
    class Reader:
        def show(self,cmd,**kw):
            assert kw=={"iap_ip":"10.9.8.10"}
            return {"Command output":"IP Address :10.9.8.1\nSerial Number :CONTROLLER-1\n"}
    assert not AM._identity(Reader(),{},device,"10.9.8.10")["verified"]

def test_missing_summary_serial_never_falls_back_to_assumed_identity():
    device=SimpleNamespace(hostname="renamed",serial_number="EXPECTED-1")
    class Reader:
        def show(self,cmd,**kw):return {"Command output":"Serial Number :DIFFERENT-1\n"}
    assert not AM._identity(Reader(),{"environment":"name:another-device\n"},device)["verified"]

def test_default_name_collection_retains_real_measurements(client,admin,arm_device,monkeypatch):
    did,_=arm_device
    monkeypatch.setattr("backend.api.quick_devices._permit_probe",lambda ip:None)
    raw=outputs();raw["environment"]="Antenna Type:internal\n"
    from backend.aruba.arm import COMMANDS
    class Reader:
        def show(self,cmd):
            if cmd=="show summary":
                return {"Command output":"Serial Number :REAL-REFERENCE-1\nIP Address :10.9.8.10\n"}
            return {"Command output":raw[next(k for k,v in COMMANDS.items() if v==cmd)]}
    with session_scope() as db:
        value=AM.collect(db,db.get(InmDevice,did),client=Reader())
        assert value["state"]=="MEASURED" and value["identity"]["verified"]
        assert value["identity"]["method"]=="LOCAL_SERIAL_NUMBER"

def test_unreachable_is_unavailable_not_a_fabricated_identity_mismatch(client,admin,arm_device,monkeypatch):
    did,_=arm_device
    monkeypatch.setattr("backend.api.quick_devices._permit_probe",lambda ip:None)
    class Reader:
        def show(self,cmd):raise TimeoutError()
    with session_scope() as db:
        value=AM.collect(db,db.get(InmDevice,did),client=Reader())
        assert value["state"]=="UNAVAILABLE" and value["errors"]=={"rf":"TimeoutError"}

def test_scheduler_attempts_imported_credentialed_ap_without_cluster(client,admin,arm_device,monkeypatch):
    did,_=arm_device
    calls=[]
    monkeypatch.setattr(AM,"_lease",lambda *a,**kw:True)
    monkeypatch.setattr(AM,"_release",lambda *a,**kw:None)
    def collect(db,device):
        calls.append(device.id)
        return {"state":"MEASURED"}
    monkeypatch.setattr(AM,"collect",collect)
    with session_scope() as db:
        row=db.get(AppSetting,AM.PREFIX+did)
        db.delete(row)
    result=AM.tick(session_scope,"test-imported",limit=2)
    assert did in calls and result["measured"]>=1

def test_failed_vc_rest_poll_does_not_stop_direct_ap_measurements(client,admin,arm_device,monkeypatch):
    did,_=arm_device
    calls=[]
    monkeypatch.setattr(AM,"_lease",lambda *a,**kw:True)
    monkeypatch.setattr(AM,"_release",lambda *a,**kw:None)
    monkeypatch.setattr(AM,"collect",lambda db,d:(calls.append(d.id) or {"state":"MEASURED"}))
    with session_scope() as db:
        site=Site(name="Failed VC",vc_host="10.9.8.1",api_username="reader",api_password_enc="not-used",
                  simulated=False,enabled=True,last_poll_ok=False)
        db.add(site);db.flush()
        db.get(InmDevice,did).legacy_site_id=site.id
    AM.tick(session_scope,"test-vc-outage",limit=2)
    assert did in calls
