import { useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import HistoryChart from "../charts/HistoryChart";
import SiteFilter from "../components/SiteFilter";
import { Card, ErrorNote, NA, SimBadge, Stat, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Link } from "react-router-dom";
import { api } from "../services/api";

const QCOL: Record<string, string> = { good: "#34d399", fair: "#fbbf24", poor: "#f87171", unavailable: "#64748b" };

export default function Dashboard() {
  const [site, setSite] = useState<number | undefined>();
  const [hours, setHours] = useState(6);
  const sum = useLoad(() => api.summary(site), [site]);
  const hist = useLoad(() => api.history(hours, site), [site, hours]);
  const s = sum.data;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><div className="inm-section-label">Overview & command</div><h1 className="inm-pagetitle">Wi-Fi dashboard {s?.simulated && <SimBadge />}</h1></div>
        <div className="flex items-center gap-2">
          <SiteFilter value={site} onChange={setSite} />
          <select className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" value={hours} onChange={(e) => setHours(Number(e.target.value))} aria-label="History range">
            {[1, 6, 24, 72].map((h) => <option key={h} value={h}>Last {h}h</option>)}
          </select>
        </div>
      </div>
      <ErrorNote error={sum.error} />
      {s && (
        <>
          {s.coverage && s.coverage.unmonitored_aps > 0 && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <b>Only {s.coverage.monitored_aps} of {s.coverage.inventory_aps} registered APs ({s.coverage.percent}%) are in a polled cluster.</b> Every figure below describes those APs only;
            the other {s.coverage.unmonitored_aps} are not observed at all. <Link className="underline" to="/aps#autodiscover">Auto-onboard the rest</Link> · <Link className="underline" to="/ap-health">AP health report</Link></div>}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Monitoring coverage" value={s.coverage ? `${s.coverage.monitored_aps} / ${s.coverage.inventory_aps}` : `${s.aps_total}`}
              tone={s.coverage && s.coverage.unmonitored_aps ? "warn" : "ok"} sub={`APs observed · ${s.sites_total} cluster(s) polled${s.clusters_failing ? `, ${s.clusters_failing} failing` : ""}`} />
            <Stat label="APs online (observed)" value={`${s.aps_up} / ${s.aps_total}`} tone={s.aps_down ? "bad" : "ok"} sub={s.aps_down ? `${s.aps_down} offline` : "all observed APs online"} />
            <Stat label="Wireless clients" value={s.clients_total} sub={Object.entries(s.clients_by_band ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ") || undefined} />
            <Stat label="Active alerts" value={s.alerts_active} tone={s.alerts_by_severity.critical ? "bad" : s.alerts_active ? "warn" : "ok"}
              sub={`${s.alerts_by_severity.critical} critical · ${s.alerts_by_severity.warning} warning`} />
            <Stat label="Avg client SNR" value={s.avg_snr_db != null ? `${s.avg_snr_db} dB` : NA} sub="AP-reported" />
            <Stat label="Channel utilization" value={s.avg_util_5g_percent != null || s.avg_util_2g_percent != null
              ? `${s.avg_util_2g_percent ?? "—"}% / ${s.avg_util_5g_percent ?? "—"}%` : NA}
              sub={s.avg_util_5g_percent == null && s.avg_util_2g_percent == null ? "not reported by the observed APs" : "2.4 GHz / 5 GHz, AP-reported"} />
            <Stat label="Availability (observed)" value={s.health_score ?? NA} tone={s.health_score == null ? undefined : s.health_score >= 90 ? "ok" : s.health_score >= 70 ? "warn" : "bad"}
              sub={s.coverage && s.coverage.unmonitored_aps ? `observed APs only — ${s.coverage.unmonitored_aps} unobserved excluded` : "70% AP uptime + 30% cluster polling"} />
            <Stat label="User experience" value={s.user_experience_score ?? NA}
              tone={s.user_experience.band === "good" ? "ok" : s.user_experience.band === "fair" ? "warn" : s.user_experience.band === "poor" ? "bad" : undefined}
              sub={s.user_experience_score == null ? "no scorable clients" : `${s.user_experience.good} good · ${s.user_experience.fair} fair · ${s.user_experience.poor} poor · partial inputs`} />
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2"><Card title="Clients & signal over time"><HistoryChart data={hist.data ?? []} /></Card></div>
            <Card title="Client connection quality">
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={Object.entries(s.client_quality).map(([k, v]) => ({ name: k, count: v }))}>
                  <XAxis dataKey="name" stroke="#64748b" fontSize={12} /><YAxis allowDecimals={false} stroke="#64748b" fontSize={12} />
                  <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} cursor={{ fill: "#1e293b" }} />
                  <Bar dataKey="count">{Object.keys(s.client_quality).map((k) => <Cell key={k} fill={QCOL[k]} />)}</Bar>
                </BarChart>
              </ResponsiveContainer>
              <p className="mt-2 text-xs text-slate-500">Quality uses the AP's own label when given, otherwise SNR thresholds (≥25 good, ≥15 fair).</p>
            </Card>
          </div>
          <Card title="AP firmware versions">
            <ul className="flex flex-wrap gap-2 text-sm">
              {Object.entries(s.firmware_versions).map(([v, n]) => (
                <li key={v} className="rounded border border-slate-700 px-2 py-1">{v} <span className="text-slate-500">× {n}</span></li>
              ))}
            </ul>
            {Object.keys(s.firmware_versions).length > 1 && <p className="mt-2 text-xs text-amber-400">More than one firmware version in view.</p>}
          </Card>
          <p className="text-xs text-slate-500">Last poll: {fmtTime(s.last_poll_at)} · auto-refreshes every 30 s</p>
        </>
      )}
    </div>
  );
}
