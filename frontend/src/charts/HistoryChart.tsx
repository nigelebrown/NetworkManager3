import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { HistoryPoint } from "../services/api";

const tick = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export default function HistoryChart({ data }: { data: HistoryPoint[] }) {
  if (data.length < 2) return <p className="py-10 text-center text-sm text-slate-500">Collecting data… history appears after a few polls.</p>;
  const hasSnr = data.some((d) => d.avg_snr_db != null);
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 5, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
        <XAxis dataKey="ts" tickFormatter={tick} stroke="#64748b" fontSize={12} minTickGap={40} />
        <YAxis yAxisId="l" stroke="#64748b" fontSize={12} allowDecimals={false} />
        {hasSnr && <YAxis yAxisId="r" orientation="right" stroke="#64748b" fontSize={12} />}
        <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155" }} labelFormatter={(v) => new Date(v as string).toLocaleString()} />
        <Legend />
        <Line yAxisId="l" type="monotone" dataKey="clients" name="Clients" stroke="#38bdf8" dot={false} strokeWidth={2} />
        {hasSnr && <Line yAxisId="r" type="monotone" dataKey="avg_snr_db" name="Avg SNR (dB)" stroke="#a78bfa" dot={false} strokeWidth={2} connectNulls />}
      </LineChart>
    </ResponsiveContainer>
  );
}
