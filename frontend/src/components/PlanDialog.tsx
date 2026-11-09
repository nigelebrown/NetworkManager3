import { useState } from "react";
import { configApi, getSession, type ApplyResult, type Change } from "../services/api";

/** Shows a change plan (what, before/after, exact request, backup, rollback) and applies it only after the typed phrase. */
export default function PlanDialog({ change, onClose, onDone }: { change: Change; onClose: () => void; onDone: () => void }) {
  const [current, setCurrent] = useState<Change>(change);
  const p = current.plan;
  const approval = (p as unknown as { approval?: { approved_by: string; approved_at: string } }).approval;
  const me = getSession();
  const requester = (current as unknown as { created_by?: string }).created_by;
  const canApprove = !approval && me && ["approver", "admin"].includes(me.role) && me.username !== requester;
  const approve = async () => {
    setBusy(true); setErr(null);
    try { setCurrent(await configApi.approve(change.id)); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  };
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [rbMsg, setRbMsg] = useState<string | null>(null);
  const [rbChange, setRbChange] = useState<Change | null>(null);

  const apply = async () => {
    setBusy(true); setErr(null);
    try { setResult(await configApi.apply(change.id, typed)); onDone(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  };
  const cancel = async () => { try { await configApi.cancel(change.id); } catch { /* already gone */ } onClose(); };
  const makeRollback = async () => {
    try { setRbChange(await configApi.rollbackPlan(change.id)); } catch (e) { setRbMsg(e instanceof Error ? e.message : "Failed"); }
  };
  if (rbChange) return <PlanDialog change={rbChange} onClose={onClose} onDone={onDone} />;

  const tone = !result ? "" : result.status === "applied" ? "border-emerald-700 bg-emerald-950/40" : result.status === "failed" ? "border-red-700 bg-red-950/40" : "border-amber-700 bg-amber-950/40";
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4" role="presentation">
      <div role="dialog" aria-modal="true" className="my-6 w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-xl">
        <h2 className="text-base font-semibold">{result ? "Result" : "Review change before applying"}</h2>
        <p className="mt-1 text-sm text-slate-300">{change.summary}</p>
        <p className="text-xs text-slate-500">Controller {p.target.controller} · safety backup #{p.backup_id} taken · plan expires {new Date(change.expires_at).toLocaleTimeString()}</p>

        {result ? (
          <div className={`mt-4 rounded-lg border p-3 text-sm ${tone}`}>
            <div className="font-semibold uppercase">{result.status.replace("_", " ")}</div>
            <p className="mt-1">{result.message}</p>
            {result.status === "applied_unverified" && <p className="mt-1 text-amber-300">The device accepted the change but the read-back did not match. Check the AP, then use rollback if needed.</p>}
          </div>
        ) : (
          <>
            <div className="mt-4 grid gap-3 text-sm md:grid-cols-2">
              <div><div className="text-xs uppercase text-slate-400">Before</div><pre className="mt-1 overflow-x-auto rounded bg-slate-950 p-2 text-xs">{p.before ? JSON.stringify(p.before, null, 1) : "(does not exist yet)"}</pre></div>
              <div><div className="text-xs uppercase text-slate-400">After (requested)</div><pre className="mt-1 overflow-x-auto rounded bg-slate-950 p-2 text-xs">{JSON.stringify(p.after, null, 1)}</pre></div>
            </div>
            {p.warnings.length > 0 && <ul className="mt-3 space-y-1 text-sm text-amber-300">{p.warnings.map((w) => <li key={w}>⚠ {w}</li>)}</ul>}
            <details className="mt-3 text-sm"><summary className="cursor-pointer text-slate-300">Exact request that will be sent (passphrase masked)</summary>
              <pre className="mt-1 overflow-x-auto rounded bg-slate-950 p-2 text-xs">{p.request.method} {p.request.path}{"\n"}{JSON.stringify(p.request.body, null, 1)}</pre></details>
            <div className="mt-3 rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm"><div className="font-semibold">Rollback plan</div><p className="mt-1 text-slate-300">{p.rollback}</p><p className="mt-1 text-xs text-slate-500">{p.verification}</p></div>
            <div className={`mt-3 rounded-lg border p-3 text-sm ${approval ? "border-emerald-800 text-emerald-200" : "border-amber-800 text-amber-200"}`}>
              {approval ? <>Independently approved by <b>{approval.approved_by}</b> at {new Date(approval.approved_at).toLocaleString()}.</>
                : <>Awaiting independent approval (two-person rule): requested by <b>{requester ?? "unknown"}</b>; a different approver or administrator must approve before it can be applied.</>}
              {canApprove && <button className="ml-3 rounded bg-emerald-700 px-3 py-1 text-xs font-semibold" disabled={busy} onClick={approve}>Approve</button>}
            </div>
            <label className="mt-4 block text-sm">To confirm, type <code className="rounded bg-slate-800 px-1">{p.confirm_phrase}</code>
              <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-950 px-2 py-1.5" /></label>
          </>
        )}
        {err && <div className="mt-3 rounded border border-red-800 bg-red-950/50 p-2 text-sm text-red-300">{err}</div>}
        {rbMsg && <div className="mt-3 rounded border border-red-800 bg-red-950/50 p-2 text-sm text-red-300">{rbMsg}</div>}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          {result ? (
            <>
              {result.status !== "failed" && <button className="rounded border border-slate-600 px-3 py-1.5 text-sm hover:bg-slate-800" onClick={makeRollback}>Create rollback plan</button>}
              <button className="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500" onClick={onClose}>Close</button>
            </>
          ) : (
            <>
              <button className="rounded border border-slate-600 px-3 py-1.5 text-sm hover:bg-slate-800" onClick={cancel}>Cancel</button>
              <button disabled={busy || typed.trim() !== p.confirm_phrase || !approval} onClick={apply}
                className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-40">{busy ? "Applying…" : "Apply to device"}</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
