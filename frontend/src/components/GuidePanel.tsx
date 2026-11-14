import { useState } from "react";
import { Link } from "react-router-dom";
import { request, getSession } from "../services/api";
import { useLoad } from "./useLoad";

type Step = { id: string; title: string; detail: string; status: "DONE" | "TODO" | "BLOCKED" | "INFO"; count: number | null;
  total: number | null; action: { kind: "link" | "api" | "click"; label: string; to?: string; api?: string; body?: unknown; target?: string } | null };
type Guide = { key: string; title: string; steps: Step[]; next_step: string | null; done: number; total: number;
  sample: { available: boolean; loaded: boolean; label: string; api: string; detail: string } };

const ICON: Record<Step["status"], string> = { DONE: "✓", TODO: "", BLOCKED: "!", INFO: "i" };

/** Step-by-step "what to do next" panel. Status is computed by the server from live data, so it always reflects
 *  where the user actually is. `onClick` receives in-page targets such as "analyze" or "train". */
export default function GuidePanel({ guide, onClick, defaultOpen = true }: { guide: string; onClick?: (target: string) => void; defaultOpen?: boolean }) {
  const g = useLoad(() => request<Guide>(`/v1/guide/${guide}`), [guide], 30000);
  const [open, setOpen] = useState(defaultOpen);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const admin = getSession()?.role === "admin";
  const run = async (fn: () => Promise<string>) => {
    setBusy(true); setMsg(null);
    try { setMsg(await fn()); g.reload(); } catch (e) { setMsg(e instanceof Error ? e.message : "Failed"); } finally { setBusy(false); }
  };
  if (!g.data) return null;
  const d = g.data;
  return <section className="inm-card" aria-label={`Guide: ${d.title}`}>
    <div className="inm-cardhead">
      <h2>Guide · {d.title} <span className="ml-2 text-xs font-normal text-slate-500">{d.done} of {d.total} done</span></h2>
      <div className="flex items-center gap-2">
        {admin && d.sample.available && <button className="inm-btn-secondary" disabled={busy} title={d.sample.detail}
          onClick={() => run(async () => { await request(d.sample.api, { method: "POST" }); return d.sample.loaded ? "Sample data removed." : "Sample network loaded — follow the steps with DEMO devices."; })}>
          {d.sample.label}</button>}
        <button className="inm-link" onClick={() => setOpen(!open)}>{open ? "Hide steps" : "Show steps"}</button>
      </div>
    </div>
    {open && <div className="inm-cardbody">
      <ol className="space-y-2">{d.steps.map((s, i) => {
        const next = s.id === d.next_step;
        const tone = s.status === "DONE" ? "bg-emerald-100 text-emerald-700" : s.status === "BLOCKED" ? "bg-red-100 text-red-700"
          : s.status === "INFO" ? "bg-slate-100 text-slate-500" : next ? "bg-blue-600 text-white" : "bg-slate-100 text-slate-600";
        return <li key={s.id} className={`flex flex-wrap items-start gap-3 rounded-lg border p-3 ${next ? "border-blue-300 bg-blue-50/60" : "border-slate-200"}`}>
          <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-bold ${tone}`}>{ICON[s.status] || i + 1}</span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><b className="text-slate-800">{s.title}</b>
              {s.count != null && <span className="text-xs text-slate-500">{s.count}{s.total != null ? ` / ${s.total}` : ""}</span>}
              {next && <span className="rounded bg-blue-600 px-2 py-0.5 text-[10px] font-bold uppercase text-white">Do this next</span>}
              {s.status === "BLOCKED" && <span className="rounded bg-red-100 px-2 py-0.5 text-[10px] font-bold uppercase text-red-700">Blocked</span>}</div>
            <p className="text-sm text-slate-600">{s.detail}</p>
          </div>
          {s.action && s.status !== "DONE" && (s.action.kind === "link" ? <Link className="inm-btn-secondary" to={s.action.to!}>{s.action.label}</Link>
            : s.action.kind === "click" ? <button className="inm-btn-secondary" onClick={() => onClick?.(s.action!.target!)}>{s.action.label}</button>
            : admin && <button className="inm-btn-primary" disabled={busy} onClick={() => run(async () => {
              const r = await request<{ message?: string }>(s.action!.api!, { method: "POST", body: JSON.stringify(s.action!.body ?? {}) });
              return r.message ?? "Done."; })}>{s.action.label}</button>)}
        </li>; })}</ol>
      {msg && <p className="mt-2 rounded bg-slate-50 p-2 text-sm text-slate-700" role="status">{msg}</p>}
    </div>}
  </section>;
}
