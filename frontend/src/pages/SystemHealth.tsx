import { useState } from "react";
import { Card, ErrorNote, fmtTime } from "../components/ui";
import { StatusBadge, Table, btnPrimary } from "../components/inm";
import { useLoad } from "../components/useLoad";
import { getSession } from "../services/api";
import { v1, type Trace } from "../services/inm";

const STAGES: { key: keyof Trace; label: string }[] = [
  { key: "management_db", label: "PostgreSQL event record" },
  { key: "timeseries_db", label: "TimescaleDB metric" },
  { key: "loki", label: "Loki log line" },
  { key: "websocket", label: "WebSocket notice from API hub" },
];

export default function SystemHealth() {
  const { data, error, reload } = useLoad(() => v1.services(), [], 15000, ["system"]);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [busy, setBusy] = useState(false);
  const [tErr, setTErr] = useState<string | null>(null);
  const isAdmin = getSession()?.role === "admin";

  const runProbe = async () => {
    setBusy(true); setTErr(null); setTrace(null);
    try {
      const { correlation_id } = await v1.synthetic();
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        const t = await v1.trace(correlation_id);
        setTrace(t);
        if (t.complete) break;
      }
    } catch (e) { setTErr(e instanceof Error ? e.message : "failed"); }
    finally { setBusy(false); reload(); }
  };
  const has = (t: Trace, k: keyof Trace) => (Array.isArray(t[k]) ? (t[k] as unknown[]).length > 0 : !!t[k]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">System health {data && <StatusBadge status={data.overall} />}</h1>
        <span className="text-xs text-slate-500">Measured {data ? fmtTime(data.checked_at) : "…"} — every status comes from a live probe</span>
      </div>
      <ErrorNote error={error} />
      <Card title="Seven service roles">
        <Table head={["Role", "Status", "Latency", "Version", "Detail"]}>
          {data?.services.map((s) => (
            <tr key={s.name} data-testid={`role-${s.name}`}>
              <td className="px-2 py-2 font-medium">{s.role}</td>
              <td className="px-2 py-2"><StatusBadge status={s.status} /></td>
              <td className="px-2 py-2 text-slate-400">{s.latency_ms != null ? `${s.latency_ms} ms` : "—"}</td>
              <td className="px-2 py-2 text-slate-400">{s.version ?? "—"}</td>
              <td className="px-2 py-2 text-xs text-slate-400">{s.detail}</td>
            </tr>
          ))}
        </Table>
        {data && <div className="mt-2 text-xs text-slate-500">WebSocket gateway: bus {data.websocket_gateway.bus_connected ? "connected" : "DISCONNECTED"}, {data.websocket_gateway.clients} browser(s), cursor {data.websocket_gateway.cursor}</div>}
      </Card>
      <Card title="End-to-end synthetic event" right={isAdmin && <button className={btnPrimary} onClick={runProbe} disabled={busy}>{busy ? "Tracing…" : "Run round-trip"}</button>}>
        <p className="text-sm text-slate-400">Asks the network worker to emit one DEMO event and follows it: NATS JetStream → ingest → PostgreSQL, TimescaleDB and Loki → UI notice through this API's WebSocket hub.</p>
        <ErrorNote error={tErr} />
        {trace && (
          <ul className="mt-3 space-y-1 text-sm" data-testid="trace">
            <li className="text-xs text-slate-500">correlation {trace.correlation_id}</li>
            {STAGES.map((s) => (
              <li key={s.key} className="flex items-center gap-2">
                <span className={has(trace, s.key) ? "text-emerald-400" : "text-slate-500"}>{has(trace, s.key) ? "✓" : "…"}</span>{s.label}
              </li>
            ))}
            <li className={trace.complete ? "font-semibold text-emerald-400" : "text-amber-300"}>{trace.complete ? "Complete" : busy ? "Waiting…" : "Incomplete — see roles above"}</li>
          </ul>
        )}
      </Card>
    </div>
  );
}
