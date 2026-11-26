import { useEffect, useState } from "react";
import { Link } from "react-router";
import DiagnosisPanel from "../components/DiagnosisPanel";
import { ErrorNote } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { Pill } from "../components/viz";
import { getSession } from "../services/api";
import { TIER_TONE, wl, type HealthReport, type HealthRow, type Tier } from "../services/wireless";

const TIER_CARD: Record<string, { cls: string; sub: string }> = {
  GOLD: { cls: "border-amber-300 bg-amber-50 text-amber-800", sub: "Stable — no threshold breach" },
  SILVER: { cls: "border-slate-300 bg-slate-50 text-slate-700", sub: "Monitor / tune" },
  BRONZE: { cls: "border-orange-300 bg-orange-50 text-orange-800", sub: "Corrective action required" },
  UNRATED: { cls: "border-slate-200 bg-white text-slate-500", sub: "No measured evidence" },
};
const n = (v: number | null | undefined) => v == null ? "—" : v.toLocaleString();

function csv(rep: HealthReport) {
  const head = ["Tier", "AP", "Cluster", "Site", "State", "Total", "Clients", "Negative", "Rejects", "DHCP TO", "Auth/Sec", "Deauth/Disassoc", "ARM", "Trigger", "Interpretation", "Action"];
  const rows = rep.rows.map(r => [r.tier, r.ap, r.cluster ?? "", r.site ?? "", r.state ?? "", r.total_events ?? "", r.unique_clients ?? r.clients_now ?? "",
    r.negative ?? "", r.events?.reject ?? "", r.events?.dhcp_timeout ?? "", r.events?.auth ?? "", r.events?.deauth ?? "", r.events?.arm ?? "", r.trigger, r.interpretation, r.action]);
  const body = [head, ...rows].map(r => r.map(x => `"${String(x).replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([body], { type: "text/csv" }));
  a.download = `AP_Health_${rep.generated_at.slice(0, 10)}.csv`; a.click();
}

export default function ApHealthReport() {
  const [hours, setHours] = useState(24);
  const [cluster, setCluster] = useState("");
  const [snapId, setSnapId] = useState("");
  const [compare, setCompare] = useState("");
  const [imported, setImported] = useState<HealthReport | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [diag, setDiag] = useState<HealthRow | null>(null);
  const fleet = useLoad(wl.fleet, [], 0);
  const snaps = useLoad(wl.snapshots, [], 0);
  const live = useLoad(() => snapId ? wl.snapshot(snapId, compare || undefined) : wl.report(hours, cluster || undefined),
    [hours, cluster, snapId, compare], snapId ? 0 : 120000);
  useEffect(() => { setImported(null); }, [hours, cluster, snapId, compare]);
  const rep = imported ?? live.data;
  const op = ["operator", "admin"].includes(getSession()?.role ?? "");
  const tiers = (t: Tier) => rep?.rows.filter(r => r.tier === t) ?? [];
  return <div className="space-y-4 ap-report">
    <div className="flex flex-wrap items-end justify-between gap-3 print:hidden"><div>
      <div className="inm-section-label">Monitoring & diagnostics</div><h1 className="inm-pagetitle">AP health — Gold / Silver / Bronze</h1>
      <p className="inm-subtitle">Point-in-time rating of every AP from event evidence (syslog / imported export) and its measured radio and system condition. The worse of the two decides the tier.</p></div>
      <div className="flex flex-wrap gap-2">
        <select aria-label="Window" className="inm-form-control" value={hours} disabled={!!snapId} onChange={e => setHours(Number(e.target.value))}>
          {[6, 24, 72, 168].map(h => <option key={h} value={h}>Last {h < 48 ? `${h} h` : `${h / 24} days`}</option>)}</select>
        <select aria-label="Cluster" className="inm-form-control" value={cluster} disabled={!!snapId} onChange={e => setCluster(e.target.value)}>
          <option value="">All clusters</option>{(fleet.data?.clusters ?? []).map(c => <option key={c.key} value={c.key}>{c.name}</option>)}</select>
        <select aria-label="Saved snapshot" className="inm-form-control" value={snapId} onChange={e => { setSnapId(e.target.value); setCompare(""); }}>
          <option value="">Live (as at now)</option>{(snaps.data ?? []).map(s => <option key={s.id} value={s.id}>{s.label} · {s.generated_at.slice(0, 16).replace("T", " ")}</option>)}</select>
        {snapId && <select aria-label="Compare with" className="inm-form-control" value={compare} onChange={e => setCompare(e.target.value)}>
          <option value="">Compare with…</option>{(snaps.data ?? []).filter(s => s.id !== snapId).map(s => <option key={s.id} value={s.id}>{s.label}</option>)}</select>}
      </div></div>
    <div className="flex flex-wrap gap-2 print:hidden">
      {op && !snapId && <button className="inm-btn-secondary" onClick={async () => { const label = window.prompt("Snapshot label", `AP health ${new Date().toLocaleString()}`);
        if (label == null) return; const r = await wl.saveSnapshot(hours, label, cluster || undefined); snaps.reload(); setMsg(`Saved snapshot “${r.label}”. Re-run after changes and compare to see tier movement.`); }}>Save snapshot</button>}
      {op && <label className="inm-btn-secondary cursor-pointer">Import event export (CSV)
        <input type="file" accept=".csv,text/csv" className="hidden" onChange={async e => { const f = e.target.files?.[0]; if (!f) return;
          setMsg("Importing…"); try { const r = await wl.importReport(f, f.name, cluster || undefined); setImported(r); snaps.reload(); setMsg(`Imported ${r.source}. Saved as a snapshot.`); }
          catch (err) { setMsg(err instanceof Error ? err.message : "Import failed"); } e.target.value = ""; }} /></label>}
      {rep && <button className="inm-btn-secondary" onClick={() => csv(rep)}>Export CSV</button>}
      {rep && <button className="inm-btn-secondary" onClick={() => window.print()}>Print / save as PDF</button>}
      {snapId && op && <button className="inm-link" onClick={async () => { if (!window.confirm("Delete this snapshot?")) return; await wl.deleteSnapshot(snapId); setSnapId(""); snaps.reload(); }}>Delete snapshot</button>}
    </div>
    {msg && <p className="rounded bg-slate-50 p-2 text-sm print:hidden" role="status">{msg}</p>}
    <ErrorNote error={live.error} />
    {!rep && !live.error && <p className="text-sm text-slate-500">Building the report…</p>}
    {rep && <>
      <section className="inm-card"><div className="inm-cardbody">
        <h2 className="text-lg font-bold text-slate-800">Wireless AP Health — Action Report{rep.label ? ` · ${rep.label}` : ""}</h2>
        <p className="text-sm text-slate-600">{rep.scope} · {rep.window_hours ? `window ${new Date(rep.window_start).toLocaleString()} → ${new Date(rep.window_end).toLocaleString()}` : "imported export"} · generated {new Date(rep.generated_at).toLocaleString()} · source: {rep.source}</p>
        {rep.notes.map(x => <p key={x} className="mt-1 text-xs text-amber-700">{x}</p>)}
        <h3 className="mt-3 font-semibold">Executive summary</h3>
        <ul className="list-disc pl-5 text-sm">{rep.executive_summary.map(x => <li key={x}>{x}</li>)}</ul>
        <div className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1 text-sm md:grid-cols-4">{[["Total events", rep.metrics.total_events], ["APs observed (events)", rep.metrics.aps_observed],
          ["Unique client MACs", rep.metrics.unique_clients], ["DHCP timeouts", rep.metrics.dhcp_timeouts], ["DHCP acknowledgements", rep.metrics.dhcp_acks],
          ["Assoc/reassoc rejects", rep.metrics.rejects], ["Deauth/disassoc", rep.metrics.deauth_disassoc], ["Auth/security", rep.metrics.auth_security],
          ["Radio ARM events", rep.metrics.radio_arm]].map(([k, v]) => <div key={String(k)} className="flex justify-between border-b border-slate-100 py-1"><span className="text-slate-500">{k}</span><b>{n(v as number)}</b></div>)}</div>
      </div></section>
      <div className="grid gap-3 md:grid-cols-4">{(["GOLD", "SILVER", "BRONZE", "UNRATED"] as Tier[]).map(t =>
        <div key={t} className={`rounded-xl border p-4 ${TIER_CARD[t].cls}`}><div className="text-xs font-bold tracking-wide">{t}</div>
          <div className="text-3xl font-extrabold">{rep.tiers[t] ?? 0} APs</div><div className="text-sm">{TIER_CARD[t].sub}</div></div>)}</div>
      {rep.movement && <section className="inm-card"><div className="inm-cardhead"><h2>Tier movement since “{rep.compared_to?.label}”</h2></div><div className="inm-cardbody">
        {rep.movement.length ? <table className="inm-table"><thead><tr><th>AP</th><th>From</th><th>To</th><th></th></tr></thead><tbody>
          {rep.movement.map(m => <tr key={m.ap}><td>{m.ap}</td><td><Pill value={m.from} tone={TIER_TONE[m.from]} /></td><td><Pill value={m.to} tone={TIER_TONE[m.to]} /></td>
            <td className={m.direction === "improved" ? "text-emerald-700" : m.direction === "worse" ? "text-red-700" : "text-slate-500"}
              title={m.direction === "not comparable" ? "Only one of the two reports had event evidence for this AP" : ""}>{m.direction}</td></tr>)}</tbody></table>
          : <p className="text-sm text-slate-500">No AP changed tier.</p>}</div></section>}
      <section className="inm-card"><div className="inm-cardhead"><h2>Full AP categorization ({rep.rows.length})</h2></div><div className="overflow-x-auto"><table className="inm-table">
        <thead><tr><th>Tier</th><th>AP</th><th>Total</th><th>Clients</th><th>Negative</th><th>Rejects</th><th>DHCP TO</th><th>Auth/Sec</th><th>Deauth</th><th>2.4 / 5 GHz busy</th><th>Trigger</th><th className="print:hidden"></th></tr></thead>
        <tbody>{rep.rows.map(r => <tr key={r.ap}><td><Pill value={r.state === "OFFLINE" ? "OFFLINE" : r.tier}
          tone={r.state === "OFFLINE" ? "poor" : TIER_TONE[r.tier]} /></td>
          <td>{r.device_id ? <Link className="inm-link" to={`/devices/${r.device_id}/360`}>{r.ap}</Link> : r.ap}<div className="text-[10px] text-slate-500">{r.cluster ?? ""}</div></td>
          <td>{n(r.total_events)}</td><td>{n(r.unique_clients ?? r.clients_now)}</td><td>{n(r.negative)}</td><td>{n(r.events?.reject)}</td><td>{n(r.events?.dhcp_timeout)}</td>
          <td>{n(r.events?.auth)}</td><td>{n(r.events?.deauth)}</td><td className="whitespace-nowrap">{r.util_2g_percent ?? "—"}% / {r.util_5g_percent ?? "—"}%</td>
          <td className="text-xs">{r.trigger}{r.client_experience && r.client_experience.tier === "GOLD" &&
            <div className="text-slate-500">{r.client_experience.detail}</div>}</td>
          <td className="print:hidden">{r.device_id && <button className="inm-btn-secondary" onClick={() => setDiag(r)}>Diagnose</button>}</td></tr>)}</tbody></table></div></section>
      {(["BRONZE", "SILVER"] as Tier[]).map(t => tiers(t).length > 0 && <section key={t} className="inm-card"><div className="inm-cardhead">
        <h2>{t === "BRONZE" ? "Bronze APs — corrective actions required" : "Silver APs — monitor and tune"}</h2></div><div className="overflow-x-auto"><table className="inm-table">
        <thead><tr><th>AP</th><th>Primary trigger</th><th>Interpretation</th><th>Recommended action</th></tr></thead>
        <tbody>{tiers(t).map(r => <tr key={r.ap}><td className="font-semibold">{r.ap}</td><td className="text-xs">{r.trigger}</td><td className="text-sm">{r.interpretation}
          {r.top_reject_ssid_band && <div className="text-xs text-slate-500">Rejects concentrated on {r.top_reject_ssid_band}{r.reject_hours?.length ? `; top hours ${r.reject_hours.map(h => `${h.hour}:00=${h.count}`).join(", ")}` : ""}</div>}</td>
          <td className="text-sm">{r.action}</td></tr>)}</tbody></table></div></section>)}
      {tiers("GOLD").length > 0 && <section className="inm-card"><div className="inm-cardhead"><h2>Gold APs — stable baseline</h2></div><div className="inm-cardbody text-sm">
        {tiers("GOLD").map(r => r.ap).join(" · ")}. No category threshold breach in this window — keep as baseline.</div></section>}
      <section className="inm-card"><div className="inm-cardhead"><h2>Method & thresholds</h2></div><div className="inm-cardbody text-sm">
        <p>Negative events: {rep.method.negative_categories.join(", ")}.</p>
        <p><b>Silver</b> (any): {Object.entries(rep.method.silver).map(([k, v]) => `${k} ≥ ${v}`).join(", ")}.</p>
        <p><b>Bronze</b> (any): {Object.entries(rep.method.bronze).map(([k, v]) => `${k} ≥ ${v}`).join(", ")}.</p>
        <p><b>Condition</b>: confirmed outages display as red Offline; utilisation ≥ {rep.method.condition_silver.util_percent}% Silver / ≥ {rep.method.condition_bronze.util_percent}% Bronze;
          CPU ≥ {rep.method.condition_silver.cpu_percent}/{rep.method.condition_bronze.cpu_percent}%; memory ≥ {rep.method.condition_silver.memory_percent}/{rep.method.condition_bronze.memory_percent}%;
          clients ≥ {rep.method.condition_silver.clients}/{rep.method.condition_bronze.clients}; noise above {rep.method.condition_silver.noise_floor_dbm}/{rep.method.condition_bronze.noise_floor_dbm} dBm. Unmeasured inputs never count as healthy.</p>
        <p><b>Client experience</b> uses the mean score of currently connected, recently measured clients. Good, fair and poor map to Gold, Silver and Bronze. The worst measured result sets the tier.</p>
        <h3 className="mt-2 font-semibold">Validation after corrective action</h3>
        <ol className="list-decimal pl-5"><li>Save a snapshot now; make the changes on Bronze APs first.</li><li>After 6–24 h, open the new live report (or import the same export window) and compare with the snapshot: the goal is Bronze → Silver/Gold and Silver → Gold.</li>
          <li>Separate AP-side issues from endpoint-driven ones (top client MACs) before changing RF globally.</li><li>Confirm RF changes did not raise rejects or roaming failures on neighbouring APs.</li></ol>
      </div></section>
    </>}
    {diag?.device_id && <DiagnosisPanel target={{ kind: "ap", id: diag.device_id, label: diag.ap }} onClose={() => setDiag(null)} />}
  </div>;
}
