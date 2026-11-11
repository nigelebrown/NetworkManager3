import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useLoad } from "../components/useLoad";
import { ErrorNote } from "../components/ui";
import { ago } from "../components/viz";
import { getSession } from "../services/api";
import { v1 } from "../services/inm";

/** Inventory view is backed exclusively by the real versioned inventory endpoints. */
export default function DeviceInventory() {
  const [params, setParams] = useSearchParams();
  const [family, setFamily] = useState("ALL");
  const query = params.get("q") ?? "";
  const devices = useLoad(v1.devices, [], 30000, ["devices"]);
  const sites = useLoad(() => v1.physicalSites().catch(() => ({ sites: [] })), [], 60000, ["sites"]);
  const siteNames = useMemo(() => new Map((sites.data?.sites ?? []).map(s => [s.id, s.name])), [sites.data]);
  const rows = useMemo(() => (devices.data ?? []).filter(d =>
    (family === "ALL" || d.family === family) &&
    (!query || [d.hostname,d.model,d.serial_number,d.firmware,d.site_id,...(d.management_addresses ?? [])]
      .some(v => (v ?? "").toLowerCase().includes(query.toLowerCase())))), [devices.data, family, query]);
  const update = (value: string) => { const p = new URLSearchParams(params);
    if (value) p.set("q",value); else p.delete("q"); setParams(p,{replace:true}); };
  return <div className="space-y-4">
    <div className="inm-titlebar"><div><div className="inm-section-label">Sites & inventory</div>
      <h1 className="inm-pagetitle">Device inventory</h1>
      <p className="inm-subtitle">Manage registered access points and CX switches, inspect status, view existing configuration and open Device 360°.</p></div>
      {getSession()?.role === "admin" && <Link to="/onboarding" className="inm-header-action">＋ Add & configure devices</Link>}
    </div>
    <ErrorNote error={devices.error}/>
    <div className="inm-kpis" style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(150px,1fr))",gap:12}}>
      {[
        ["All devices",devices.data?.length ?? "—"],
        ["Access points",devices.data?.filter(d=>d.family==="access_point").length ?? "—"],
        ["CX switches",devices.data?.filter(d=>d.family==="switch").length ?? "—"],
        ["Live records",devices.data?.filter(d=>d.source_type==="REAL").length ?? "—"],
        ["Demo records",devices.data?.filter(d=>d.source_type==="DEMO").length ?? "—"],
      ].map(([label,value])=><div className="inm-kpi" key={String(label)}>
        <div style={{color:"#63748b",fontSize:11}}>{label}</div><div className="inm-kpi-value">{value}</div></div>)}
    </div>
    <section className="inm-card"><div className="inm-cardhead"><h2>Registered equipment ({rows.length})</h2>
      <div className="inm-actionbar"><input aria-label="Search inventory" value={query} onChange={e=>update(e.target.value)}
        placeholder="Hostname, serial, model or IP" style={{border:"1px solid #d3e0ec",borderRadius:7,padding:"8px 10px"}}/>
        <select aria-label="Device family" value={family} onChange={e=>setFamily(e.target.value)}
          style={{border:"1px solid #d3e0ec",borderRadius:7,padding:"8px 10px"}}>
          <option value="ALL">All equipment</option><option value="access_point">Access points</option><option value="switch">CX switches</option></select></div>
    </div>
      {rows.length ? <div className="inm-tablewrap"><table className="inm-table"><thead><tr>
        <th>Device</th><th>Model / firmware</th><th>Site</th><th>Management IP</th><th>Status</th><th>Source</th><th>Observed</th><th>Actions</th>
      </tr></thead><tbody>{rows.map(d=><tr key={d.id}>
        <td><Link className="inm-link" to={"/devices/"+d.id+"/360"}>{d.hostname || d.serial_number}</Link>
          <div style={{fontSize:10,color:"#7b8a9d"}}>{d.serial_number}</div></td>
        <td>{d.model ?? d.family}<div style={{fontSize:10,color:"#7b8a9d"}}>{d.firmware ?? "Not observed"}</div></td>
        <td>{d.site_id ? siteNames.get(d.site_id) ?? "Assigned site" : "Unassigned"}</td>
        <td>{d.management_addresses?.join(", ") || "Not reported"}</td>
        <td><span className={"inm-pill "+(d.status.toLowerCase()==="up"?"good":d.status.toLowerCase()==="down"?"bad":"warn")}>{d.status}</span></td>
        <td><span className={"inm-pill "+(d.source_type==="REAL"?"info":"warn")}>{d.source_type}</span></td>
        <td>{ago(d.last_observed_at)}</td>
        <td><Link className="inm-link" to={"/devices/"+d.id+"/360"}>Device 360°</Link></td>
      </tr>)}</tbody></table></div>
      : <div className="inm-cardbody"><div className="inm-empty">No devices match the current filter. Add hardware through guided onboarding.</div></div>}
      <div className="inm-cardfooter">All device data is retrieved from the management API. DEMO records are explicitly labelled.</div>
    </section>
  </div>;
}
