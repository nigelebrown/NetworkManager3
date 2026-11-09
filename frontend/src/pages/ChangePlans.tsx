import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, Steps, ago, btn, btn2, btnDanger, inputCls, statusTone, useAction } from "../components/viz";
import { getSession } from "../services/api";
import type { Device } from "../services/inm";
import { ops, rw, type ChangePlan, type ExecResult } from "../services/ops";
import { useSearchParams } from "react-router-dom";

type Op = Record<string, unknown>;
const OPS: Record<string, { op: string; label: string; fields: { key: string; label: string; kind: "int" | "text" | "list" | "select"; options?: string[] }[] }[]> = {
  switch: [
    { op: "switch.lag.members", label: "LACP LAG members", fields: [{ key: "lag", label: "LAG (e.g. lag10)", kind: "text" }, { key: "value", label: "Member ports (comma separated)", kind: "text" }] },
    { op: "switch.lldp", label: "Port LLDP", fields: [{ key: "port", label: "Port", kind: "text" }, { key: "value", label: "LLDP", kind: "select", options: ["enabled", "disabled"] }] },
    { op: "switch.vlan.define", label: "Create VLAN", fields: [{ key: "value", label: "VLAN ID", kind: "int" }] },
    { op: "switch.port.access_vlan", label: "Set port access VLAN", fields: [{ key: "port", label: "Port (e.g. 1/1/4)", kind: "text" }, { key: "value", label: "VLAN ID", kind: "int" }] },
    { op: "switch.port.trunk_vlans", label: "Set port trunk VLANs", fields: [{ key: "port", label: "Port", kind: "text" }, { key: "value", label: "VLANs (comma separated)", kind: "list" }] },
    { op: "switch.port.admin_state", label: "Port admin state", fields: [{ key: "port", label: "Port", kind: "text" }, { key: "value", label: "State", kind: "select", options: ["up", "down"] }] },
    { op: "switch.port.poe", label: "Port PoE", fields: [{ key: "port", label: "Port", kind: "text" }, { key: "value", label: "PoE", kind: "select", options: ["disabled", "enabled"] }] },
  ],
  access_point: [
    { op: "ap.radio.channel", label: "Radio channel", fields: [{ key: "band", label: "Band", kind: "select", options: ["5", "2.4"] }, { key: "value", label: "Channel", kind: "int" }, { key: "power", label: "EIRP dBm", kind: "int" }] },
    { op: "ap.radio.width", label: "Channel width", fields: [{ key: "value", label: "Width (MHz)", kind: "select", options: ["20", "40"] }] },
    { op: "ap.radio.tx_power", label: "Transmit power range", fields: [{ key: "min_dbm", label: "Min dBm", kind: "int" }, { key: "max_dbm", label: "Max dBm", kind: "int" }] },
    { op: "ap.ssid.vlan", label: "SSID VLAN", fields: [{ key: "ssid", label: "SSID", kind: "text" }, { key: "value", label: "VLAN", kind: "int" }] },
    { op: "ap.band_steering", label: "Band steering", fields: [{ key: "value", label: "Mode", kind: "select", options: ["prefer-5ghz", "force-5ghz", "balance-bands", "disabled"] }] },
    { op: "ap.client_match", label: "ClientMatch", fields: [{ key: "value", label: "State", kind: "select", options: ["enabled", "disabled"] }] },
    { op: "ap.airtime_fairness", label: "Airtime fairness", fields: [{ key: "value", label: "Mode", kind: "select", options: ["fair-access", "default-access", "preferred-access"] }] },
  ],
};

function OpBuilder({ family, onAdd }: { family: string; onAdd: (o: Op) => void }) {
  const defs = OPS[family] ?? [];
  const [kind, setKind] = useState(defs[0]?.op ?? "");
  const [vals, setVals] = useState<Record<string, string>>({});
  const def = defs.find(d => d.op === kind) ?? defs[0];
  if (!def) return null;
  const add = () => {
    const o: Op = { op: def.op };
    for (const f of def.fields) {
      const v = vals[f.key] ?? (f.options?.[0] ?? "");
      o[f.key] = def.op === "switch.lag.members" && f.key === "value" ? v.split(/[\s,]+/).filter(Boolean)
        : f.kind === "int" || (f.key === "value" && def.op === "ap.radio.width") ? Number(v)
        : f.kind === "list" ? v.split(/[\s,]+/).filter(Boolean).map(Number) : v;
    }
    onAdd(o); setVals({});
  };
  return <div className="flex flex-wrap items-end gap-2">
    <label className="text-sm">Operation<select className={inputCls} value={def.op} onChange={e => { setKind(e.target.value); setVals({}); }}>
      {defs.map(d => <option key={d.op} value={d.op}>{d.label}</option>)}</select></label>
    {def.fields.map(f => <label key={f.key} className="text-sm">{f.label}{f.kind === "select"
      ? <select className={inputCls} value={vals[f.key] ?? f.options![0]} onChange={e => setVals({ ...vals, [f.key]: e.target.value })}>{f.options!.map(o => <option key={o}>{o}</option>)}</select>
      : <input className={inputCls} value={vals[f.key] ?? ""} onChange={e => setVals({ ...vals, [f.key]: e.target.value })} />}</label>)}
    <button className={btn2} onClick={add}>Add to plan</button>
  </div>;
}

