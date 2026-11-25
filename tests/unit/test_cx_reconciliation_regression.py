"""Regression coverage for CX facts, reconciliation and native VLAN drift."""
from backend.domain.desired_state import cx_facts, reconcile


def test_nested_qos_and_reference_maps_match_intent():
    cfg = {
        "vlans": {"1": {}, "20": {}},
        "interfaces": {"1/1/1": {
            "vlan_mode": "native-untagged",
            "vlan_tag": {"native": "/rest/v10.14/system/vlans/1"},
            "vlan_trunks": {"member": "/rest/v10.14/system/vlans/20"},
            "qos_config": {"qos_trust": "dscp"},
        }},
    }
    intent = {"interfaces": [{"name": "1/1/1", "trunk_vlans": [20],
                              "native_vlan": 1, "qos_trust": "dscp"}]}
    assert reconcile("switch", intent, cx_facts(cfg)) == []
    cfg["interfaces"]["1/1/1"]["vlan_tag"] = "/rest/v10.14/system/vlans/20"
    assert reconcile("switch", intent, cx_facts(cfg)) == [
        {"op": "switch.port.trunk_vlans", "port": "1/1/1", "value": [20], "native": 1},
    ]


def test_legacy_qos_field_is_supported():
    cfg = {"interfaces": {"1/1/1": {"qos_trust": "cos"}}}
    assert cx_facts(cfg)["ports"]["1/1/1"]["qos_trust"] == "cos"
