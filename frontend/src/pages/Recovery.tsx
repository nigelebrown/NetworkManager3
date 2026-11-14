import { PageHeader } from "../components/viz";
import { useState } from "react";
import { Link } from "react-router-dom";
import { Card, ErrorNote, fmtTime } from "../components/ui";
import { Table } from "../components/inm";
import { useLoad } from "../components/useLoad";
import { Notice, Pill, ago, btn2, inputCls, statusTone, useAction } from "../components/viz";
import { v1 } from "../services/inm";
import { rem } from "../services/remediation";

/** Interrupted configuration changes, recorded encrypted before the first device write (audit E2E-14). */
function RecoveryJournal() {
  const j = useLoad(() => rem.journal(), [], 15000, ["changes", "alerts"]);
  const act = useAction();
  const [note, setNote] = useState("");
  const [guide, setGuide] = useState<{ device: string; automatic: boolean; summary: string; steps: string[] } | null>(null);
  const rows = j.data ?? [];
  const open = rows.filter(r => ["RECOVERY_REQUIRED", "OPEN"].includes(r.state));
  return <Card title={`Change-recovery journal — ${open.filter(r => r.state === "RECOVERY_REQUIRED").length} need recovery`}>
    <p className="mb-2 text-sm text-slate-400">Every execution writes an encrypted record of how to undo it before touching the device. If a process dies
      mid-change the entry becomes RECOVERY_REQUIRED (and a critical alert opens). Recovery restores the pre-change configuration and verifies it.</p>
    <table className="w-full text-sm"><thead><tr className="text-left text-xs text-slate-500"><th>Started</th><th>Device</th><th>Env</th><th>State</th><th>Strategy</th><th>Last step</th><th>Notes</th><th></th></tr></thead>
      <tbody>{rows.map(r => <tr key={r.id} className="border-t border-slate-800 align-top">
        <td className="py-1 text-xs">{ago(r.created_at)}</td><td><Link className="text-sky-300" to={`/devices/${r.device_id}/360`}>{r.device_id.slice(0, 8)}</Link></td>
        <td><Pill value={r.environment} tone={r.environment === "EMULATED" ? "demo" : "info"} /></td>
        <td><Pill value={r.state} tone={r.state === "RECOVERY_REQUIRED" ? "poor" : r.state === "OPEN" ? "fair" : "good"} /></td>
        <td className="text-xs">{r.strategy}</td><td className="font-mono text-xs">{r.last_step}</td><td className="text-xs text-slate-400">{r.notes}</td>
        <td className="space-x-1 whitespace-nowrap">{["RECOVERY_REQUIRED", "OPEN"].includes(r.state) && <>
          <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const o = await rem.recover(r.id); j.reload();
            return o.recovered ? `Recovered: ${o.steps.join(", ")}; configuration matches the pre-change backup.` : `Not recovered: ${o.error ?? o.journal.notes}`; })}>Recover</button>
          <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const g = await rem.guided(r.device_id); setGuide({ device: r.device_id, ...g }); })}>Guided procedure</button>
          <button className={btn2} disabled={act.busy || note.length < 10} onClick={() => act.run(async () => { await rem.resolve(r.id, note); j.reload(); return "Closed with your note."; })}>Close with note</button></>}
        </td></tr>)}</tbody></table>
    {!rows.length && <p className="text-sm text-slate-500">No executions recorded yet.</p>}
    <label className="mt-2 block text-xs">Note for manual closure (what you did, which plans)<input className={inputCls} value={note} onChange={e => setNote(e.target.value)} /></label>
    {guide && <div className="mt-3 rounded border border-slate-700 p-2 text-sm"><b>{guide.automatic ? "Automatic restore available" : "Guided restore (manual)"}</b> — {guide.summary}
      <ol className="ml-5 mt-1 list-decimal text-xs">{guide.steps.map((s, i) => <li key={i}>{s}</li>)}</ol></div>}
    <Notice error={act.error} message={act.message} />
  </Card>;
}

export default function Recovery() {
  const { data, error } = useLoad(() => v1.recovery(), [], 60000, ["config"]);
  return (
    <div className="space-y-4">
      <PageHeader eyebrow="Configuration & changes" title="Recovery & restore" subtitle="Encrypted backups, durable recovery journals and independently approved restoration." />
      <ErrorNote error={error} />
      <RecoveryJournal />
      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Encrypted configuration vault"><p className="text-sm text-slate-400">Full-fidelity, signed and encrypted captures from each device,
          scheduled capture, version diff and restore plans. <Link className="text-sky-300" to="/vault">Open the vault →</Link></p></Card>
        <Card title="Device replacement"><p className="text-sm text-slate-400">Compatibility checks, then a restore plan for the new unit
          (approval, backup, readback). <Link className="text-sky-300" to="/replace">Replace a device →</Link></p></Card>
      </div>
      <Card title="Legacy SIMS sanitised snapshots (display copies only)">
        <p className="mb-2 text-sm text-amber-200">
          These snapshots had passphrases and passwords removed before storage. They are useful for review and diffs but they
          <b> cannot restore a device</b>, so they are never offered as restore candidates.
        </p>
        <Table head={["Taken", "Site", "Kind", "SHA-256", "Classification", "Restorable"]} empty={data?.sanitized_snapshots.length === 0}>
          {data?.sanitized_snapshots.map((b) => (
            <tr key={b.id}>
              <td className="px-2 py-1.5 text-xs">{fmtTime(b.taken_at)}</td>
              <td className="px-2 py-1.5 text-xs">#{b.site_id}</td>
              <td className="px-2 py-1.5 text-xs">{b.kind}</td>
              <td className="px-2 py-1.5 font-mono text-[10px] text-slate-500">{b.sha256.slice(0, 16)}…</td>
              <td className="px-2 py-1.5 text-xs">{b.classification}</td>
              <td className="px-2 py-1.5"><span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[10px] font-bold text-red-300" title={b.reason}>NOT RESTORABLE</span></td>
            </tr>
          ))}
        </Table>
        {data && data.restore_candidates.length === 0 && <div className="mt-2 text-xs text-slate-500">Restore candidates: none (no verified full-fidelity backup exists yet).</div>}
      </Card>
    </div>
  );
}
