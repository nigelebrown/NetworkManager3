"""Phase 3 API E2E contract: author -> preview -> draft against baseline."""
from backend.database.session import session_scope
from backend.database.models_onboarding import OnboardingConfigSnapshot
from tests.conftest import make_user

def register(client, admin, serial, family="switch"):
    res=client.post("/api/v1/quick-devices",headers=admin,json={
        "site_name":"HQ Pilot","family":family,"hostname":serial,
        "serial_number":serial,"model":"CX 6200F 24G" if family=="switch" else "AP-535",
        "status":"unknown","metrics":{}})
    assert res.status_code==201,res.text
    return res.json()["id"]

def add(client,admin,name,scope,config,*,family="switch",site_id=None,device_id=None,model=None):
    return client.post("/api/v1/config-templates",headers=admin,json={
        "name":name,"scope":scope,"family":family,"site_id":site_id,
        "device_id":device_id,"model_contains":model,
        "priority":100,"enabled":True,"config":config})

def test_inheritance_api_and_baseline_gated_draft(client,admin):
    did=register(client,admin,"PHASE3-CX-001")
    sites=client.get("/api/v1/sites",headers=admin).json()["sites"]
    site_id=next(x["id"] for x in sites if x["name"]=="HQ Pilot" and x["source_type"]=="REAL")
    for args in [
      ("Global CX","global",{"vlans":[100,200],"interfaces":[{"name":"1/1/1","access_vlan":100}]},None,None,"6200F"),
      ("HQ Standards","site",{"vlans":[300],"interfaces":[{"name":"1/1/1","access_vlan":200}]},site_id,None,None),
      ("CX Exception","device",{"vlans":[400],"interfaces":[{"name":"1/1/1","access_vlan":300}]},None,did,None),
    ]:
        name,scope,config,site,device,model=args
        res=add(client,admin,name,scope,config,site_id=site,device_id=device,model=model)
        assert res.status_code==201,res.text
    fetched=client.get("/api/v1/config-templates/devices/"+did+"/preview",headers=admin)
    assert fetched.status_code==200,fetched.text
    data=fetched.json()
    assert data["effective"]["vlans"]==[100,200,300,400]
    assert data["effective"]["interfaces"][0]["access_vlan"]==300
    assert data["provenance"]["interface:1/1/1"]=="CX Exception"
    assert data["applied_to_hardware"] is False
    missing=client.post("/api/v1/config-templates/devices/"+did+"/draft",headers=admin)
    assert missing.status_code==409
    with session_scope() as db:
        db.add(OnboardingConfigSnapshot(
          device_id=did,source="aruba_cx_rest",completeness="PARTIAL",
          config_json={"vlans":[{"id":100}],"interfaces":[]},"warning":"CI fixture only",
          collected_by="test"))
    draft=client.post("/api/v1/config-templates/devices/"+did+"/draft",headers=admin)
    assert draft.status_code==201,draft.text
    assert draft.json()["state"]=="DRAFT"
    assert draft.json()["hardware_writes"]=="LOCKED"
    saved=client.get("/api/v1/onboarding/devices/"+did+"/config",headers=admin).json()
    assert saved["drafts"][0]["proposed"]["vlans"]==[100,200,300,400]

def test_scope_validation_rbac_and_revision_conflict(client,admin):
    did=register(client,admin,"PHASE3-AP-001","access_point")
    viewer=make_user(client,admin,"p3.viewer","viewer")
    res=add(client,admin,"Office AP","device",{"vlans":[100],
        "ssids":[{"profile":"Office","essid":"Office","vlan":100,"channel_width_mhz":20}]},
        family="access_point",device_id=did)
    assert res.status_code==201,res.text
    tid=res.json()["id"]
    assert client.get("/api/v1/config-templates",headers=viewer).status_code==403
    assert client.get("/api/v1/config-templates/devices/"+did+"/preview",headers=viewer).status_code==403
    before=client.patch("/api/v1/config-templates/"+tid,headers=admin,json={
        "expected_revision":1,"priority":200})
    assert before.status_code==200 and before.json()["revision"]==2
    stale=client.patch("/api/v1/config-templates/"+tid,headers=admin,json={
        "expected_revision":1,"enabled":False})
    assert stale.status_code==409
    bad=add(client,admin,"Invalid 80","global",{"vlans":[100],
        "ssids":[{"profile":"Bad","essid":"Bad","channel_width_mhz":80}]},
        family="access_point")
    assert bad.status_code==422
    assert add(client,admin,"Cross Device","device",{"vlans":[100]},
        family="switch",device_id=did).status_code==422
    assert add(client,admin,"Ghost Site","site",{"vlans":[100]},
        site_id="not-real").status_code==422
    assert add(client,admin,"Secret config","global",{"vlans":[100],"password":"secret"}).status_code==422