function Timeline({ r }: { r: ExecResult }) {
  return <div className="mt-2 rounded border border-slate-700 p-2 text-xs" aria-label="Execution timeline">
    <div className="mb-1 font-semibold">{r.outcome ?? r.decision} {r.environment && <Pill value={r.environment} tone={r.environment === "EMULATED" ? "demo" : "info"} />} · device writes {r.device_writes}</div>
    {r.denial_reason && <div className="text-red-300">Denied: {r.denial_reason}</div>}
    <ol className="space-y-1">{(r.steps ?? []).map((st, i) => <li key={i} className="flex flex-wrap gap-2">
      <span className={st.ok ? "text-emerald-400" : "text-red-400"}>{st.ok ? "✓" : "✗"}</span><b>{st.step.replace(/_/g, " ")}</b>
      {st.op && <span className="font-mono">{String(st.op.op)}</span>}{st.readback !== undefined && <span className="font-mono text-slate-400">readback {JSON.stringify(st.readback)}</span>}
      {st.authority && <span>{st.authority}</span>}{st.category && <span className="text-red-300">{st.category}: {st.message}</span>}
      {st.matches_pre_change !== undefined && <span>config restored: {String(st.matches_pre_change)}</span>}</li>)}</ol></div>;
}

function PlanRow({ p, onDone }: { p: ChangePlan; onDone: () => void }) {
  const act = useAction();
  const [render, setRender] = useState<Awaited<ReturnType<typeof rw.render>> | null>(null);
  const [exec, setExec] = useState<ExecResult | null>(null);
  const me = getSession();
  const canDecide = me && ["approver", "admin"].includes(me.role) && me.username !== p.requester;
  return <div className="rounded-lg border border-slate-800 p-3">
    <div className="flex flex-wrap items-center gap-2"><b>{p.title}</b><Pill value={p.state} tone={statusTone(p.state === "APPLIED_VERIFIED" ? "PASS" : p.state.includes("FAIL") ? "FAIL" : p.state)} />
      {p.source && p.source !== "OPERATOR" && <Pill value={p.source} tone="info" />}
      <span className="text-xs text-slate-500">by {p.requester} · {ago(p.created_at)}{p.approver ? ` · decided by ${p.approver}` : ""}</span>
      <Link className="ml-auto text-xs text-sky-300" to={`/devices/${p.device_id}/360`}>device 360°</Link></div>
    <ul className="mt-2 grid gap-1 text-xs md:grid-cols-2">{p.operations.map((o, i) => <li key={i} className="flex items-center gap-2 font-mono">
      <span>{JSON.stringify(o)}</span><Pill value={p.capability_report[i]?.capability ?? "?"} tone={p.capability_report[i]?.capability === "CERTIFIED_WRITE" ? "good" : p.capability_report[i]?.capability === "UNCERTIFIED" ? "fair" : "none"} /></li>)}</ul>
    <div className="mt-2 flex flex-wrap gap-2">
      {p.state === "PENDING_APPROVAL" && canDecide && <>
        <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await ops.decideChange(p.id, "approve", p.revision, "Reviewed"); onDone(); return "Approved."; })}>Approve</button>
        <button className={btnDanger} disabled={act.busy} onClick={() => act.run(async () => { await ops.decideChange(p.id, "reject", p.revision, "Rejected in review"); onDone(); })}>Reject</button></>}
      {p.state === "PENDING_APPROVAL" && !canDecide && <span className="text-xs text-slate-500">Awaiting an independent approver (two-person rule).</span>}
      <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => setRender(await rw.render(p.id)))}>Show device requests</button>
      {p.state === "APPROVED" && me?.role === "admin" && <button className={btn} disabled={act.busy} onClick={() => act.run(async () => {
        const r = await rw.execute(p.id); setExec(r); onDone();
        return r.decision !== "ALLOW" ? `Execution gate: DENY. Not applied — ${r.denial_reason}` : `Execution ${r.outcome}.`; })}>Apply to device</button>}
      {["APPLIED_VERIFIED", "FAILED_ROLLED_BACK", "ROLLBACK_FAILED"].includes(p.state) && !exec && <button className={btn2} onClick={() => act.run(async () => {
        const e = (await rw.executions(p.id))[0]; if (e) setExec({ decision: "ALLOW", outcome: e.outcome, steps: e.steps, device_writes: 0, environment: e.environment }); })}>Show execution</button>}
    </div>
    {render && <div className="mt-2 rounded border border-slate-700 p-2 text-xs"><div className="mb-1 font-semibold">Exact device requests ({render.environment})</div>
      {render.operations.map((o, i) => <div key={i} className="mb-1"><span className="font-mono">{String(o.op.op)}</span> <Pill value={render.certification[String(o.op.op)] ?? "UNCERTIFIED"}
        tone={render.certification[String(o.op.op)] === "CERTIFIED_WRITE" ? "good" : "fair"} /><pre className="overflow-x-auto text-[10px] text-slate-400">{JSON.stringify(o.requests ?? o.error, null, 1)}</pre></div>)}</div>}
    {exec && <Timeline r={exec} />}
    <Notice error={act.error} message={act.message} />
  </div>;
}

