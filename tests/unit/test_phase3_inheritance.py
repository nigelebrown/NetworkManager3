"""Phase 3 inheritance precedence, provenance and write-lock semantics."""
from types import SimpleNamespace
from backend.domain.template_inheritance import preview

def t(name, scope, config, *, family="switch", site=None, device=None,
      model=None, priority=100, enabled=True):
    return SimpleNamespace(
        id=name, name=name, scope=scope, family=family,
        site_id=site, device_id=device, model_contains=model,
        priority=priority, enabled=enabled, revision=1, config_json=config)

def test_global_site_device_order_union_vlan_and_port_override():
    dev = SimpleNamespace(id="dev1", source_type="REAL", site_id="hq",
                          family="switch", model="CX 6200F 24G")
    templates=[
        t("Global 24 port", "global", {"vlans":[100,200,300],
            "interfaces":[{"name":"1/1/1","access_vlan":100}]}, model="6200F"),
        t("Head Office", "site", {"vlans":[400],
            "interfaces":[{"name":"1/1/1","access_vlan":200}]}, site="hq"),
        t("Port Exception", "device", {"vlans":[500],
            "interfaces":[{"name":"1/1/1","access_vlan":300}]}, device="dev1"),
        t("Another Site", "site", {"vlans":[999]}, site="elsewhere"),
        t("Wrong Model", "global", {"vlans":[888]}, model="6300"),
    ]
    data=preview(dev, templates)
    assert data["effective"]["vlans"]==[100,200,300,400,500]
    assert data["effective"]["interfaces"]==[{"name":"1/1/1","access_vlan":300}]
    assert [x["scope"] for x in data["templates"]]==["global","site","device"]
    assert data["provenance"]["interface:1/1/1"]=="Port Exception"
    assert data["applied_to_hardware"] is False and data["operation"]=="PREVIEW_ONLY"

def test_ap_20_40_widths_inherit_by_ssid_identity_without_secret_fields():
    dev=SimpleNamespace(id="AP1",source_type="REAL",site_id="hq",
                        family="access_point",model="AP-535")
    data=preview(dev,[
        t("global-ssid","global",{"vlans":[100],"ssids":[
            {"profile":"Admin","essid":"Admin","channel_width_mhz":20}]},family="access_point"),
        t("site-ssid","site",{"vlans":[200],"ssids":[
            {"profile":"Admin","essid":"HQ Admin","channel_width_mhz":40}]},
            family="access_point",site="hq"),
    ])
    assert data["effective"]["vlans"]==[100,200]
    assert data["effective"]["ssids"][0]["essid"]=="HQ Admin"
    assert data["effective"]["ssids"][0]["channel_width_mhz"]==40
    assert data["applied_to_hardware"] is False
