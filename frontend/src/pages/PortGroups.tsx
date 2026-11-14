import { useState } from "react";
import { Link } from "react-router-dom";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, ErrorNote } from "../components/ui";
import { PageHeader, btn, btn2, inputCls } from "../components/viz";
import { useLoad } from "../components/useLoad";
import { request, getSession } from "../services/api";
import { v1 } from "../services/inm";

type Member = { device_id: string; port: string; label: string; minimum_watts: number | null;
  error_threshold: number | null; reset_threshold: number | null };
type Group = { id: string; name: string; members: Member[] };
type Status = "Critical" | "Warning" | "No data" | "Healthy";
type Port = Member & { site: string; site_id: string | null; switch: string; source: string | null;
  status: Status; reasons: string[]; observed_at: string | null; metrics: Record<string, number> };
type Point = { observed_at: string; value: number };
const base = "/v1/port-groups";
const metrics = [
  ["port_link_up", "Link (1 up, 0 down)"], ["port_poe_w", "PoE (W)"],
  ["port_errors_delta", "Errors per poll"], ["port_link_resets_delta", "Link resets per poll"],
] as const;
const priority: Status[] = ["Critical", "Warning", "No data", "Healthy"];
const tone: Record<Status, string> = {
  Critical: "border-red-400 bg-red-50 text-red-900", Warning: "border-amber-400 bg-amber-50 text-amber-900",
  "No data": "border-slate-300 bg-slate-100 text-slate-800", Healthy: "border-emerald-400 bg-emerald-50 text-emerald-900",
};

