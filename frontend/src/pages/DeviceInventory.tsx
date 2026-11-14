import { useMemo, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { useLoad } from "../components/useLoad";
import { ErrorNote } from "../components/ui";
import { ago, btn, inputCls } from "../components/viz";
import { getSession, request } from "../services/api";
import { onboardingApi } from "../services/onboarding";
import { v1 } from "../services/inm";

/** Inventory view is backed exclusively by the real versioned inventory endpoints. */
export default function DeviceInventory() {
  const [params, setParams] = useSearchParams();
  const handoff = useLocation().state as {credentialDeviceIds?: string[]; family?: string} | null;
  const [family, setFamily] = useState(handoff?.family ?? "ALL");
  const query = params.get("q") ?? "";
  const devices = useLoad(v1.devices, [], 30000, ["devices"]);
  const sites = useLoad(() => v1.physicalSites().catch(() => ({ sites: [] })), [], 60000, ["sites"]);
  const siteNames = useMemo(() => new Map((sites.data?.sites ?? []).map(s => [s.id, s.name])), [sites.data]);
  const rows = useMemo(() => (devices.data ?? []).filter(d =>
    (family === "ALL" || d.family === family) &&
    (!query || [d.hostname,d.model,d.serial_number,d.firmware,d.site_id,...(d.management_addresses ?? [])]
      .some(v => (v ?? "").toLowerCase().includes(query.toLowerCase())))), [devices.data, family, query]);
  const admin = getSession()?.role === "admin";
  const profiles = useLoad(() => admin ? onboardingApi.credentials() : Promise.resolve([]), [admin], 30000);
  const [selected, setSelected] = useState<string[]>(handoff?.credentialDeviceIds ?? []);
  const [profile, setProfile] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{error?: string; message?: string}>({});
  const selectedFamily = devices.data?.find(d => d.id === selected[0])?.family;
  const eligible = rows.filter(d => d.source_type === "REAL" && d.family === (selectedFamily ?? family));
  const matchingProfiles = profiles.data?.filter(p => p.enabled && p.device_family === selectedFamily) ?? [];
  const changeSelection = (ids: string[]) => {
    setSelected(ids);
    if (!ids.length || !selected.length) setProfile("");
    setFeedback({});
  };
  const update = (value: string) => { const p = new URLSearchParams(params);
    if (value) p.set("q",value); else p.delete("q"); setParams(p,{replace:true}); };
  return <div className="space-y-4">
    <div className="inm-titlebar"><div><div className="inm-section-label">Sites & inventory</div>
      <h1 className="inm-pagetitle">Device inventory</h1>
      <p className="inm-subtitle">Manage registered access points and CX switches, inspect status, view existing configuration and open Device 360°.</p></div>
      {getSession()?.role === "admin" && <Link to="/onboarding" className="inm-header-action">＋ Add & configure devices</Link>}
    </div>
    <ErrorNote error={feedback.error ?? devices.error ?? profiles.error}/>
    {feedback.message && <p role="status">{feedback.message}</p>}
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
        <select aria-label="Device family" value={family} onChange={e=>{setFamily(e.target.value); changeSelection([]);}}
          style={{border:"1px solid #d3e0ec",borderRadius:7,padding:"8px 10px"}}>
          <option value="ALL">All equipment</option><option value="access_point">Access points</option><option value="switch">CX switches</option></select></div>
    </div>
      {admin && <form className="inm-cardbody flex flex-wrap items-end gap-3" onSubmit={async e => {
        e.preventDefault();
        if (!selected.length || !matchingProfiles.some(p => p.id === profile)) return;
        setBusy(true); setFeedback({});
        try {
          const result = await request<{assigned: number}>("/v1/onboarding/credentials/assign", {
            method: "POST", body: JSON.stringify({credential_id: profile, device_ids: selected}),
          });
          changeSelection([]);
          setFeedback({message: `Saved profile assigned to ${result.assigned} devices.`});
        } catch (e) {
          setFeedback({error: e instanceof Error ? e.message : "Could not assign the profile"});
        } finally { setBusy(false); }
      }}>
        <p>{selected.length} selected</p>
        <button type="button" className={btn} disabled={busy || !eligible.length}
          onClick={() => changeSelection([...new Set([...selected, ...eligible.map(d => d.id)])].slice(0, 500))}>Select visible</button>
        <button type="button" className={btn} disabled={busy || !selected.length}
          onClick={() => changeSelection([])}>Clear selection</button>
        <label>Saved credential profile<select className={inputCls} value={profile} disabled={busy || !selected.length}
          onChange={e => setProfile(e.target.value)}>
          <option value="">Choose a profile</option>{matchingProfiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select></label>
        <button className={btn} disabled={busy || !selected.length || !matchingProfiles.some(p => p.id === profile)}>
          {busy ? "Assigning…" : "Assign profile"}
        </button>
        <p className="basis-full text-sm text-slate-600">Select devices of one type. The new profile replaces any previous assignment.</p>
      </form>}
      {admin && selectedFamily && <details className="inm-cardbody">
        <summary className="cursor-pointer font-medium">Create a credential profile for selected devices</summary>
        <form className="mt-3 flex flex-wrap items-end gap-3" onSubmit={async e => {
          e.preventDefault();
          const form = e.currentTarget;
          const data = new FormData(form);
          setBusy(true); setFeedback({});
          try {
            const saved = await onboardingApi.createCredential({
              name: String(data.get("name") ?? "").trim(), device_family: selectedFamily,
              username: String(data.get("username") ?? "").trim(), password: String(data.get("password") ?? ""),
            });
            profiles.reload(); setProfile(saved.id);
            setFeedback({message: "Profile saved. Click Assign profile to apply it to the selected devices."});
          } catch (error) {
            setFeedback({error: error instanceof Error ? error.message : "Could not create the profile"});
          } finally {
            form.reset(); setBusy(false);
          }
        }}>
          <label>Profile name<input className={inputCls} name="name" required /></label>
          <label>Username<input className={inputCls} name="username" required /></label>
          <label>Password<input className={inputCls} name="password" type="password" autoComplete="new-password" required /></label>
          <button className={btn} disabled={busy}>Save credential profile</button>
        </form>
      </details>}
      {rows.length ? <div className="inm-tablewrap"><table className="inm-table"><thead><tr>
        {admin && <th scope="col">Select</th>}<th>Device</th><th>Model / firmware</th><th>Site</th><th>Management IP</th><th>Status</th><th>Source</th><th>Observed</th><th>Actions</th>
      </tr></thead><tbody>{rows.map(d=><tr key={d.id}>
        {admin && <td><input type="checkbox" aria-label={`Select ${d.hostname || d.serial_number}`}
          checked={selected.includes(d.id)} disabled={busy || d.source_type !== "REAL" ||
            (!!selectedFamily && selectedFamily !== d.family) || (selected.length >= 500 && !selected.includes(d.id))}
          onChange={e => changeSelection(e.target.checked ? [...selected, d.id] : selected.filter(id => id !== d.id))} /></td>}
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
