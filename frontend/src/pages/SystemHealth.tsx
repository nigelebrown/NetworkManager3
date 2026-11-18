import { PageHeader } from "../components/viz";
import { useState } from "react";
import { Card, ErrorNote, fmtTime } from "../components/ui";
import { StatusBadge, Table, btnPrimary } from "../components/inm";
import { useLoad } from "../components/useLoad";
import { getSession } from "../services/api";
import { v1, type PollFamily, type Trace } from "../services/inm";
import { rem } from "../services/remediation";

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
        <PageHeader eyebrow="Overview & command" title="Platform health" subtitle="Live service checks, messaging, telemetry persistence and end-to-end traces." right={data && <StatusBadge status={data.overall} />} />
        <span className="text-xs text-slate-500">Measured {data ? fmtTime(data.checked_at) : "…"} — every status comes from a live probe</span>
      </div>
      <ErrorNote error={error} />
      <ServerResourcesCard />
      <DatabaseHealthCard />
      <CollectionCapacityCard isAdmin={isAdmin} />
      <OutboxCard />
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

function DatabaseHealthCard() {
  const { data, error } = useLoad(v1.databaseHealth, [], 30000);
  return <Card title="Database health" right={data && <span className="text-xs text-slate-500">{fmtTime(data.checked_at)}</span>}>
    <ErrorNote error={error} />
    {data && <Table head={["Database", "Pressure", "Connections", "Active", "Lock waits", "Oldest transaction", "Data size"]}>
      {([["Management · PostgreSQL", data.management], ["Telemetry · TimescaleDB", data.timeseries]] as const)
        .map(([label, row]) => <tr key={label}>
          <td className="p-2">{label}</td>
          <td className="p-2"><StatusBadge status={row.status} /></td>
          {row.status === "UNAVAILABLE" ? <td className="p-2" colSpan={5}>Database measurements unavailable</td> : <>
            <td className="p-2">{row.connections} / {row.connection_limit}</td>
            <td className="p-2">{row.active}</td>
            <td className="p-2">{row.lock_waits}</td>
            <td className="p-2">{row.oldest_transaction_seconds == null ? "—" : `${row.oldest_transaction_seconds} s`}</td>
            <td className="p-2">{(row.size_bytes / 1024 ** 2).toFixed(1)} MiB</td>
          </>}
        </tr>)}
    </Table>}
    <p className="mt-2 text-xs text-slate-500">Pressure is flagged at 80% connection usage or when sessions are waiting on locks.</p>
  </Card>;
}

