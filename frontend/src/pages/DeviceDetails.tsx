import { PageHeader } from "../components/viz";
import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Card, ErrorNote, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { v1, type MetricPoint } from "../services/inm";

const labels: Record<string, string> = {
  clients: "Connected clients",
  util_5g_percent: "5 GHz channel utilization",
  util_2g_percent: "2.4 GHz channel utilization",
  cpu_percent: "CPU utilization",
  memory_percent: "Memory utilization",
  uptime_seconds: "Uptime",
  ports_up: "Ports up",
  ports_total: "Total interfaces",
  poe_power_w: "PoE consumption",
};

function metricValue(p: MetricPoint): string {
  if (p.value == null) return "Not measured";
  const value = Number.isInteger(p.value) ? String(p.value) : p.value.toFixed(1);
  return `${value}${p.unit === "%" ? "%" : p.unit ? ` ${p.unit}` : ""}`;
}

export default function DeviceDetails() {
  const { id = "" } = useParams<{ id: string }>();
  const device = useLoad(() => v1.getDevice(id), [id], 30000, ["devices"]);
  const points = useLoad(() => v1.metrics(id, 168), [id], 30000, ["devices", "metrics"]);
  const sites = useLoad(() => v1.physicalSites(), [], 30000, ["sites"]);
  const latest = useMemo(() => {
    const first: Record<string, MetricPoint> = {};
    for (const p of points.data?.points ?? []) {
      if (!(p.metric_name in first)) first[p.metric_name] = p;
    }
    return Object.entries(first).sort(([a], [b]) => a.localeCompare(b));
  }, [points.data]);
  const d = device.data;
  const site = sites.data?.sites.find((s) => s.id === d?.site_id)?.name ?? "Unassigned";
  return (
    <div className="space-y-4">
      <Link to="/onboarding/manual" className="text-sm text-sky-400 hover:underline">← Back to device inventory</Link>
      <div>
        <PageHeader eyebrow="Sites & inventory" title={d?.hostname ?? "Device details"} subtitle="Device identity, provenance, observations and management details." />
        <p className="mt-1 text-sm text-slate-400">
          {d?.family === "switch" ? "Aruba CX switch" : "Aruba access point"} · {site}
        </p>
      </div>
      <ErrorNote error={device.error ?? points.error ?? sites.error} />
      {d && <>
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Device identity">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-slate-400">Data source</dt><dd className="font-medium text-emerald-300">
                {d.source_type === "REAL" ? (d.adapter === "manual_snapshot" ? "REAL · MANUAL" : "REAL · REST READ") : "DEMO · SIMULATED"}
              </dd>
              <dt className="text-slate-400">Model</dt><dd>{d.model ?? "Unknown"}</dd>
              <dt className="text-slate-400">Serial number</dt><dd className="font-mono text-xs">{d.serial_number}</dd>
              <dt className="text-slate-400">Management IP</dt><dd>{d.management_addresses?.join(", ") || "Not recorded"}</dd>
              <dt className="text-slate-400">Firmware</dt><dd>{d.firmware ?? "Not recorded"}</dd>
              <dt className="text-slate-400">Status</dt><dd><Badge value={d.status} /></dd>
              <dt className="text-slate-400">Last observation</dt><dd>{fmtTime(d.last_observed_at)}</dd>
            </dl>
          </Card>
          <Card title="Observation quality">
            <p className="text-sm text-slate-300">
              {d.adapter === "manual_snapshot"
                ? "Values were entered by an administrator. They are not continuously polled or independently verified."
                : d.adapter === "aruba_readonly_probe"
                  ? "A one-time read-only Aruba REST request populated this record. A REST snapshot is not continuous monitoring."
                  : "This device is simulated and is not linked to physical network equipment."}
            </p>
            <p className="mt-3 text-sm text-amber-200">Configuration writes are disabled. No SSIDs, VLANs, radio settings or switch ports will be modified.</p>
            <div className="mt-3 flex flex-wrap gap-3">
              <Link className="text-sm text-sky-400 underline" to={`/devices/${id}/config`}>
                View imported configuration / edit draft
              </Link>
              <Link className="text-sm text-slate-300 underline" to="/onboarding/manual">
                Update inventory details manually
              </Link>
            </div>
          </Card>
        </div>
        <Card title="Latest recorded readings">
          {latest.length === 0 ? (
            <p className="text-sm text-slate-400">No observations recorded for the selected period. Add readings in Inventory → Add device.</p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {latest.map(([name, point]) => (
                <div key={name} className="rounded-lg border border-slate-700 bg-slate-900/40 p-3">
                  <div className="text-xs text-slate-400">{labels[name] ?? name}</div>
                  <div className="mt-1 text-xl font-semibold">{metricValue(point)}</div>
                  <div className="mt-2 text-xs text-slate-500">{fmtTime(point.observed_at)} · {point.collection_method ?? point.quality}</div>
                </div>
              ))}
            </div>
          )}
        </Card>
        <Card title="Recent observations (last 7 days)">
          <div className="max-h-80 overflow-auto">
            <table className="w-full min-w-[500px] text-left text-sm">
              <thead className="sticky top-0 bg-slate-900 text-xs uppercase text-slate-400"><tr>
                {["Observed", "Metric", "Value", "Source"].map((h) => <th key={h} className="px-3 py-2">{h}</th>)}
              </tr></thead>
              <tbody className="divide-y divide-slate-800">
                {(points.data?.points ?? []).slice(0, 100).map((p, i) => (
                  <tr key={`${p.observed_at}-${p.metric_name}-${i}`}>
                    <td className="px-3 py-2">{fmtTime(p.observed_at)}</td>
                    <td className="px-3 py-2">{labels[p.metric_name] ?? p.metric_name}</td>
                    <td className="px-3 py-2">{metricValue(p)}</td>
                    <td className="px-3 py-2 text-xs text-slate-400">{p.collection_method ?? p.quality}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </>}
    </div>
  );
}
