import { ReactNode, useState } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { useLoad } from "./useLoad";
import { v1, type Device } from "../services/inm";

export const COLORS = { good: "#2d9d70", fair: "#dfaa51", poor: "#c8626b", none: "#a0afc2", info: "#357fdb", violet: "#8f77c5" };
export const TT = { contentStyle: { background: "#fff", border: "1px solid #dfe6ed", borderRadius: 8 }, itemStyle: { color: "#253c55" } };
export const inputCls = "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-800 focus:border-blue-500 focus:outline-none";
export const btn = "rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40";
export const btn2 = "rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40";
export const btnDanger = "rounded-lg bg-red-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-40";

export function scoreColor(s: number | null | undefined) {
  return s == null ? COLORS.none : s >= 80 ? COLORS.good : s >= 60 ? COLORS.fair : COLORS.poor;
}

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return <div className="flex flex-wrap items-end justify-between gap-3">
    <div><h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      {subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-400">{subtitle}</p>}</div>{right}</div>;
}

export function Gauge({ value, label, size = 120 }: { value: number | null | undefined; label?: string; size?: number }) {
  const v = value == null ? 0 : Math.max(0, Math.min(100, value));
  const r = size / 2 - 10, c = 2 * Math.PI * r, arc = c * 0.75;
  return <div className="flex flex-col items-center">
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <g transform={`rotate(135 ${size / 2} ${size / 2})`}>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#e5edf6" strokeWidth={10} strokeDasharray={`${arc} ${c}`} strokeLinecap="round" />
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={scoreColor(value)} strokeWidth={10}
          strokeDasharray={`${(arc * v) / 100} ${c}`} strokeLinecap="round" />
      </g>
      <text x="50%" y="52%" textAnchor="middle" dominantBaseline="middle" fill="#253c55" fontSize={size / 4.5} fontWeight={600}>
        {value == null ? "—" : Math.round(value)}</text>
    </svg>
    {label && <div className="-mt-3 text-xs uppercase tracking-wide text-slate-400">{label}</div>}
  </div>;
}

export function Donut({ data, height = 180 }: { data: { name: string; value: number; color?: string }[]; height?: number }) {
  const rows = data.filter(d => d.value > 0);
  if (!rows.length) return <p className="py-8 text-center text-sm text-slate-500">No data yet</p>;
  const palette = [COLORS.info, COLORS.good, COLORS.fair, COLORS.poor, COLORS.violet, COLORS.none];
  return <div className="flex items-center gap-4">
    <div style={{ width: height, height }}><ResponsiveContainer>
      <PieChart><Pie data={rows} dataKey="value" nameKey="name" innerRadius="60%" outerRadius="95%" paddingAngle={2} stroke="none">
        {rows.map((d, i) => <Cell key={d.name} fill={d.color ?? palette[i % palette.length]} />)}</Pie><Tooltip {...TT} /></PieChart>
    </ResponsiveContainer></div>
    <ul className="space-y-1 text-sm">{rows.map((d, i) => <li key={d.name} className="flex items-center gap-2">
      <span className="h-2.5 w-2.5 rounded-full" style={{ background: d.color ?? palette[i % palette.length] }} />
      <span className="text-slate-300">{d.name}</span><span className="font-semibold">{d.value}</span></li>)}</ul>
  </div>;
}

export function Pill({ value, tone }: { value: ReactNode; tone?: "good" | "fair" | "poor" | "info" | "demo" | "none" }) {
  const map = { good: "bg-emerald-50 border border-emerald-200 text-emerald-700", fair: "bg-amber-50 border border-amber-200 text-amber-700", poor: "bg-red-50 border border-red-200 text-red-700",
    info: "bg-sky-50 border border-sky-200 text-sky-700", demo: "bg-fuchsia-50 border border-fuchsia-200 text-fuchsia-700", none: "bg-slate-100 border border-slate-200 text-slate-700" };
  return <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${map[tone ?? "none"]}`}>{value}</span>;
}

export const statusTone = (s: string): "good" | "fair" | "poor" | "info" | "none" =>
  ["PASS", "FRESH", "GOOD", "VERIFIED", "APPROVED", "SUCCEEDED", "ACTIVE", "COMPLIANT", "up", "A"].includes(s) ? "good"
    : ["WARN", "STALE", "FAIR", "CORRELATED", "PENDING_APPROVAL", "RETRY", "PAUSED", "B", "C", "SHADOW", "DRAFT", "PRECHECK_PASSED"].includes(s) ? "fair"
      : ["FAIL", "GAP", "POOR", "UNKNOWN_ACTOR", "REJECTED", "DLQ", "DENY", "DRIFT", "down", "D", "BLOCKED", "INTEGRITY_FAILED"].includes(s) ? "poor"
        : "none";

export function SourceTag({ source }: { source: string }) {
  return source === "DEMO" ? <Pill value="DEMO" tone="demo" /> : <Pill value="LIVE" tone="info" />;
}

export function DevicePicker({ value, onChange, family, label = "Device" }:
  { value: string; onChange: (id: string, d?: Device) => void; family?: "access_point" | "switch"; label?: string }) {
  const devices = useLoad(() => v1.devices(family), [family], 60000, ["devices"]);
  const list = devices.data ?? [];
  return <label className="block text-sm text-slate-300">{label}
    <select aria-label={label} className={inputCls} value={value} onChange={e => onChange(e.target.value, list.find(d => d.id === e.target.value))}>
      <option value="">Select a device…</option>
      {list.map(d => <option key={d.id} value={d.id}>{d.source_type === "DEMO" ? "[DEMO] " : ""}{d.hostname ?? d.serial_number} — {d.model ?? "?"} ({d.family === "switch" ? "switch" : "AP"})</option>)}
    </select></label>;
}

/** Wrap async UI actions with busy/error/message state. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const run = async (fn: () => Promise<string | void>) => {
    setBusy(true); setError(null); setMessage(null);
    try { const m = await fn(); if (m) setMessage(m); }
    catch (e) { setError(e instanceof Error ? e.message : "Action failed"); }
    finally { setBusy(false); }
  };
  return { busy, error, message, run, setMessage };
}

export function Notice({ error, message }: { error: string | null; message: string | null }) {
  if (error) return <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>;
  if (message) return <div role="status" className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-700">{message}</div>;
  return null;
}

export function Steps({ steps, current }: { steps: string[]; current: number }) {
  return <ol className="flex flex-wrap gap-2 text-xs">{steps.map((s, i) =>
    <li key={s} className={`flex items-center gap-2 rounded-full border px-3 py-1 ${i === current ? "border-sky-500 bg-sky-500/10 text-sky-200" : i < current ? "border-emerald-800 text-emerald-300" : "border-slate-700 text-slate-500"}`}>
      <span className="font-semibold">{i < current ? "✓" : i + 1}</span>{s}</li>)}</ol>;
}

export const ago = (iso: string | null | undefined) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  return s < 60 ? `${Math.round(s)}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : s < 86400 ? `${Math.round(s / 3600)}h ago` : `${Math.round(s / 86400)}d ago`;
};
