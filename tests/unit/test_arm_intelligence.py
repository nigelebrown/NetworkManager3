"""Recorded Instant 8.10 format tests. These fixtures are not live hardware certification."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import pytest
from backend.aruba import arm
from backend.domain.change_control import validate_operation,PolicyViolation

def outputs():
    return {
        "rf": """Channel Summary
phy-type channel retry phy-err mac-err noise util(Qual) cov-idx(Total) intf_idx(Total)
5GHz 149 0 0 0 92 1/1/0/0/100 0/0(0) 9/1//0/0(10)
5GHz 153 0 0 0 92 4/4/0/0/100 0/0(0) 3/2//0/0(5)
5GHz 165 2 0 0 96 0/0/0/0/100 6/0(6) 0/0//0/0(0)
2.4GHz 11 0 0 10 96 20/18/0/0/98 4/0(4) 38/5//0/0(43)
Interface Name :wifi0
Phy-Type :5GHz
Current ARM Assignment :165/18.0
Last change channel/pwr :7d:22h:17m:6s/7d:22h:17m:6s
Assignment Mode :Single Band
Interface Name :wifi1
Phy-Type :2.4GHz
Current ARM Assignment :11/12.0
Last change channel/pwr :2d:22h:49m:40s/2d:22h:49m:40s
Assignment Mode :Single Band
CCA stats history:wifi0
bcn_f: 10824 10824 10824
Channel quality history:wifi0
165:Q: 100 99 100 100 99 100
   :U: 0 0 0 1 0 0
   :R: 0 0 0 0 0 0
Channel quality history:wifi1
11:Q: 98 100 84 100 73 100 79 100 53 100
   :U: 2 0 0 0 0 0 0 0 0 0
   :R: 0 0 16 0 27 0 21 0 47 0
""",
        "history": """Interface :wifi0
Phy-Type :5GHz
2026-11-15 19:51:26 161 165 18 18 E Configured
Interface :wifi1
Phy-Type :2.4GHz
2026-11-20 21:51:59 1 11 12 12 I Configured
""",
        "neighbors": """bssid essid phy-type channel snr tx-power PL (dB) AP Flags Last Update
34:8a:12:0c:92:c0 Example 2.4GHz 1 24 12 82 Passive
34:8a:12:0c:42:50 Guest With Spaces 5GHz 149 15 18 95 Passive
""",
        "config": """Minimum Transmit Power :6
