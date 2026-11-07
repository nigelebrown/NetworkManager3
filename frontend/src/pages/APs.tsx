import { useState } from "react";
import SiteFilter from "../components/SiteFilter";
import { Badge, Card, ErrorNote, SimBadge, fmtUptime, val } from "../components/ui";
import { useLoad } from "../components/useLoad";
import ConfirmDialog from "../components/ConfirmDialog";
import PlanDialog from "../components/PlanDialog";
import { api, configApi, getSession, type AP, type Change, type MoveResult } from "../services/api";

export default function APs() {
  const [site, setSite] = useState<number | undefined>();
  const { data, error, reload } = useLoad(() => api.aps(site), [site]);
  const isAdmin = getSession()?.role === "admin";
  const [toRemove, setToRemove] = useState<AP | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const sites = useLoad(() => api.sites(), [], 0);
  const status = useLoad(() => api.status(), [], 0);
  const canConfig = isAdmin && !!status.data?.config_changes_enabled;
  const [renameAp, setRenameAp] = useState<AP | null>(null);
  const [newName, setNewName] = useState("");
  const [plan, setPlan] = useState<Change | null>(null);
  const [moveAp, setMoveAp] = useState<AP | null>(null);
  const [target, setTarget] = useState<number | "">("");
  const [moveResult, setMoveResult] = useState<MoveResult | null>(null);
  const targets = sites.data?.filter((x) => !x.simulated && x.id !== moveAp?.site_id) ?? [];
  const makeRenamePlan = async () => {
    if (!renameAp) return; setErr(null);
    try { setPlan(await configApi.planRename(renameAp.site_id, renameAp.id, newName.trim())); setRenameAp(null); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); setRenameAp(null); }
  };
  const doMove = async () => {
    if (!moveAp || target === "") return; const ap = moveAp; setMoveAp(null); setErr(null);
    try { setMoveResult(await configApi.moveAp(ap.id, Number(target))); reload(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
  };
  const remove = async (id: number) => {
    setToRemove(null); setErr(null);
    try { await api.deleteAp(id); reload(); } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
  };
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h1 className="text-lg font-semibold">Access points</h1><SiteFilter value={site} onChange={setSite} /></div>
      {toRemove && (
        <ConfirmDialog title={`Remove access point "${toRemove.name}"?`} confirmLabel="Remove AP" onCancel={() => setToRemove(null)} onConfirm={() => remove(toRemove.id)}>
          <p>This deletes the AP record, its history and its alerts from SIMS.</p>
          <p className="text-slate-400">Nothing is changed on the device. If it is still part of the Instant cluster, the next poll will discover it again.</p>
        </ConfirmDialog>
      )}
      {renameAp && (
        <ConfirmDialog title={`Rename "${renameAp.name}"`} confirmLabel="Review change…" onCancel={() => setRenameAp(null)} onConfirm={makeRenamePlan}>
          <label className="block">New AP name (letters, digits, “-”, up to 32)
            <input autoFocus value={newName} onChange={(e) => setNewName(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-950 px-2 py-1.5" /></label>
          <p className="text-slate-400">Nothing is changed yet. You will review the exact change, backup and rollback plan first.</p>
        </ConfirmDialog>
      )}
      {moveAp && (
        <ConfirmDialog title={`Move "${moveAp.name}" to another site?`} confirmLabel="Move AP" onCancel={() => setMoveAp(null)} onConfirm={doMove}>
          <label className="block">Destination site
            <select value={target} onChange={(e) => setTarget(e.target.value === "" ? "" : Number(e.target.value))} className="mt-1 w-full rounded border border-slate-600 bg-slate-950 px-2 py-1.5">
              <option value="">Select a site…</option>{targets.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
          <p>SIMS will re-assign the AP to that site, then read the destination’s inventory and configuration.</p>
          <p className="text-amber-300">This changes SIMS only. Which Instant cluster an AP belongs to is decided by the network: the AP must be connected so it can reach and join the destination Virtual Controller, and it then takes that cluster’s configuration.</p>
        </ConfirmDialog>
      )}
      {moveResult && (
        <ConfirmDialog title={`Moved to ${moveResult.to}`} confirmLabel="Close" onCancel={() => setMoveResult(null)} onConfirm={() => setMoveResult(null)}>
          <p>From <b>{moveResult.from}</b> to <b>{moveResult.to}</b>. Destination configuration {moveResult.config_pulled ? "pulled (see the Configuration page)" : "could not be pulled"}. AP {moveResult.seen_in_destination ? "is reporting to the destination controller." : "is not reporting to the destination controller yet."}</p>
          {moveResult.warnings.map((w) => <p key={w} className="text-amber-300">⚠ {w}</p>)}
        </ConfirmDialog>
      )}
      {plan && <PlanDialog change={plan} onClose={() => { setPlan(null); reload(); }} onDone={reload} />}
      <ErrorNote error={error ?? err} />
      <Card>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1000px] text-left text-sm">
            <thead className="text-xs uppercase text-slate-400"><tr>
              {["Name", "Site", "Status", "IP", "MAC", "Model", "Firmware", "Uptime", "Clients", "Radios (channel)", "CPU", "Mem", ""].map((h) => <th key={h} className="px-2 py-2">{h}</th>)}
            </tr></thead>
            <tbody className="divide-y divide-slate-800">
              {data?.map((a) => (
                <tr key={a.id}>
                  <td className="px-2 py-2 font-medium">{a.name} {a.simulated && <SimBadge />}</td>
                  <td className="px-2 py-2">{a.site_name}</td>
                  <td className="px-2 py-2"><Badge value={a.status} /></td>
                  <td className="px-2 py-2">{val(a.ip)}</td>
                  <td className="px-2 py-2 font-mono text-xs">{a.mac.startsWith("na:") ? val(null) : a.mac}</td>
                  <td className="px-2 py-2">{val(a.model)}</td>
                  <td className="px-2 py-2">{val(a.firmware)}</td>
                  <td className="px-2 py-2">{fmtUptime(a.uptime_seconds)}</td>
                  <td className="px-2 py-2">{val(a.client_count)}</td>
                  <td className="px-2 py-2">{a.radios?.length ? a.radios.map((r) => (
                    <div key={r.index} className="whitespace-nowrap">
                      {`${r.band ?? "r" + r.index}: ch ${r.channel}`}
                      <span className="text-slate-400">
                        {r.util_percent != null ? ` · ${r.util_percent}% busy` : ""}
                        {r.noise_floor_dbm != null ? ` · ${r.noise_floor_dbm} dBm noise` : ""}
                      </span>
                    </div>
                  )) : val(null)}</td>
                  <td className="px-2 py-2">{val(a.cpu_percent, "%")}</td>
                  <td className="px-2 py-2">{val(a.memory_percent, "%")}</td>
                  <td className="px-2 py-2">{isAdmin && !a.simulated && (
                    <div className="flex gap-1 whitespace-nowrap">
                      {canConfig && <button className="rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800" onClick={() => { setNewName(""); setRenameAp(a); }}>Rename</button>}
                      <button className="rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800" onClick={() => { setTarget(""); setMoveAp(a); }}>Move</button>
                      <button className="rounded border border-red-800 px-2 py-1 text-xs text-red-300 hover:bg-red-950" onClick={() => setToRemove(a)}>Remove</button>
                    </div>)}</td>
                </tr>
              ))}
              {data?.length === 0 && <tr><td colSpan={13} className="px-2 py-6 text-center text-slate-500">No access points yet. Register a site and wait for the first poll.</td></tr>}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
