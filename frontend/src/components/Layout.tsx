import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api, getSession, setSession, type Role, type SystemStatus } from "../services/api";
import { live, type LiveState } from "../services/live";

type Item = { to: string; label: string; roles?: Role[]; tag?: string };
// Workflow-first navigation (master design K.4). Unfinished modules are reachable and say PLANNED.
const GROUPS: { title: string; items: Item[] }[] = [
  { title: "Overview", items: [{ to: "/", label: "Dashboard" }, { to: "/system", label: "System health", roles: ["admin", "auditor"] }] },
  { title: "Inventory", items: [{ to: "/onboarding", label: "Add device", roles: ["admin"] }, { to: "/sites", label: "Sites" }, { to: "/aps", label: "Access points" }, { to: "/switches", label: "Switches" }, { to: "/clients", label: "Clients" }] },
  { title: "Observability", items: [{ to: "/experience", label: "Experience" }, { to: "/alerts", label: "Alerts" }, { to: "/logs", label: "Events & logs", roles: ["admin", "auditor", "operator"] }] },
  { title: "Configuration", items: [{ to: "/config", label: "Configuration", tag: "READ ONLY" }, { to: "/changes", label: "Change control", tag: "LOCKED" }] },
  { title: "Recovery", items: [{ to: "/recovery", label: "Backups & recovery", roles: ["admin"], tag: "PLANNED" }] },
  { title: "Intelligence", items: [{ to: "/assistant", label: "Assistant" }, { to: "/ai-learning", label: "AI learning", tag: "RULES" }] },
  { title: "Operations", items: [{ to: "/firmware", label: "Firmware & certificates" }] },
  { title: "Security", items: [{ to: "/audit", label: "Audit", roles: ["admin", "auditor"] }, { to: "/users", label: "Users", roles: ["admin"] }] },
];

const LIVE_LABEL: Record<LiveState, [string, string]> = {
  live: ["bg-emerald-400", "Live"], connecting: ["bg-amber-400 animate-pulse", "Connecting…"],
  offline: ["bg-red-400 animate-pulse", "Reconnecting — polling"], stopped: ["bg-slate-500", "Not live"],
};

export default function Layout() {
  const nav = useNavigate();
  const s = getSession();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [open, setOpen] = useState(false);
  const [ls, setLs] = useState<LiveState>(live.state);
  useEffect(() => { api.status().then(setStatus).catch(() => {}); }, []);
  useEffect(() => { live.start(); return live.onState(setLs); }, []);

  const logout = async () => {
    try { await api.logout(); } catch { /* token may already be invalid */ }
    live.stop(); setSession(null); nav("/login");
  };
  const visible = (i: Item) => !i.roles || (s && i.roles.includes(s.role));
  const [dot, label] = LIVE_LABEL[ls];
  return (
    <div className="min-h-screen">
      {status?.simulated_data && (
        <div className="bg-fuchsia-900/60 px-4 py-1.5 text-center text-xs font-semibold text-fuchsia-100">
          DEMO MODE — simulated fixtures are marked DEMO. Added real devices are marked MANUAL or REST READ. Device configuration writes remain locked.
        </div>
      )}
      {status?.device_write_lock && (
        <div className="bg-slate-800 px-4 py-1 text-center text-[11px] font-semibold text-slate-300">
          Device writes are LOCKED in this release (Phase 1 safety gate). Configuration screens can read and plan only.
        </div>
      )}
      {status && !status.tls_verification && (
        <div className="bg-amber-900/60 px-4 py-1.5 text-center text-xs font-semibold text-amber-100">
          Development setting: TLS certificate verification for Aruba devices is DISABLED.
        </div>
      )}
      <header className="border-b border-slate-800 bg-slate-900">
        <div className="flex items-center justify-between gap-4 px-4 py-3">
          <div className="flex items-center gap-3">
            <button className="rounded border border-slate-700 px-2 py-1 text-sm lg:hidden" onClick={() => setOpen(!open)} aria-label="Menu">☰</button>
            <div className="font-semibold text-sky-400">Intelligent Network Manager</div>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <span className="flex items-center gap-1.5 text-xs text-slate-400" data-testid="live-indicator" data-state={ls}>
              <span className={`h-2 w-2 rounded-full ${dot}`} />{label}
            </span>
            <span className="hidden text-slate-400 sm:inline">{s?.username} <span className="text-slate-600">({s?.role})</span></span>
            <button onClick={logout} className="rounded border border-slate-700 px-2 py-1 hover:bg-slate-800">Sign out</button>
          </div>
        </div>
      </header>
      <div className="flex">
        <nav className={`${open ? "block" : "hidden"} absolute z-20 w-60 shrink-0 border-r border-slate-800 bg-slate-900 p-3 lg:static lg:block lg:min-h-[calc(100vh-57px)]`}>
          {GROUPS.map((g) => {
            const items = g.items.filter(visible);
            if (!items.length) return null;
            return (
              <div key={g.title} className="mb-3">
                <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-slate-500">{g.title}</div>
                {items.map((i) => (
                  <NavLink key={i.to} to={i.to} end={i.to === "/"} onClick={() => setOpen(false)}
                    className={({ isActive }) => `flex items-center justify-between rounded px-2 py-1.5 text-sm ${isActive ? "bg-sky-500/20 text-sky-300" : "text-slate-300 hover:bg-slate-800"}`}>
                    {i.label}
                    {i.tag && <span className="rounded bg-slate-800 px-1 text-[9px] font-bold text-slate-400">{i.tag}</span>}
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <main className="min-w-0 flex-1 p-4"><div className="mx-auto max-w-7xl"><Outlet /></div></main>
      </div>
    </div>
  );
}
