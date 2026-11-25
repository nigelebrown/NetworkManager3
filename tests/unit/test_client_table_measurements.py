"""Sanitized firmware-shaped CLI tables: blank names are valid, dropped clients are not."""
from backend.aruba import parsers
from backend.aruba.monitoring import collect_snapshot
import pytest

HEADERS=["Name","IP Address","MAC Address","OS","ESSID","Access Point","Channel","Type","Role","IPv6 Address","Signal","Speed (mbps)"]
WIDTHS=[36,17,19,9,19,26,9,6,19,41,10,14]
def line(values):
    return "".join(str(v).ljust(w) for v,w in zip(values,WIDTHS))
def table(count=2,broken=False):
    return "\n".join(["Client List","-----------",line(HEADERS),line(["-"*len(h) for h in HEADERS]),
      line(["","10.9.8.21","aa:bb:cc:00:00:01","Win 10","Staff","Office  AP","149","AC","Staff","--","35(good)","144(ok)"]),
      line(["Second","10.9.8.22","bad" if broken else "aa:bb:cc:00:00:02","Android","Staff","Office  AP","11","g-HE","Staff","","24(good)","72(ok)"]),
      "Number of Clients   :"+str(count),"Info timestamp      :4239980"])
class Reader:
    def __init__(self,raw):self.raw=raw
    def show(self,cmd,**kw):
        if cmd=="show clients":return {"Command output":self.raw}
        if cmd=="show aps":return {"show aps":[{"Name":"Office AP","IP Address":"10.9.8.10","Serial #":"EXAMPLE-1","Type":"505(indoor)"}]}
        if cmd=="show version":return {"Command output":"ArubaOS Version 8.10.0.21"}
        return {"Command output":""}

def test_fixed_columns_preserve_blank_name_ipv6_and_internal_spaces():
    rows=parsers.parse_text_table(table())
    assert len(rows)==2
    assert rows[0]["Name"]=="" and rows[0]["MAC Address"]=="aa:bb:cc:00:00:01"
    assert rows[1]["IPv6 Address"]=="" and rows[1]["Signal"]=="24(good)"
    clients=parsers.normalize_clients(rows)
    assert len(clients)==2 and clients[0]["ap_name"]=="Office  AP"
    assert clients[0]["snr_db"]==35 and clients[0]["speed_mbps"]==144

def test_complete_inventory_becomes_valid_without_weakening_guard():
    snap=collect_snapshot(Reader(table()))
    assert snap.clients_ok and len(snap.clients)==2
    assert snap.clients[0]["hostname"] is None

@pytest.mark.parametrize("raw",[table(count=3),table(broken=True)])
def test_missing_or_malformed_rows_still_block_disconnect_inference(raw):
    assert not collect_snapshot(Reader(raw)).clients_ok

def test_explicit_empty_inventory_is_not_replaced_by_an_unrelated_table():
    assert parsers.extract_rows({"show clients":[],"other":[{"MAC Address":"aa:bb:cc:00:00:01"}]},"show clients")==[]
