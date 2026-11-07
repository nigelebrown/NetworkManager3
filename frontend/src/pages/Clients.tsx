import { useState } from "react";
import SiteFilter from "../components/SiteFilter";
import { Badge, Card, ErrorNote, NA, SimBadge, val } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { api } from "../services/api";

export default function Clients() {
  const [site, setSite] = useState<number | undefined>();
  const [text, setText] = useState(""); const [search, setSearch] = useState("");
  const [quality, setQuality] = useState("");
  const { data, error } = useLoad(() => api.clients({ site_id: site, q: search, quality }), [site, search, quality]);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-lg font-semibold">Wireless clients <span className="text-sm text-slate-500">({data?.length ?? 0})</span></h1>
        <div className="flex flex-wrap gap-2">
          <form onSubmit={(e) => { e.preventDefault(); setSearch(text.trim()); }}>
            <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Search MAC, IP, host, SSID, AP" maxLength={64}
              className="w-56 rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" />
          </form>
          <select value={quality} onChange={(e) => setQuality(e.target.value)} className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" aria-label="Quality">
            <option value="">Any quality</option>{["good", "fair", "poor", "unavailable"].map((q) => <option key={q}>{q}</option>)}
          </select>
          <SiteFilter value={site} onChange={setSite} />
        </div>
      </div>
      <ErrorNote error={error} />
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
