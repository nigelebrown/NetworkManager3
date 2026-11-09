import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorNote, Stat } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { COLORS, Donut, Gauge, PageHeader, Pill, SourceTag, TT, ago, inputCls, scoreColor, statusTone } from "../components/viz";
import { ops, type NocTile } from "../services/ops";

function Tile({ t }: { t: NocTile }) {
  const s = t.health.score;
  return <Link to={`/devices/${t.device_id}/360`} title={t.health.reasons.join("\n") || "Healthy"}
    className="group block rounded-lg border border-slate-800 bg-slate-900/70 p-2.5 transition hover:border-sky-600"
    style={{ boxShadow: `inset 4px 0 0 ${scoreColor(s)}` }}>
    <div className="flex items-start justify-between gap-1">
      <div className="truncate text-sm font-medium">{t.hostname ?? t.device_id.slice(0, 8)}</div>
      <span className="text-sm font-bold" style={{ color: scoreColor(s) }}>{s ?? "—"}</span>
    </div>
    <div className="mt-0.5 truncate text-[11px] text-slate-500">{t.site ?? "No site"} · {t.model ?? t.family}</div>
    <div className="mt-1.5 flex flex-wrap gap-1 text-[10px]">
      <span title={t.state_reason}><Pill value={t.state} tone={t.state === "ONLINE" ? "good" : t.state === "OFFLINE" ? "poor" : t.state === "DEGRADED" || t.state === "STALE" ? "fair" : "none"} /></span>
      <Pill value={t.freshness} tone={statusTone(t.freshness)} />
      {t.source_type === "DEMO" && <Pill value="DEMO" tone="demo" />}
      {t.metrics.clients != null && <Pill value={`${t.metrics.clients} clients`} tone="info" />}
    </div>
  </Link>;
}

