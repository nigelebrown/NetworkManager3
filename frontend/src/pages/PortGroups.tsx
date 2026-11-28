import { useState } from "react";
import { Link } from "react-router";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorNote } from "../components/ui";
import { PageHeader, btn, btn2, btnDanger, inputCls } from "../components/viz";
import { useLoad } from "../components/useLoad";
import { request, getSession } from "../services/api";
import { v1 } from "../services/inm";

type Member = { device_id: string; port: string; label: string; minimum_watts: number | null;
  error_threshold: number | null; reset_threshold: number | null; utilization_threshold?: number | null };
type Group = { id: string; name: string; description?: string; members: Member[] };
type Status = "Critical" | "Warning" | "Unsupported" | "Stale" | "No data" | "Healthy";
type Port = Member & { site: string; site_id: string | null; switch: string; source: string | null;
  status: Status; reasons: string[]; unsupported: string[]; observed_at: string | null;
  metrics: Record<string, number>; collection_plan: string };
type Location = { site_id: string | null; site: string; ports: number; status: Status; anomalies: number;
  counts: Record<Status, number> };
type Dashboard = { ports: Port[]; locations: Location[]; summary: Record<Status, number>; generated_at: string };
type Point = { observed_at: string; value: number };
const base = "/v1/port-groups";
const metrics = [
  ["port_link_up", "Link (1 up, 0 down)"], ["port_util_percent", "Utilization (%)"],
  ["port_rx_bps", "Receive (bps)"], ["port_tx_bps", "Transmit (bps)"],
  ["port_poe_w", "PoE (W)"], ["port_errors_delta", "Errors per poll"], ["port_link_resets_delta", "Link resets per poll"],
] as const;
const order: Status[] = ["Critical", "Warning", "Unsupported", "Stale", "No data", "Healthy"];
const tone: Record<Status, string> = {
  Critical: "border-red-400 bg-red-50 text-red-900", Warning: "border-amber-400 bg-amber-50 text-amber-900",
  Unsupported: "border-violet-300 bg-violet-50 text-violet-900", Stale: "border-orange-300 bg-orange-50 text-orange-900",
  "No data": "border-slate-300 bg-slate-100 text-slate-800", Healthy: "border-emerald-400 bg-emerald-50 text-emerald-900",
};
const num = (v: string) => v === "" ? null : Number(v);
const fmtBps = (v: number | undefined) => v == null ? "—" : v >= 1e9 ? `${(v / 1e9).toFixed(2)} Gbps`
  : v >= 1e6 ? `${(v / 1e6).toFixed(1)} Mbps` : `${(v / 1e3).toFixed(0)} kbps`;