Maximum Transmit Power :21
Band Steering Mode :prefer-5ghz
Client Aware :enable
Scanning :enable
Wide Channel Bands :5ghz
80Mhz Support :disable
160Mhz Support :none
Client Match :enable
2.4 GHz Channels
1 enable
6 enable
11 enable
5.0 GHz Channels
36 disable
149 enable
153 enable
165 enable
6.0 GHz Channels
""",
        "environment":"name:Measured-AP\nuap_controller_less:1\n",
    }

def measured():
    return {**arm.parse(outputs()),"observed_at":datetime.now(timezone.utc).isoformat()}

def test_real_format_keeps_vendor_units_and_exact_assignment():
    m=measured()
    assert m["state"]=="MEASURED"
    assert len(m["channels"])==4 and m["channels"][0]["noise_floor_dbm"]==-92
    assert m["channels"][0]["interference_index"]==10
    assert m["assignments"][0]["channel"]==165 and m["assignments"][0]["power_dbm"]==18
    assert m["assignments"][0]["seconds_since_channel_change"]>86400
    assert m["config"]["allowed_channels"]["5"]==[149,153,165]
    assert m["neighbors"][0]["snr_db"]==24 and "rssi_dbm" not in m["neighbors"][0]
    assert len(m["history"])==2 and "device_local_time" in m["history"][0]
    assert "bcn_f" not in str(m["quality_history"])

def test_legacy_channel_rows_and_assignment_inference():
    data=outputs()
    data["rf"]="36 0 0 0 97 1/0/0/0/99 0/0(0) 25/28//0/0(53)\nInterface Name :wifi0\nCurrent ARM Assignment :36/18\n"
    m=arm.parse(data)
    assert m["channels"][0]["band"]=="5" and m["assignments"][0]["band"]=="5"

def test_healthy_arm_is_a_successful_keep_decision_not_forced_change():
    f=arm.assessments(measured())
    assert any(x["id"]=="arm_healthy_5" for x in f)
    assert not any(x.get("proposal") for x in f)
    assert not any("10824" in x["why"] for x in f)

def test_short_retry_spike_does_not_become_persistent_fault():
    m=measured()
    m["quality_history"]["wifi0:165"]={"quality":[100]*9+[10],"retry":[0]*9+[70]}
    f=arm.assessments(m)
    assert not any(x["id"]=="arm_quality_5" for x in f)

@pytest.mark.parametrize("offset",[timedelta(minutes=-16),timedelta(minutes=2)])
def test_stale_and_future_measurements_never_propose_changes(offset):
    m=measured()
    m["observed_at"]=(datetime.now(timezone.utc)+offset).isoformat()
    m["overrides"]["5"]={"chan":149,"tx_power":18}
    assert [x["id"] for x in arm.assessments(m)]==["arm_missing"]

def test_missing_or_empty_data_is_unknown_never_healthy():
    assert arm.parse({})["state"]=="UNAVAILABLE"
    assert arm.assessments({})[0]["id"]=="arm_missing"

def test_static_override_proposes_explicit_return_to_arm():
    data=outputs();data["environment"]+="a_chan:149\na_tx_power:18\n"
    f=next(x for x in arm.assessments({**arm.parse(data),"observed_at":datetime.now(timezone.utc).isoformat()})
           if x["id"]=="arm_override_5")
    assert f["proposal"]["arm_managed"] and f["proposal"]["to"]==0 and f["proposal"]["power"]==0
    assert validate_operation({"op":"ap.radio.channel","band":"5","value":0,"power":0,"arm_managed":True},family="access_point")["value"]==0
    with pytest.raises(PolicyViolation):
        validate_operation({"op":"ap.radio.channel","band":"5","value":0,"power":0},family="access_point")

def test_sustained_quality_uses_measured_enabled_channels_and_settling_age():
    m=measured()
    m["assignments"][0]["channel"]=149
    m["channels"][0].update(util_percent=90,quality_percent=50,interference_index=100)
    m["quality_history"]["wifi0:149"]={"quality":[50]*10,"retry":[40]*10}
    f=next(x for x in arm.assessments(m) if x["id"]=="arm_quality_5")
    assert f["proposal"]["to"] in [153,165] and f["proposal"]["to"]!=36
    m["assignments"][0]["seconds_since_channel_change"]=30
    assert next(x for x in arm.assessments(m) if x["id"]=="arm_quality_5")["proposal"] is None

def test_configuration_failure_does_not_invent_legal_channels():
    m=measured();m["config"]={}
    m["assignments"][0]["channel"]=149
    m["channels"][0].update(util_percent=90,quality_percent=50,interference_index=100)
    m["quality_history"]["wifi0:149"]={"quality":[50]*10}
    assert not any(f.get("proposal") for f in arm.assessments(m))

def test_baseline_differences_are_comparisons_not_auto_application():
    from backend.api.ap_operations import _arm_baseline_comparison
    m=measured()
    b={"source_name":"Commissioners Office initial reference","config":deepcopy(m["config"])}
    b["config"]["Client Match"]="disable"
    r=_arm_baseline_comparison(m,b)
    assert r["differences"][0]["setting"]=="Client Match"
    assert r["automatic_application"] is False

def test_native_arm_operation_cannot_be_used_to_hide_arbitrary_manual_channel():
    with pytest.raises(PolicyViolation):
        validate_operation({"op":"ap.radio.channel","band":"5","value":149,"power":18,"arm_managed":True},family="access_point")