export default function NocDashboard() {
  const noc = useLoad(ops.noc, [], 15000, ["devices", "telemetry", "alerts", "*"]);
  const [filter, setFilter] = useState("");
  const [band, setBand] = useState("ALL");
  const d = noc.data;
  const tiles = useMemo(() => (d?.devices ?? []).filter(t =>
    (band === "ALL" || t.health.band === band) &&
    (!filter || `${t.hostname} ${t.site} ${t.model}`.toLowerCase().includes(filter.toLowerCase()))), [d, filter, band]);
  const scored = (d?.devices ?? []).map(t => t.health.score).filter((x): x is number => x != null);
  const fleet = scored.length ? scored.reduce((a, b) => a + b, 0) / scored.length : null;
  const bySite = useMemo(() => {
    const m: Record<string, number[]> = {};
    (d?.devices ?? []).forEach(t => { if (t.health.score != null) (m[t.site ?? "No site"] ??= []).push(t.health.score); });
    return Object.entries(m).map(([site, v]) => ({ site, score: Math.round(v.reduce((a, b) => a + b, 0) / v.length) }));
  }, [d]);
  return <div className="space-y-4">
    <PageHeader title="Network Operations Centre" subtitle={`Live health of every managed AP and switch. Updated ${ago(d?.generated_at)} — refreshes automatically.`} />
    <ErrorNote error={noc.error} />
    {d && <>
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-7">
        <div className="col-span-1 flex items-center justify-center rounded-xl border border-slate-800 bg-slate-900/60 p-2 lg:row-span-1">
          <Gauge value={fleet} label="Experience" size={110} /></div>
        <Stat label="Devices" value={d.summary.devices} sub={`${d.summary.states?.ONLINE ?? 0} online · ${d.summary.states?.DEGRADED ?? 0} degraded · ${d.summary.states?.STALE ?? 0} stale · ${d.summary.states?.UNKNOWN ?? 0} unknown`} />
        <Stat label="Offline" value={d.summary.states?.OFFLINE ?? 0} tone={(d.summary.states?.OFFLINE ?? 0) ? "bad" : "ok"} sub="not answering polls" />
        <Stat label="Clients now" value={d.summary.clients} />
        <Stat label="Fresh telemetry" value={d.summary.fresh} sub={`${d.summary.stale_or_gap} stale/gap · ${d.summary.never_polled} not polled`}
          tone={d.summary.stale_or_gap ? "warn" : "ok"} />
        <Stat label="Active alerts" value={d.summary.active_alerts} tone={d.summary.active_alerts ? "warn" : "ok"} />
        <Stat label="Polls last hour" value={Object.values(d.collection_last_hour).reduce((a, b) => a + b, 0)}
          sub={`${d.collection_last_hour.SUCCEEDED ?? 0} ok · ${(d.collection_last_hour.RETRY ?? 0) + (d.collection_last_hour.DLQ ?? 0)} failed`} />
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Health distribution"><Donut data={[
          { name: "Good", value: d.summary.health_bands.GOOD ?? 0, color: COLORS.good },
          { name: "Fair", value: d.summary.health_bands.FAIR ?? 0, color: COLORS.fair },
          { name: "Poor", value: d.summary.health_bands.POOR ?? 0, color: COLORS.poor },
          { name: "No data", value: d.summary.health_bands.NO_DATA ?? 0, color: COLORS.none }]} /></Card>
        <Card title="Experience by site">
          {bySite.length ? <ResponsiveContainer width="100%" height={180}><BarChart data={bySite} layout="vertical" margin={{ left: 20 }}>
            <CartesianGrid stroke="#1e293b" horizontal={false} /><XAxis type="number" domain={[0, 100]} stroke="#64748b" fontSize={11} />
            <YAxis type="category" dataKey="site" stroke="#64748b" fontSize={11} width={110} /><Tooltip {...TT} />
            <Bar dataKey="score" radius={[0, 4, 4, 0]}>{bySite.map(r => <Cell key={r.site} fill={scoreColor(r.score)} />)}</Bar>
          </BarChart></ResponsiveContainer> : <p className="py-8 text-center text-sm text-slate-500">No measured telemetry yet.</p>}
        </Card>
        <Card title="Worst user experience">
          <ul className="space-y-1.5 text-sm">{d.worst_experience.slice(0, 7).map(t =>
            <li key={t.device_id} className="flex items-center justify-between gap-2">
              <Link className="truncate text-sky-300 hover:underline" to={`/devices/${t.device_id}/360`}>{t.hostname}</Link>
              <span className="truncate text-xs text-slate-500">{t.health.reasons[0] ?? ""}</span>
              <span className="font-semibold" style={{ color: scoreColor(t.health.score) }}>{t.health.score}</span></li>)}
            {!d.worst_experience.length && <li className="text-slate-500">No scored devices.</li>}</ul>
        </Card>
      </div>
      <Card title={`Device wall (${tiles.length})`} right={<div className="flex gap-2">
        <input aria-label="Filter devices" placeholder="Filter by name, site, model" className={`${inputCls} mt-0 w-56`} value={filter} onChange={e => setFilter(e.target.value)} />
        <select aria-label="Health band" className={`${inputCls} mt-0 w-32`} value={band} onChange={e => setBand(e.target.value)}>
          {["ALL", "GOOD", "FAIR", "POOR", "NO_DATA"].map(b => <option key={b}>{b}</option>)}</select></div>}>
        {tiles.length ? <div className="grid gap-2 sm:grid-cols-2 md:grid-cols-4 xl:grid-cols-6">{tiles.map(t => <Tile key={t.device_id} t={t} />)}</div>
          : <p className="py-6 text-center text-sm text-slate-500">No devices yet. Add one under <Link className="text-sky-300" to="/onboarding">Inventory → Add device</Link>, or load demo data from <Link className="text-sky-300" to="/data">Data management</Link>.</p>}
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Live event stream"><ul className="max-h-72 space-y-1 overflow-auto text-sm">{d.events.map((e, i) =>
          <li key={i} className="flex gap-2"><span className="w-16 shrink-0 text-xs text-slate-500">{ago(e.at)}</span>
            <Pill value={e.severity} tone={e.severity === "error" || e.severity === "critical" ? "poor" : e.severity === "warning" ? "fair" : "none"} />
            {e.config_change && <Pill value="CONFIG" tone="fair" />}<span className="text-slate-300">{e.message}</span></li>)}
          {!d.events.length && <li className="text-slate-500">No device syslog received yet. Point devices' syslog at this server.</li>}</ul></Card>
        <Card title="Active alerts"><ul className="max-h-72 space-y-1 overflow-auto text-sm">{d.alerts.map(a =>
          <li key={a.id} className="flex gap-2"><Pill value={a.severity} tone={a.severity === "critical" ? "poor" : "fair"} />
            <span className="text-slate-300">{a.message}</span>{a.simulated && <SourceTag source="DEMO" />}</li>)}
          {!d.alerts.length && <li className="text-slate-500">No active alerts.</li>}</ul></Card>
      </div>
    </>}
  </div>;
}
