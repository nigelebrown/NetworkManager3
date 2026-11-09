import { useState } from "react";
import { Link } from "react-router-dom";
import { COLORS, Pill, ago } from "./viz";
import type { Port, Ports } from "../services/ops";

const portColor = (p: Port) => {
  if ((p.admin ?? "up") === "down") return "#334155";
  if (p.link !== "up") return "#1e293b";
  if ((p.rx_errors ?? 0) + (p.tx_errors ?? 0) > 0) return COLORS.poor;
  if ((p.rates?.util_percent ?? 0) >= 80) return COLORS.fair;
  return COLORS.good;
};
const speed = (b: number | null) => b == null ? "—" : b >= 1e9 ? `${b / 1e9} G` : `${b / 1e6} M`;
const bps = (b: number | null | undefined) => b == null ? "—" : b >= 1e9 ? `${(b / 1e9).toFixed(2)} Gbps` : b >= 1e6 ? `${(b / 1e6).toFixed(1)} Mbps` : `${(b / 1e3).toFixed(0)} kbps`;

/** Live switch faceplate: port grid in physical order (odd top row, even bottom row like CX hardware). */
export default function Faceplate({ data, deviceId }: { data: Ports; deviceId: string }) {
  const [sel, setSel] = useState<Port | null>(null);
  const ports = [...data.ports].sort((a, b) => Number(a.name.split("/").pop()) - Number(b.name.split("/").pop()));
  const top = ports.filter((_, i) => i % 2 === 0), bottom = ports.filter((_, i) => i % 2 === 1);
  const up = ports.filter(p => p.link === "up").length;
  const poe = ports.reduce((a, p) => a + (p.poe_w ?? 0), 0);
  const hw = data.hardware;
  if (!ports.length) return <p className="text-sm text-slate-500">{data.note ?? "No port data yet."}</p>;
  const Cell = ({ p }: { p: Port }) => <button aria-label={`Port ${p.name}`} onClick={() => setSel(p)}
    title={`${p.name} · ${p.link}${p.vlan ? ` · VLAN ${p.vlan}` : ""}${p.lldp ? ` · ${p.lldp.system_name}` : ""}`}
    className={`relative h-8 w-8 rounded-sm border text-[9px] font-semibold ${sel?.name === p.name ? "border-sky-400" : "border-slate-600"}`}
    style={{ background: portColor(p) }}>
    {p.name.split("/").pop()}
    {(p.poe_w ?? 0) > 0 && <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full bg-amber-300" title={`PoE ${p.poe_w} W`} />}
    {p.lldp && <span className="absolute -bottom-1 -right-1 h-2.5 w-2.5 rounded-full bg-sky-400" title={p.lldp.system_name} />}
  </button>;
  return <div className="space-y-3">
    <div className="flex flex-wrap gap-4 text-sm">
      <span><b>{up}</b>/{ports.length} ports up</span><span>PoE <b>{poe.toFixed(1)} W</b></span>
      {hw.cpu_percent != null && <span>CPU <b>{hw.cpu_percent}%</b></span>}
      {hw.temp_max_c != null && <span>Temp <b>{hw.temp_max_c} °C</b></span>}
      <span>Fans {hw.fans_failed ? <Pill value={`${hw.fans_failed} failed`} tone="poor" /> : <Pill value={`${hw.fans_total ?? 0} ok`} tone="good" />}</span>
      <span>PSU {hw.psu_failed ? <Pill value={`${hw.psu_failed} failed`} tone="poor" /> : <Pill value={`${hw.psu_total ?? 0} ok`} tone="good" />}</span>
      <span className="text-xs text-slate-500">updated {ago(data.collected_at)} {data.environment === "EMULATED" && "· EMULATED"}</span>
    </div>
    <div className="overflow-x-auto rounded-lg border border-slate-700 bg-slate-950 p-3" data-testid="faceplate">
      <div className="flex gap-1">{top.map(p => <Cell key={p.name} p={p} />)}</div>
      <div className="mt-1 flex gap-1">{bottom.map(p => <Cell key={p.name} p={p} />)}</div>
    </div>
    <div className="flex flex-wrap gap-3 text-[11px] text-slate-400">
      {[["Up", COLORS.good], ["Busy ≥80%", COLORS.fair], ["Errors", COLORS.poor], ["Down", "#1e293b"], ["Admin down", "#334155"]].map(([l, c]) =>
        <span key={l} className="flex items-center gap-1"><span className="h-3 w-3 rounded-sm" style={{ background: c }} />{l}</span>)}
      <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-amber-300" />PoE</span>
      <span className="flex items-center gap-1"><span className="h-2.5 w-2.5 rounded-full bg-sky-400" />LLDP neighbour</span>
    </div>
    {sel && <div className="rounded-lg border border-slate-700 p-3 text-sm" role="region" aria-label="Port details">
      <div className="flex items-center gap-2"><b>Port {sel.name}</b><Pill value={sel.link} tone={sel.link === "up" ? "good" : "none"} />
        {sel.admin === "down" && <Pill value="admin down" tone="fair" />}<button className="ml-auto text-xs text-slate-400" onClick={() => setSel(null)}>close</button></div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 md:grid-cols-4">
        <dt className="text-slate-400">Speed / duplex</dt><dd>{speed(sel.speed_bps)} {sel.duplex ?? ""}</dd>
        <dt className="text-slate-400">VLAN</dt><dd>{sel.vlan_mode} {sel.vlan ?? ""}</dd>
        <dt className="text-slate-400">RX / TX</dt><dd>{bps(sel.rates?.rx_bps)} / {bps(sel.rates?.tx_bps)}</dd>
        <dt className="text-slate-400">Utilisation</dt><dd>{sel.rates?.util_percent != null ? `${sel.rates.util_percent}%` : "—"}</dd>
        <dt className="text-slate-400">Errors RX/TX</dt><dd>{sel.rx_errors ?? "—"} / {sel.tx_errors ?? "—"}</dd>
        <dt className="text-slate-400">PoE</dt><dd>{sel.poe_w != null ? `${sel.poe_w} W` : "—"} {sel.poe_status ?? ""}</dd>
        <dt className="text-slate-400">Neighbour</dt><dd>{sel.lldp ? `${sel.lldp.system_name} (${sel.lldp.port})` : "—"}</dd>
        <dt className="text-slate-400">Description</dt><dd>{sel.description || "—"}</dd>
      </dl>
      <Link className="mt-2 inline-block text-xs text-sky-300" to={`/changes?device=${deviceId}&port=${encodeURIComponent(sel.name)}`}>Plan a change on this port →</Link>
    </div>}
  </div>;
}
