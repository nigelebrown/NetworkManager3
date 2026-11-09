import { useState } from "react";
import { Card, ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, ago, btn, btn2, inputCls, statusTone, useAction } from "../components/viz";
import { getSession } from "../services/api";
import type { Device } from "../services/inm";
import { rw } from "../services/ops";

export default function Certification() {
  const certs = useLoad(rw.certs, [], 30000);
  const [device, setDevice] = useState<Device | undefined>();
  const [port, setPort] = useState("");
  const [lag, setLag] = useState("");
  const [ssid, setSsid] = useState("");
  const [vlan, setVlan] = useState(4090);
  const [ack, setAck] = useState(false);
  const [run, setRun] = useState<Awaited<ReturnType<typeof rw.certify>> | null>(null);
  const act = useAction();
  const me = getSession();
  const groups: Record<string, typeof certs.data> = {};
  for (const c of certs.data ?? []) (groups[`${c.model_family} · firmware ${c.firmware_train} · ${c.environment}`] ??= []).push(c);
  return <div className="space-y-4">
    <PageHeader title="Hardware write certification" subtitle="A configuration operation is executable on a model + firmware train only after it passed a certification run on an authorised LAB device (apply → readback → rollback → full-config comparison) AND an independent approver signed off. Emulated results never certify hardware." />
    <ErrorNote error={certs.error} />
    {me?.role === "admin" && <Card title="Run certification on a lab device">
      <div className="grid gap-3 md:grid-cols-3">
        <DevicePicker value={device?.id ?? ""} onChange={(_, d) => { setDevice(d); setRun(null); }} />
        {device?.family === "switch" ? <>
          <label className="text-sm">Unused test port<input className={inputCls} value={port} onChange={e => setPort(e.target.value)} placeholder="e.g. 1/1/24" /></label>
          <label className="text-sm">LAG test ports (optional, 2)<input className={inputCls} value={lag} onChange={e => setLag(e.target.value)} placeholder="e.g. 1/1/23, 1/1/24" /></label>
        </> : <label className="text-sm">Test SSID profile (optional)<input className={inputCls} value={ssid} onChange={e => setSsid(e.target.value)} /></label>}
        <label className="text-sm">Test VLAN<input type="number" className={inputCls} value={vlan} onChange={e => setVlan(Number(e.target.value))} /></label>
      </div>
      <label className="mt-3 flex items-center gap-2 text-sm text-amber-200"><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />
        I confirm this is an authorised LAB device. Certification writes test configuration and rolls it back.</label>
      <button className={`${btn} mt-3`} disabled={!device || !ack || act.busy} onClick={() => act.run(async () => {
        const r = await rw.certify({ device_id: device!.id, lab_device_acknowledged: true, test_port: port || null, test_vlan: vlan,
          lag_ports: lag ? lag.split(/[\s,]+/).filter(Boolean) : null, test_ssid: ssid || null });
        setRun(r); certs.reload();
        const uns = r.results.filter(x => (x as { unsupported?: boolean }).unsupported).length;
        return `Certification run complete: ${r.results.filter(x => x.passed).length}/${r.results.length - uns} operations passed${uns ? ` (${uns} not supported by this adapter)` : ""} on ${r.model_family} firmware ${r.firmware_train} (${r.environment}). An independent approver must now approve each passed operation.`;
      })}>{act.busy ? "Running…" : "Run certification"}</button>
      <Notice error={act.error} message={act.message} />
      {run && <table className="mt-3 w-full text-xs"><thead><tr className="text-left text-slate-500"><th>Operation</th><th>Result</th><th>Readback</th><th>Rollback restored</th></tr></thead>
        <tbody>{run.results.map(r => <tr key={r.operation} className="border-t border-slate-800"><td className="py-1 font-mono">{r.operation}</td>
          <td><Pill value={r.passed ? "PASS" : (r as { unsupported?: boolean }).unsupported ? "UNSUPPORTED" : "FAIL"} tone={r.passed ? "good" : (r as { unsupported?: boolean }).unsupported ? "none" : "poor"} /></td>
          <td className="font-mono">{JSON.stringify((r.evidence as { readback?: unknown }).readback ?? (r.evidence as { error?: string }).error ?? "")}</td>
          <td>{String((r.evidence as { rollback_restored?: boolean }).rollback_restored ?? "")}</td></tr>)}</tbody></table>}
    </Card>}
    {Object.entries(groups).map(([k, rows]) => <Card key={k} title={k} right={me && ["approver", "admin"].includes(me.role) &&
      (rows ?? []).some(c => c.status === "TESTED_PENDING_APPROVAL" && c.tested_by !== me.username) ? <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => {
        const todo = (rows ?? []).filter(c => c.status === "TESTED_PENDING_APPROVAL" && c.tested_by !== me.username);
        for (const c of todo) await rw.decideCert(c.id, "approve");
        certs.reload(); return `${todo.length} operation(s) approved as CERTIFIED_WRITE for ${k}.`; })}>Approve all pending</button> : undefined}>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th className="py-1">Operation</th><th>Status</th><th>Tested by</th><th>Approved by</th><th></th></tr></thead>
        <tbody>{(rows ?? []).map(c => <tr key={c.id} className="border-t border-slate-800">
          <td className="py-1 font-mono text-xs">{c.operation}</td><td><Pill value={c.status} tone={statusTone(c.status === "CERTIFIED_WRITE" ? "PASS" : c.status === "FAILED" ? "FAIL" : "WARN")} /></td>
          <td className="text-xs">{c.tested_by} · {ago(c.tested_at)}</td><td className="text-xs">{c.approved_by ?? "—"}</td>
          <td className="space-x-1 text-right">
            {c.status === "TESTED_PENDING_APPROVAL" && me && ["approver", "admin"].includes(me.role) && me.username !== c.tested_by &&
              <button className={btn2} onClick={() => act.run(async () => { await rw.decideCert(c.id, "approve"); certs.reload(); })}>Approve</button>}
            {c.status === "CERTIFIED_WRITE" && me && ["approver", "admin"].includes(me.role) &&
              <button className={btn2} onClick={() => act.run(async () => { await rw.decideCert(c.id, "revoke"); certs.reload(); })}>Revoke</button>}</td></tr>)}</tbody></table></Card>)}
    {!certs.data?.length && <Card><p className="text-sm text-slate-500">No certification runs yet. Every model/firmware starts UNCERTIFIED, so its change plans are refused at the execution gate.</p></Card>}
  </div>;
}
