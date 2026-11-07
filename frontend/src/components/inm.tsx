import { ReactNode } from "react";

const STATUS: Record<string, string> = {
  UP: "bg-emerald-500/15 text-emerald-300 ring-emerald-500/30",
  DEGRADED: "bg-amber-500/15 text-amber-300 ring-amber-500/30",
  DOWN: "bg-red-500/15 text-red-300 ring-red-500/30",
  UNKNOWN: "bg-slate-600/30 text-slate-300 ring-slate-500/30",
};
export function StatusBadge({ status }: { status: string }) {
  return <span className={`rounded px-2 py-0.5 text-xs font-bold ring-1 ${STATUS[status] ?? STATUS.UNKNOWN}`}>{status}</span>;
}

/** Every synthetic artifact says DEMO (P1-08). */
export function DemoBadge({ source }: { source?: string }) {
  if (source && source !== "DEMO") return null;
  return <span className="rounded bg-fuchsia-500/20 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-fuchsia-300" title="Synthetic demo data — not from a real device">DEMO</span>;
}

const CAP: Record<string, string> = {
  DEMO: "border-fuchsia-700/60 text-fuchsia-300", READ_ONLY: "border-emerald-700/60 text-emerald-300",
  CERTIFIED_WRITE: "border-sky-700/60 text-sky-300", UNVERIFIED: "border-amber-700/60 text-amber-300",
  UNSUPPORTED: "border-slate-700 text-slate-500 line-through",
};
export function CapChip({ op, status, evidence }: { op: string; status: string; evidence?: string | null }) {
  return <span title={`${status}${evidence ? ` — ${evidence}` : ""}`} className={`rounded border px-1.5 py-0.5 text-[10px] ${CAP[status] ?? CAP.UNVERIFIED}`}>{op}</span>;
}

export function PlannedNotice({ phase, title, children }: { phase: number; title: string; children?: ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/40 p-5">
      <div className="flex items-center gap-2">
        <span className="rounded bg-slate-700/50 px-2 py-0.5 text-xs font-bold text-slate-300">PLANNED · Phase {phase}</span>
        <h2 className="text-sm font-semibold text-slate-200">{title}</h2>
      </div>
      <div className="mt-2 text-sm text-slate-400">{children}</div>
    </div>
  );
}

export function Table({ head, children, empty }: { head: string[]; children: ReactNode; empty?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase text-slate-500"><tr>{head.map((h) => <th key={h} className="px-2 py-2 font-medium">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-slate-800">{children}</tbody>
      </table>
      {empty && <div className="py-6 text-center text-sm text-slate-500">Nothing to show.</div>}
    </div>
  );
}

export const btn = "rounded border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800 disabled:opacity-50";
export const btnPrimary = "rounded bg-sky-600 px-3 py-1.5 text-sm font-medium hover:bg-sky-500 disabled:opacity-50";
export const input = "rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-sm";
