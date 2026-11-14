import { Link } from "react-router-dom";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { PageHeader, Pill } from "../components/viz";
import { rem } from "../services/remediation";

/** Physical topology from LLDP: each switch with the neighbours its ports report (audit E2E-20 / M10). */
export default function Topology() {
  const t = useLoad(() => rem.topology(), [], 60000, ["telemetry", "devices"]);
  const g = t.data;
  const switches = (g?.nodes ?? []).filter(n => (g?.edges ?? []).some(e => e.from === n.id));
  const byId = Object.fromEntries((g?.nodes ?? []).map(n => [n.id, n]));
  const unlinked = (g?.nodes ?? []).filter(n => n.inventory && !(g?.edges ?? []).some(e => e.from === n.id || e.to === n.id));
  return <div className="space-y-4">
    <PageHeader title="Topology" subtitle={g?.note ?? "Built from LLDP at the last poll."} />
    <ErrorNote error={t.error} />
    {switches.map(sw => {
      const links = (g?.edges ?? []).filter(e => e.from === sw.id).sort((a, b) => Number(a.local_port.split("/").pop()) - Number(b.local_port.split("/").pop()));
      const h = Math.max(80, links.length * 34 + 20);
      return <Card key={sw.id} title={`${sw.label} · ${sw.model ?? ""}`} right={<Link className="text-xs text-sky-300" to={`/devices/${sw.id}/360`}>Device 360 →</Link>}>
        <svg viewBox={`0 0 760 ${h}`} className="w-full" role="img" aria-label={`Topology of ${sw.label}`}>
          <rect x={10} y={h / 2 - 22} width={170} height={44} rx={8} fill="#0c4a6e" stroke="#38bdf8" />
          <text x={95} y={h / 2 + 5} textAnchor="middle" fill="#e2e8f0" fontSize={13}>{sw.label}</text>
          {links.map((e, i) => {
            const y = 20 + i * 34, peer = byId[e.to];
            const up = e.link === "up";
            return <g key={i}>
              <line x1={180} y1={h / 2} x2={470} y2={y + 12} stroke={up ? "#34d399" : "#64748b"} strokeDasharray={up ? undefined : "4 3"} />
              <text x={300} y={(h / 2 + y + 12) / 2 - 4} fill="#94a3b8" fontSize={10}>{e.local_port}{e.poe_w ? ` · ${e.poe_w} W` : ""}</text>
              <rect x={470} y={y} width={270} height={24} rx={5} fill={peer?.inventory ? "#1e293b" : "#0f172a"} stroke={peer?.inventory ? "#475569" : "#334155"} strokeDasharray={peer?.inventory ? undefined : "3 2"} />
              <text x={480} y={y + 16} fill={peer?.inventory ? "#e2e8f0" : "#94a3b8"} fontSize={11}>{peer?.label ?? e.to}{e.remote_port ? ` (${e.remote_port})` : ""}{peer && !peer.inventory ? " — not in inventory" : ""}</text>
            </g>;
          })}
        </svg>
      </Card>;
    })}
    {g && !switches.length && <Card><p className="text-sm text-slate-600">No LLDP links have been collected. Register a switch and include LLDP in its collection plan.</p><div className="mt-2 flex gap-4"><Link className="text-blue-700 underline" to="/switches">Switch inventory</Link><Link className="text-blue-700 underline" to="/collection">Collection plans</Link></div></Card>}
    {!!unlinked.length && <Card title="Inventory devices without LLDP links">
      <div className="flex flex-wrap gap-2">{unlinked.map(n => <Link key={n.id} to={`/devices/${n.id}/360`}><Pill value={n.label} tone={n.source_type === "DEMO" ? "demo" : "none"} /></Link>)}</div></Card>}
  </div>;
}
