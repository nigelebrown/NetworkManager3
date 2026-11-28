import { Suspense, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { api, getSession, request, setSession, type Role, type SystemStatus } from "../services/api";
import { live, type LiveState } from "../services/live";
import { ops, type DemoStatus } from "../services/ops";

type View = { to: string; label: string; roles?: Role[] };
// One menu entry per purpose. Closely related screens are VIEWS of that entry (tabs above the page), so no two
// menu entries lead to overlapping content; `purpose` is shown as the entry's tooltip and under the tabs.
type Item = { to: string; label: string; purpose: string; roles?: Role[]; tag?: string; views?: View[]; match?: RegExp };
// Approved full-system wireframe navigation. Existing SIMS tools remain discoverable as views.
// Menu tags that must reflect the server's current state rather than fixed text (rc17).
const LIVE_TAGS: Record<string, string> = { "/predictive": "predictive", "/firmware": "firmware",
  "/optimization": "optimization" };
const TAG_TEXT: Record<string, string> = { NO_RUN: "NO RUN", INSUFFICIENT_HISTORY: "LEARNING", PARTIALLY_VALIDATED: "PARTLY VALIDATED",
  PER_DEVICE_OPT_IN: "OPT-IN", OFF: "OFF", IMPLEMENTED_UNCERTIFIED: "UNCERTIFIED" };
const OPS_ROLES: Role[] = ["admin", "auditor", "approver", "operator"];
export const GROUPS: { title: string; items: Item[] }[] = [
  { title: "Overview & command", items: [
    { to: "/", label: "Network command center", purpose: "Live operational status across every site", views: [
      { to: "/", label: "Command center" }, { to: "/wifi", label: "Wi-Fi view" }] },
    { to: "/executive", label: "Executive overview", purpose: "Management KPIs and trends", roles: OPS_ROLES },
    { to: "/system", label: "Platform health", purpose: "Health of this platform's own services", roles: ["admin","auditor"] }] },
  { title: "Sites & inventory", items: [
    { to: "/sites", label: "Sites & formations", purpose: "Site hierarchy and formation assignments" },
    { to: "/inventory", label: "Device inventory", purpose: "Every registered access point and switch", match: /^\/devices\/[^/]+(\/config)?$/, views: [
      { to: "/inventory", label: "All devices" }, { to: "/aps", label: "Access points" },
      { to: "/aps/controller", label: "AP controller view" }, { to: "/switches", label: "CX switches" }] },
    { to: "/clients", label: "Clients & experience", purpose: "Who is connected, how they roam and how well they are served", views: [
      { to: "/clients", label: "Wireless clients" }, { to: "/client-journeys", label: "Connectivity journeys" },
      { to: "/experience", label: "Experience scores" }] },
    { to: "/printers", label: "Printers", purpose: "Printer fleet, supplies, usage and purchasing", tag: "READ-ONLY" },
    { to: "/onboarding", label: "Add & configure devices", purpose: "Bring new equipment under management", roles: ["admin"], tag: "START", views: [
      { to: "/onboarding", label: "Guided onboarding" }, { to: "/discovery", label: "Discovery rules" },
      { to: "/discovery/planner", label: "Range planner" }, { to: "/onboarding/manual", label: "Manual / CSV intake" },
      { to: "/onboarding/import", label: "Inventory import" }] }] },
  { title: "Monitoring & diagnostics", items: [
    { to: "/collection", label: "Live collection", purpose: "Polling plans, freshness and collection failures", roles: ["admin","operator"] },
    { to: "/rf-health", label: "Wireless & RF health", purpose: "Radio quality and AP health", views: [
      { to: "/rf-health", label: "RF health" }, { to: "/ap-health", label: "AP health report" }] },
    { to: "/port-health", label: "Ports, PoE & optics", purpose: "Switch ports, PoE budget, LLDP, optics and watched port groups", views: [
      { to: "/port-health", label: "Ports, PoE & LLDP" }, { to: "/optics", label: "Optical transceivers" },
      { to: "/port-groups", label: "Port monitoring groups" }] },
    { to: "/device-alerts", label: "Alerts & incidents", purpose: "Open problems that need action", views: [
      { to: "/device-alerts", label: "Device alerts & incidents" }, { to: "/alerts", label: "Wi-Fi client alerts" }] },
    { to: "/topology", label: "Network topology", purpose: "How devices connect to each other" },
    { to: "/device-360", label: "Device 360°", purpose: "Everything about one device in one place", match: /^\/devices\/[^/]+\/360$/ },
    { to: "/logs", label: "Logs & events", purpose: "Platform, collector and device events", roles: ["admin","auditor","operator"] }] },
  { title: "Configuration & changes", items: [
    { to: "/config-templates", label: "Templates & inheritance", purpose: "Reusable configuration standards", roles: ["admin"] },
    { to: "/ssid-policies", label: "WLAN & SSID policies", purpose: "Wireless network definitions", views: [
      { to: "/ssid-policies", label: "SSID policies" }, { to: "/config", label: "Legacy WLAN settings" }] },
    { to: "/switch-config", label: "Switch configuration", purpose: "Switch VLANs, ports and services" },
    { to: "/changes", label: "Change requests", purpose: "Plan, approve and deploy configuration changes", tag: "APPROVAL", views: [
      { to: "/changes", label: "Change plans" }, { to: "/approvals", label: "Approvals queue", roles: OPS_ROLES },
      { to: "/deployments", label: "Group deployment", roles: ["admin","approver","operator"] }] },
    { to: "/firmware", label: "Firmware lifecycle", purpose: "Images, staging, activation and recovery", tag: "PARTIAL" },
    { to: "/vault", label: "Backup & restore", purpose: "Configuration versions, restore and device replacement", roles: ["admin","auditor","operator"], views: [
      { to: "/vault", label: "Configuration vault" }, { to: "/replace", label: "Restore & replace", roles: ["admin"] },
      { to: "/recovery", label: "Recovery journal", roles: ["admin"] }] },
    { to: "/certification", label: "Hardware certification", purpose: "Which operations are proven on which hardware", roles: ["admin","approver","auditor"] }] },
  { title: "Intelligence & automation", items: [
    { to: "/optimization", label: "RF optimization", purpose: "Governed automatic channel and power changes", tag: "OPT-IN" },
    { to: "/recommendations", label: "Recommendations", purpose: "Suggested fixes with evidence", views: [
      { to: "/recommendations", label: "Recommendations" }, { to: "/phase4-insights", label: "Device insights", roles: ["admin"] }] },
    { to: "/predictive", label: "Predictive health", purpose: "Forecast problems before they happen" },
    { to: "/analytics", label: "Operational analytics", purpose: "Trends and capacity over time", roles: OPS_ROLES },
    { to: "/learning", label: "AI learning & rules", purpose: "How the platform learns and the rules it applies", views: [
      { to: "/learning", label: "AI learning centre" }, { to: "/ai-learning", label: "AI rules" },
      { to: "/ai-site-rules", label: "Site rules" }] },
    { to: "/assistant", label: "Assistant", purpose: "Ask questions about the network (read-only)" }] },
  { title: "Governance & security", items: [
    { to: "/compliance", label: "Compliance & drift", purpose: "Configuration standards and drift" },
    { to: "/accountability", label: "Change attribution", purpose: "Who changed what on a device", roles: ["admin","auditor","operator"] },
    { to: "/audit", label: "Audit trail", purpose: "Tamper-evident record of platform actions", roles: ["admin","auditor"] }] },
  { title: "Administration", items: [
    { to: "/users", label: "Users & roles", purpose: "Accounts, roles and site access", roles: ["admin"] },
    { to: "/credentials", label: "Credential profiles", purpose: "Encrypted device credentials", roles: ["admin"] },
    { to: "/notifications", label: "Alert notifications", purpose: "Where alerts and events are sent" },
    { to: "/integrations", label: "Integrations", purpose: "External system connections: e-mail, webhooks, Teams, syslog, monitoring, API tokens", roles: ["admin","auditor"] },
    { to: "/data", label: "Data management", purpose: "Retention, demo data and storage", roles: ["admin"] },
    { to: "/platform", label: "Platform settings", purpose: "Platform-wide configuration" }] },
];

const onPath = (path: string, to: string) => to === "/" ? path === "/" : path === to || path.startsWith(to + "/");
/** The menu entry that owns a path: exact view/route first, then the longest prefix, then an explicit pattern. */
export function ownerOf(path: string): { item: Item; group: string; view?: View } | undefined {
  const all = GROUPS.flatMap(g => g.items.map(item => ({ item, group: g.title })));
  for (const e of all) {
    const v = e.item.views?.find(x => x.to === path);
    if (v) return { ...e, view: v };
  }
  const prefix = all.flatMap(e => [e.item.to, ...(e.item.views ?? []).map(v => v.to)].map(to => ({ ...e, to })))
    .filter(x => x.to !== "/" && onPath(path, x.to)).sort((a, b) => b.to.length - a.to.length)[0];
  if (prefix) return { item: prefix.item, group: prefix.group, view: prefix.item.views?.find(v => v.to === prefix.to) };
  const pattern = all.find(e => e.item.match?.test(path));
  if (pattern) return pattern;
  return path === "/" ? all[0] : undefined;
}

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
  const [liveTags, setLiveTags] = useState<Record<string, string>>({});
  useEffect(() => {
    const f = () => request<{ modules: { key: string; status: string }[] }>("/v1/system/features").then(r => {
      const byKey = Object.fromEntries(r.modules.map(m => [m.key, m.status]));
      setLiveTags(Object.fromEntries(Object.entries(LIVE_TAGS).filter(([, k]) => byKey[k])
        .map(([to, k]) => [to, TAG_TEXT[byKey[k]] ?? byKey[k].replace(/_/g, " ")])));
    }).catch(() => {});
    f(); const t = setInterval(f, 300000); return () => clearInterval(t);
  }, []);
  useEffect(() => { live.start(); return live.onState(setLs); }, []);
  // Keep the active entry visible in the scrollable menu (entries in lower groups are otherwise off-screen).
  useEffect(() => { document.querySelector("a.inm-navitem.active")?.scrollIntoView?.({ block: "nearest" }); }, [location.pathname]);

  const logout = async () => {
    try { await api.logout(); } catch { /* token may already be invalid */ }
    live.stop(); setSession(null); nav("/login");
  };
  const visible = (i: { roles?: Role[] }) => !i.roles || (s && i.roles.includes(s.role));
  const [dot, label] = LIVE_LABEL[ls];
  const owner = ownerOf(location.pathname);
  const current = owner?.item;
  const views = (current?.views ?? []).filter(visible);
  return (
    <div className="inm-layout">
      {status?.simulated_data && (
        <div className="inm-banner bg-fuchsia-900/60 px-4 py-1.5 text-center text-xs font-semibold text-fuchsia-100">
          DEMO MODE — simulated fixtures are marked DEMO. Added real devices are marked MANUAL or REST READ. Device configuration writes remain locked.
        </div>
      )}
      {!status?.simulated_data && demo?.loaded && (
        <div className="inm-banner bg-fuchsia-900/60 px-4 py-1.5 text-center text-xs font-semibold text-fuchsia-100">
          DEMO DATA LOADED alongside live data — {demo.demo_devices} demo devices are labelled DEMO. <NavLink className="underline" to="/data">Remove demo data</NavLink>
        </div>
      )}
      {status?.device_write_lock && (
        <div className="inm-banner bg-slate-800 px-4 py-1 text-center text-[11px] font-semibold text-slate-300">
          Device configuration writes are LOCKED (write lock on). Monitoring, compliance and change planning work normally; approved changes are evaluated but not applied (including automatic optimization).
        </div>
      )}
      {status && !status.tls_verification && (
        <div className="inm-banner bg-amber-900/60 px-4 py-1.5 text-center text-xs font-semibold text-amber-100">
          Development setting: TLS certificate verification for Aruba devices is DISABLED.
        </div>
      )}
      <header className="inm-topbar">
        <div className="inm-topbar-inner flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <button className="inm-mobile-menu" onClick={() => setOpen(!open)} aria-label="Menu" aria-expanded={open}>☰ Menu</button>
            <div className="inm-breadcrumbs">Platform <span className="mx-2">›</span> <b>{current?.label ?? "Device details"}</b>{owner?.view && owner.view.label !== current?.label && <><span className="mx-2">›</span>{owner.view.label}</>}</div>
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
                  <NavLink key={i.to} to={i.to} end={i.to === "/"} onClick={() => setOpen(false)} title={i.purpose}
                    aria-current={current === i ? "page" : undefined}
                    className={() => `inm-navitem ${current === i ? "active" : ""}`}>
                    <span className="inm-navicon" aria-hidden="true">{ICON_FOR[i.to] ?? "▤"}</span><span style={{flex:1}}>{i.label}</span>
                    {(liveTags[i.to] ?? i.tag) && <span className="inm-navtag">{liveTags[i.to] ?? i.tag}</span>}
                  </NavLink>
                ))}
              </div>
            );
          })}
          </div>
          <div className="inm-sidebarfooter">NetworkManager3<br/>Monitoring uses persisted device observations.<br/>Configuration writes require independent approval and certification.</div>
        </nav>
        <main className="inm-body" id="inm-main-content">
          {views.length > 1 && <nav className="inm-viewtabs" aria-label={`${current?.label} views`}>
            {views.map(v => <NavLink key={v.to} to={v.to} end className={({ isActive }) => `inm-viewtab ${isActive ? "active" : ""}`}>{v.label}</NavLink>)}
            <span className="inm-viewpurpose">{current?.purpose}</span>
          </nav>}
          <Suspense fallback={<div className="p-6 text-sm text-slate-500" role="status">Loading…</div>}><Outlet /></Suspense></main>
      </div>
    </div>
  );
}
