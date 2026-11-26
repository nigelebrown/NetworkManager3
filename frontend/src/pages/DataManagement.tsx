import { useState } from "react";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, btn, btnDanger, useAction } from "../components/viz";
import { getSession, request } from "../services/api";
import { ops } from "../services/ops";

export default function DataManagement() {
  const st = useLoad(ops.demo, [], 15000);
  const act = useAction();
  const [confirm, setConfirm] = useState(false);
  const admin = getSession()?.role === "admin";
  const s = st.data;
  return <div className="space-y-4">
    <PageHeader title="Data management" subtitle="Load a realistic demonstration dataset to explore every screen, then remove it with one click. Demo records are always labelled DEMO, are never contacted on the network, and removing them never touches live devices, telemetry, audit history or users." />
    <DeviceLogRetention />
    <Card title="Demo dataset">
      <div className="flex flex-wrap items-center gap-3 text-sm">Status: {s?.loaded ? <Pill value="DEMO DATA LOADED" tone="demo" /> : <Pill value="No demo data" tone="good" />}
        {s?.loaded && <span className="text-slate-400">{s.demo_devices} demo devices · loaded by {s.by} {s.loaded_at && new Date(s.loaded_at).toLocaleString()}</span>}</div>
      <p className="mt-3 text-sm text-slate-400">Includes 3 sites, ~16 APs and switches, 24 hours of telemetry, collection plans, encrypted configurations with good and poor practice, syslog and change attribution examples, change plans, AI recommendations and a shadow enrollment.</p>
      {admin ? <div className="mt-4 flex gap-3">
        <button className={btn} disabled={act.busy} onClick={() => act.run(async () => {
          const r = await ops.demoLoad(); st.reload(); return `Demo data loaded: ${Object.entries(r.created).map(([k, v]) => `${v} ${k.replace(/_/g, " ")}`).join(", ")}.`; })}>
          {act.busy ? "Working…" : s?.loaded ? "Reload demo data" : "Load demo data"}</button>
        {!confirm ? <button className={btnDanger} disabled={act.busy || !s?.loaded} onClick={() => setConfirm(true)}>Remove demo data</button>
          : <span className="flex items-center gap-2"><span className="text-sm text-red-300">Remove all DEMO records?</span>
            <button className={btnDanger} disabled={act.busy} onClick={() => act.run(async () => {
              const r = await ops.demoRemove(); setConfirm(false); st.reload();
              return `Demo data removed (${Object.values(r.removed).reduce((a, b) => a + b, 0)} records). Live data untouched.`; })}>Yes, remove</button>
            <button className="text-sm text-slate-400" onClick={() => setConfirm(false)}>Cancel</button></span>}
      </div> : <p className="mt-3 text-sm text-slate-500">Administrator role required.</p>}
      <div className="mt-3"><Notice error={act.error} message={act.message} /></div>
    </Card>
  </div>;
}


type Retention = { enabled: boolean; days: number | null; approval_reference: string | null; updated_by: string | null;
  last_run: { at: string; deleted: number } | null; preview: { enabled: boolean; would_delete: number; protected: number } };

/** Device-log retention (rc17). Off until an administrator records a period and its approval reference. */
export function DeviceLogRetention() {
  const role = getSession()?.role;
  const r = useLoad(() => role === "admin" || role === "auditor" ? request<Retention>("/v1/device-log-retention") : Promise.resolve(null), [role]);
  const act = useAction();
  const [days, setDays] = useState("365");
  const [ref, setRef] = useState("");
  if (!r.data) return null;
  const save = (enabled: boolean) => act.run(async () => {
    await request("/v1/device-log-retention", { method: "PUT", body: JSON.stringify({ enabled, days: enabled ? Number(days) : null,
      approval_reference: ref }) });
    r.reload(); return enabled ? "Retention policy saved." : "Automatic deletion turned off.";
  });
  return <Card title="Device-log retention">
    <Notice error={act.error || r.error} message={act.message} />
    <p className="text-sm">{r.data.enabled ? `Device logs older than ${r.data.days} days are deleted daily (reference ${r.data.approval_reference}). `
      : "Automatic deletion is off; device logs are kept indefinitely. "}
      Events cited as change-attribution evidence are never deleted.
      {r.data.preview.enabled && ` Next run: ${r.data.preview.would_delete} to delete, ${r.data.preview.protected} kept as evidence.`}
      {r.data.last_run && ` Last run ${new Date(r.data.last_run.at).toLocaleString()}: ${r.data.last_run.deleted} deleted.`}</p>
    {role === "admin" && <div className="mt-2 flex flex-wrap items-end gap-2">
      <label className="text-sm">Keep for (days, 30–3650)<input className="ml-1 w-24 rounded border px-2 py-1" type="number" min={30} max={3650}
        value={days} onChange={e => setDays(e.target.value)} /></label>
      <label className="text-sm">Approval / policy reference<input className="ml-1 rounded border px-2 py-1" maxLength={200}
        value={ref} onChange={e => setRef(e.target.value)} placeholder="e.g. DPO-2026-17" /></label>
      <button className={btn} disabled={act.busy || ref.trim().length < 3} onClick={() => save(true)}>Save retention</button>
      {r.data.enabled && <button className={btnDanger} disabled={act.busy} onClick={() => save(false)}>Turn off deletion</button>}
    </div>}
  </Card>;
}
