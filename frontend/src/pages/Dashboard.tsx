import { useState } from "react";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import HistoryChart from "../charts/HistoryChart";
import SiteFilter from "../components/SiteFilter";
import { Card, ErrorNote, NA, SimBadge, Stat, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Link } from "react-router-dom";
import { api } from "../services/api";
import DashboardSwitcher from "../components/DashboardSwitcher";
import FirmwareExperience from "../components/FirmwareExperience";
import { COLORS, Donut } from "../components/viz";

const QCOL: Record<string, string> = { good: "#34d399", fair: "#fbbf24", poor: "#f87171", unavailable: "#64748b" };

export default function Dashboard() {
  const [site, setSite] = useState<number | undefined>();
  const [hours, setHours] = useState(6);
  const [view, setView] = useState("experience");
  const sum = useLoad(() => api.summary(site), [site], 15000, ["devices", "telemetry", "sites", "clients"]);
  const hist = useLoad(() => api.history(hours, site), [site, hours]);
  const s = sum.data;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><div className="inm-section-label">Overview & command</div><h1 className="inm-pagetitle">Wi-Fi dashboard {s?.simulated && <SimBadge />}</h1></div>
        <div className="flex items-center gap-2">
          <DashboardSwitcher />
          <SiteFilter value={site} onChange={setSite} dashboard />
          <select className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" value={hours} onChange={(e) => setHours(Number(e.target.value))} aria-label="History range">
            {[1, 6, 24, 72].map((h) => <option key={h} value={h}>Last {h}h</option>)}
          </select>
        </div>
      </div>
      <ErrorNote error={sum.error} />
      <nav className="inm-tabs" aria-label="Wireless dashboard view">
        {[["experience", "User experience"], ["performance", "RF & device performance"], ["firmware", "Firmware comparison"]].map(([key, label]) =>
          <button key={key} className={`inm-tab ${view === key ? "active" : ""}`} onClick={() => setView(key)}>{label}</button>)}
      </nav>
      {s && (
        <>
          {s.coverage && s.coverage.unmonitored_aps > 0 && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
            <b>{s.coverage.monitored_aps} of {s.coverage.inventory_aps} registered APs ({s.coverage.percent}%) are linked to a controller.</b>{" "}
            <Link className="underline" to="/aps#autodiscover">Auto-onboard the rest</Link> · <Link className="underline" to="/ap-health">AP health report</Link></div>}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Monitoring coverage" value={s.coverage ? `${s.coverage.monitored_aps} / ${s.coverage.inventory_aps}` : `${s.aps_total}`}
              tone={s.coverage && s.coverage.unmonitored_aps ? "warn" : "ok"} sub={`APs observed · ${s.sites_total} cluster(s) polled${s.clusters_failing ? `, ${s.clusters_failing} failing` : ""}`} />
            <Stat label="APs online" value={`${s.ap_states.ONLINE ?? 0} / ${s.coverage?.inventory_aps ?? s.aps_total}`}
              tone={s.ap_states.OFFLINE ? "bad" : undefined}
              sub={`${s.ap_states.OFFLINE ?? 0} offline · ${s.ap_states.STALE ?? 0} stale · ${s.ap_states.UNKNOWN ?? 0} unknown`} />
            <Stat label="Connected wireless clients" value={s.clients_total} sub={Object.entries(s.clients_by_band ?? {}).map(([k, v]) => `${k} ${v}`).join(" · ") || undefined} />
            <Stat label="Active alerts" value={s.alerts_active} tone={s.alerts_by_severity.critical ? "bad" : s.alerts_active ? "warn" : "ok"}
              sub={`${s.alerts_by_severity.critical} critical · ${s.alerts_by_severity.warning} warning`} />
            <Stat label="Avg client SNR" value={s.avg_snr_db != null ? `${s.avg_snr_db} dB` : NA} sub="AP-reported" />
            <Stat label="Channel utilization" value={s.avg_util_5g_percent != null || s.avg_util_2g_percent != null
              ? `${s.avg_util_2g_percent ?? "—"}% / ${s.avg_util_5g_percent ?? "—"}%` : NA}
              sub={s.avg_util_5g_percent == null && s.avg_util_2g_percent == null ? "not reported by the observed APs" : "2.4 GHz / 5 GHz, AP-reported"} />
            <Stat label="Availability (observed)" value={s.health_score ?? NA} tone={s.health_score == null ? undefined : s.health_score >= 90 ? "ok" : s.health_score >= 70 ? "warn" : "bad"}
              sub="70% AP availability from fresh observations + 30% current cluster polling" />
            <Stat label="User experience" value={s.user_experience_score ?? NA}
              tone={s.user_experience.band === "good" ? "ok" : s.user_experience.band === "fair" ? "warn" : s.user_experience.band === "poor" ? "bad" : undefined}
              sub={s.user_experience_score == null ? "no scorable clients" : `${s.user_experience.good} good · ${s.user_experience.fair} fair · ${s.user_experience.poor} poor · partial inputs`} />
          </div>
          {view === "experience" && <div className="grid gap-4 lg:grid-cols-2">
            <Card title="Client experience">
              <Donut data={[{ name: "Good", value: s.user_experience.good, color: COLORS.good },
                { name: "Fair", value: s.user_experience.fair, color: COLORS.fair },
                { name: "Poor", value: s.user_experience.poor, color: COLORS.poor },
                { name: "Unscored", value: s.user_experience.unscored, color: COLORS.none }]} />
              <Link className="inm-link" to="/experience">Investigate affected users</Link>
            </Card>
            <Card title="Experience by wireless band">
              {(s.experience_by_band ?? []).map(b => <div key={b.band} className="mb-4">
                <div className="flex justify-between text-sm"><strong>{b.band}</strong><span>{b.clients} clients · {b.score == null ? "unscored" : `${b.score} / 100`}</span></div>
                <div className="mt-2 flex h-3 overflow-hidden rounded bg-slate-100">
                  {[["Good", b.good, COLORS.good], ["Fair", b.fair, COLORS.fair], ["Poor", b.poor, COLORS.poor], ["Unscored", b.unscored, COLORS.none]].map(([label, count, color]) =>
                    <div key={String(label)} title={`${label}: ${count}`} style={{ width: `${b.clients ? 100 * Number(count) / b.clients : 0}%`, background: String(color) }} />)}
                </div>
                <p className="mt-1 text-xs text-slate-500">{b.good} good · {b.fair} fair · {b.poor} poor · {b.unscored} unscored</p>
              </div>)}
              {!s.experience_by_band?.length && <p className="text-sm text-slate-500">Awaiting fresh connected-client observations.</p>}
            </Card>
          </div>}
          {view === "performance" && <div className="grid gap-4 lg:grid-cols-3">
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
          </div>}
          {view === "firmware" && <FirmwareExperience summary={s} />}
          <p className="text-xs text-slate-500">Last poll: {fmtTime(s.last_poll_at)} · auto-refreshes every 30 s</p>
        </>
      )}
    </div>
  );
}
