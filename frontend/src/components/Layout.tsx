import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, getSession, setSession, type Role, type SystemStatus } from "../services/api";
import { live, type LiveState } from "../services/live";
import { ops, type DemoStatus } from "../services/ops";

type Item = { to: string; label: string; roles?: Role[]; tag?: string };
// Workflow-first navigation (master design K.4). Unfinished modules are reachable and say PLANNED.
const GROUPS: { title: string; items: Item[] }[] = [
  { title: "Overview & command", items: [{ to: "/", label: "Network command center" }, { to: "/executive", label: "Executive overview", roles: ["admin", "auditor", "approver", "operator"] }, { to: "/wifi", label: "Wi-Fi dashboard" }, { to: "/system", label: "System health", roles: ["admin", "auditor"] }] },
  { title: "Sites & inventory", items: [{ to: "/inventory", label: "Device inventory" },{ to: "/onboarding", label: "Add & configure devices", roles: ["admin"], tag: "START HERE" }, { to: "/onboarding/manual", label: "Manual / CSV import", roles: ["admin"] }, { to: "/discovery", label: "Discovery ranges", roles: ["admin"] }, { to: "/sites", label: "Sites" }, { to: "/aps", label: "Access points" }, { to: "/switches", label: "Switches" }, { to: "/clients", label: "Clients" }] },
  { title: "Monitoring & diagnostics", items: [{ to: "/collection", label: "Live collection", roles: ["admin", "operator"] }, { to: "/experience", label: "User experience" }, { to: "/phase4-insights", label: "Device insights", roles: ["admin"], tag: "RULES" }, { to: "/device-alerts", label: "Device alerts & thresholds", tag: "LIVE" }, { to: "/topology", label: "Topology (LLDP)" }, { to: "/notifications", label: "Alert notifications" }, { to: "/alerts", label: "Wi-Fi alerts" }, { to: "/logs", label: "Events & logs", roles: ["admin", "auditor", "operator"] }] },
  { title: "Configuration & changes", items: [{ to: "/compliance", label: "Best practice & compliance" }, { to: "/changes", label: "Change workflow", tag: "APPROVAL" }, { to: "/config-templates", label: "Templates & inheritance", roles: ["admin"] }, { to: "/deployments", label: "Groups & deployment", roles: ["admin", "approver", "operator"] }, { to: "/vault", label: "Config vault", roles: ["admin", "auditor", "operator"] }, { to: "/config", label: "SSID & AP config (SIMS)" }, { to: "/replace", label: "Replace device", roles: ["admin"] }, { to: "/certification", label: "Write certification", roles: ["admin", "approver", "auditor"] }] },
  { title: "Governance & security", items: [{ to: "/accountability", label: "Who changed what", roles: ["admin", "auditor", "operator"] }, { to: "/audit", label: "Audit log", roles: ["admin", "auditor"] }] },
  { title: "Intelligence & automation", items: [{ to: "/learning", label: "AI learning centre" }, { to: "/optimization", label: "RF optimization", tag: "OPT-IN" }, { to: "/assistant", label: "Assistant" }, { to: "/ai-learning", label: "AI rules" }] },
  { title: "Administration", items: [{ to: "/firmware", label: "Firmware & certificates" }, { to: "/printers", label: "Printer intelligence", roles: ["admin"] }, { to: "/platform", label: "Vendors, roles & rules" }, { to: "/recovery", label: "Recovery & change journal", roles: ["admin"] }, { to: "/data", label: "Data management (demo)", roles: ["admin"] }, { to: "/users", label: "Users", roles: ["admin"] }] },
];

const LIVE_LABEL: Record<LiveState, [string, string]> = {
  live: ["bg-emerald-400", "Live"], connecting: ["bg-amber-400 animate-pulse", "Connecting…"],
  offline: ["bg-red-400 animate-pulse", "Reconnecting — polling"], stopped: ["bg-slate-500", "Not live"],
};