export default function ChangePlans() {
  const plans = useLoad(ops.changePlans, [], 20000);
  const decisions = useLoad(() => ops.decisions().catch(() => []), [], 30000);
  const [params] = useSearchParams();
  const [device, setDevice] = useState<Device | undefined>();
  const [title, setTitle] = useState(params.get("port") ? `Change port ${params.get("port")}` : "");
  const [items, setItems] = useState<Op[]>([]);
  const [excluded, setExcluded] = useState("");
  const [uplinks, setUplinks] = useState("");
  const act = useAction();
  const step = !device ? 0 : !items.length ? 1 : 2;
  const submit = () => act.run(async () => {
    const p = await ops.createChange({ device_id: device!.id, title, operations: items,
      excluded_channels: excluded.split(/[\s,]+/).filter(Boolean).map(Number), protected_uplinks: uplinks.split(/[\s,]+/).filter(Boolean) });
    setItems([]); setTitle(""); plans.reload();
    return `Change plan submitted for approval (${p.operations.length} operation(s)). Policy validation passed.`;
  });
  return <div className="space-y-4">
    <PageHeader title="Configuration change workflow" subtitle="Engineer-driven: build a typed change, validate against RF/switch policy and device capabilities, get independent approval, then the execution gate checks backup, lock and certification before anything is applied." />
    <ErrorNote error={plans.error} />
    <Card title="New change plan">
      <div className="space-y-3">
        <Steps steps={["Target device", "Build operations", "Validate & submit", "Independent approval", "Execution gate"]} current={step} />
        <div className="grid gap-3 md:grid-cols-3">
          <DevicePicker value={device?.id ?? params.get("device") ?? ""} onChange={(_, d) => { setDevice(d); setItems([]); }} />
          <label className="text-sm">Change title<input className={inputCls} value={title} onChange={e => setTitle(e.target.value)} placeholder="e.g. Move port 1/1/4 to Voice VLAN" /></label>
          {device?.family === "access_point"
            ? <label className="text-sm">Excluded channels (site policy)<input className={inputCls} value={excluded} onChange={e => setExcluded(e.target.value)} placeholder="e.g. 144, 165" /></label>
            : <label className="text-sm">Protected uplink ports<input className={inputCls} value={uplinks} onChange={e => setUplinks(e.target.value)} placeholder="e.g. 1/1/49, 1/1/50" /></label>}
        </div>
        {device && <OpBuilder family={device.family} onAdd={o => setItems([...items, o])} />}
        {items.length > 0 && <ul className="space-y-1 text-sm">{items.map((o, i) => <li key={i} className="flex items-center gap-2 font-mono text-xs">
          <span>{i + 1}. {JSON.stringify(o)}</span><button className="text-red-300" onClick={() => setItems(items.filter((_, j) => j !== i))}>remove</button></li>)}</ul>}
        <div className="flex items-center gap-3"><button className={btn} disabled={!device || !items.length || title.length < 3 || act.busy} onClick={submit}>Validate & submit for approval</button>
          <span className="text-xs text-slate-500">80/160 MHz, invalid/excluded/DFS channels and protected uplinks are refused before any device is contacted.</span></div>
        <Notice error={act.error} message={act.message} />
      </div>
    </Card>
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="space-y-3 lg:col-span-2">{(plans.data ?? []).map(p => <PlanRow key={p.id} p={p} onDone={() => { plans.reload(); decisions.reload(); }} />)}
        {!plans.data?.length && <Card><p className="text-sm text-slate-500">No change plans yet.</p></Card>}</div>
      <Card title="Execution gate decisions"><ul className="space-y-2 text-xs">{(decisions.data ?? []).slice(0, 15).map(d => <li key={d.id}>
        <div className="flex gap-2"><Pill value={d.decision} tone={statusTone(d.decision)} /><span className="text-slate-400">{ago(d.evaluated_at)} by {d.evaluated_by}</span></div>
        <div className="mt-1 flex flex-wrap gap-1">{d.guards.map(g => <span key={g.guard} className={g.pass ? "text-emerald-400" : "text-red-400"}>{g.pass ? "✓" : "✗"} {g.guard}</span>)}</div></li>)}</ul></Card>
    </div>
  </div>;
}
