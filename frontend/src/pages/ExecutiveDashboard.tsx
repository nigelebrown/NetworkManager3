import { Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorNote, Stat } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { COLORS, Donut, Gauge, Notice, PageHeader, TT, btn2, useAction } from "../components/viz";
import { download, ops } from "../services/ops";

export default function ExecutiveDashboard() {
  const exec = useLoad(ops.executive, [], 60000);
  const trends = useLoad(ops.trends, [], 60000);
  const fleet = useLoad(ops.fleetCompliance, [], 120000);
  const noc = useLoad(ops.noc, [], 60000);
  const act = useAction();
  const e = exec.data, t = trends.data, c = fleet.data, n = noc.data;
  const exp = n ? n.devices.map(x => x.health.score).filter((x): x is number => x != null) : [];
  const expScore = exp.length ? exp.reduce((a, b) => a + b, 0) / exp.length : null;
  const exportCsv = (kind: string) => act.run(async () => {
    const end = new Date(); const start = new Date(end.getTime() - 30 * 86400e3);
    const r = await ops.exportReport({ kind, start: start.toISOString(), end: new Date(end.getTime() + 60e3).toISOString() });
    download(`inm-${kind}-${end.toISOString().slice(0, 10)}.csv`, r.body);
    return `Signed ${kind} export: ${r.row_count} rows · SHA-256 ${r.sha256.slice(0, 16)}… (signature recorded in audit).`;
  });
  return <div className="space-y-4">
    <PageHeader title="Executive overview" subtitle="Service quality, risk and governance across all sites. Unmeasured values are shown as unavailable, never as zero."
      right={<div className="flex gap-2">{["inventory", "attribution", "audit"].map(k =>
        <button key={k} className={btn2} disabled={act.busy} onClick={() => exportCsv(k)}>Export {k}</button>)}</div>} />
    <ErrorNote error={exec.error ?? trends.error ?? fleet.error} />
    <Notice error={act.error} message={act.message} />
    <div className="grid gap-3 md:grid-cols-4">
      <Card><div className="flex justify-around"><Gauge value={expScore} label="User experience" /><Gauge value={c?.fleet_score} label="Config compliance" /></div></Card>
      <Stat label="Managed devices" value={n?.summary.devices ?? "—"} sub={`${e?.devices_real ?? 0} live · ${(n?.summary.devices ?? 0) - (e?.devices_real ?? 0)} demo`} />
      <Stat label="Monitoring coverage" value={e?.poll_coverage_percent == null ? "—" : `${e.poll_coverage_percent}%`}
        sub={e?.poll_coverage_reason ?? `${e?.devices_never_polled ?? 0} device(s) never polled`} tone={(e?.poll_coverage_percent ?? 0) >= 90 ? "ok" : "warn"} />
      <Stat label="Config backups" value={e?.vault.artifacts ?? "—"} sub={`${e?.vault.restore_verified ?? 0} lab restore-verified`} />
    </div>
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Clients & 5 GHz utilisation — last 24 h">
        {t?.hourly.length ? <ResponsiveContainer width="100%" height={220}><AreaChart data={t.hourly}>
          <defs><linearGradient id="g1" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={COLORS.info} stopOpacity={0.5} /><stop offset="100%" stopColor={COLORS.info} stopOpacity={0} /></linearGradient></defs>
          <CartesianGrid stroke="#1e293b" /><XAxis dataKey="hour" stroke="#64748b" fontSize={11} /><YAxis yAxisId="l" stroke="#64748b" fontSize={11} />
          <YAxis yAxisId="r" orientation="right" stroke="#64748b" fontSize={11} domain={[0, 100]} /><Tooltip {...TT} /><Legend />
          <Area yAxisId="l" dataKey="clients" name="Clients" stroke={COLORS.info} fill="url(#g1)" />
          <Area yAxisId="r" dataKey="util_5g_percent" name="5 GHz util %" stroke={COLORS.fair} fill="none" />
        </AreaChart></ResponsiveContainer> : <p className="py-10 text-center text-sm text-slate-500">Telemetry appears once collection plans run.</p>}
      </Card>
      <Card title="Compliance grades"><Donut data={["A", "B", "C", "D", "UNKNOWN"].map(g => ({ name: g === "UNKNOWN" ? "No config" : `Grade ${g}`, value: c?.grades[g] ?? 0,
        color: { A: COLORS.good, B: "#86efac", C: COLORS.fair, D: COLORS.poor, UNKNOWN: COLORS.none }[g] }))} /></Card>
      <Card title="Who changed the network (attribution)"><Donut data={[
        { name: "Verified technician", value: t?.attribution.VERIFIED ?? 0, color: COLORS.good },
        { name: "Correlated", value: t?.attribution.CORRELATED ?? 0, color: COLORS.fair },
        { name: "Unknown actor", value: t?.attribution.UNKNOWN ?? 0, color: COLORS.poor }]} /></Card>
    </div>
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Change governance — last 14 days">
        {t?.changes_by_day.length ? <ResponsiveContainer width="100%" height={200}><BarChart data={t.changes_by_day}>
          <CartesianGrid stroke="#1e293b" /><XAxis dataKey="day" stroke="#64748b" fontSize={11} /><YAxis allowDecimals={false} stroke="#64748b" fontSize={11} /><Tooltip {...TT} /><Legend />
          <Bar dataKey="plans" name="Requested" fill={COLORS.info} /><Bar dataKey="approved" name="Approved" fill={COLORS.good} /><Bar dataKey="rejected" name="Rejected" fill={COLORS.poor} />
        </BarChart></ResponsiveContainer> : <p className="py-10 text-center text-sm text-slate-500">No change plans yet.</p>}
      </Card>
      <Card title="Top configuration risks">
        <ul className="space-y-2 text-sm">{(c?.top_failures ?? []).slice(0, 8).map(f => <li key={f.id} className="flex items-center gap-3">
          <span className="w-20 shrink-0 font-mono text-xs text-slate-500">{f.id}</span><span className="flex-1 text-slate-300">{f.title}</span>
          <div className="h-2 w-28 rounded bg-slate-800"><div className="h-2 rounded" style={{ width: `${Math.min(100, (f.devices / Math.max(1, c?.devices.length ?? 1)) * 100)}%`, background: f.severity === "critical" ? COLORS.poor : COLORS.fair }} /></div>
          <span className="w-16 text-right text-xs text-slate-400">{f.devices} dev.</span></li>)}
          {!c?.top_failures.length && <li className="text-slate-500">No failing rules (or no configuration captured yet).</li>}</ul>
      </Card>
    </div>
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Devices by model"><Donut data={Object.entries(e?.by_model ?? {}).map(([name, value]) => ({ name, value }))} /></Card>
      <Card title="AI recommendations"><Donut data={Object.entries(t?.recommendations ?? {}).map(([name, value]) => ({ name, value }))} />
        <p className="mt-2 text-xs text-slate-500">Rule-based advisories. No automatic optimization is performed.</p></Card>
      <Card title="Governance">
        <dl className="grid grid-cols-2 gap-2 text-sm"><dt className="text-slate-400">Audit events (24 h)</dt><dd className="font-semibold">{t?.audit_events_24h ?? "—"}</dd>
          <dt className="text-slate-400">Config drift detected</dt><dd className="font-semibold">{c?.drift.DRIFT ?? 0}</dd>
          <dt className="text-slate-400">Devices without captured config</dt><dd className="font-semibold">{c?.devices_without_config ?? "—"}</dd>
          <dt className="text-slate-400">Not measured</dt><dd className="text-xs">{e?.kpis_not_measured.join(", ")}</dd></dl>
      </Card>
    </div>
  </div>;
}
