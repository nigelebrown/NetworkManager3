"""Aruba Instant ARM measurements, parsed from read-only show commands.

Metrics retain their vendor units. BSSID rows are radios/SSIDs, never a count of physical APs.
Device-local history timestamps are not relabelled as UTC. No channel is inferred from cluster membership.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone

SOURCE = "ARUBA_ARM"
REFERENCE = "https://arubanetworking.hpe.com/techdocs/CLI-Bank/Content/instant/sh-ap-arm.htm"
COMMANDS = {"rf": "show ap arm rf-summary", "history": "show ap arm history",
            "neighbors": "show ap arm neighbors", "config": "show arm config", "environment": "show ap-env"}


def channel(value):
    m = re.match(r"^(\d+)", str(value).strip())
    return int(m[1]) if m else None


def duration(value):
    parts = re.findall(r"(\d+)([dhms])", value)
    return sum(int(v) * {"d":86400,"h":3600,"m":60,"s":1}[u] for v,u in parts) if parts else None


def parse(outputs):
    result = {"source": SOURCE, "reference": REFERENCE, "channels": [], "assignments": [],
              "history": [], "neighbors": [], "quality_history": {}, "config": {}, "overrides": {},
              "unavailable": [], "parser_version": 1}
    for key in COMMANDS:
        if not outputs.get(key):
            result["unavailable"].append(key)
    rf = outputs.get("rf", "")
    for line in rf.splitlines():
        # 8.10 includes phy-type; older Instant output starts with the channel.
        m = re.match(r"^\s*(?:(2\.4GHz|5GHz|6GHz)\s+)?(\d+[+\-ES]?)\s+(\d+)\s+(\d+)\s+(\d+)\s+([*\-]?\d+)\s+(\d+)/(\d+)/(\d+)/(\d+)/(\d+)\s+(\S+)\s+(\S+)\s*$", line)
        if not m:
            continue
        phy, ch, retry, phyerr, macerr, noise, util, rx, tx, ext, quality, cov, interf = m.groups()
        c = channel(ch)
        vals = [int(x) for x in (retry,util,rx,tx,ext,quality)]
        if any(not 0 <= v <= 100 for v in vals):
            continue
        total = lambda s: int(re.search(r"\((\d+)\)", s)[1]) if re.search(r"\((\d+)\)", s) else None
        result["channels"].append({"band": "2.4" if phy == "2.4GHz" or (not phy and c <= 14) else "6" if phy == "6GHz" else "5",
            "channel": c, "channel_label":ch, "retry_percent":int(retry), "phy_errors":int(phyerr),
            "mac_errors":int(macerr), "noise_raw":noise, "noise_floor_dbm":-abs(int(noise.lstrip("*"))),
            "split_scan":"*" in noise, "util_percent":int(util), "rx_percent":int(rx), "tx_percent":int(tx),
            "external_util_percent":int(ext), "quality_percent":int(quality),
            "coverage_index":total(cov), "interference_index":total(interf)})
    current = None
    for line in rf.splitlines():
        m = re.match(r"\s*Interface Name\s*:\s*(\S+)",line)
        if m:
            current = {"interface": m[1]}
            result["assignments"].append(current)
        if current is None:
            continue
        m = re.match(r"\s*Phy-Type\s*:\s*(2\.4|5|6)GHz",line)
        if m: current["band"] = m[1]
        m = re.match(r"\s*Current ARM Assignment\s*:\s*(\d+[+\-ES]?)/([\d.]+)",line)
        if m:
            current.update(channel=channel(m[1]), channel_label=m[1], power_dbm=float(m[2]))
            current.setdefault("band","2.4" if channel(m[1]) <= 14 else "5")
        m = re.match(r"\s*Last change channel/pwr\s*:\s*(\S+)",line)
        if m: current["seconds_since_channel_change"] = duration(m[1].split("/")[0])
        m = re.match(r"\s*Assignment Mode\s*:\s*(.+)",line)
        if m: current["assignment_mode"] = m[1].strip()
    iface, ch = None, None
    for line in rf.splitlines():
        m = re.search(r"Channel quality history:\s*(\S+)",line)
        if m: iface, ch = m[1], None
        m = re.match(r"\s*(\d+)\s*:\s*Q:\s*(.*)",line)
        if m and iface:
            ch = m[1]
            result["quality_history"][iface+":"+ch] = {"quality": [int(v) for v in m[2].split() if v.isdigit()][-60:]}
        m = re.match(r"\s*:([UR]):\s*(.*)",line)
        if m and iface and ch:
            result["quality_history"][iface+":"+ch]["non_wifi_util" if m[1]=="U" else "retry"] = [int(v) for v in m[2].split() if v.isdigit()][-60:]
    iface, band = None, None
    for line in outputs.get("history","").splitlines():
        m = re.match(r"\s*Interface\s*:\s*(\S+)",line)
        if m: iface = m[1]
        m = re.match(r"\s*Phy-Type\s*:\s*(2\.4|5|6)GHz",line)
        if m: band = m[1]
        m = re.match(r"\s*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(.*))?$",line)
        if m:
            result["history"].append(dict(zip(
                ("device_local_time","old_channel","new_channel","old_power","new_power","reason","result"),m.groups()),
                interface=iface,band=band))
    # Preserve SNR versus RSSI explicitly; firmware 8.10 reports SNR here, not signed dBm.
    neighbor_unit = None
    for line in outputs.get("neighbors","").splitlines():
        if "bssid" in line.lower() and ("snr" in line.lower() or "rssi" in line.lower()):
            neighbor_unit = "snr_db" if "snr" in line.lower() else "rssi_vendor"
        m = re.match(r"\s*([0-9a-fA-F:]{17})\s+(.*?)\s+(2\.4GHz|5GHz|6GHz)\s+(\S+)\s+(-?\d+)\s+(-?\d+)\s+(\d+)\s+(Active|Passive|Indirect)",line)
        if m:
            bssid,_,phy,ch,strength,power,loss,flags = m.groups()
            result["neighbors"].append({"bssid":bssid.lower(),"band":{"2.4GHz":"2.4","5GHz":"5","6GHz":"6"}[phy],
                "channel":channel(ch),neighbor_unit or "signal_vendor":int(strength),
                "tx_power_dbm":int(power) if int(power) else None,"path_loss_db":int(loss) if int(loss) else None,"flags":flags})
    cfg = result["config"]
    band = None
    cfg["allowed_channels"] = {}
    for line in outputs.get("config","").splitlines():
        m = re.match(r"\s*(2\.4|5\.0|6\.0) GHz Channels",line)
        if m: band = {"2.4":"2.4","5.0":"5","6.0":"6"}[m[1]]; cfg["allowed_channels"][band] = []
        m = re.match(r"\s*(\d+)\s+enable\s*$",line)
        if m and band: cfg["allowed_channels"][band].append(int(m[1]))
        if ":" in line:
            key, val = [x.strip() for x in line.split(":",1)]
            if key in ("Minimum Transmit Power","Maximum Transmit Power","Client Aware","Scanning",
                       "Wide Channel Bands","80Mhz Support","160Mhz Support","Client Match",
                       "Band Steering Mode","Channel Quality Aware","Channel Quality Threshold",
                       "Channel Quality Wait Time","Error Rate Threshold","Error Rate Wait Time"):
                cfg[key] = int(val) if val.isdigit() else val
    env = outputs.get("environment","")
    for b,p in (("5","a"),("2.4","g")):
        values = {}
        for field in ("chan","tx_power"):
            m = re.search(r"(?im)^"+p+"_"+field+r"\s*:\s*(-?\d+(?:\.\d+)?)\s*$",env)
            if m: values[field] = float(m[1])
        result["overrides"][b] = values
    result["history"] = result["history"][:200]
    result["neighbors"] = result["neighbors"][:500]
    result["state"] = "MEASURED" if result["channels"] and result["assignments"] else "UNAVAILABLE"
    return result


def assessments(snapshot, *, now=None):
    """Conservative ARM-native rules. Healthy ARM is a successful optimization outcome."""
    now = now or datetime.now(timezone.utc)
    raw = snapshot.get("observed_at")
    age = (now - datetime.fromisoformat(raw.replace("Z","+00:00"))).total_seconds() if raw else None
    if age is None or age < -60 or age > 900 or snapshot.get("state") != "MEASURED":
        return [{"id":"arm_missing","severity":"info","title":"Fresh ARM measurements required",
                 "why":"No current per-AP ARM RF evidence is available.","actions":["Collect ARM evidence before proposing an RF change."],
                 "confidence":"high","owner":"Monitoring","proposal":None,"evidence":{}}]
    findings = []
    def finding(fid,severity,title,why,actions,evidence,proposal=None):
        findings.append({"id":fid,"severity":severity,"title":title,"why":why,"actions":actions,
                         "confidence":"high" if not proposal else "medium","owner":"RF / ARM","proposal":proposal,"evidence":evidence})
    cfg = snapshot.get("config",{})
    for a in snapshot.get("assignments",[]):
        band,ch = a.get("band"),a.get("channel")
        row = next((r for r in snapshot["channels"] if r["band"]==band and r["channel"]==ch),None)
        if not row: continue
        hist = snapshot.get("quality_history",{}).get(a["interface"]+":"+str(ch),{})
        qs = hist.get("quality",[])
        rs = hist.get("retry",[])
        sustained_bad = len(qs)>=5 and sum(v<70 for v in qs)/len(qs)>=0.6
        retry_bad = len(rs)>=5 and sum(v>=25 for v in rs)/len(rs)>=0.6
        overrides = snapshot.get("overrides",{}).get(band,{})
        if overrides.get("chan",0)>0:
            finding("arm_override_"+band,"medium","Static channel override limits native ARM",
                    f"{band} GHz has an explicit channel override; ARM may not choose its own channel.",
                    ["Review why the override exists and remove it through a reviewed device change if no longer required."],
                    {"channel":ch,"override":overrides})
        if cfg.get("Scanning") == "disable" or cfg.get("Client Aware") == "disable":
            finding("arm_controls_"+band,"medium","ARM protection or scanning is disabled",
                    "Native scanning/client-aware protection is not fully enabled.",
                    ["Review ARM policy and the maintenance constraints before enabling the missing protection."],cfg)
        if sustained_bad or retry_bad:
            candidates = [r for r in snapshot["channels"] if r["band"]==band and r["channel"]!=ch
                and r["channel"] in cfg.get("allowed_channels",{}).get(band,[])
                and r["quality_percent"]>=90 and r["util_percent"]+15<=row["util_percent"]
                and (r["interference_index"] is not None and row["interference_index"] is not None
                     and r["interference_index"] < row["interference_index"])]
            candidates.sort(key=lambda r:(r["interference_index"],r["util_percent"],-r["quality_percent"]))
            target = candidates[0] if candidates else None
            # Even a justified candidate is manual review: do not fight native ARM's client-aware decisions.
            proposal = ({"operation":"ap.radio.channel","band":band+" GHz","from":ch,"to":target["channel"],
                         "power":a.get("power_dbm"),"basis":"ARM RF and quality history; manual override requires review"}
                        if target and a.get("seconds_since_channel_change",0)>=900 else None)
            finding("arm_quality_"+band,"medium","Persistent ARM quality or retry degradation",
                    f"{band} GHz channel {ch} shows repeated poor quality or retries in ARM's own history.",
                    ["Check connected-client experience, ARM history reasons, client-aware deferrals and scanning before overriding ARM.",
                     "Review the measured alternative only if ARM is not recovering the radio."],
                    {"current":row,"quality_samples":len(qs),"retry_samples":len(rs),
                     "candidate":target,"measurement_interval":"vendor history interval not assumed"},proposal)
        elif row["quality_percent"]>=90:
            finding("arm_healthy_"+band,"info","Keep native ARM control",
                    f"{band} GHz channel {ch}: ARM quality {row['quality_percent']}%, utilization {row['util_percent']}%.",
                    ["Keep ARM channel/power selection; continue monitoring client experience."],row)
        nonwifi = hist.get("non_wifi_util",[])
        if len(nonwifi)>=5 and sum(v>=30 for v in nonwifi)/len(nonwifi)>=0.6:
            finding("arm_nonwifi_"+band,"medium","ARM measures sustained non-Wi-Fi airtime",
                    "Repeated ARM samples show non-Wi-Fi utilization; extra APs or transmit power may not fix it.",
                    ["Use a spectrum survey to identify the interference source; compare client impact before changing RF policy."],
                    {"samples":len(nonwifi),"above_30_percent":sum(v>=30 for v in nonwifi)})
    if cfg.get("80Mhz Support") not in (None,"disable"):
        finding("arm_width","medium","Review wide-channel policy for this deployment",
                "ARM permits 80 MHz; the application's approved policy supports 20/40 MHz.",
                ["Review density, client capabilities, and approved channel widths before submitting a policy change."],
                {"80Mhz Support":cfg["80Mhz Support"]})
    return findings