export default function Layout() {
  const nav = useNavigate();
  const location = useLocation();
  const s = getSession();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [demo, setDemo] = useState<DemoStatus | null>(null);
  useEffect(() => { const f = () => ops.demo().then(setDemo).catch(() => {}); f(); const t = setInterval(f, 30000); return () => clearInterval(t); }, []);
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [ls, setLs] = useState<LiveState>(live.state);
  useEffect(() => { api.status().then(setStatus).catch(() => {}); }, []);
  useEffect(() => { live.start(); return live.onState(setLs); }, []);

  const logout = async () => {
    try { await api.logout(); } catch { /* token may already be invalid */ }
    live.stop(); setSession(null); nav("/login");
  };
  const visible = (i: Item) => !i.roles || (s && i.roles.includes(s.role));
  const [dot, label] = LIVE_LABEL[ls];
  const current = GROUPS.flatMap(g => g.items.map(i => ({...i, group: g.title}))).sort((a,b)=>b.to.length-a.to.length).find(i => i.to === "/" ? location.pathname === "/" : location.pathname === i.to || location.pathname.startsWith(i.to + "/"));
  return (
    <div className="inm-layout">
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
      <header className="inm-topbar">
        <div className="inm-topbar-inner flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button className="inm-mobile-menu" onClick={() => setOpen(!open)} aria-label="Menu" aria-expanded={open}>☰ Menu</button>
            <div className="inm-breadcrumbs">Platform <span className="mx-2">›</span> <b>{current?.label ?? "Device details"}</b></div>
          </div>
          <div className="inm-topright">
            <span className={`inm-runtime ${ls}`} data-testid="live-indicator" data-state={ls}>
              <span className={`inm-green-dot ${dot}`} />{label}
            </span>
            <span className="hidden text-slate-400 sm:inline">{s?.username} <span className="text-slate-600">({s?.role})</span></span>
            <button onClick={logout} className="inm-signout">Sign out</button>
          </div>
        </div>
      </header>
      <div className="inm-layout-body">
        {open && <button className="inm-scrim open" aria-label="Close navigation" onClick={() => setOpen(false)}/>}
        <nav className={`inm-sidebar ${open ? "mobileopen" : ""}`} aria-label="Primary navigation">
          <div className="inm-brand">
            <div className="inm-brandtop"><div className="inm-brandmark" aria-hidden="true">IN</div>
              <div><div className="inm-brandname">Intelligent Network<br/>Manager</div>
                <div className="inm-brandsub">NETWORK OPERATIONS PLATFORM</div></div></div>
            <div className="inm-environment"><span><span className="inm-green-dot"/> {status?.simulated_data ? "DEMO" : "MANAGED NETWORK"}</span><span>{status?.simulated_data ? "DEMO" : "LIVE"}</span></div>
          </div>
          <div className="inm-navscroll">
          {GROUPS.map((g) => {
            const items = g.items.filter(visible);
            if (!items.length) return null;
            return (
              <div key={g.title} className="inm-navgroup">
                <button className="inm-navhead" type="button" aria-expanded={!collapsed[g.title]} onClick={() => setCollapsed(v => ({...v, [g.title]: !v[g.title]}))}>
                  <span>{g.title}</span><span aria-hidden="true">{collapsed[g.title] ? "+" : "−"}</span></button>
                {!collapsed[g.title] && items.map((i) => (
                  <NavLink key={i.to} to={i.to} end={i.to === "/"} onClick={() => setOpen(false)}
                    className={({ isActive }) => `inm-navitem ${isActive ? "active" : ""}`}>
                    <span className="inm-navicon" aria-hidden="true">▤</span><span style={{flex:1}}>{i.label}</span>
                    {i.tag && <span className="inm-navtag">{i.tag}</span>}
                  </NavLink>
                ))}
              </div>
            );
          })}
          </div>
          <div className="inm-sidebarfooter">NetworkManager3<br/>Monitoring uses persisted device observations.<br/>Configuration writes require independent approval and certification.</div>
        </nav>
        <main className="inm-body" id="inm-main-content"><Outlet /></main>
      </div>
    </div>
  );
}
