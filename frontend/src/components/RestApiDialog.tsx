import { useEffect, useState } from "react";
import { restApi, type RestCheck, type RestEnableResult, type Site } from "../services/api";

/** Check whether the controller's REST API is on; if not, enable it over SSH after the admin confirms the host key. */
export default function RestApiDialog({ site, onClose }: { site: Site; onClose: () => void }) {
  const [state, setState] = useState<RestCheck | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const [seen, setSeen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<RestEnableResult | null>(null);

  const check = async () => {
    setBusy(true); setErr(null);
    try { setState(await restApi.check(site.id)); } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  };
  useEffect(() => { check(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

  const fp = state?.ssh?.fingerprint;
  const enable = async () => {
    if (!fp) return; setBusy(true); setErr(null);
    try { setResult(await restApi.enable(site.id, fp, typed)); } catch (e) { setErr(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  };
  const on = state?.rest_enabled === true || result?.ok === true;
  const canEnable = !!state && !!fp && state.writes_enabled && seen && typed.trim() === state.confirm_phrase && !busy;

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4" role="presentation">
      <div role="dialog" aria-modal="true" className="my-6 w-full max-w-xl rounded-xl border border-slate-700 bg-slate-900 p-5 shadow-xl">
        <h2 className="text-base font-semibold">REST API — {site.name}</h2>
        <p className="mt-1 text-xs text-slate-500">Controller {site.vc_host}</p>
        {!state && !err && <p className="mt-4 text-sm text-slate-300">Checking…</p>}
        {err && <div className="mt-3 rounded border border-red-800 bg-red-950/50 p-2 text-sm text-red-300">{err}</div>}

        {state && (
          <div className="mt-4 space-y-3 text-sm">
            <div className={`rounded-lg border p-3 ${on ? "border-emerald-700 bg-emerald-950/40" : "border-slate-700 bg-slate-950/60"}`}>
              <div className="font-semibold">{on ? "REST API is ON" : state.rest_enabled === false ? "REST API is OFF" : "Could not tell"}</div>
              <p className="mt-1 text-slate-300">{result ? result.message : state.detail}</p>
            </div>

            {!on && state.rest_enabled !== null && (
              <>
                <p className="text-slate-300">Aruba Instant ships with the REST API off, and it cannot turn itself on. SIMS can connect to the AP over SSH with the same admin login and run these commands for you:</p>
                <pre className="rounded bg-slate-950 p-2 text-xs">{state.commands.join("\n")}</pre>
                <div>
                  <div className="text-xs uppercase text-slate-400">AP SSH host key (read without sending any credentials)</div>
                  {fp && state.ssh?.legacy_algorithms && <p className="mt-1 text-xs text-amber-300">This AP only accepts older SSH algorithms (ssh-rsa / SHA-1), so SIMS used the legacy fallback. Compare the fingerprint below with the one on the AP before continuing.</p>}
                  {fp ? <code className="mt-1 block break-all rounded bg-slate-950 p-2 text-xs">{fp}</code>
                      : <p className="mt-1 text-amber-300">{state.ssh?.error ?? "Could not read the SSH host key. Is SSH (TCP 22) reachable from SIMS?"}</p>}
                  <label className="mt-2 flex items-start gap-2 text-slate-300"><input type="checkbox" className="mt-1" checked={seen} onChange={(e) => setSeen(e.target.checked)} />
                    <span>I confirm this is the fingerprint of my AP (PuTTY or <code>ssh</code> shows the same <code>SHA256:</code> value on first connect). SIMS refuses to send the password to any other key.</span></label>
                </div>
                {!state.writes_enabled && <div className="rounded border border-amber-700 bg-amber-950/40 p-2 text-amber-200">Disabled. Enabling the REST API is a configuration change: set <code>SIMS_ALLOW_CONFIG_CHANGES=true</code> in <code>.env</code> and restart SIMS.</div>}
                <div className="rounded border border-slate-700 p-2 text-xs text-slate-400">To undo later: {state.rollback}</div>
                <label className="block">To confirm, type <code className="rounded bg-slate-800 px-1">{state.confirm_phrase}</code>
                  <input value={typed} onChange={(e) => setTyped(e.target.value)} className="mt-1 w-full rounded border border-slate-600 bg-slate-950 px-2 py-1.5" /></label>
              </>
            )}
            {result && !result.ok && <div className="rounded border border-red-800 bg-red-950/50 p-2 text-red-300">{result.message}</div>}
          </div>
        )}

        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <button className="rounded border border-slate-600 px-3 py-1.5 text-sm hover:bg-slate-800" onClick={check} disabled={busy}>Check again</button>
          {state && !on && state.rest_enabled !== null && (
            <button disabled={!canEnable} onClick={enable} className="rounded bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-40">{busy ? "Working…" : "Enable REST API"}</button>
          )}
          <button className="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
