import { useState } from "react";
import { Card, ErrorNote, fmtTime } from "../components/ui";
import { DemoBadge, Table, btn, input } from "../components/inm";
import { useLoad } from "../components/useLoad";
import { v1 } from "../services/inm";

export default function Logs() {
  const [stream, setStream] = useState<"events" | "syslog">("events");
  const [filter, setFilter] = useState("");
  const [applied, setApplied] = useState("");
  const [minutes, setMinutes] = useState(60);
  const logs = useLoad(() => v1.logs(stream, applied, minutes), [stream, applied, minutes], 30000, ["events", "logs"]);
  const events = useLoad(() => v1.events(50), [], 30000, ["events", "devices", "dashboard", "system"]);
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Events &amp; logs</h1>
      <Card title="Log search (Loki, read-only)" right={
        <div className="flex flex-wrap items-center gap-2">
          <select className={input} value={stream} onChange={(e) => setStream(e.target.value as "events" | "syslog")} aria-label="Stream">
            <option value="events">Platform events</option><option value="syslog">Syslog (Alloy)</option>
          </select>
          <select className={input} value={minutes} onChange={(e) => setMinutes(Number(e.target.value))} aria-label="Range">
            {[15, 60, 360, 1440].map((m) => <option key={m} value={m}>Last {m >= 60 ? `${m / 60}h` : `${m}m`}</option>)}
          </select>
          <form onSubmit={(e) => { e.preventDefault(); setApplied(filter); }} className="flex gap-1">
            <input className={input} placeholder="contains…" value={filter} onChange={(e) => setFilter(e.target.value)} maxLength={100} />
            <button className={btn}>Search</button>
          </form>
        </div>}>
        <ErrorNote error={logs.error} />
        {logs.data && <div className="mb-2 font-mono text-[11px] text-slate-500">{logs.data.query}</div>}
        <Table head={stream === "syslog" ? ["Time", "Host", "Device mapping", "Message"] : ["Time", "Event", "Source", "Summary"]} empty={logs.data?.entries.length === 0}>
          {logs.data?.entries.map((e, i) => (
            <tr key={i}>
              <td className="whitespace-nowrap px-2 py-1.5 text-xs text-slate-400">{fmtTime(e.ts)}</td>
              {stream === "syslog" ? (<>
                <td className="px-2 py-1.5">{e.labels.host ?? "—"}</td>
                <td className="px-2 py-1.5 text-xs">{e.correlation === "MAPPED" ? <span className="text-emerald-300">{e.device?.family} {e.device?.hostname} <DemoBadge source={e.device?.source_type} /></span> : <span className="text-amber-300" title={e.unmapped_reason ?? ""}>UNMAPPED — {e.unmapped_reason}</span>}</td>
                <td className="px-2 py-1.5 font-mono text-xs">{e.line}</td>
              </>) : (<>
                <td className="px-2 py-1.5">{String(e.json?.event_type ?? e.labels.event_type ?? "")}</td>
                <td className="px-2 py-1.5 text-xs">{String(e.json?.source_service ?? "")} <DemoBadge source={String(e.json?.source_type ?? "")} /></td>
                <td className="px-2 py-1.5 text-xs text-slate-300">{String(e.json?.summary ?? e.line)}<div className="text-[10px] text-slate-500">corr {String(e.json?.correlation_id ?? "")}</div></td>
              </>)}
            </tr>
          ))}
        </Table>
      </Card>
      <Card title="Latest platform events (management database)">
        <ErrorNote error={events.error} />
        <Table head={["Ingested", "Type", "Actor", "Summary", "Deliveries"]} empty={events.data?.length === 0}>
          {events.data?.map((e) => (
            <tr key={e.event_id}>
              <td className="whitespace-nowrap px-2 py-1.5 text-xs text-slate-400">{fmtTime(e.ingested_at)}</td>
              <td className="px-2 py-1.5 text-xs">{e.event_type} <DemoBadge source={e.source_type} /></td>
              <td className="px-2 py-1.5 text-xs text-slate-400">{e.actor}</td>
              <td className="px-2 py-1.5 text-xs">{e.summary}</td>
              <td className="px-2 py-1.5 text-xs text-slate-400" title="JetStream deliveries; >1 means a retry was absorbed idempotently">{e.delivery_count}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
