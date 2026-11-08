import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, ErrorNote, NA, fmtTime } from "../components/ui";
import { CapChip, DemoBadge, Table } from "../components/inm";
import { useLoad } from "../components/useLoad";
import { api } from "../services/api";
import { v1, type Device, type MetricPoint } from "../services/inm";

function latest(points: MetricPoint[]) {
  const out: Record<string, number | null> = {};
  for (const p of points) if (!(p.metric_name in out)) out[p.metric_name] = p.value;  // API returns newest first
  return out;
}

export default function Switches() {
  const devs = useLoad(() => v1.devices("switch"), [], 30000, ["devices"]);
  const sites = useLoad(() => api.sites(), [], 0, ["sites"]);
  const physical = useLoad(() => v1.physicalSites(), [], 30000, ["sites"]);
  const [metrics, setMetrics] = useState<Record<string, Record<string, number | null>>>({});
  useEffect(() => {
    devs.data?.forEach((d) => v1.metrics(d.id, 1).then((m) => setMetrics((x) => ({ ...x, [d.id]: latest(m.points) }))).catch(() => {}));
  }, [devs.data]);
  const siteName = (d: Device) => d.legacy_site_id
    ? (sites.data?.find((s) => s.id === d.legacy_site_id)?.name ?? "—")
    : (d.site_id ? physical.data?.sites.find((s) => s.id === d.site_id)?.name ?? "Real inventory" : "—");
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Switches</h1>
      <div className="rounded-lg border border-slate-700 bg-slate-900/40 p-3 text-sm text-slate-200">
        DEMO switches are simulated. REAL devices are imported from actual readings or a read-only Aruba CX REST probe.
        No switch configuration changes are permitted.
        {" "}<Link className="text-sky-400 underline" to="/onboarding">Add a switch</Link>
      </div>
      <ErrorNote error={devs.error} />
      <Card>
        <Table head={["Switch", "Model", "Site", "Status", "Ports up", "PoE", "CPU", "Capabilities", "Observed"]} empty={devs.data?.length === 0}>
          {devs.data?.map((d) => {
            const m = metrics[d.id] ?? {};
            return (
              <tr key={d.id}>
                <td className="px-2 py-2"><div className="flex items-center gap-2 font-medium">{d.hostname}<DemoBadge source={d.source_type} />{d.source_type === "REAL" && <span className="text-xs text-emerald-300">{d.adapter === "manual_snapshot" ? "MANUAL" : "REST READ"}</span>}</div><div className="text-xs text-slate-500">{d.serial_number} · {d.firmware}</div></td>
                <td className="px-2 py-2">{d.model}</td>
                <td className="px-2 py-2">{siteName(d)}</td>
                <td className="px-2 py-2"><Badge value={d.status} /></td>
                <td className="px-2 py-2">{m.ports_up != null ? `${m.ports_up}/${m.ports_total}` : NA}</td>
                <td className="px-2 py-2">{m.poe_power_w != null ? `${m.poe_power_w} W` : NA}</td>
                <td className="px-2 py-2">{m.cpu_percent != null ? `${m.cpu_percent}%` : NA}</td>
                <td className="px-2 py-2"><div className="flex max-w-xs flex-wrap gap-1">{d.capabilities?.map((c) => <CapChip key={c.operation} op={c.operation} status={c.status} evidence={c.evidence} />)}</div></td>
                <td className="px-2 py-2 text-xs text-slate-500">{fmtTime(d.last_observed_at)}</td>
              </tr>
            );
          })}
        </Table>
      </Card>
    </div>
  );
}
