import { useState } from "react";
import { configApi, type ApplyResult, type FwChange } from "../services/api";

const mb = (n: number) => `${(n / 1048576).toFixed(1)} MB`;

/** Review + typed confirmation for firmware-stage and certificate-upload plans. */
export default function FwPlanDialog({ change, onClose, onDone }: { change: FwChange; onClose: () => void; onDone: () => void }) {
  const p = change.plan;
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<ApplyResult | null>(null);

  const apply = async () => {
    setBusy(true); setErr(null);
    try { setResult(await configApi.apply(change.id, typed)); onDone(); }
    catch (e) { setErr(e instanceof Error ? e.message : "Failed"); }
    finally { setBusy(false); }
  };
  const cancel = async () => { try { await configApi.cancel(change.id); } catch { /* already gone */ } onClose(); };
  const tone = !result ? "" : result.status === "applied" ? "border-emerald-700 bg-emerald-950/40" : result.status === "failed" ? "border-red-700 bg-red-950/40" : "border-amber-700 bg-amber-950/40";
  const cert = p.certificate;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4" role="presentation">
      <div role="dialog" aria-modal="true" className="my-6 w-full max-w-2xl rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-xl">
        <h2 className="text-base font-semibold">{result ? "Result" : "Review before applying"}</h2>
        <p className="mt-1 text-sm text-slate-300">{change.summary}</p>
        <p className="text-xs text-slate-500">Plan expires {new Date(change.expires_at).toLocaleTimeString()}</p>

        {result ? (
          <div className={`mt-4 rounded-lg border p-3 text-sm ${tone}`}>
            <div className="font-semibold uppercase">{result.status.replace("_", " ")}</div>
            <p className="mt-1">{result.message}</p>
          </div>
        ) : (
          <>
            {p.kind === "firmware_stage" && (
              <div className="mt-4 space-y-2 text-sm">
                <p>Running <b>{p.running ?? "unknown"}</b> → staging <b>{p.target}</b> · automatic reboot: <b>{p.auto_reboot}</b></p>
                {p.images.map((i: { filename: string; ap_class: string; size_bytes: number; sha256: string; url: string }) => (
                  <div key={i.filename} className="rounded bg-slate-950 p-2 text-xs">
                    <div className="font-medium">{i.ap_class}: {i.filename} ({mb(i.size_bytes)}, sha256 {i.sha256}…)</div>
                    <div className="break-all text-slate-400">The Virtual Controller will download: {i.url}</div>
                  </div>
                ))}
              </div>
            )}
            {p.kind === "cert_upload" && cert && (
              <div className="mt-4 space-y-1 text-sm">
                <p>Target: <b>{p.target}</b></p>
                <div className="rounded bg-slate-950 p-2 text-xs">
                  <div><b>{cert.subject_cn ?? "(no CN)"}</b> issued by {cert.issuer_cn ?? "?"} · {cert.key} · chain of {cert.chain_length}</div>
                  <div>Valid until {new Date(cert.not_after).toLocaleDateString()} ({cert.days_left} days)</div>
                  <div>Names: {[...cert.san_dns, ...cert.san_ip].join(", ") || "none listed"}</div>
                  <div className="break-all">SHA-256 {cert.sha256}</div>
                </div>
                <p className="text-xs text-slate-400">AP SSH host key {p.ssh_host_key} · AP fetches from {p.tftp_from}</p>
              </div>
            )}
            {p.warnings?.length > 0 && <ul className="mt-3 space-y-1 text-sm text-amber-300">{p.warnings.map((w: string) => <li key={w}>⚠ {w}</li>)}</ul>}
            <div className="mt-3 rounded-lg border border-slate-700 bg-slate-950/60 p-3 text-sm"><div className="font-semibold">Rollback</div><p className="mt-1 text-slate-300">{p.rollback}</p></div>
            <label className="mt-4 block text-sm">To confirm, type <code className="rounded bg-slate-800 px-1">{p.confirm_phrase}</code>
              <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-950 px-2 py-1.5" /></label>
          </>
        )}
        {err && <div className="mt-3 rounded border border-red-800 bg-red-950/50 p-2 text-sm text-red-300">{err}</div>}
        <div className="mt-5 flex justify-end gap-2">
          {result ? (
            <button className="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500" onClick={onClose}>Close</button>
          ) : (
            <>
              <button className="rounded border border-slate-600 px-3 py-1.5 text-sm hover:bg-slate-800" onClick={cancel}>Cancel</button>
              <button disabled={busy || typed.trim() !== p.confirm_phrase} onClick={apply}
                className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-40">{busy ? "Applying…" : "Apply to device"}</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