function ServerResourcesCard() {
  const { data, error } = useLoad(v1.resources, [], 15000);
  const gib = (bytes: number) => `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
  const vm = data?.vm;
  const disk = data?.app_disk;
  const api = data?.api_container;
  const worker = data?.worker;
  const pressure = [
    vm?.load_5m != null && vm.logical_cpus && vm.load_5m >= vm.logical_cpus ? "High VM load" : null,
    vm?.memory_available != null && vm.memory_total && vm.memory_available / vm.memory_total < 0.15 ? "Low VM memory" : null,
    disk && disk.available / disk.total < 0.2 ? "Low app data disk space" : null,
    api?.memory_current != null && api.memory_limit && api.memory_current / api.memory_limit >= 0.9
      ? "API container is near its memory limit" : null,
    worker?.container?.memory_current != null && worker.container.memory_limit &&
      worker.container.memory_current / worker.container.memory_limit >= 0.9
      ? "Network worker is near its memory limit" : null,
  ].filter(Boolean);
  return <Card title="Server and Docker resources" right={data && <span className="text-xs text-slate-500">{fmtTime(data.checked_at)}</span>}>
    <ErrorNote error={error} />
    {data && <>
      {pressure.length > 0 && <p className="mb-2 font-semibold text-amber-700" role="status">{pressure.join(" · ")}</p>}
      <Table head={["Resource", "Current reading"]}>
        <tr><td className="p-2">VM CPU</td><td className="p-2">{vm?.logical_cpus ?? "Unavailable"} vCPUs · 5-minute load {vm?.load_5m?.toFixed(2) ?? "unavailable"}</td></tr>
        <tr><td className="p-2">VM memory</td><td className="p-2">{vm?.memory_available != null && vm.memory_total != null
          ? `${gib(vm.memory_available)} available of ${gib(vm.memory_total)}` : "Unavailable"}</td></tr>
        <tr><td className="p-2">App data disk</td><td className="p-2">{disk ? `${gib(disk.available)} available of ${gib(disk.total)}` : "Unavailable"}</td></tr>
        <tr><td className="p-2">API container</td><td className="p-2">
          {api?.memory_current != null ? `${gib(api.memory_current)} memory used` : "Memory usage unavailable"}
          {api?.memory_limit != null && ` · ${gib(api.memory_limit)} memory limit`}
          {api?.cpu_limit != null && ` · ${api.cpu_limit} vCPU limit`}
        </td></tr>
        <tr><td className="p-2">Network worker container</td><td className="p-2">
          {worker?.container?.memory_current != null ? `${gib(worker.container.memory_current)} memory used` : "Memory usage unavailable"}
          {worker?.container?.memory_limit != null && ` · ${gib(worker.container.memory_limit)} memory limit`}
          {worker?.container?.cpu_limit != null && ` · ${worker.container.cpu_limit} vCPU limit`}
          {worker?.checked_at && ` · reported ${fmtTime(worker.checked_at)}`}
        </td></tr>
        <tr><td className="p-2">Worker poll limits</td><td className="p-2">
          AP controllers {worker?.ap_poll_limit ?? "unavailable"} · Switches {worker?.switch_poll_limit ?? "unavailable"} · Printers {worker?.printer_poll_limit ?? "unavailable"}
        </td></tr>
      </Table>
    </>}
  </Card>;
}

function CollectionCapacityCard({ isAdmin }: { isAdmin: boolean }) {
  const capacity = useLoad(v1.collectionCapacity, [], 15000, ["system"]);
  const [values, setValues] = useState<Partial<Record<PollFamily, string>>>({});
  const [modes, setModes] = useState<Partial<Record<PollFamily, "auto" | "manual">>>({});
  const [saving, setSaving] = useState<PollFamily | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = capacity.data;

  const save = async (family: PollFamily, concurrency: number, mode: "auto" | "manual") => {
    setSaving(family); setError(null); setMessage(null);
    try {
      await v1.setCollectionCapacity(concurrency, family, mode);
      capacity.reload();
      setMessage("Saved. Applies at the start of the next worker polling cycle.");
    } catch (e) { setError(e instanceof Error ? e.message : "Saving collection capacity failed."); }
    finally { setSaving(null); }
  };

  return <Card title="Collection capacity">
    <ErrorNote error={capacity.error ?? error} />
    {data && <>
      <p className="text-sm">
        AP controllers: {data.controllers} · {data.overdue} overdue · {data.failed} last poll failed · {data.never_polled} never polled
      </p>
      <p className="mt-1 text-sm">Parallel AP polls: {data.effective} · Target interval: {data.interval_seconds} seconds</p>
      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        {(["ap", "switch", "printer"] as const).map(family => {
          const label = { ap: "AP controllers", switch: "Switches", printer: "Printers" }[family];
          const policy = data.policies[family];
          const mode = modes[family] ?? policy.mode;
          const value = values[family] ?? (mode === "auto" ? policy.maximum : data.limits[family]);
          return <form key={family} className="rounded border p-3" onSubmit={e => {
            e.preventDefault();
            void save(family, Number(value), mode);
          }}>
            <div className="mb-2 flex justify-between text-sm"><strong>{label}</strong><span>{policy.effective} active slots</span></div>
            <label className="mb-2 flex flex-col gap-1 text-sm">Control
              <select className="rounded border border-slate-300 px-2 py-1" disabled={!isAdmin} value={mode}
                onChange={e => setModes(current => ({ ...current, [family]: e.target.value as "auto" | "manual" }))}>
                <option value="auto">Automatic</option><option value="manual">Manual</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-sm">{mode === "auto" ? "Maximum parallel polls" : "Parallel polls"}
              {isAdmin ? <input className="rounded border border-slate-300 px-2 py-1" type="number" required
                min={1} max={data.max_concurrency} value={value}
                onChange={e => setValues(current => ({ ...current, [family]: e.target.value }))} />
                : <strong>{value}</strong>}
            </label>
            {isAdmin && <button className={`${btnPrimary} mt-2`} disabled={saving !== null ||
              (mode === policy.mode && Number(value) === (mode === "auto" ? policy.maximum : data.limits[family]))}>
              {saving === family ? "Saving…" : "Save control"}
            </button>}
            <p className="mt-2 text-xs text-slate-500">{policy.mode === "auto" ? policy.reason : "Administrator sets polling capacity."}</p>
          </form>;
        })}
      </div>
      <div className="mt-4">
        <Table head={["Poller", "Fresh coverage", "Latest failures", "Last batch", "Attempts per minute"]}>
          {(["ap", "switch", "printer"] as const).map(family => {
            const p = data.performance[family];
            const label = { ap: "AP controllers", switch: "Switches", printer: "Printers (SNMP)" }[family];
            return <tr key={family}>
              <td className="p-2">{label}</td>
              <td className="p-2">{p.monitored ? `${p.fresh}/${p.monitored} (${Math.round(100 * p.fresh / p.monitored)}%)` : "—"}</td>
              <td className="p-2">{p.failures}</td>
              <td className="p-2">{p.batch ? `${p.batch.count} in ${p.batch.seconds.toFixed(1)} s · ${fmtTime(p.batch.finished_at)}` : "Awaiting a completed batch"}</td>
              <td className="p-2">{p.batch && p.batch.seconds > 0 ? Math.round(60 * p.batch.count / p.batch.seconds) : "—"}</td>
            </tr>;
          })}
        </Table>
        <p className="mt-1 text-xs text-slate-500">Fresh coverage uses two configured intervals for AP controllers and switches, and three for printers.</p>
      </div>
      {message && <p className="mt-2 text-sm" role="status">{message}</p>}
      <p className="mt-3 text-sm">Automatic control reviews demand, completed polling batches, CPU load, available memory and database pressure once per minute. Capacity changes gradually within the configured maximum.</p>
    </>}
    <p className="mt-2 text-sm">Polling capacity applies to concurrent jobs inside the worker. Syslog and database capacity appear in the environment monitoring panels.</p>
  </Card>;
}

/** Transactional event outbox (collector + change engine -> ingest): nothing is acknowledged until stored. */
function OutboxCard() {
  const o = useLoad(() => rem.outbox(), [], 15000, ["system"]);
  if (!o.data) return null;
  const d = o.data;
  return <Card title="Event delivery (transactional outbox)">
    <div className="flex flex-wrap gap-6 text-sm">
      <span>Mode <b>{d.mode === "jetstream" ? "NATS JetStream" : "in-process (standalone)"}</b></span>
      <span>Pending <b className={d.pending ? "text-amber-300" : ""}>{d.pending}</b></span>
      <span>Delivered <b>{d.delivered}</b></span>
      <span>Dead-lettered <b className={d.dlq ? "text-red-300" : ""}>{d.dlq}</b></span>
      {d.oldest_pending_at && <span className="text-amber-300">oldest pending {fmtTime(d.oldest_pending_at)}</span>}
    </div>
    <p className="mt-1 text-xs text-slate-500">Telemetry, poll failures and change executions are written here in the same transaction as their state, then delivered with retries. A collection is SUCCEEDED only after its telemetry was verifiably stored.</p>
  </Card>;
}
