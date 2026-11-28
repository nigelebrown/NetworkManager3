import { useState } from "react";
import { Link } from "react-router";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { PageHeader, Pill } from "../components/viz";
import { request } from "../services/api";

type Row = { device_id: string; hostname: string | null; source_type: string | null; port: string; type: string | null; vendor_name: string | null;
  part_number: string | null; serial_number: string | null; lane_count: number | null; state: string; threshold_source: string | null; observed_at: string };
type Metric = { value: number | null; state: string; reason?: string; threshold?: { low_alarm: number; low_warn: number; high_warn: number; high_alarm: number } };
type Port = { type: string | null; vendor_name: string | null; part_number: string | null; serial_number: string | null; wavelength_nm: number | null;
  lane_count: number; dom_supported: boolean; evaluation: { state: string; threshold_source: string | null; reason?: string; lanes: { lane: number; metrics: Record<string, Metric> }[] };
  rx_trend?: { state: string; slope_db_per_day?: number; days_to_low_warning?: number | null; samples: number } };
const tone = (s: string) => s === "OK" ? "good" : s === "WARNING" ? "fair" : s === "CRITICAL" ? "poor" : "none";
const COLS: [string, string, string][] = [["rx_power_dbm", "Rx dBm", ""], ["tx_power_dbm", "Tx dBm", ""], ["temperature_c", "Temp °C", ""], ["voltage_v", "Volt V", ""], ["bias_ma", "Bias mA", ""]];

export default function Optics() {
  const fleet = useLoad(() => request<Row[]>("/v1/optics"), [], 60000);
  const [sel, setSel] = useState<string | null>(null);
  const detail = useLoad(() => sel ? request<{ ports: Record<string, Port>; observed_at: string }>(`/v1/devices/${encodeURIComponent(sel)}/optics`) : Promise.resolve(null), [sel], 60000);
  const rows = fleet.data ?? [];
  return <div className="space-y-4">
    <PageHeader eyebrow="Monitoring & diagnostics" title="Optical transceivers" subtitle="Identification, per-lane DOM against the module's own thresholds, Rx-power degradation trend and alerts. Missing or unsupported diagnostics are shown as such — never as healthy." />
    <ErrorNote error={fleet.error || detail.error} />
    <Card title={`Transceivers (${rows.length})`}>
      <table className="inm-table"><thead><tr><th>Switch</th><th>Port</th><th>Type</th><th>Vendor / part</th><th>Serial</th><th>Lanes</th><th>State</th><th>Thresholds</th><th>Observed</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.device_id + r.port} className={sel === r.device_id ? "bg-sky-50" : ""}>
          <td><button className="inm-link" onClick={() => setSel(r.device_id)}>{r.hostname ?? r.device_id.slice(0, 8)}</button>{r.source_type === "DEMO" && <> <Pill value="DEMO" tone="demo" /></>}</td>
          <td className="font-mono">{r.port}</td><td>{r.type ?? "—"}</td><td>{[r.vendor_name, r.part_number].filter(Boolean).join(" · ") || "—"}</td>
          <td className="font-mono text-xs">{r.serial_number ?? "—"}</td><td>{r.lane_count ?? "—"}</td><td><Pill value={r.state} tone={tone(r.state)} /></td>
          <td className="text-xs">{r.threshold_source === "MODULE" ? "Module" : r.threshold_source === "PLATFORM_DEFAULT" ? "Platform default" : "—"}</td>
          <td className="text-xs">{new Date(r.observed_at).toLocaleString()}</td></tr>)}
          {!rows.length && <tr><td colSpan={9}>No optics collected yet. Add the “optics” metric family to a switch collection plan.</td></tr>}</tbody></table>
    </Card>
    {sel && detail.data && <Card title="Per-lane diagnostics" right={<Link className="inm-link" to={`/devices/${sel}/360`}>Device 360°</Link>}>
      {Object.entries(detail.data.ports).map(([port, p]) => <div key={port} className="mb-3">
        <div className="text-sm font-semibold">{port} · {p.type ?? "unknown"} {p.wavelength_nm ? `· ${p.wavelength_nm} nm` : ""} <Pill value={p.evaluation.state} tone={tone(p.evaluation.state)} />
          {p.rx_trend && <span className="ml-2 text-xs">Rx trend: {p.rx_trend.state}{p.rx_trend.slope_db_per_day != null ? ` (${p.rx_trend.slope_db_per_day} dB/day${p.rx_trend.days_to_low_warning != null ? `, low warning in ~${p.rx_trend.days_to_low_warning} d` : ""})` : ""}</span>}</div>
        {p.evaluation.state === "UNSUPPORTED" ? <p className="text-xs text-slate-500">{p.evaluation.reason}</p> :
          <table className="inm-table"><thead><tr><th>Lane</th>{COLS.map(c => <th key={c[0]}>{c[1]}</th>)}</tr></thead>
            <tbody>{p.evaluation.lanes.map(l => <tr key={l.lane}><td>{l.lane}</td>{COLS.map(([k]) => { const m = l.metrics[k];
              return <td key={k}>{m?.value ?? (m?.reason ?? "not measured")} {m && m.state !== "OK" && <Pill value={m.state} tone={tone(m.state)} />}</td>; })}</tr>)}</tbody></table>}
      </div>)}
    </Card>}
  </div>;
}
