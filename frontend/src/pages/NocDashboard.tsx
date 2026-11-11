import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { ago } from "../components/viz";
import { getSession } from "../services/api";
import { ops, type NocTile } from "../services/ops";
import { wf } from "../services/remediation";

function Badge({ value, kind = "" }: { value: string; kind?: string }) {
  return <span className={"inm-pill " + kind}>{value}</span>;
}
function Panel({ title, href, footer, children }: { title: string; href?: string; footer?: string; children: ReactNode }) {
  return <section className="inm-card">
    <div className="inm-cardhead"><h2>{title}</h2>{href && <Link className="inm-link" to={href}>View details</Link>}</div>
    <div className="inm-cardbody">{children}</div>{footer && <div className="inm-cardfooter">{footer}</div>}
  </section>;
}
function Kpi({ label, value, sub, href }: { label: string; value: string | number; sub: string; href: string }) {
  return <Link className="inm-kpi" to={href}>
    <div style={{fontSize:11,color:"#63748b"}}>{label}</div><div className="inm-kpi-value">{value}</div>
    <div className="inm-kpi-sub">{sub}</div>
  </Link>;
}
const tone = (state: string) => state === "ONLINE" || state === "GOOD" ? "good"
  : state === "OFFLINE" || state === "POOR" ? "bad"
  : state === "STALE" || state === "DEGRADED" || state === "FAIR" ? "warn" : "";