export default function PortGroups() {
  const admin = getSession()?.role === "admin";
  const groups = useLoad(() => request<Group[]>(base), [], 30000);
  const switches = useLoad(() => admin ? v1.devices("switch") : Promise.resolve([]), [admin]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [device, setDevice] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [label, setLabel] = useState("");
  const [watts, setWatts] = useState("");
  const [errors, setErrors] = useState("");
  const [resets, setResets] = useState("");
  const [util, setUtil] = useState("");
  const [site, setSite] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<Status | "">("");
  const [history, setHistory] = useState<Port | null>(null);
  const [hours, setHours] = useState(24);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const group = groups.data?.find(g => g.id === selected);
  const interfaces = useLoad(() => device ? request<{ports: {name: string; link?: string; description?: string}[]}>(`/v1/devices/${device}/ports`)
    : Promise.resolve(null), [device]);
  const dashboard = useLoad(() => selected ? request<Dashboard>(`${base}/${selected}/dashboard`) : Promise.resolve(null), [selected], 30000);
  const trends = useLoad(() => history ? request<{series: Record<string, Point[]>}>(
    `${base}/${selected}/history?${new URLSearchParams({device_id: history.device_id, port: history.port, hours: String(hours)})}`)
    : Promise.resolve(null), [selected, history, hours], 30000);
  const ports = dashboard.data?.ports ?? [];
  const locations = dashboard.data?.locations ?? [];
  const visible = ports.filter(row => (!site || site === (row.site_id ?? "unassigned")) && (!statusFilter || row.status === statusFilter))
    .sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status) || a.site.localeCompare(b.site) || a.port.localeCompare(b.port, undefined, {numeric: true}));
  const available = (interfaces.data?.ports ?? []).filter(p => !group?.members.some(m => m.device_id === device && m.port === p.name))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, {numeric: true}));

  async function save(groupName: string, members: Member[], id?: string, description?: string) {
    setError(null);
    setBusy(true);
    try {
      const updated = await request<Group>(id ? `${base}/${id}` : base, {
        method: id ? "PUT" : "POST", body: JSON.stringify({name: groupName, description: description ?? "", members}),
      });
      setSelected(updated.id);
      groups.reload();
      dashboard.reload();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save group");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function remove(target: Group) {
    if (!window.confirm(`Delete monitoring group "${target.name}"? Collected history is kept.`)) return;
    setBusy(true); setError(null);
    try {
      await request(`${base}/${target.id}`, {method: "DELETE"});
      setSelected(""); setHistory(null); groups.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete group");
    } finally { setBusy(false); }
  }

  function exportCsv() {
    const header = ["location", "switch", "port", "label", "status", "reasons", "last_sample", "util_percent", "poe_w", "errors_per_poll"];
    const rows = visible.map(r => [r.site, r.switch, r.port, r.label, r.status, r.reasons.join("; "), r.observed_at ?? "",
      r.metrics.port_util_percent ?? "", r.metrics.port_poe_w ?? "", r.metrics.port_errors_delta ?? ""]);
    const csv = [header, ...rows].map(row => row.map(value => {
      const cell = String(value ?? "");
      const safe = /^[\s]*[=+\-@]/.test(cell) ? "'" + cell : cell;
      return '"' + safe.replace(/"/g, '""') + '"';
    }).join(",")).join("\r\n");
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([csv], {type: "text/csv;charset=utf-8"}));
    link.download = `${group?.name ?? "port-group"}-status.csv`; link.click(); URL.revokeObjectURL(link.href);
  }

  return <div className="space-y-4">
    <PageHeader title="Switch-port monitoring groups"
      subtitle="Track selected switch ports across locations. Measurements come only from scheduled read-only collection; missing or old samples are never shown as healthy." />
    <Link className="text-blue-700 underline" to="/switches">Switch inventory</Link>
    <ErrorNote error={error ?? groups.error ?? switches.error ?? interfaces.error ?? dashboard.error ?? trends.error} />
    <Card title="Monitoring groups">
      <div className="flex flex-wrap items-end gap-3">
        <div><label htmlFor="pg-group">Group</label><select id="pg-group" className={inputCls} value={selected} onChange={e => {
          setSelected(e.target.value); setSite(null); setHistory(null); setStatusFilter("");
        }}><option value="">Choose a group</option>
          {groups.data?.map(g => <option key={g.id} value={g.id}>{g.name} ({g.members.length} ports)</option>)}
        </select></div>
        {admin && <form className="flex flex-wrap items-end gap-2" onSubmit={async e => {
          e.preventDefault();
          if (await save(name.trim(), [])) { setName(""); setSite(null); setHistory(null); }
        }}>
          <label>New group name<input className={inputCls} value={name} maxLength={100} required
            placeholder="AP Uplink, BWC Dock…" onChange={e => setName(e.target.value)} /></label>
          <button className={btn} disabled={busy || !name.trim()}>Create group</button>
        </form>}
        {admin && group && <button className={btnDanger} disabled={busy} onClick={() => remove(group)}>Delete group</button>}
      </div>
      {!admin && <p className="mt-2 text-sm text-slate-500">Read-only view. Administrators create groups and choose ports.</p>}
    </Card>
    {group && <>
      <Card title={group.name + " · Locations"} right={<button className={btn2} disabled={!visible.length} onClick={exportCsv}>Export CSV</button>}>
        {dashboard.data && <p className="mb-2 text-sm" role="status">
          {order.filter(s => dashboard.data!.summary[s]).map(s => `${dashboard.data!.summary[s]} ${s}`).join(" · ") || "No ports"}
          {" · updated "}{new Date(dashboard.data.generated_at).toLocaleTimeString()}
        </p>}
        <div className="mb-3 flex flex-wrap gap-2">
          <button className={btn2} aria-pressed={site === null} onClick={() => setSite(null)}>All locations</button>
          {locations.map(location => <button key={location.site_id ?? "unassigned"}
            className={`rounded border p-3 text-left ${tone[location.status]}`} aria-pressed={site === (location.site_id ?? "unassigned")}
            onClick={() => setSite(location.site_id ?? "unassigned")}>
            <strong className="block">{location.site}</strong>
            {location.status} · {location.anomalies} anomalies / {location.ports} ports
            {location.counts.Stale > 0 && ` · ${location.counts.Stale} stale`}
          </button>)}
        </div>
        <label className="mb-2 inline-block text-sm">Show<select className={inputCls} value={statusFilter}
          onChange={e => setStatusFilter(e.target.value as Status | "")}>
          <option value="">All states</option>{order.map(s => <option key={s}>{s}</option>)}
        </select></label>
        {!ports.length && <p>{admin ? "Add switch ports below to populate this dashboard." : "No ports you can view are in this group."}</p>}
        <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-left text-sm">
          <thead><tr>{["Location", "Switch and port", "State", "Utilization", "PoE", "Errors / poll", "Last sample", "Actions"].map(heading =>
            <th key={heading} scope="col" className="p-2">{heading}</th>)}</tr></thead>
          <tbody>{visible.map(row =>
            <tr key={row.device_id + "/" + row.port} className="border-t">
              <td className="p-2">{row.site}</td>
              <td className="p-2">{row.switch} · {row.port}{row.label && ` · ${row.label}`}{row.source === "DEMO" && " · Demo"}
                {row.collection_plan !== "ACTIVE" && <p className="text-xs text-amber-700">Collection plan {row.collection_plan.toLowerCase()}</p>}</td>
              <td className="p-2"><span className={`rounded border px-2 py-1 ${tone[row.status]}`}>{row.status}</span>
                {row.reasons.length > 0 && <p className="text-xs">{row.reasons.join(" · ")}</p>}</td>
              <td className="p-2">{row.metrics.port_util_percent == null ? "Not measured" : `${row.metrics.port_util_percent}%`}
                <p className="text-xs text-slate-500">↓{fmtBps(row.metrics.port_rx_bps)} ↑{fmtBps(row.metrics.port_tx_bps)}</p></td>
              <td className="p-2">{row.unsupported.some(u => u.startsWith("PoE")) ? "Not supported" :
                row.metrics.port_poe_w == null ? "Not measured" : `${row.metrics.port_poe_w} W`}</td>
              <td className="p-2">{row.metrics.port_errors_delta == null ? "Not measured" : row.metrics.port_errors_delta}</td>
              <td className="p-2">{row.observed_at ? new Date(row.observed_at).toLocaleString() : "None in 24 h"}</td>
              <td className="p-2 space-x-2"><button className={btn2} onClick={() => setHistory(row)}>History</button>
                {admin && <button className={btn2} disabled={busy} onClick={async () => {
                  if (await save(group.name, group.members.filter(m => m.device_id !== row.device_id || m.port !== row.port), group.id, group.description)) {
                    if (history?.device_id === row.device_id && history.port === row.port) setHistory(null);
                  }
                }}>Remove from group</button>}</td>
            </tr>)}</tbody>
        </table></div>
      </Card>
      {history && <Card title={`${history.site} · ${history.switch} · ${history.port}`}>
        <div className="mb-3 flex gap-2"><label>History window<select className={inputCls} value={hours} onChange={e => setHours(Number(e.target.value))}>
          <option value={1}>1 hour</option><option value={24}>24 hours</option><option value={168}>7 days</option><option value={720}>30 days</option>
        </select></label><button className={btn2} onClick={() => setHistory(null)}>Close history</button></div>
        <p className="mb-2 text-xs text-slate-500">Points are measured samples only; gaps in collection are not filled in.</p>
        <div className="grid gap-4 lg:grid-cols-2">{metrics.map(([metric, title]) => {
          const data = (trends.data?.series[metric] ?? []).map(p => ({...p, time: Date.parse(p.observed_at)}))
            .sort((a, b) => a.time - b.time);
          return <section key={metric}><h3 className="font-semibold">{title}</h3>
            {data.length ? <ResponsiveContainer width="100%" height={180}>
              <LineChart data={data}><XAxis dataKey="time" type="number" domain={["dataMin", "dataMax"]}
                tickFormatter={n => new Date(n).toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"})} />
                <YAxis /><Tooltip labelFormatter={n => new Date(n).toLocaleString()} />
                <Line type={metric === "port_link_up" ? "stepAfter" : "linear"} dataKey="value" stroke="#2563eb" dot={data.length < 10} isAnimationActive={false} />
              </LineChart></ResponsiveContainer> : <p>No measurements in this window.</p>}
          </section>;
        })}</div>
      </Card>}
      {admin && <Card title="Add switch ports">
        <form className="grid items-end gap-3 md:grid-cols-3" onSubmit={async e => {
          e.preventDefault();
          const added: Member[] = picked.map(port => ({device_id: device, port, label: picked.length === 1 ? label : (label ? `${label} ${port}` : ""),
            minimum_watts: num(watts), error_threshold: num(errors), reset_threshold: num(resets), utilization_threshold: num(util)}));
          if (await save(group.name, [...group.members, ...added], group.id, group.description)) {
            setLabel(""); setWatts(""); setErrors(""); setResets(""); setUtil(""); setPicked([]);
          }
        }}>
          <div><label htmlFor="pg-switch">Switch</label><select id="pg-switch" className={inputCls} value={device} required onChange={e => {setDevice(e.target.value); setPicked([]);}}>
            <option value="">Choose a switch</option>{switches.data?.map(d => <option key={d.id} value={d.id}>{d.hostname}{d.source_type === "DEMO" ? " (Demo)" : ""}</option>)}
          </select></div>
          <label>Port label<input className={inputCls} value={label} maxLength={90} placeholder="e.g. AP uplink" onChange={e => setLabel(e.target.value)} /></label>
          <label>Minimum PoE (W)<input className={inputCls} type="number" min={0} max={1000} step="any" value={watts} onChange={e => setWatts(e.target.value)} /></label>
          <label>Errors per poll ≥<input className={inputCls} type="number" min={1} step={1} value={errors} onChange={e => setErrors(e.target.value)} /></label>
          <label>Link resets per poll ≥<input className={inputCls} type="number" min={1} step={1} value={resets} onChange={e => setResets(e.target.value)} /></label>
          <label>Utilization % ≥<input className={inputCls} type="number" min={1} max={100} step="any" value={util} onChange={e => setUtil(e.target.value)} /></label>
          <fieldset className="md:col-span-3"><legend className="text-sm font-medium">Ports ({picked.length} selected)</legend>
            {device && interfaces.data && !interfaces.data.ports.length && <p>No port snapshot yet. Enable a collection plan for this switch and wait for one poll.</p>}
            {available.length > 0 && <div className="mb-2 space-x-2">
              <button type="button" className={btn2} onClick={() => setPicked(available.map(p => p.name))}>Select all</button>
              <button type="button" className={btn2} onClick={() => setPicked([])}>Clear</button></div>}
            <div className="grid max-h-56 grid-cols-2 gap-1 overflow-auto sm:grid-cols-4 lg:grid-cols-6">
              {available.map(p => <label key={p.name} className="flex items-center gap-1 text-sm">
                <input type="checkbox" checked={picked.includes(p.name)} onChange={e => setPicked(current =>
                  e.target.checked ? [...current, p.name] : current.filter(x => x !== p.name))} />
                {p.name}{p.link === "up" ? "" : " (down)"}</label>)}
            </div>
          </fieldset>
          <button className={btn} disabled={busy || !device || !picked.length}>Add {picked.length || ""} port{picked.length === 1 ? "" : "s"}</button>
        </form>
      </Card>}
    </>}
  </div>;
}
