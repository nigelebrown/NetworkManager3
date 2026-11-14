import { useState } from "react";
import DiagnosisPanel from "../components/DiagnosisPanel";
import { ErrorNote, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Pill, ago } from "../components/viz";
import { wl, type Breakdown, type WClient } from "../services/wireless";

const QTONE: Record<string, "good" | "fair" | "poor" | "none"> = { good: "good", fair: "fair", poor: "poor", unavailable: "none" };

function Kpi({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return <div className="inm-kpi"><span className="inm-kpi-label">{label}</span><span className="inm-kpi-value">{value}</span>{sub && <span className="inm-kpi-sub">{sub}</span>}</div>;
}
function BreakTable({ title, rows, onPick }: { title: string; rows: Breakdown[]; onPick: (n: string) => void }) {
  return <section className="inm-card"><div className="inm-cardhead"><h2>{title}</h2></div><div className="max-h-72 overflow-auto"><table className="inm-table">
    <thead><tr><th>Name</th><th>Clients</th><th>Poor</th><th>Avg SNR</th><th>Disc.</th><th>Roams</th></tr></thead>
    <tbody>{rows.map(r => <tr key={r.name} className="cursor-pointer hover:bg-slate-50" onClick={() => onPick(r.name)}>
      <td className="inm-link">{r.name}</td><td>{r.connected}/{r.clients}</td><td className={r.poor ? "font-semibold text-red-600" : ""}>{r.poor}</td>
      <td>{r.avg_snr_db ?? "—"}</td><td>{r.disconnects}</td><td>{r.roams}</td></tr>)}</tbody></table></div></section>;
}

export default function ClientConnectivity() {
  const [f, setF] = useState({ hours: 24, cluster: "", ap: "", ssid: "", band: "", quality: "", q: "" });
  const [sel, setSel] = useState<WClient | null>(null);
  const [diag, setDiag] = useState<WClient | null>(null);
  const v = useLoad(() => wl.clients({ ...f, cluster: f.cluster || undefined }), [JSON.stringify(f)], 30000, ["telemetry", "devices"]);
  const journey = useLoad(() => sel ? wl.journey(sel.id, f.hours) : Promise.resolve(null), [sel?.id, f.hours], 0);
  const d = v.data;
  const set = (k: keyof typeof f, val: string | number) => setF({ ...f, [k]: val });
  return <div className="space-y-4">
    <div><div className="inm-section-label">Monitoring & diagnostics</div><h1 className="inm-pagetitle">Client connectivity</h1>
      <p className="inm-subtitle">Who is connected where, how well, and what happened to each client (connect → roam → disconnect). Click a client for its journey, or Diagnose for the cause and fix.</p></div>
    <ErrorNote error={v.error} />
    {d && <section className="inm-status-overview">
      <Kpi label="Connected now" value={d.summary.connected} sub={`${d.summary.clients_seen} seen in ${d.window_hours} h`} />
      <Kpi label="Quality" value={`${d.summary.quality.good ?? 0} / ${d.summary.quality.fair ?? 0} / ${d.summary.quality.poor ?? 0}`} sub="good / fair / poor" />
      <Kpi label="Avg SNR" value={d.summary.avg_snr_db != null ? `${d.summary.avg_snr_db} dB` : "—"} sub={Object.entries(d.summary.bands).map(([k, n]) => `${k} ${n}`).join(" · ")} />
      <Kpi label="Disconnects / roams" value={`${d.summary.disconnects} / ${d.summary.roams}`} sub={`${d.summary.connects} connects in window`} />
      <Kpi label="Clients with issues" value={d.summary.with_issues} sub="weak signal, instability, band" />
    </section>}
    <section className="inm-card"><div className="inm-cardbody flex flex-wrap gap-2">
      <select aria-label="Window" className="inm-form-control" value={f.hours} onChange={e => set("hours", Number(e.target.value))}>{[1, 6, 24, 72, 168].map(h => <option key={h} value={h}>Last {h} h</option>)}</select>
      <select aria-label="Cluster" className="inm-form-control" value={f.cluster} onChange={e => setF({ ...f, cluster: e.target.value, ap: "" })}>
        <option value="">All clusters</option>{(d?.clusters ?? []).map(c => <option key={c.key} value={c.key}>{c.name}</option>)}</select>
      <input aria-label="AP" className="inm-form-control" placeholder="AP name" value={f.ap} onChange={e => set("ap", e.target.value)} />
      <input aria-label="SSID" className="inm-form-control" placeholder="SSID" value={f.ssid} onChange={e => set("ssid", e.target.value)} />
      <select aria-label="Band" className="inm-form-control" value={f.band} onChange={e => set("band", e.target.value)}><option value="">Both bands</option><option>2.4GHz</option><option>5GHz</option></select>
      <select aria-label="Quality" className="inm-form-control" value={f.quality} onChange={e => set("quality", e.target.value)}><option value="">Any quality</option>{["poor", "fair", "good", "unavailable"].map(x => <option key={x}>{x}</option>)}</select>
      <input aria-label="Search" className="inm-form-control" placeholder="MAC, hostname, AP" value={f.q} onChange={e => set("q", e.target.value)} />
      {Object.entries(f).some(([k, x]) => k !== "hours" && x) && <button className="inm-link" onClick={() => setF({ hours: f.hours, cluster: "", ap: "", ssid: "", band: "", quality: "", q: "" })}>Clear filters</button>}
    </div></section>
    {d && <div className="grid gap-4 lg:grid-cols-3">
      <BreakTable title="By cluster" rows={d.by_cluster} onPick={n => { const c = d.clusters.find(x => x.name === n); if (c) set("cluster", c.key); }} />
      <BreakTable title="Worst APs" rows={d.by_ap} onPick={n => set("ap", n)} />
      <BreakTable title="By SSID" rows={d.by_ssid} onPick={n => set("ssid", n)} /></div>}
    <div className="grid gap-4 2xl:grid-cols-3">
      <section className="inm-card 2xl:col-span-2"><div className="inm-cardhead"><h2>Clients ({d?.clients.length ?? 0})</h2></div><div className="max-h-[560px] overflow-auto"><table className="inm-table">
        <thead><tr><th>Client</th><th>Cluster / AP</th><th>SSID</th><th>Band / ch</th><th>SNR</th><th>Rate</th><th>Score</th><th>Disc / roams</th><th>Issues</th><th>Action</th></tr></thead>
        <tbody>{(d?.clients ?? []).map(c => <tr key={c.id} className={sel?.id === c.id ? "bg-blue-50" : ""}>
          <td><button className="inm-link font-mono text-xs" onClick={() => { setSel(c); setTimeout(() => document.getElementById("journey")?.scrollIntoView({ behavior: "smooth", block: "nearest" }), 60); }}>{c.mac}</button><div className="text-[10px] text-slate-500">{c.hostname ?? ""} {c.connected ? "" : "· left " + ago(c.last_seen)}</div></td>
          <td className="text-xs">{c.cluster}<div className="font-semibold">{c.ap ?? "—"}</div></td><td>{c.ssid ?? "—"}</td><td className="text-xs">{c.band ?? "—"} {c.channel ? `/ ${c.channel}` : ""}</td>
          <td>{c.snr_db ?? "—"}</td><td>{c.speed_mbps ?? "—"}</td><td><Pill value={`${c.exp_score ?? "—"} ${c.quality}`} tone={QTONE[c.quality] ?? "none"} /></td>
          <td>{c.disconnects} / {c.roams}</td><td className="max-w-[180px] text-xs text-amber-800">{c.issues.join("; ")}</td>
          <td><button className="inm-btn-secondary" onClick={() => setDiag(c)}>Diagnose</button></td></tr>)}</tbody></table>
        {d && !d.clients.length && <div className="inm-empty">No clients match. Clients appear once their AP's cluster is polled.</div>}</div></section>
      <section className="inm-card" id="journey"><div className="inm-cardhead"><h2>Journey {sel ? `· ${sel.mac}` : ""}</h2></div><div className="inm-cardbody text-sm">
        {!sel && <p className="text-slate-500">Select a client to see its connection timeline.</p>}
        {journey.data && <>
          <p><b>Now:</b> {journey.data.current.connected ? "connected" : "not connected"} to {String(journey.data.current.ap ?? "—")} on {String(journey.data.current.ssid ?? "—")} ({String(journey.data.current.band ?? "?")}, SNR {String(journey.data.current.snr_db ?? "—")})</p>
          <p><b>Score:</b> {journey.data.score.score ?? "unavailable"} ({journey.data.score.band})</p>
          {!!journey.data.aps_visited.length && <p><b>APs used:</b> {journey.data.aps_visited.map(a => `${a.ap} ×${a.times}`).join(", ")}</p>}
          <ol className="mt-2 border-l-2 border-slate-200 pl-3">{journey.data.events.map((e, i) => <li key={i} className="mb-2">
            <span className="text-xs text-slate-500">{fmtTime(e.ts)}</span> <Pill value={e.kind} tone={e.kind === "disconnect" ? "poor" : e.kind === "roam" ? "fair" : "good"} />
            <div className="text-xs">{e.kind === "roam" ? `${e.ap_from} → ${e.ap_to}` : e.ap_to ?? e.ap_from} {e.ssid ? `· ${e.ssid}` : ""}</div></li>)}</ol>
          {!journey.data.events.length && <p className="text-slate-500">No connect/roam/disconnect in this window.</p>}
          <button className="inm-btn-primary mt-2" onClick={() => setDiag(sel)}>Why is this client poor?</button></>}
        {d && <p className="mt-3 text-[11px] text-slate-500">{d.evidence_note}</p>}
      </div></section>
    </div>
    {diag && <DiagnosisPanel target={{ kind: "client", id: diag.id, label: `${diag.mac} on ${diag.ap ?? "?"}` }} onClose={() => setDiag(null)} />}
  </div>;
}
