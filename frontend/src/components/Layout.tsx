import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api, getSession, setSession, type Role, type SystemStatus } from "../services/api";
import { live, type LiveState } from "../services/live";
import { ops, type DemoStatus } from "../services/ops";

type Item = { to: string; label: string; roles?: Role[]; tag?: string };
// Workflow-first navigation (master design K.4). Unfinished modules are reachable and say PLANNED.
const GROUPS: { title: string; items: Item[] }[] = [
  { title: "Operations", items: [{ to: "/", label: "NOC dashboard" }, { to: "/executive", label: "Executive overview", roles: ["admin", "auditor", "approver", "operator"] }, { to: "/wifi", label: "Wi-Fi dashboard" }, { to: "/system", label: "System health", roles: ["admin", "auditor"] }] },
  { title: "Inventory", items: [{ to: "/onboarding", label: "Add & configure devices", roles: ["admin"], tag: "START HERE" }, { to: "/onboarding/manual", label: "Manual / CSV import", roles: ["admin"] }, { to: "/discovery", label: "Discovery ranges", roles: ["admin"] }, { to: "/sites", label: "Sites" }, { to: "/aps", label: "Access points" }, { to: "/switches", label: "Switches" }, { to: "/clients", label: "Clients" }] },
  { title: "Monitoring", items: [{ to: "/collection", label: "Live collection", roles: ["admin", "operator"] }, { to: "/experience", label: "User experience" }, { to: "/phase4-insights", label: "Device insights", roles: ["admin"], tag: "RULES" }, { to: "/device-alerts", label: "Device alerts & thresholds", tag: "LIVE" }, { to: "/topology", label: "Topology (LLDP)" }, { to: "/notifications", label: "Alert notifications" }, { to: "/alerts", label: "Wi-Fi alerts" }, { to: "/logs", label: "Events & logs", roles: ["admin", "auditor", "operator"] }] },
  { title: "Configuration", items: [{ to: "/compliance", label: "Best practice & compliance" }, { to: "/changes", label: "Change workflow", tag: "APPROVAL" }, { to: "/config-templates", label: "Templates & inheritance", roles: ["admin"] }, { to: "/deployments", label: "Groups & deployment", roles: ["admin", "approver", "operator"] }, { to: "/vault", label: "Config vault", roles: ["admin", "auditor", "operator"] }, { to: "/config", label: "SSID & AP config (SIMS)" }, { to: "/replace", label: "Replace device", roles: ["admin"] }, { to: "/certification", label: "Write certification", roles: ["admin", "approver", "auditor"] }] },
  { title: "Accountability", items: [{ to: "/accountability", label: "Who changed what", roles: ["admin", "auditor", "operator"] }, { to: "/audit", label: "Audit log", roles: ["admin", "auditor"] }] },
  { title: "Intelligence", items: [{ to: "/learning", label: "AI learning centre" }, { to: "/optimization", label: "RF optimization", tag: "OPT-IN" }, { to: "/assistant", label: "Assistant" }, { to: "/ai-learning", label: "AI rules" }] },
  { title: "Administration", items: [{ to: "/firmware", label: "Firmware & certificates" }, { to: "/printers", label: "Printer intelligence", roles: ["admin"] }, { to: "/platform", label: "Vendors, roles & rules" }, { to: "/recovery", label: "Recovery & change journal", roles: ["admin"] }, { to: "/data", label: "Data management (demo)", roles: ["admin"] }, { to: "/users", label: "Users", roles: ["admin"] }] },
];

const LIVE_LABEL: Record<LiveState, [string, string]> = {
  live: ["bg-emerald-400", "Live"], connecting: ["bg-amber-400 animate-pulse", "Connecting…"],
  offline: ["bg-red-400 animate-pulse", "Reconnecting — polling"], stopped: ["bg-slate-500", "Not live"],
};

export default function Layout() {
  const nav = useNavigate();
  const s = getSession();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [demo, setDemo] = useState<DemoStatus | null>(null);
  useEffect(() => { const f = () => ops.demo().then(setDemo).catch(() => {}); f(); const t = setInterval(f, 30000); return () => clearInterval(t); }, []);
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
      {!status?.simulated_data && demo?.loaded && (
        <div className="bg-fuchsia-900/60 px-4 py-1.5 text-center text-xs font-semibold text-fuchsia-100">
          DEMO DATA LOADED alongside live data — {demo.demo_devices} demo devices are labelled DEMO. <NavLink className="underline" to="/data">Remove demo data</NavLink>
        </div>
      )}
      {status?.device_write_lock && (
        <div className="bg-slate-800 px-4 py-1 text-center text-[11px] font-semibold text-slate-300">
          Device configuration writes are LOCKED (write lock on). Monitoring, compliance and change planning work normally; approved changes are evaluated but not applied (including automatic optimization).
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
