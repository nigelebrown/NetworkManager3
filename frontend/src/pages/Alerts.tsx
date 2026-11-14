import { PageHeader } from "../components/viz";
import { useState } from "react";
import { Badge, Card, ErrorNote, SimBadge, fmtTime } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { api, getSession } from "../services/api";

export default function Alerts() {
  const [status, setStatus] = useState("");
  const { data, error, reload } = useLoad(() => api.alerts(status || undefined), [status]);
  const canAct = getSession()?.role !== "viewer";
  const act = async (fn: () => Promise<unknown>) => { await fn(); reload(); };
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <PageHeader eyebrow="Monitoring & diagnostics" title="Wi-Fi alerts" subtitle="Review, acknowledge and resolve observed network alerts." />
        <select value={status} onChange={(e) => setStatus(e.target.value)} className="rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm" aria-label="Status">
          <option value="">All</option><option>active</option><option>acknowledged</option><option>resolved</option>
        </select>
      </div>
      <ErrorNote error={error} />
      <Card>
        <ul className="divide-y divide-slate-800">
          {data?.map((a) => (
            <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
              <div>
                <div className="flex items-center gap-2"><Badge value={a.severity} /><Badge value={a.status} /><span className="text-sm">{a.message}</span>{a.simulated && <SimBadge />}</div>
                <div className="mt-1 text-xs text-slate-500">Raised {fmtTime(a.raised_at)}{a.acknowledged_by && ` · ack by ${a.acknowledged_by}`}{a.resolved_at && ` · resolved ${fmtTime(a.resolved_at)}`}</div>
              </div>
              {canAct && a.status !== "resolved" && (
                <div className="flex gap-2 text-sm">
                  {a.status === "active" && <button className="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" onClick={() => act(() => api.ackAlert(a.id))}>Acknowledge</button>}
                  <button className="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800" onClick={() => act(() => api.resolveAlert(a.id))}>Resolve</button>
                </div>
              )}
            </li>
          ))}
          {data?.length === 0 && <li className="py-6 text-center text-sm text-slate-500">No alerts.</li>}
        </ul>
      </Card>
    </div>
  );
}
