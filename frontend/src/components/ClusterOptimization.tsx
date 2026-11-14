import { useState } from "react";
import { Link } from "react-router-dom";
import { getSession } from "../services/api";
import { STATE_TONE, TIER_TONE, pct, wl } from "../services/wireless";
import DiagnosisPanel from "./DiagnosisPanel";
import { useLoad } from "./useLoad";
import { Pill } from "./viz";

/** Pick a cluster → per-AP diagnosis, proposed 20/40 MHz channel changes, enrollment status and blockers, and a
 *  one-click PROPOSAL that creates a separate pending enrollment for each selected AP (never cluster inheritance). */
export default function ClusterOptimization({ onChanged }: { onChanged: () => void }) {
  const fleet = useLoad(wl.fleet, [], 0);
  const [key, setKey] = useState("");
  const view = useLoad(() => key ? wl.clusterOpt(key) : Promise.resolve(null), [key], 30000);
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [mode, setMode] = useState("SHADOW");
  const [msg, setMsg] = useState<string | null>(null);
  const [diag, setDiag] = useState<{ id: string; name: string } | null>(null);
  const admin = getSession()?.role === "admin";
  const v = view.data;
  const eligible = (v?.aps ?? []).filter(a => a.eligible);
  const chosen = eligible.filter(a => sel[a.device_id]);
  return <section className="inm-card"><div className="inm-cardhead"><h2>Optimize by cluster</h2>
    <select aria-label="Cluster" className="inm-form-control" value={key} onChange={e => { setKey(e.target.value); setSel({}); setMsg(null); }}>
      <option value="">Select a cluster…</option>{(fleet.data?.clusters ?? []).filter(c => c.key !== "unlinked").map(c =>
        <option key={c.key} value={c.key}>{c.name} — {c.aps_total} APs, {c.tiers.BRONZE ?? 0} Bronze</option>)}</select></div>
    <div className="inm-cardbody space-y-3 text-sm">
      {!key && <p className="text-slate-600">Choose a cluster to see, for every AP, why it performs poorly, the channel change the analysis proposes, and what is blocking optimization. You can then propose enrollment for all eligible APs in one step — each AP still gets its own approval.</p>}
      {v && <>
        {!!v.platform_blockers.length && <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-amber-900"><b>Why nothing will be changed automatically right now:</b>
          <ul className="list-disc pl-5">{v.platform_blockers.map(b => <li key={b}>{b}</li>)}</ul>
          <p className="mt-1 text-xs">SHADOW enrollments still simulate what the optimizer would do, and every proposal below can be applied manually through a change plan.</p></div>}
        <p>{v.summary.aps} APs · {v.summary.with_proposals} with a proposed channel change · {v.summary.enrolled} enrolled · {v.summary.eligible} eligible to propose</p>
        <div className="overflow-x-auto"><table className="inm-table"><thead><tr><th>{admin && <input type="checkbox" aria-label="Select all eligible" checked={!!eligible.length && eligible.every(a => sel[a.device_id])}
          onChange={e => setSel(Object.fromEntries(eligible.map(a => [a.device_id, e.target.checked])))} />}</th><th>AP</th><th>State / tier</th><th>2.4 / 5 GHz</th><th>Why (top finding)</th><th>Proposed change</th><th>Enrollment / blockers</th><th></th></tr></thead>
          <tbody>{v.aps.map(a => <tr key={a.device_id}>
            <td>{admin && <input type="checkbox" disabled={!a.eligible} checked={!!sel[a.device_id]} onChange={e => setSel({ ...sel, [a.device_id]: e.target.checked })} aria-label={`Select ${a.name}`} />}</td>
            <td><Link className="inm-link" to={`/devices/${a.device_id}/360`}>{a.name}</Link></td>
            <td><Pill value={a.state} tone={STATE_TONE(a.state)} /> <Pill value={a.tier} tone={TIER_TONE[a.tier]} /></td>
            <td className="whitespace-nowrap text-xs">ch {a.radio_2g.channel ?? "?"} {pct(a.radio_2g.util_percent)} · ch {a.radio_5g.channel ?? "?"} {pct(a.radio_5g.util_percent)}</td>
            <td className="max-w-xs text-xs">{a.headline}</td>
            <td className="text-xs">{a.proposals.filter(Boolean).map((p, i) => <div key={i}>{p!.band}: {p!.from ?? "?"} → <b>{p!.to}</b></div>)}{!a.proposals.length && "—"}</td>
            <td className="max-w-xs text-xs">{a.enrollment && <Pill value={`${a.enrollment.mode} ${a.enrollment.status}`} tone="fair" />}
              {a.blockers.map(b => <div key={b} className="text-amber-800">• {b}</div>)}</td>
            <td><button className="inm-btn-secondary" onClick={() => setDiag({ id: a.device_id, name: a.name })}>Diagnose</button></td></tr>)}</tbody></table></div>
        {admin && <div className="flex flex-wrap items-center gap-2">
          <select aria-label="Mode" className="inm-form-control" value={mode} onChange={e => setMode(e.target.value)}>
            <option value="SHADOW">SHADOW — simulate only</option><option value="DEVICE_OPT_IN">DEVICE_OPT_IN — automatic within limits</option></select>
          <button className="inm-btn-primary" disabled={!chosen.length} onClick={async () => {
            if (!window.confirm(`Create ${chosen.length} separate ${mode} enrollment request(s)? Each AP will need its own independent approval.`)) return;
            try { const r = await wl.clusterPropose(key, mode, chosen.map(a => a.device_id)); setMsg(`${r.proposals} enrollment request(s) created. ${r.note}`); setSel({}); view.reload(); onChanged(); }
            catch (e) { setMsg(e instanceof Error ? e.message : "Failed"); } }}>Propose for {chosen.length} selected AP(s)</button>
          <span className="text-xs text-slate-500">Channel/TX-power only, 20/40 MHz, 2 actions/day, 2 h cooldown, 30-day expiry.</span></div>}
        {msg && <p className="rounded bg-slate-50 p-2" role="status">{msg}</p>}
      </>}
    </div>
    {diag && <DiagnosisPanel target={{ kind: "ap", id: diag.id, label: diag.name }} onClose={() => setDiag(null)} />}
  </section>;
}
