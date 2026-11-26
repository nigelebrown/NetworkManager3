import { useState } from "react";
import { Link } from "react-router";
import { Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorNote, Stat } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { COLORS, Donut, Gauge, PageHeader, TT } from "../components/viz";
import { api, getSession } from "../services/api";
import { ops } from "../services/ops";
import DashboardSwitcher from "../components/DashboardSwitcher";
import FirmwareExperience from "../components/FirmwareExperience";

const pct = (v: number | null | undefined) => v == null ? "—" : `${Math.round(v)}%`;
const score = (v: number | null | undefined) => v == null ? "—" : `${Math.round(v)} / 100`;

export default function ExecutiveDashboard() {
  const summary = useLoad(() => api.summary(), [], 30000, ["dashboard", "experience", "clients", "devices"]);
  const trends = useLoad(ops.trends, [], 60000, ["dashboard"]);
  const noc = useLoad(ops.noc, [], 60000, ["devices", "telemetry"]);
  const s = summary.data, n = noc.data, hours = trends.data?.hourly ?? [];
  const experience = s?.user_experience;
  const scored = experience ? experience.good + experience.fair + experience.poor : 0;
  const goodShare = scored && experience ? 100 * experience.good / scored : null;
  const online = n?.devices.filter(d => d.state === "ONLINE" || d.state === "DEGRADED").length;
  const runs = n?.collection_last_hour;
  const completed = runs ? (runs.SUCCEEDED ?? 0) + (runs.FAILED ?? 0) : 0;
  const collectionSuccess = completed && runs ? 100 * (runs.SUCCEEDED ?? 0) / completed : null;
  const bands = s?.experience_by_band ?? [];
  const sites = [...(s?.experience_by_site ?? [])].sort((a, b) =>
    b.poor - a.poor || b.fair - a.fair || (a.score ?? 101) - (b.score ?? 101)).slice(0, 6);
  const cpu = n?.devices.filter(d => ["ONLINE", "DEGRADED"].includes(d.state))
    .map(d => d.metrics.cpu_percent).filter((v): v is number => v != null) ?? [];
  const meanCpu = cpu.length ? cpu.reduce((a, b) => a + b, 0) / cpu.length : null;
  return <div className="space-y-5">
    <PageHeader title="Executive overview" eyebrow="SERVICE EXPERIENCE"
      subtitle="User experience, wireless demand and service performance across the network."
      right={<DashboardSwitcher />} />
    <ErrorNote error={summary.error ?? noc.error ?? trends.error} />
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <Stat label="User experience" value={score(experience?.score)}
        sub={s ? `${scored} scored · ${experience?.unscored ?? 0} unscored connected clients` : "Awaiting measurements"}
        tone={experience?.band === "poor" ? "bad" : experience?.band === "fair" ? "warn" : experience?.band === "good" ? "ok" : undefined} />
      <Stat label="Users with good experience" value={pct(goodShare)}
        sub={experience ? `${experience.fair} fair · ${experience.poor} poor` : "Awaiting measurements"} />
      <Stat label="Connected wireless users" value={s?.clients_total ?? "—"} sub="Unique clients with fresh observations" />
      <Stat label="Devices reporting" value={n ? `${online} / ${n.summary.devices}` : "—"}
        sub={n ? `${n.summary.states.OFFLINE ?? 0} offline · ${(n.summary.states.STALE ?? 0) + (n.summary.states.UNKNOWN ?? 0)} stale or unknown` : "Awaiting collection"} />
    </div>
    <div className="grid gap-4 xl:grid-cols-3">
      <Card title="The experience users receive">
        <Donut data={experience ? [
          { name: "Good", value: experience.good, color: COLORS.good },
          { name: "Fair", value: experience.fair, color: COLORS.fair },
          { name: "Poor", value: experience.poor, color: COLORS.poor },
          { name: "Unscored", value: experience.unscored, color: COLORS.none },
        ] : []} />
        <p className="text-xs text-slate-500">Client scores use measured signal, connection rates and observed stability. Coverage depends on available measurements.</p>
      </Card>
      <Card title="5 GHz and 2.4 GHz experience">
        <Donut data={bands.map(b => ({ name: b.band === "unknown" ? "Band unreported" : b.band,
          value: b.clients, color: b.band === "5GHz" ? COLORS.info : b.band === "2.4GHz" ? COLORS.violet : COLORS.none }))} />
        <div className="space-y-2">{bands.map(b => <div key={b.band} className="flex justify-between gap-3 text-sm">
          <span>{b.band === "unknown" ? "Band unreported" : b.band} · {b.clients} users</span><span>{score(b.score)}</span>
        </div>)}</div>
        {!bands.length && <p className="text-sm text-slate-500">No fresh connected-client observations.</p>}
      </Card>
      <Card title="Service delivery">
        <div className="flex justify-center"><Gauge value={collectionSuccess} label="Successful polls · last hour" /></div>
        <dl className="mt-4 space-y-3 text-sm">
          <div className="flex justify-between"><dt>2.4 GHz channel use</dt><dd className="font-semibold">{pct(s?.avg_util_2g_percent)}</dd></div>
          <div className="flex justify-between"><dt>5 GHz channel use</dt><dd className="font-semibold">{pct(s?.avg_util_5g_percent)}</dd></div>
          <div className="flex justify-between"><dt>Average device CPU</dt><dd className="font-semibold">{pct(meanCpu)}</dd></div>
          <div className="flex justify-between"><dt>Open incidents</dt><dd><Link className="inm-link font-semibold" to="/device-alerts">{n?.summary.active_alerts ?? "—"}</Link></dd></div>
        </dl>
        <p className="mt-3 text-xs text-slate-500">CPU covers {cpu.length} reporting devices. Poll success measures collection reliability.</p>
      </Card>
    </div>
    <div className="grid gap-4 xl:grid-cols-2">
      <Card title="User experience over 24 hours">
        {hours.some(h => h.experience_score != null) ? <ResponsiveContainer width="100%" height={240}>
          <LineChart data={hours}><CartesianGrid stroke="#e5edf6" vertical={false} />
            <XAxis dataKey="hour" stroke="#64748b" fontSize={11} /><YAxis domain={[0, 100]} stroke="#64748b" fontSize={11} />
            <Tooltip {...TT} /><Legend /><Line dataKey="experience_score" name="Measured client experience / 100"
              stroke={COLORS.good} strokeWidth={2} dot={false} connectNulls={false} />
          </LineChart>
        </ResponsiveContainer> : <p className="py-12 text-center text-sm text-slate-500">Experience history appears as scored client observations are collected.</p>}
        <p className="text-xs text-slate-500">Hourly scores are weighted by scored client observations.</p>
      </Card>
      <Card title="Wireless demand over 24 hours">
        {hours.some(h => h.clients != null) ? <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={hours}><CartesianGrid stroke="#e5edf6" vertical={false} />
            <XAxis dataKey="hour" stroke="#64748b" fontSize={11} /><YAxis allowDecimals={false} stroke="#64748b" fontSize={11} />
            <Tooltip {...TT} /><Legend /><Area dataKey="clients" name="Average reported client sessions"
              stroke={COLORS.info} fill={COLORS.info} fillOpacity={0.13} connectNulls={false} />
          </AreaChart>
        </ResponsiveContainer> : <p className="py-12 text-center text-sm text-slate-500">Demand history appears after successful collection.</p>}
      </Card>
    </div>
    <Card title="Where users need attention" right={<Link className="inm-link" to="/experience">View details</Link>}>
      <div className="overflow-x-auto"><table className="inm-table">
        <thead><tr><th>Site / controller</th><th>Connected users</th><th>Experience</th><th>Good</th><th>Fair</th><th>Poor</th><th>Unscored</th></tr></thead>
        <tbody>{sites.map(site => <tr key={site.site}><td>{site.site}</td><td>{site.clients}</td><td>{score(site.score)}</td>
          <td>{site.good}</td><td className={site.fair ? "text-amber-700" : ""}>{site.fair}</td>
          <td className={site.poor ? "text-red-700 font-semibold" : ""}>{site.poor}</td><td>{site.unscored}</td></tr>)}</tbody>
      </table></div>
      {!sites.length && <p className="py-6 text-sm text-slate-500">No fresh connected-client observations to compare.</p>}
    </Card>
    {s && <FirmwareExperience summary={s} />}
    {["admin", "auditor"].includes(getSession()?.role ?? "") && <SignedExports />}
    <p className="text-xs text-slate-500">Live refreshes are coalesced; polling provides a fallback.
      {s?.last_poll_at && ` Latest controller observation ${new Date(s.last_poll_at).toLocaleString()}.`}</p>
  </div>;
}

