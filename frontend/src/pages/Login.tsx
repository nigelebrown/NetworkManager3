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
    <div className="flex min-h-screen items-center justify-center bg-slate-50 p-4">
      <form onSubmit={submit} className="w-full max-w-md space-y-5 rounded-2xl border border-slate-200 bg-white p-8 text-slate-800 shadow-xl">
        <div className="flex items-center gap-3"><span className="inline-flex h-12 w-12 items-center justify-center rounded-xl bg-blue-700 text-lg font-bold text-white">IN</span><div><h1 className="text-xl font-bold text-slate-800">Intelligent Network Manager</h1><p className="mt-1 text-xs font-semibold uppercase tracking-widest text-slate-500">Network operations platform</p></div></div><p className="text-sm text-slate-500">Sign in to your management console</p>
        <ErrorNote error={err} />
        <label className="block text-sm">Username
          <input className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-800 focus:border-blue-500 focus:outline-none" value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" autoFocus required />
        </label>
        <label className="block text-sm">Password
          <input type="password" className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2.5 text-slate-800 focus:border-blue-500 focus:outline-none" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" required />
        </label>
        <button disabled={busy} className="w-full rounded-lg bg-blue-700 py-2.5 font-semibold text-white hover:bg-blue-800 disabled:opacity-50">{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}
