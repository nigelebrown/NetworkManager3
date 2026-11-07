import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setSession } from "../services/api";
import { ErrorNote } from "../components/ui";

export default function Login() {
  const nav = useNavigate();
  const [u, setU] = useState(""); const [p, setP] = useState("");
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try { const s = await api.login(u, p); setSession(s); nav(s.must_change_password ? "/change-password" : "/"); }
    catch (x) { setErr(x instanceof Error ? x.message : "Login failed"); }
    finally { setBusy(false); }
  };
  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-slate-800 bg-slate-900 p-6">
        <h1 className="text-xl font-semibold text-sky-400">Intelligent Network Manager</h1>
        <ErrorNote error={err} />
        <label className="block text-sm">Username
          <input className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2" value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" autoFocus required />
        </label>
        <label className="block text-sm">Password
          <input type="password" className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required />
        </label>
        <button disabled={busy} className="w-full rounded bg-sky-600 py-2 font-medium hover:bg-sky-500 disabled:opacity-50">{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