/** Signed, audited exports (GAP-T14): site-scoped CSV with masked identities, SHA-256 and a server signature.
 *  The CSV and its signature record are downloaded separately; POST /v1/reports/exports/verify detects tampering. */
function SignedExports() {
  const [days, setDays] = useState(30);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [last, setLast] = useState<{ name: string; record: string } | null>(null);
  const save = (name: string, text: string, type: string) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  };
  const run = async (kind: "inventory" | "audit" | "attribution", label: string) => {
    setBusy(true); setErr(null); setMsg(null);
    try {
      const end = new Date(), start = new Date(end.getTime() - days * 86400000);
      const r = await ops.exportReport({ kind, start: start.toISOString(), end: end.toISOString() });
      const stamp = end.toISOString().slice(0, 10);
      const name = `inm-${kind}-${stamp}.csv`;
      save(name, r.body, "text/csv");
      const record = JSON.stringify({ sha256: r.sha256, signature: r.signature, meta: r.meta }, null, 1);
      setLast({ name: `inm-${kind}-${stamp}.signature.json`, record });
      setMsg(`Signed ${label} export: ${r.row_count} row(s) · SHA-256 ${r.sha256.slice(0, 16)}… · identities ${String(r.meta.identities ?? "MASKED")} · scope ${String(r.meta.scope ?? "")}`);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return <Card title="Signed exports">
    <p className="text-sm text-slate-600">CSV exports are limited to your site scope, mask user identities, and carry a SHA-256 and a
      server signature. Every export is audited.</p>
    <div className="mt-2 flex flex-wrap items-end gap-2">
      <label className="text-sm">Period (days)<input aria-label="Export period in days" className="ml-2 w-20 rounded border px-2 py-1" type="number" min={1} max={366}
        value={days} onChange={e => setDays(Math.min(366, Math.max(1, Number(e.target.value) || 1)))} /></label>
      <button className="inm-btn-secondary" disabled={busy} onClick={() => run("inventory", "inventory")}>Export inventory</button>
      <button className="inm-btn-secondary" disabled={busy} onClick={() => run("audit", "audit-log")}>Export audit log</button>
      <button className="inm-btn-secondary" disabled={busy} onClick={() => run("attribution", "attribution")}>Export change attribution</button>
      {last && <button className="inm-btn-secondary" onClick={() => save(last.name, last.record, "application/json")}>Download signature record</button>}
    </div>
    <ErrorNote error={err} />
    {msg && <p role="status" className="mt-2 text-sm text-emerald-800">{msg}</p>}
  </Card>;
}