export default function PortGroups() {
  const admin = getSession()?.role === "admin";
  const groups = useLoad(() => admin ? request<Group[]>(base) : Promise.resolve([]), [admin], 30000);
  const switches = useLoad(() => admin ? v1.devices("switch") : Promise.resolve([]), [admin]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [device, setDevice] = useState("");
  const [port, setPort] = useState("");
  const [label, setLabel] = useState("");
  const [watts, setWatts] = useState("");
  const [errors, setErrors] = useState("");
  const [resets, setResets] = useState("");
  const [site, setSite] = useState<string | null>(null);
  const [history, setHistory] = useState<Port | null>(null);
  const [hours, setHours] = useState(24);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const group = groups.data?.find(g => g.id === selected);
  const interfaces = useLoad(() => device ? request<{ports: {name: string}[]}>(`/v1/devices/${device}/ports`) : Promise.resolve(null), [device]);
  const dashboard = useLoad(() => selected ? request<{ports: Port[]}>(`${base}/${selected}/dashboard`) : Promise.resolve(null), [selected], 30000);
  const trends = useLoad(() => history ? request<{series: Record<string, Point[]>}>(
    `${base}/${selected}/history?${new URLSearchParams({device_id: history.device_id, port: history.port, hours: String(hours)})}`)
    : Promise.resolve(null), [selected, history, hours], 30000);
  const ports = dashboard.data?.ports ?? [];
  const locations = Object.values(ports.reduce<Record<string, Port[]>>((bySite, row) => {
    (bySite[row.site_id ?? "unassigned"] ??= []).push(row);
    return bySite;
  }, {})).map(rows => ({
    id: rows[0].site_id ?? "unassigned", name: rows[0].site, rows,
    status: priority.find(status => rows.some(row => row.status === status)) ?? "No data",
  })).sort((a, b) => priority.indexOf(a.status) - priority.indexOf(b.status) || a.name.localeCompare(b.name));

  async function save(groupName: string, members: Member[], id?: string) {
    setError(null);
    setBusy(true);
    try {
      const updated = await request<Group>(id ? `${base}/${id}` : base, {
        method: id ? "PUT" : "POST", body: JSON.stringify({name: groupName, members}),
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

  return <div className="space-y-4">
    <PageHeader title="Switch-port monitoring groups" subtitle="Track selected switch ports across locations." />
    <Link className="text-blue-700 underline" to="/switches">Switch inventory</Link>
    {!admin ? <p>Sign in as an administrator to manage and view monitoring groups.</p> : <>
      <ErrorNote error={error ?? groups.error ?? switches.error ?? interfaces.error ?? dashboard.error ?? trends.error} />
      <Card title="Monitoring groups">
        <div className="flex flex-wrap items-end gap-3">
          <label>Group<select className={inputCls} value={selected} onChange={e => {
            setSelected(e.target.value); setSite(null); setHistory(null);
          }}><option value="">Choose a group</option>
            {groups.data?.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select></label>
          <form className="flex flex-wrap items-end gap-2" onSubmit={async e => {
            e.preventDefault();
            if (await save(name.trim(), [])) { setName(""); setSite(null); setHistory(null); }
          }}>
            <label>New group name<input className={inputCls} value={name} maxLength={100} required
              placeholder="AP Uplinks" onChange={e => setName(e.target.value)} /></label>
            <button className={btn} disabled={busy || !name.trim()}>Create group</button>
          </form>
        </div>
      </Card>
      {group && <>
        <Card title={group.name + " · Locations"}>
          <div className="mb-3 flex flex-wrap gap-2">
            <button className={btn2} onClick={() => setSite(null)}>All locations</button>
            {locations.map(location => <button key={location.id}
              className={`rounded border p-3 text-left ${tone[location.status]}`} aria-pressed={site === location.id}
              onClick={() => setSite(location.id)}>
              <strong className="block">{location.name}</strong>
              {location.status} · {location.rows.filter(row => row.status === "Critical" || row.status === "Warning").length} flagged / {location.rows.length} ports
            </button>)}
          </div>
          {!ports.length && <p>Add a switch port to populate this dashboard.</p>}
          <div className="overflow-x-auto"><table className="w-full text-left text-sm">
            <thead><tr>{["Location", "Switch and port", "State", "PoE", "Last sample", "Actions"].map(heading => <th key={heading} className="p-2">{heading}</th>)}</tr></thead>
            <tbody>{ports.filter(row => !site || site === (row.site_id ?? "unassigned")).map(row =>
              <tr key={row.device_id + "/" + row.port} className="border-t">
                <td className="p-2">{row.site}</td><td className="p-2">{row.switch} · {row.port}{row.label && ` · ${row.label}`}{row.source === "DEMO" && " · Demo"}</td>
                <td className="p-2"><span className={`rounded border px-2 py-1 ${tone[row.status]}`}>{row.status}</span>
                  {row.reasons.length > 0 && <p>{row.reasons.join(" · ")}</p>}</td>
                <td className="p-2">{row.metrics.port_poe_w == null ? "Unavailable" : `${row.metrics.port_poe_w} W`}</td>
                <td className="p-2">{row.observed_at ? new Date(row.observed_at).toLocaleString() : "Unavailable"}</td>
                <td className="p-2 space-x-2"><button className={btn2} onClick={() => setHistory(row)}>History</button>
                  <button className={btn2} disabled={busy} onClick={async () => {
                    if (await save(group.name, group.members.filter(m => m.device_id !== row.device_id || m.port !== row.port), group.id)) {
                      if (history?.device_id === row.device_id && history.port === row.port) setHistory(null);
                    }
                  }}>Remove from group</button></td>
              </tr>)}</tbody>
          </table></div>
        </Card>
        {history && <Card title={`${history.site} · ${history.switch} · ${history.port}`}>
          <div className="flex gap-2 mb-3"><label>History window<select className={inputCls} value={hours} onChange={e => setHours(Number(e.target.value))}>
            <option value={1}>1 hour</option><option value={24}>24 hours</option><option value={168}>7 days</option>
          </select></label><button className={btn2} onClick={() => setHistory(null)}>Close history</button></div>
          <div className="grid gap-4 lg:grid-cols-2">{metrics.map(([metric, title]) => {
            const data = (trends.data?.series[metric] ?? []).map(p => ({...p, time: Date.parse(p.observed_at)})).reverse();
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
        <Card title="Add a switch port">
          <form className="grid items-end gap-3 md:grid-cols-3" onSubmit={async e => {
            e.preventDefault();
            if (await save(group.name, [...group.members, {device_id: device, port, label,
              minimum_watts: watts === "" ? null : Number(watts), error_threshold: errors === "" ? null : Number(errors),
              reset_threshold: resets === "" ? null : Number(resets)}], group.id)) {
              setLabel(""); setWatts(""); setErrors(""); setResets(""); setPort("");
            }
          }}>
            <label>Switch<select className={inputCls} value={device} required onChange={e => {setDevice(e.target.value); setPort("");}}>
              <option value="">Choose a switch</option>{switches.data?.map(d => <option key={d.id} value={d.id}>{d.hostname}{d.source_type === "DEMO" ? " (Demo)" : ""}</option>)}
            </select></label>
            <label>Port<select className={inputCls} value={port} required onChange={e => setPort(e.target.value)}>
              <option value="">{device && interfaces.data && !interfaces.data.ports.length ? "No port snapshot available" : "Choose a port"}</option>
              {interfaces.data?.ports.filter(p => !group.members.some(m => m.device_id === device && m.port === p.name)).map(p => <option key={p.name}>{p.name}</option>)}
            </select></label>
            <label>Port label<input className={inputCls} value={label} maxLength={100} onChange={e => setLabel(e.target.value)} /></label>
            <label>Minimum PoE (W)<input className={inputCls} type="number" min={0} max={1000} step="any" value={watts} onChange={e => setWatts(e.target.value)} /></label>
            <label>Errors per poll<input className={inputCls} type="number" min={1} step={1} value={errors} onChange={e => setErrors(e.target.value)} /></label>
            <label>Link resets per poll<input className={inputCls} type="number" min={1} step={1} value={resets} onChange={e => setResets(e.target.value)} /></label>
            <button className={btn} disabled={busy || !device || !port}>Add port</button>
          </form>
        </Card>
      </>}
    </>}
  </div>;
}
