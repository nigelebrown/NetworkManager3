import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, getSession, setSession, type Role, type SystemStatus } from "../services/api";
import { live, type LiveState } from "../services/live";
import { ops, type DemoStatus } from "../services/ops";

type Item = { to: string; label: string; roles?: Role[]; tag?: string };
// Approved full-system wireframe navigation. Existing SIMS tools remain discoverable.
const GROUPS: { title: string; items: Item[] }[] = [
  { title: "Overview & command", items: [
    { to: "/", label: "Network command center" }, { to: "/executive", label: "Executive overview", roles: ["admin","auditor","approver","operator"] },
    { to: "/system", label: "Platform health", roles: ["admin","auditor"] },
    { to: "/wifi", label: "Wi-Fi dashboard" }] },
  { title: "Sites & inventory", items: [
    { to: "/sites", label: "Sites & formations" }, { to: "/inventory", label: "Device inventory" },
    { to: "/aps", label: "Access points" }, { to: "/switches", label: "CX switches" },
    { to: "/clients", label: "Wireless clients" }, { to: "/printers", label: "Printers", roles: ["admin"], tag: "PARTIAL" },
    { to: "/onboarding", label: "Add & configure devices", roles: ["admin"], tag: "START" },
    { to: "/discovery", label: "Discovery jobs", roles: ["admin"] },
    { to: "/onboarding/manual", label: "Manual / CSV intake", roles: ["admin"] }] },
  { title: "Monitoring & diagnostics", items: [
    { to: "/collection", label: "Live collection", roles: ["admin","operator"] },
    { to: "/rf-health", label: "Wireless & RF health" }, { to: "/port-health", label: "Ports, PoE & LLDP" },
    { to: "/client-journeys", label: "Client connectivity", tag: "CONCEPT" },
    { to: "/device-alerts", label: "Alerts & incidents" }, { to: "/topology", label: "Network topology" },
    { to: "/logs", label: "Logs & events", roles: ["admin","auditor","operator"] },
    { to: "/device-360", label: "Device 360°" },
    { to: "/experience", label: "User experience" }, { to: "/alerts", label: "Wi-Fi alerts" }] },
  { title: "Configuration & changes", items: [
    { to: "/config-templates", label: "Templates & inheritance", roles: ["admin"] },
    { to: "/ssid-policies", label: "WLAN & SSID policies" }, { to: "/switch-config", label: "Switch configuration" },
    { to: "/changes", label: "Change requests", tag: "APPROVAL" },
    { to: "/approvals", label: "Approvals queue", roles: ["admin","approver","operator","auditor"] },
    { to: "/deployments", label: "Group deployment", roles: ["admin","approver","operator"] },
    { to: "/vault", label: "Configuration vault", roles: ["admin","auditor","operator"] },
    { to: "/replace", label: "Restore & replace", roles: ["admin"] },
    { to: "/certification", label: "Hardware certification", roles: ["admin","approver","auditor"] },
    { to: "/config", label: "Legacy WLAN settings" }, { to: "/recovery", label: "Recovery journal", roles: ["admin"] }] },
  { title: "Intelligence & automation", items: [
    { to: "/optimization", label: "RF optimization", tag: "OPT-IN" },
    { to: "/recommendations", label: "Recommendations" },
    { to: "/predictive", label: "Predictive health", tag: "CONCEPT" },
    { to: "/analytics", label: "Operational analytics", roles: ["admin","auditor","approver","operator"] },
    { to: "/learning", label: "AI learning centre" },
    { to: "/phase4-insights", label: "Device insights", roles: ["admin"], tag: "RULES" },
    { to: "/assistant", label: "Assistant" }, { to: "/ai-learning", label: "AI rules" }] },
  { title: "Governance & security", items: [
    { to: "/compliance", label: "Compliance & drift" },
    { to: "/accountability", label: "Change attribution", roles: ["admin","auditor","operator"] },
    { to: "/audit", label: "Audit trail", roles: ["admin","auditor"] }] },
  { title: "Administration", items: [
    { to: "/users", label: "Users & roles", roles: ["admin"] },
    { to: "/credentials", label: "Credential profiles", roles: ["admin"] },
    { to: "/notifications", label: "Alert notifications" },
    { to: "/firmware", label: "Firmware lifecycle", tag: "PARTIAL" },
    { to: "/integrations", label: "Integrations", roles: ["admin","auditor"], tag: "CONCEPT" },
    { to: "/data", label: "Data management", roles: ["admin"] },
    { to: "/platform", label: "Platform settings" }] },
];

const ICON_FOR: Record<string, string> = {
  "/": "◫", "/executive": "▥", "/system": "♡", "/wifi": "⌁",
  "/sites": "⌖", "/inventory": "▤", "/aps": "⌁", "/switches": "▧",
  "/clients": "◉", "/printers": "▣", "/onboarding": "＋", "/discovery": "⌕",
  "/collection": "▤", "/rf-health": "⌁", "/port-health": "▦",
  "/client-journeys": "↝", "/device-alerts": "⚑", "/topology": "⤳",
  "/logs": "≡", "/device-360": "◎", "/config-templates": "◈",
  "/ssid-policies": "⌁", "/switch-config": "▤", "/changes": "⇄",
  "/approvals": "✓", "/deployments": "▦", "/vault": "▣",
  "/replace": "↻", "/certification": "◆", "/optimization": "◎",
  "/recommendations": "✦", "/predictive": "◇", "/analytics": "▥",
  "/compliance": "▣", "/accountability": "↔", "/audit": "≡",
  "/users": "♙", "/credentials": "⚿", "/notifications": "✉",
  "/firmware": "⬡", "/integrations": "⧉", "/data": "▤", "/platform": "⚙",
};

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
  const [search, setSearch] = useState("");
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
            <form className="inm-topsearch" role="search"
              onSubmit={e => { e.preventDefault(); if (search.trim()) nav("/inventory?q=" + encodeURIComponent(search.trim())); }}>
              <span aria-hidden="true">⌕</span>
              <input aria-label="Search inventory" value={search} onChange={e => setSearch(e.target.value)}
                placeholder="Search equipment…" />
              <button type="submit" aria-label="Submit inventory search" className="inm-search-submit">Search</button>
            </form>
            {s?.role === "admin" && <NavLink to="/onboarding" className="inm-header-action">＋ Add device</NavLink>}
            <span className={`inm-runtime ${ls}`} data-testid="live-indicator" data-state={ls}>
              <span className={`inm-green-dot ${dot}`} />{label}
            </span>
            <span className="inm-account" title={s?.role}><span className="inm-avatar" aria-hidden="true">{s?.username?.slice(0, 2).toUpperCase() ?? "IN"}</span>
              <span className="inm-account-label">{s?.username}<small>{s?.role}</small></span></span>
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
                    <span className="inm-navicon" aria-hidden="true">{ICON_FOR[i.to] ?? "▤"}</span><span style={{flex:1}}>{i.label}</span>
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
