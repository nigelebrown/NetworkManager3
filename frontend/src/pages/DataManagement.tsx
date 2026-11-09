import { useState } from "react";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, btn, btnDanger, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { ops } from "../services/ops";

export default function DataManagement() {
  const st = useLoad(ops.demo, [], 15000);
  const act = useAction();
  const [confirm, setConfirm] = useState(false);
  const admin = getSession()?.role === "admin";
  const s = st.data;
  return <div className="space-y-4">
    <PageHeader title="Data management" subtitle="Load a realistic demonstration dataset to explore every screen, then remove it with one click. Demo records are always labelled DEMO, are never contacted on the network, and removing them never touches live devices, telemetry, audit history or users." />
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
