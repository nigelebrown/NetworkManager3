import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ErrorNote } from "../components/ui";
import { api, getSession, setSession } from "../services/api";

export default function ChangePassword() {
  const nav = useNavigate();
  const s = getSession();
  const [cur, setCur] = useState(""); const [next, setNext] = useState(""); const [again, setAgain] = useState("");
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setErr(null);
    if (next !== again) { setErr("The new passwords do not match."); return; }
    setBusy(true);
    try { const rotated = await api.changePassword(cur, next); setSession(rotated); nav("/", { replace: true }); }
    catch (x) { setErr(x instanceof Error ? x.message : "Change failed"); }
    finally { setBusy(false); }
  };
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-slate-800 bg-slate-900 p-6">
        <h1 className="text-lg font-semibold text-sky-400">Set your password</h1>
        <p className="text-sm text-slate-400">{s?.must_change_password ? "This account uses a temporary password. Choose a new one (12+ characters) to continue." : "Change your password."}</p>
        <ErrorNote error={err} />
        <label className="block text-sm">Current password
          <input type="password" className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" required />
        </label>
        <label className="block text-sm">New password
          <input type="password" className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" minLength={12} required />
        </label>
        <label className="block text-sm">Repeat new password
          <input type="password" className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" minLength={12} required />
        </label>
        <button disabled={busy} className="w-full rounded bg-sky-600 py-2 font-medium hover:bg-sky-500 disabled:opacity-50">{busy ? "Saving…" : "Save password"}</button>
      </form>
    </div>
  );
}
