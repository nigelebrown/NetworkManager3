import { useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import HistoryChart from "../charts/HistoryChart";
import SiteFilter from "../components/SiteFilter";
import { Card, ErrorNote, NA, SimBadge, Stat, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
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
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Sites" value={s.sites_total} />
            <Stat label="APs online" value={`${s.aps_up} / ${s.aps_total}`} tone={s.aps_down ? "bad" : "ok"} sub={s.aps_down ? `${s.aps_down} offline` : "all online"} />
            <Stat label="Wireless clients" value={s.clients_total} />
            <Stat label="Active alerts" value={s.alerts_active} tone={s.alerts_by_severity.critical ? "bad" : s.alerts_active ? "warn" : "ok"}
              sub={`${s.alerts_by_severity.critical} critical · ${s.alerts_by_severity.warning} warning`} />
            <Stat label="Avg client SNR" value={s.avg_snr_db != null ? `${s.avg_snr_db} dB` : NA} sub="AP-reported" />
            <Stat label="Channel utilization" value={s.avg_channel_utilization_percent != null ? `${s.avg_channel_utilization_percent}%` : NA}
              sub={s.avg_channel_utilization_percent == null ? "not collected yet (live)" : "AP-reported"} />
            <Stat label="Network health" value={s.health_score ?? NA} tone={s.health_score == null ? undefined : s.health_score >= 90 ? "ok" : s.health_score >= 70 ? "warn" : "bad"}
              sub="availability-based (AP uptime + site polling)" />
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
