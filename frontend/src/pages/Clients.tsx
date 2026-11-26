import { useState } from "react";
import SiteFilter from "../components/SiteFilter";
import { Badge, Card, ErrorNote, NA, SimBadge, val } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { api, request } from "../services/api";

export default function Clients() {
  const [site, setSite] = useState<number | undefined>();
  const [text, setText] = useState(""); const [search, setSearch] = useState("");
  const [quality, setQuality] = useState("");
  const { data, error } = useLoad(() => api.clients({ site_id: site, q: search, quality }), [site, search, quality]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><div className="inm-section-label">Monitoring & diagnostics</div><h1 className="inm-pagetitle">Wireless clients <span className="text-sm text-slate-500">({data?.length ?? 0})</span></h1></div>
        <div className="flex flex-wrap gap-2">
          <form onSubmit={(e) => { e.preventDefault(); setSearch(text.trim()); }}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Search MAC, IP, host, SSID, AP" maxLength={64}
              className="w-56 rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" />
          </form>
          <select value={quality} onChange={(e) => setQuality(e.target.value)} className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" aria-label="Quality">
            <option value="">Any quality</option>{["good", "fair", "poor", "unavailable"].map((q) => <option key={q}>{q}</option>)}
          </select>
          <SiteFilter value={site} onChange={setSite} dashboard />
        </div>
      </div>
      <ErrorNote error={error} />
      <RoamingInterference site={site} />
      <Card>
        <p className="mb-2 text-xs text-slate-500">Viewers see masked MAC addresses and no IP/hostname. RSSI, VLAN, auth state and roaming events show “unavailable” until the AP reports them (Phase 2 probes).</p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-left text-sm">
            <thead className="text-xs uppercase text-slate-400"><tr>
              {["MAC", "Host", "IP", "AP", "SSID", "Band/Ch", "SNR", "RSSI", "Speed", "VLAN", "Quality", "Experience"].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}
            </tr></thead>
            <tbody className="divide-y divide-slate-800">
              {data?.map((c) => (
                <tr key={c.id}>
                  <td className="px-2 py-2 font-mono text-xs">{c.mac} {c.simulated && <SimBadge />}</td>
                  <td className="px-2 py-2">{val(c.hostname)}</td><td className="px-2 py-2">{val(c.ip)}</td>
                  <td className="px-2 py-2">{val(c.ap_name)}</td><td className="px-2 py-2">{val(c.ssid)}</td>
                  <td className="px-2 py-2">{c.band ? `${c.band} / ${c.channel}` : val(null)}</td>
                  <td className="px-2 py-2">{val(c.snr_db, " dB")}</td><td className="px-2 py-2">{val(c.rssi_dbm, " dBm")}</td>
                  <td className="px-2 py-2">{val(c.speed_mbps, " Mbps")}</td><td className="px-2 py-2">{val(c.vlan)}</td>
                  <td className="px-2 py-2"><Badge value={c.quality} /></td>
                  <td className="px-2 py-2">{c.exp_score == null ? NA : <span title="Experience score 0-100 (partial inputs; see Experience page)">{c.exp_score} <Badge value={c.exp_band} /></span>}</td>
                </tr>
              ))}
              {data?.length === 0 && <tr><td colSpan={12} className="px-2 py-6 text-center text-slate-500">No clients match.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

type RoamInterf = { hours: number; caveat: string; telemetry_store: boolean;
  roaming: { roams: number; clients_roaming: number; ping_pong_clients: number; top_pairs: { from: string; to: string; count: number }[];
    ping_pong_pairs: { aps: string[]; count: number }[]; excessive_roamers: { mac: string; roams_per_hour: number }[] };
  sticky_candidates: { mac: string; ap: string | null; rssi_dbm: number; connected_seconds: number }[];
  interference: { device_id: string; hostname: string | null; state: string; samples: number; paired_samples?: number;
    median_noise_dbm?: number; high_noise_share?: number; noise_retry_correlation?: number | null; reason?: string }[] };

export function RoamingInterference({ site }: { site?: number }) {
  const { data, error } = useLoad(() => request<RoamInterf>(`/v1/analytics/roaming-interference?hours=24${site != null ? `&site_id=${site}` : ""}`), [site], 120000);
  if (error) return <ErrorNote error={error} />;
  if (!data) return null;
  const r = data.roaming;
  const tone = (s: string) => s === "INTERFERENCE_SUSPECTED" ? "text-red-700" : s === "NOISY" ? "text-amber-700" : s === "CLEAR" ? "text-emerald-700" : "text-slate-500";
  return <Card title="Roaming & RF interference (last 24 h)">
    <p className="mb-2 text-xs text-slate-500">{data.caveat}</p>
    <div className="grid gap-4 md:grid-cols-3 text-sm">
      <div><div className="font-semibold">Roaming</div>
        <div>{r.roams} roams · {r.clients_roaming} clients · {r.ping_pong_clients} ping-pong clients</div>
        <ul className="mt-1 text-xs">{r.top_pairs.slice(0, 6).map(x => <li key={x.from + x.to}>{x.from} → {x.to}: {x.count}</li>)}</ul>
        {r.ping_pong_pairs.length > 0 && <div className="mt-1 text-xs text-amber-700">Ping-pong: {r.ping_pong_pairs.slice(0, 4).map(x => `${x.aps.join(" ⇄ ")} (${x.count})`).join(", ")}</div>}
        {r.excessive_roamers.length > 0 && <div className="mt-1 text-xs">Excessive roamers: {r.excessive_roamers.slice(0, 5).map(x => `${x.mac} ${x.roams_per_hour}/h`).join(", ")}</div>}</div>
      <div><div className="font-semibold">Sticky-client candidates</div>
        {data.sticky_candidates.length ? <ul className="text-xs">{data.sticky_candidates.slice(0, 8).map(c => <li key={c.mac} className="font-mono">{c.mac} on {c.ap ?? "?"} · {c.rssi_dbm} dBm · {Math.round(c.connected_seconds / 60)} min</li>)}</ul>
          : <div className="text-xs text-slate-500">None with a measured weak RSSI.</div>}</div>
      <div><div className="font-semibold">Interference by AP</div>
        {!data.telemetry_store ? <div className="text-xs text-slate-500">Telemetry store unavailable.</div> :
          <ul className="text-xs">{data.interference.slice(0, 10).map(x => <li key={x.device_id}>{x.hostname ?? x.device_id.slice(0, 8)}: <span className={tone(x.state)}>{x.state.replace(/_/g, " ")}</span>
            {x.median_noise_dbm != null && <> · noise {x.median_noise_dbm} dBm · r={x.noise_retry_correlation ?? "n/a"}</>}</li>)}</ul>}</div>
    </div>
  </Card>;
}

