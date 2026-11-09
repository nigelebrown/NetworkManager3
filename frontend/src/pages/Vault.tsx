import { useState } from "react";
import { Card } from "../components/ui";
import { useLoad } from "../components/useLoad";
import { DevicePicker, Notice, PageHeader, Pill, btn, btn2, inputCls, statusTone, useAction } from "../components/viz";
import { ops, rw } from "../services/ops";

export default function Vault() {
  const [device, setDevice] = useState("");
  const [target, setTarget] = useState("");
  const [text, setText] = useState("");
  const [pick, setPick] = useState<string[]>([]);
  const [diff, setDiff] = useState<string[] | null>(null);
  const vers = useLoad(() => device ? ops.versions(device) : Promise.resolve(null), [device], 0);
  const act = useAction();
  const toggle = (id: string) => setPick(p => p.includes(id) ? p.filter(x => x !== id) : [...p.slice(-1), id]);
  return <div className="space-y-4">
    <PageHeader title="Configuration vault" subtitle="Full-fidelity configurations are envelope-encrypted, hashed and signed. Tampering is detected. Restore pre-checks validate model and firmware compatibility before any lab restore." />
    <Card title="1 · Select device"><DevicePicker value={device} onChange={id => { setDevice(id); setPick([]); setDiff(null); }} /></Card>
    {device && <Card title="2 · Capture configuration">
      <div className="mb-3 flex flex-wrap gap-2">
        <button className={btn} disabled={act.busy} onClick={() => act.run(async () => { const r = await rw.captureDevice(device); vers.reload();
          return `Captured live from the device (${r.environment}): ${r.size} bytes, SHA-256 ${r.sha256.slice(0, 12)}…`; })}>Capture from device now</button>
        <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await rw.schedule(device, 24); return "Daily scheduled capture enabled."; })}>Schedule daily capture</button>
      </div>
      <p className="mb-2 text-sm text-slate-400">Or paste or upload the device's full running configuration (<code>show running-config</code>). It is encrypted before storage and never logged.</p>
      <input type="file" accept=".txt,.cfg,.conf" className="mb-2 text-sm" onChange={async e => { const f = e.target.files?.[0]; if (f) setText(await f.text()); }} />
      <textarea aria-label="Configuration text" className={`${inputCls} h-40 font-mono text-xs`} value={text} onChange={e => setText(e.target.value)} placeholder="hostname …" />
      <button className={`${btn} mt-2`} disabled={!text.trim() || act.busy} onClick={() => act.run(async () => {
        const a = await ops.capture(device, text); setText(""); vers.reload();
        return a && (a as unknown as { duplicate?: boolean }).duplicate ? "Identical to the latest version — no new version created." : `Sealed version ${a.plaintext_sha256.slice(0, 16)}… with key ${a.key_id}.`; })}>Encrypt & store</button>
    </Card>}
    <Notice error={act.error} message={act.message} />
    {vers.data && <Card title="3 · Versions" right={<button className={btn2} disabled={pick.length !== 2 || act.busy} onClick={() => act.run(async () => setDiff((await ops.diff(pick[0], pick[1])).diff))}>Compare selected (2)</button>}>
      <table className="w-full text-sm"><thead><tr className="text-left text-xs uppercase text-slate-500"><th></th><th>Captured</th><th>Trigger</th><th>Model / firmware</th><th>SHA-256</th><th>State</th><th>Actions</th></tr></thead>
        <tbody>{vers.data.full_fidelity.map(a => <tr key={a.id} className="border-t border-slate-800">
          <td><input type="checkbox" aria-label="select version" checked={pick.includes(a.id)} onChange={() => toggle(a.id)} /></td>
          <td className="py-1.5">{new Date(a.created_at).toLocaleString()}</td><td>{a.trigger}</td><td className="text-xs">{a.model} / {a.firmware}</td>
          <td className="font-mono text-xs">{a.plaintext_sha256.slice(0, 16)}…</td><td><Pill value={a.restore_state} tone={statusTone(a.restore_state)} /></td>
          <td className="space-x-1 whitespace-nowrap"><button className={btn2} disabled={act.busy} onClick={() => act.run(async () => {
            const r = await ops.verify(a.id); vers.reload(); return r.integrity === "PASS" ? "Integrity verified: signature, hashes and decryption all match." : `INTEGRITY FAILURE: ${r.reason}`; })}>Verify</button>
            <button className={btn2} disabled={!target || act.busy} onClick={() => act.run(async () => {
              const r = await ops.precheck(a.id, target); vers.reload(); return r.result === "PASS" ? "Restore pre-check PASSED (integrity + compatibility). Lab readback is still required." : `Restore pre-check FAILED: ${r.reasons.join("; ")}`; })}>Restore pre-check</button>
            <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await rw.restorePlan(a.id, target || undefined);
              return `Restore change plan ${r.plan_id.slice(0, 8)} created — it needs independent approval in Change workflow.`; })}>Create restore plan</button></td></tr>)}
          {!vers.data.full_fidelity.length && <tr><td colSpan={7} className="py-3 text-center text-slate-500">No vault versions yet.</td></tr>}</tbody></table>
      <div className="mt-3 max-w-md"><DevicePicker label="Restore pre-check target (lab or replacement device)" value={target} onChange={id => setTarget(id)} /></div>
      <p className="mt-2 text-xs text-slate-500">{vers.data.redacted_baselines.length} redacted onboarding baseline(s) exist for this device; they are labelled NOT RESTORABLE and kept separate.</p>
    </Card>}
    {diff && <Card title="Differences (secret lines redacted)"><pre className="max-h-96 overflow-auto text-xs">{diff.map((l, i) =>
      <div key={i} className={l.startsWith("+") ? "text-emerald-300" : l.startsWith("-") ? "text-red-300" : "text-slate-400"}>{l}</div>)}</pre></Card>}
  </div>;
}