export default function NocDashboard() {
  const noc = useLoad(ops.noc, [], 15000, ["devices", "telemetry", "alerts", "*"]);
  const trends = useLoad(() => ops.trends().catch(() => null), [], 60000, ["telemetry"]);
  const plans = useLoad(() => ops.changePlans().catch(() => []), [], 30000, ["changes"]);
  const workflows = useLoad(() => wf.list().catch(() => []), [], 30000, ["workflows"]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("ALL");
  const d = noc.data;
  const devices = d?.devices ?? [];
  const ap = devices.filter(x => x.family === "access_point");
  const switches = devices.filter(x => x.family === "switch");
  const online = (rows: NocTile[]) => rows.filter(x => x.state === "ONLINE").length;
  const scores = devices.map(x => x.health.score).filter((v): v is number => v != null);
  const score = scores.length ? scores.reduce((a,b)=>a+b,0)/scores.length : null;
  const bySite = useMemo(() => {
    const m: Record<string,number[]> = {};
    (d?.devices ?? []).forEach(x => { if(x.health.score != null) (m[x.site ?? "Unassigned"] ??= []).push(x.health.score); });
    return Object.entries(m).map(([name,vals]) => ({ name,score:Math.round(vals.reduce((a,b)=>a+b,0)/vals.length) }))
      .sort((a,b)=>a.score-b.score).slice(0,7);
  },[d]);
  const openChanges = (plans.data ?? []).filter(p => ["PENDING_APPROVAL","APPROVED","DRAFT"].includes(p.state));
  const pending = openChanges.filter(p => p.state === "PENDING_APPROVAL");
  const activeWf = (workflows.data ?? []).filter(w => !["DONE","CANCELLED"].includes(w.state));
  const filtered = devices.filter(x => (filter === "ALL" || x.state === filter)
    && [x.hostname ?? "",x.model ?? "",x.site ?? ""].join(" ").toLowerCase().includes(query.toLowerCase()));
  const polled = d ? Object.values(d.collection_last_hour).reduce((a,b)=>a+b,0) : 0;
  const succeeded = d?.collection_last_hour.SUCCEEDED ?? 0;
  return <div className="inm-noc">
    <div className="inm-titlebar"><div><div className="inm-section-label">Operations / NOC</div>
      <h1 className="inm-pagetitle">Network command center</h1>
      <p className="inm-subtitle">Live network health, devices, clients, incidents, changes, telemetry and site performance. {d ? "Updated " + ago(d.generated_at) : "Loading…"}</p></div>
      <div className="inm-actionbar">{getSession()?.role === "admin" && <Link to="/onboarding" className="inm-header-action">＋ Add / configure devices</Link>}
        <Link to="/device-alerts" className="inm-link">View incidents</Link></div></div>
    <ErrorNote error={noc.error}/>
    {!d && !noc.error && <div className="inm-notice">Loading measured information from the NOC API…</div>}
    {d && <>
      {devices.some(x => x.source_type === "DEMO") && <div className="inm-notice">This view includes DEMO-labelled devices. Sample observations remain distinguishable from real equipment.</div>}
      <div className="inm-kpis">
        <Kpi label="Network experience" value={score == null ? "—" : Math.round(score)+"%"} sub={scores.length+" measured devices"} href="/experience"/>
        <Kpi label="Access points" value={online(ap)+" / "+ap.length} sub={(ap.length-online(ap))+" not online"} href="/aps"/>
        <Kpi label="CX switches" value={online(switches)+" / "+switches.length} sub={(switches.length-online(switches))+" not online"} href="/switches"/>
        <Kpi label="Connected clients" value={d.summary.clients} sub="Last persisted observation" href="/clients"/>
        <Kpi label="Active incidents" value={d.summary.active_alerts} sub="Open monitoring alerts" href="/device-alerts"/>
        <Kpi label="Pending changes" value={pending.length} sub={openChanges.length+" open plans"} href="/changes"/>
      </div>
      <div className="inm-panel-grid">
        <Panel title="Client sessions — last 24 hours" href="/executive" footer="Measured client activity from collection history; not an estimated availability chart.">
          {trends.data?.hourly?.some(x => x.clients != null) ? <ResponsiveContainer width="100%" height={185}>
            <AreaChart data={trends.data.hourly}>
              <defs><linearGradient id="clients-color" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#357fdb" stopOpacity={0.26}/>
                <stop offset="1" stopColor="#357fdb" stopOpacity={0}/></linearGradient></defs>
              <CartesianGrid vertical={false} stroke="#e7edf3"/><XAxis dataKey="hour" stroke="#8999ab" fontSize={10} minTickGap={18}/>
              <YAxis stroke="#8999ab" fontSize={10} allowDecimals={false}/>
              <Tooltip contentStyle={{background:"#fff",color:"#253b53",border:"1px solid #dfe6ed",borderRadius:8}}/>
              <Area dataKey="clients" type="monotone" stroke="#357fdb" strokeWidth={2.5} fill="url(#clients-color)" connectNulls={false}/>
            </AreaChart></ResponsiveContainer> : <div className="inm-empty">No historical client data. Configure collection plans to display the measured trend.</div>}
          <p style={{fontSize:11,color:"#72839a",marginTop:10}}>Current connected clients: {d.summary.clients}</p>
        </Panel>
        <Panel title="Service availability & data integrity" href="/system" footer="These are collection indicators; see Platform health for per-service status.">
          <div className="inm-row"><span className="inm-dot"/><span className="grow"><b>Collection jobs</b><small>{succeeded} succeeded of {polled} events in the last hour</small></span>
            <Badge value={polled ? Math.round(100*succeeded/polled)+"% success" : "No runs"} kind={polled && succeeded===polled ? "good" : "warn"}/></div>
          <div className="inm-row"><span className={"inm-dot "+(d.summary.stale_or_gap ? "warn" : "")}/><span className="grow"><b>Telemetry freshness</b>
            <small>{d.summary.fresh} fresh / {d.summary.stale_or_gap} stale or gaps</small></span>
            <Badge value={d.summary.stale_or_gap ? "Needs attention" : "Fresh"} kind={d.summary.stale_or_gap ? "warn" : "good"}/></div>
          <div className="inm-row"><span className={"inm-dot "+(d.summary.never_polled ? "warn" : "")}/><span className="grow"><b>Never polled</b><small>Registered devices without a collected sample</small></span>
            <Badge value={String(d.summary.never_polled)} kind={d.summary.never_polled ? "warn" : "good"}/></div>
          <div className="inm-row"><span className={"inm-dot "+(d.summary.active_alerts ? "bad" : "")}/><span className="grow"><b>Open alerts</b><small>Review incident and source evidence</small></span>
            <Badge value={String(d.summary.active_alerts)} kind={d.summary.active_alerts ? "bad" : "good"}/></div>
        </Panel>
      </div>
      <div className="inm-panel-grid">
        <Panel title="Priority incidents" href="/device-alerts" footer="Incidents are sourced from the alert service; demo events retain their labels.">
          {d.alerts.length ? <div className="inm-tablewrap"><table className="inm-table"><thead><tr><th>Severity</th><th>Issue</th><th>Detected</th></tr></thead><tbody>
            {d.alerts.slice(0,5).map(a => <tr key={a.id}><td><Badge value={a.severity} kind={a.severity==="critical"?"bad":"warn"}/></td>
              <td>{a.message} {a.simulated && <Badge value="DEMO" kind="info"/>}</td><td>{ago(a.created_at)}</td></tr>)}</tbody></table></div>
            : <div className="inm-empty">No active incidents reported.</div>}
        </Panel>
        <Panel title="Site health" href="/sites" footer="Measured site experience only; unmeasured sites are excluded.">
          {bySite.length ? bySite.map(s => <div className="inm-site-row" key={s.name}>
            <span className="name" title={s.name}>{s.name}</span><div className="inm-sitebar"><span style={{width:s.score+"%",background:s.score<60?"#c96168":s.score<80?"#dea54b":"#3f85d9"}}/></div><b>{s.score}%</b>
          </div>) : <div className="inm-empty">No measured site scores yet.</div>}
        </Panel>
      </div>
      <div className="inm-bottom-grid">
        <Panel title="Change approvals" href="/changes" footer="All real-device writes remain approval and certification-gated.">
          {openChanges.length ? openChanges.slice(0,5).map(p => <div className="inm-row" key={p.id}>
            <span className="grow"><Link className="inm-link" to={"/changes?device="+encodeURIComponent(p.device_id)}>{p.title}</Link>
              <small>Requested by {p.requester} · {ago(p.created_at)}</small></span>
            <Badge value={p.state.replaceAll("_"," ")} kind={p.state==="PENDING_APPROVAL"?"warn":p.state==="APPROVED"?"good":"info"}/></div>)
            : <div className="inm-empty">No outstanding change requests.</div>}
        </Panel>
        <Panel title="Onboarding & deployment" href="/onboarding" footer="Guided discovery, baseline, approvals and verified application.">
          <div style={{fontSize:27,fontWeight:800}}>{activeWf.length}</div><p style={{fontSize:11,color:"#72849a"}}>active workflows</p>
          {activeWf.slice(0,4).map(w => <div className="inm-row" key={w.id}><span className="grow">
            <Link className="inm-link" to={"/onboarding?id="+w.id}>{w.title}</Link><small>{w.site} · {w.devices} devices</small></span>
            <Badge value={w.step.replaceAll("_"," ")} kind="info"/></div>)}
          {!activeWf.length && <div className="inm-empty">No active workflows.</div>}
        </Panel>
        <Panel title="Recent activity" href="/logs" footer="Monitoring and change events share correlation IDs.">
          {d.events.length ? d.events.slice(0,6).map((e,i) => <div className="inm-row" key={i}>
            <span className={"inm-dot "+((e.severity==="critical"||e.severity==="error")?"bad":e.severity==="warning"?"warn":"")}/>
            <span className="grow"><b>{e.message}</b><small>{e.config_change?"Configuration":"Monitoring"} · {ago(e.at)}</small></span></div>)
            : <div className="inm-empty">No collected events yet.</div>}
        </Panel>
      </div>
      <section className="inm-card"><div className="inm-cardhead"><h2>Managed devices ({filtered.length})</h2>
        <div className="inm-actionbar"><input aria-label="Filter device wall" placeholder="Name, model or site" value={query} onChange={e=>setQuery(e.target.value)}
          style={{padding:"8px 10px",border:"1px solid #dfe6ed",borderRadius:7}}/>
          <select aria-label="Operational state" value={filter} onChange={e=>setFilter(e.target.value)}
            style={{padding:"8px 10px",border:"1px solid #dfe6ed",borderRadius:7}}>
            {["ALL","ONLINE","DEGRADED","STALE","OFFLINE","UNKNOWN"].map(s=><option key={s}>{s}</option>)}</select></div>
      </div><div className="inm-cardbody" style={{display:"grid",gridTemplateColumns:"repeat(auto-fit,minmax(240px,1fr))",gap:"0 24px"}}>
        {filtered.slice(0,36).map(t => <Link to={"/devices/"+t.device_id+"/360"} key={t.device_id} className="inm-row" style={{textDecoration:"none",color:"#243b57"}}>
          <span className={"inm-dot "+tone(t.state)}/><span className="grow"><b>{t.hostname ?? t.device_id.slice(0,8)}</b>
          <small>{t.site ?? "Unassigned"} · {t.model ?? t.family} {t.source_type==="DEMO"?"· DEMO":""}</small></span>
          <Badge value={t.state} kind={tone(t.state)}/></Link>)}
        {!filtered.length && <div className="inm-empty">No matching devices. <Link to="/onboarding">Start onboarding</Link>.</div>}
      </div><div className="inm-cardfooter">Showing {Math.min(36,filtered.length)} of {filtered.length} devices · Select any device to open Device 360°.</div></section>
    </>}
  </div>;
}
