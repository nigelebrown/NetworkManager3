import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getSession } from "../services/api";
import { Pill } from "./viz";
import { wl, type Diagnosis } from "../services/wireless";

const SEV: Record<string, "poor" | "fair" | "none" | "info"> = { high: "poor", medium: "fair", low: "none", info: "info" };

/** "Why is this performing poorly and what do I change?" — rule-based root causes with evidence, an ordered fix
 *  list and (optionally) an AI-written explanation over the same evidence. Nothing here changes a device. */
export default function DiagnosisPanel({ target, onClose }: {
  target: { kind: "ap"; id: string; label: string } | { kind: "client"; id: number; label: string };
  onClose: () => void;
}) {
  const [d, setD] = useState<Diagnosis | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [collecting,setCollecting] = useState(false);
  const [preparing,setPreparing] = useState(false);
  const [planId,setPlanId] = useState("");
  const canManage = ["operator","admin"].includes(getSession()?.role ?? "");
  const load = (ai: boolean) => (target.kind === "ap" ? wl.diagnoseAp(target.id, ai) : wl.diagnoseClient(target.id, ai));
  useEffect(() => {
    setD(null); setErr(null);
    let active=true;
    load(false).then(x => { if(active) setD(x); }).catch(e => { if(active) setErr(e instanceof Error ? e.message : "Diagnosis failed"); });
    return () => { active=false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.kind, target.id]);
  useEffect(() => {
    if(!collecting) return;
    let cancelled=false;
    const timer=window.setInterval(() => { load(false).then(x => {
      if(cancelled) return;
      setD(x);
      if(x.arm?.collection_state !== "QUEUED") setCollecting(false);
    }).catch(e => { if(!cancelled) { setErr(e instanceof Error ? e.message : "ARM status failed"); setCollecting(false); } }); },3000);
    return () => { cancelled=true; window.clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  },[collecting,target.kind,target.id]);
  return <div className="fixed inset-0 z-50 flex justify-end" style={{ background: "rgba(15,23,42,0.35)" }} role="dialog" aria-modal="true"
    aria-label={`Diagnosis for ${target.label}`} onClick={onClose}>
    <aside className="h-full w-full max-w-xl overflow-y-auto bg-white p-5 shadow-2xl" onClick={e => e.stopPropagation()}>
      <div className="flex items-start justify-between gap-3">
        <div><div className="inm-section-label">{target.kind === "ap" ? "Access point diagnosis" : "Client diagnosis"}</div>
          <h2 className="text-xl font-bold text-slate-800">{target.label}</h2></div>
        <button className="rounded border px-2 py-1 text-sm" onClick={onClose} aria-label="Close">✕</button>
      </div>
      {err && <div className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">{err}</div>}
      {!d && !err && <p className="mt-4 text-sm text-slate-500">Analysing measured data…</p>}
      {target.kind==="ap" && canManage && <button className="inm-btn-secondary mt-3" disabled={collecting} onClick={async () => {
        setErr(null);
        try { await wl.collectArm(target.id); setCollecting(true); } catch(e) { setErr(e instanceof Error ? e.message : "Collection failed"); }
      }}>{collecting ? "Collecting ARM measurements…" : "Refresh native ARM measurements"}</button>}
      {d && <>
        {d.arm && <section className="mt-3 rounded border p-3 text-xs">
          <b>Native Aruba ARM · {d.arm.state} · {d.arm.freshness ?? "MISSING"}</b>
          <p>{d.arm.observed_at ? new Date(d.arm.observed_at).toLocaleString() : "Not collected"} · {d.arm.transport ?? "No transport evidence"}</p>
          {(d.arm.assignments ?? []).map(a => <p key={a.interface}>{a.band} GHz: channel {a.channel}, {a.power_dbm} dBm · {a.interface}</p>)}
          <div className="overflow-x-auto"><table className="inm-table mt-2"><thead><tr><th>GHz/ch</th><th>Busy</th><th>Quality</th><th>Retry</th><th>Noise</th><th>Interference index</th></tr></thead>
            <tbody>{(d.arm.channels ?? []).map(r => <tr key={r.band+":"+r.channel}><td>{r.band}/{r.channel}</td><td>{r.util_percent}%</td><td>{r.quality_percent}%</td><td>{r.retry_percent}%</td><td>{r.noise_floor_dbm} dBm</td><td>{r.interference_index ?? "not measured"}</td></tr>)}</tbody>
          </table></div>
          {!!d.arm.unavailable?.length && <p>Unavailable commands: {d.arm.unavailable.join(", ")}.</p>}
          {!!Object.keys(d.arm.errors ?? {}).length && <p>Collection details: {Object.entries(d.arm.errors ?? {}).map(([k,v]) => k+": "+v).join("; ")}</p>}
          <p className="mt-2">ARM chooses channels and power. Measurements describe RF conditions; they do not establish physical AP distance.</p>
        </section>}
        {d.baseline && <section className="mt-3 rounded border p-3 text-xs"><b>5 GHz reference baseline</b>
          <p>{d.baseline.available ? d.baseline.source_name : d.baseline.reason}</p>
          {d.baseline.available && <><p>{d.baseline.scope}</p><p>Reference channels: {d.baseline.reference_channels_5g?.join(", ") ?? "not measured"} · Observed: {d.baseline.observed_channels_5g?.join(", ") ?? "not measured"}</p>
            {(d.baseline.differences ?? []).map(x => <p key={x.setting}>{x.setting}: {String(x.observed ?? "not measured")} · reference {String(x.reference)} · {x.status}</p>)}</>}
        </section>}
        {planId && <p role="status" className="mt-2 text-sm">Reviewed change plan created. <Link className="inm-link" to={`/changes?plan=${planId}`}>Open Change Management</Link></p>}
        <div className="mt-4 rounded-lg border border-blue-100 bg-blue-50 p-3 text-sm text-slate-800"><b>Most likely cause: </b>{d.headline}</div>
        <ol className="mt-4 space-y-3">{d.findings.map((f, i) => <li key={f.id} className="rounded-lg border border-slate-200 p-3">
          <div className="flex flex-wrap items-center gap-2"><span className="text-xs font-bold text-slate-400">#{i + 1}</span>
            <b className="text-slate-800">{f.title}</b><Pill value={f.severity} tone={SEV[f.severity]} />
            <span className="text-[11px] text-slate-500">owner: {f.owner} · confidence {f.confidence}</span></div>
          <p className="mt-1 text-sm text-slate-600">{f.why}</p>
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-sm text-slate-700">{f.actions.map(a => <li key={a}>{a}</li>)}</ul>
          {f.proposal && f.proposal.to != null && <div className="mt-2 flex flex-wrap items-center gap-2 rounded bg-slate-50 p-2 text-xs">
            <span>Proposed change: <b>{f.proposal.arm_managed ? `${f.proposal.band}: return channel and power to native ARM` : `${f.proposal.band} channel ${f.proposal.from ?? "?"} → ${f.proposal.to}`}</b> (20/40 MHz only)</span>
            {target.kind === "ap" && canManage && <button className="inm-btn-secondary" disabled={preparing || !d.arm?.observed_at} onClick={async () => {
              setPreparing(true); setErr(null);
              try { const p=await wl.diagnosisPlan(target.id,f.id,d.arm!.observed_at!); setPlanId(p.id); }
              catch(e) { setErr(e instanceof Error ? e.message : "Could not prepare change plan"); }
              finally { setPreparing(false); }
            }}>{preparing ? "Preparing…" : "Prepare reviewed change plan"}</button>}
            <Link className="text-blue-700 underline" to="/optimization">or enroll for optimization</Link></div>}
        </li>)}</ol>
        {!d.findings.length && <p className="mt-3 text-sm text-slate-500">No issue found in the measured inputs.</p>}
        {d.checked && <div className="mt-4 rounded-lg border border-slate-200 p-3 text-xs"><b className="text-sm">Evidence checked</b>
          <dl className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1">{Object.entries(d.checked).map(([k, v]) => <div key={k} className="flex justify-between gap-2 border-b border-slate-100">
            <dt className="text-slate-500">{k}</dt><dd className="text-right font-medium">{v == null || v === "" ? "not measured" : String(v)}</dd></div>)}</dl></div>}
        <div className="mt-5 rounded-lg border border-violet-200 bg-violet-50 p-3">
          <div className="flex items-center justify-between gap-2"><b className="text-sm text-violet-900">AI engineer's explanation</b>
            <button className="rounded border border-violet-300 bg-white px-3 py-1 text-sm disabled:opacity-50" disabled={aiBusy}
              onClick={async () => { setAiBusy(true); try { setD(await load(true)); } catch (e) { setErr(e instanceof Error ? e.message : "AI failed"); } finally { setAiBusy(false); } }}>
              {aiBusy ? "Thinking…" : d.ai ? "Ask again" : "Ask the AI"}</button></div>
          {d.ai?.available && <><div className="mt-2 whitespace-pre-wrap text-sm text-slate-800">{d.ai.text}</div>
            <p className="mt-2 text-[11px] text-slate-500">AI-generated by {d.ai.model} from the evidence above only. Advisory — verify before changing anything.</p></>}
          {d.ai && !d.ai.available && <p className="mt-2 text-sm text-slate-600">{d.ai.reason}</p>}
          {!d.ai && <p className="mt-1 text-xs text-slate-600">Sends the findings and measured values (client identities are removed) to the on-premises AI model for a plain-language fix plan.</p>}
        </div>
        <p className="mt-3 text-[11px] text-slate-500">{d.method}</p>
      </>}
    </aside>
  </div>;
}
