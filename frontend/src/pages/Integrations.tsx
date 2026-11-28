import { Fragment, useState } from "react";
import { Link } from "react-router";
import { useLoad } from "../components/useLoad";
import { Notice, PageHeader, Pill, ago, btn, btn2, inputCls, useAction } from "../components/viz";
import { getSession } from "../services/api";
import { integ, type Connection, type ConnectionIn, type Delivery } from "../services/integrations";

const HINT: Record<string, string> = {
  webhook: "https://hooks.example.org/inm (no credentials in the URL)", teams: "https://….logic.azure.com/… (Teams Workflows webhook URL)",
  email: "noc@example.org, oncall@example.org", syslog: "siem.example.org:514", heartbeat: "https://status.example.org/api/push/…" };
const BLANK: ConnectionIn = { name: "", kind: "email", target: "", config: {}, secret: "", min_severity: "major", include_demo: false, enabled: true };
const healthTone = (h: string) => h === "OK" ? "good" : h === "FAILING" ? "poor" : h === "DISABLED" ? "none" : "fair";

/** Connection settings that depend on the type; secrets are write-only. */
function KindFields({ f, setF, editing }: { f: ConnectionIn; setF: (v: ConnectionIn) => void; editing: boolean }) {
  const cfg = f.config as Record<string, string | number | undefined>;
  const set = (k: string, v: string | number) => setF({ ...f, config: { ...f.config, [k]: v } });
  const secretLabel = f.kind === "email" ? "SMTP password" : f.kind === "webhook" ? "Bearer token / HMAC secret" : "";
  return <>
    {f.kind === "email" && <>
      <input aria-label="SMTP relay host" className={inputCls} placeholder="SMTP relay host (blank = server default)" value={String(cfg.smtp_host ?? "")} onChange={e => set("smtp_host", e.target.value)} />
      <input aria-label="SMTP port" className={inputCls} placeholder="Port (587)" value={String(cfg.smtp_port ?? "")} onChange={e => set("smtp_port", Number(e.target.value.replace(/\D/g, "")) || "")} />
      <select aria-label="SMTP security" className={inputCls} value={String(cfg.smtp_security ?? "starttls")} onChange={e => set("smtp_security", e.target.value)}>
        <option value="starttls">STARTTLS</option><option value="tls">Implicit TLS</option><option value="none">None (trusted relay only)</option></select>
      <input aria-label="SMTP username" className={inputCls} placeholder="Username (optional)" value={String(cfg.smtp_username ?? "")} onChange={e => set("smtp_username", e.target.value)} />
      <input aria-label="From address" className={inputCls} placeholder="From address" value={String(cfg.from_address ?? "")} onChange={e => set("from_address", e.target.value)} />
    </>}
    {f.kind === "webhook" && <select aria-label="Webhook authentication" className={inputCls} value={String(cfg.auth ?? "none")} onChange={e => set("auth", e.target.value)}>
      <option value="none">No authentication</option><option value="bearer">Bearer token</option><option value="hmac">HMAC-SHA256 signature</option></select>}
    {f.kind === "syslog" && <select aria-label="Syslog protocol" className={inputCls} value={String(cfg.protocol ?? "udp")} onChange={e => set("protocol", e.target.value)}>
      <option value="udp">UDP (RFC 5424)</option><option value="tcp">TCP (RFC 6587 octet counting)</option></select>}
    {f.kind === "heartbeat" && <input aria-label="Heartbeat interval minutes" className={inputCls} placeholder="Interval minutes (5)" value={String(cfg.interval_minutes ?? "")} onChange={e => set("interval_minutes", Number(e.target.value.replace(/\D/g, "")) || "")} />}
    {secretLabel && <input aria-label={secretLabel} type="password" autoComplete="new-password" className={inputCls}
      placeholder={editing ? `${secretLabel} (blank = keep current)` : `${secretLabel} (optional)`} value={f.secret ?? ""} onChange={e => setF({ ...f, secret: e.target.value })} />}
  </>;
}

function ConnectionForm({ initial, onDone, kinds }: { initial?: Connection; onDone: () => void; kinds: Record<string, string> }) {
  const [f, setF] = useState<ConnectionIn>(initial ? { name: initial.name, kind: initial.kind, target: "", config: initial.config,
    secret: "", min_severity: initial.min_severity, include_demo: initial.include_demo, enabled: initial.enabled } : BLANK);
  const act = useAction();
  return <form className="grid gap-2 md:grid-cols-3" aria-label={initial ? `Edit ${initial.name}` : "New connection"} onSubmit={e => { e.preventDefault(); act.run(async () => {
    const body = { ...f, secret: f.secret || undefined, config: Object.fromEntries(Object.entries(f.config).filter(([, v]) => v !== "" && v != null)) };
    if (initial) await integ.update(initial.id, body); else await integ.create(body);
    setF(BLANK); onDone(); return initial ? "Connection updated." : "Connection added. Send a test to verify it."; }); }}>
    <input aria-label="Connection name" className={inputCls} placeholder="Name" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} />
    <select aria-label="Connection type" className={inputCls} value={f.kind} disabled={!!initial} onChange={e => setF({ ...f, kind: e.target.value, config: {} })}>
      {Object.entries(kinds).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
    <input aria-label="Destination" className={inputCls} placeholder={initial ? `Re-enter destination (${initial.target})` : HINT[f.kind]} value={f.target} onChange={e => setF({ ...f, target: e.target.value })} />
    <KindFields f={f} setF={setF} editing={!!initial} />
    <div className="flex items-center gap-2 md:col-span-3">
      <button className={btn} type="submit" disabled={act.busy || !f.name.trim() || !f.target.trim()}>{initial ? "Save connection" : "Add connection"}</button>
      {initial?.secret_configured && <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={!!f.clear_secret} onChange={e => setF({ ...f, clear_secret: e.target.checked })} /> remove stored credential</label>}
      <Notice error={act.error} message={act.message} />
    </div>
  </form>;
}

function Deliveries({ id, admin }: { id: string; admin: boolean }) {
  const d = useLoad(() => integ.deliveries(id), [id], 20000);
  const act = useAction();
  return <div className="mt-2 rounded border p-2">
    <Notice error={act.error || d.error} message={act.message} />
    <table className="inm-table text-xs"><thead><tr><th>When</th><th>Kind</th><th>State</th><th>Attempts</th><th>Error</th><th /></tr></thead>
      <tbody>{(d.data ?? []).map((x: Delivery) => <tr key={x.id}><td>{x.created_at?.replace("T", " ").slice(0, 19)}</td><td>{x.kind}</td>
        <td><Pill value={x.state} tone={x.state === "SENT" ? "good" : x.state === "DEAD" ? "poor" : "info"} /></td><td>{x.attempts}</td>
        <td className="text-rose-600">{x.last_error}</td>
        <td>{admin && x.state === "DEAD" && !["TEST", "HEARTBEAT"].includes(x.kind) && <button className={btn2} disabled={act.busy}
          onClick={() => act.run(async () => { await integ.retry(x.id); d.reload(); return "Queued for redelivery."; })}>Retry</button>}</td></tr>)}</tbody></table>
    {!d.data?.length && <p className="text-xs text-slate-500">No deliveries recorded for this connection.</p>}
  </div>;
}

/** External-system connections: notification transports, external monitoring, read-only REST API access. */
export default function Integrations() {
  const me = getSession();
  const admin = me?.role === "admin";
  const o = useLoad(integ.overview, [], 30000);
  const audit = useLoad(integ.audit, [], 60000);
  const act = useAction();
  const [editing, setEditing] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [tok, setTok] = useState({ name: "", scopes: ["read"], days: 90 });
  const [newToken, setNewToken] = useState<string | null>(null);
  const reload = () => { o.reload(); audit.reload(); };
  const kinds = o.data?.kinds ?? {};
  return <div className="space-y-4">
    <PageHeader eyebrow="Administration" title="Integrations" subtitle="Connections to external systems: e-mail, webhooks, Microsoft Teams, syslog, external monitoring and read-only REST API access. Alert routing is configured on the Alert notifications screen." />
    <Notice error={act.error || o.error} message={act.message} />
    {o.data && <p className="text-xs text-slate-500">{o.data.policy}</p>}

    <section className="inm-card" aria-label="Connections">
      <h2 className="mb-2 font-semibold">Connections</h2>
      <table className="inm-table text-sm"><thead><tr><th>Name</th><th>Type</th><th>Destination</th><th>Credential</th><th>Health</th><th>Deliveries</th><th /></tr></thead>
        <tbody>{(o.data?.connections ?? []).map(c => <Fragment key={c.id}>
          <tr><td className="font-semibold">{c.name}</td><td>{c.kind_label}</td><td className="font-mono text-xs">{c.target}</td>
            <td>{c.secret_configured ? <Pill value="encrypted" tone="good" /> : "—"}</td>
            <td><Pill value={c.health} tone={healthTone(c.health)} />{c.last_test_at && <div className="text-[10px] text-slate-500">tested {ago(c.last_test_at)}</div>}</td>
            <td className="text-xs">{Object.entries(c.deliveries.by_state).map(([k, v]) => `${k} ${v}`).join(" · ") || "—"}
              {c.deliveries.last_error && <div className="text-rose-600">{c.deliveries.last_error}</div>}</td>
            <td className="space-x-1 whitespace-nowrap">
              <button className={btn2} onClick={() => setOpen(open === c.id ? null : c.id)}>{open === c.id ? "Hide history" : "History"}</button>
              {admin && <>
                <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { const r = await integ.action(c.id, "test"); reload();
                  if (!r.delivered) throw new Error(`Test NOT delivered: ${r.error}`); return `Test delivered to ${c.name}.`; })}>Test</button>
                <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await integ.action(c.id, c.enabled ? "disable" : "enable"); reload();
                  return `${c.name} ${c.enabled ? "disabled" : "enabled"}.`; })}>{c.enabled ? "Disable" : "Enable"}</button>
                <button className={btn2} onClick={() => setEditing(editing === c.id ? null : c.id)}>Edit</button>
                <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await integ.remove(c.id); reload(); return `${c.name} removed.`; })}>Remove</button></>}
            </td></tr>
          {editing === c.id && <tr><td colSpan={7}><ConnectionForm initial={c} kinds={kinds} onDone={() => { setEditing(null); reload(); }} /></td></tr>}
          {open === c.id && <tr><td colSpan={7}><Deliveries id={c.id} admin={admin} /></td></tr>}
        </Fragment>)}</tbody></table>
      {!o.data?.connections.length && <p className="text-sm text-slate-500">No connections yet.</p>}
      {admin && <div className="mt-3 border-t pt-3"><h3 className="mb-2 text-sm font-semibold">Add a connection</h3><ConnectionForm kinds={kinds} onDone={reload} /></div>}
      <p className="mt-2 text-xs text-slate-500">Choose which alerts reach each connection on <Link className="inm-link" to="/notifications">Alert notifications</Link>.</p>
    </section>

    <section className="inm-card" aria-label="External monitoring">
      <h2 className="mb-2 font-semibold">External monitoring</h2>
      <p className="text-sm">Prometheus scrape endpoint <code>{o.data?.monitoring.path}</code>: <Pill value={o.data?.monitoring.metrics_enabled ? "ENABLED" : "DISABLED"} tone={o.data?.monitoring.metrics_enabled ? "good" : "none"} /></p>
      <p className="text-xs text-slate-500">{o.data?.monitoring.auth}. For push-style monitors (Healthchecks, Uptime Kuma) add an “External monitoring (heartbeat)” connection above.</p>
      {admin && o.data && <button className={`${btn2} mt-2`} disabled={act.busy} onClick={() => act.run(async () => {
        const r = await integ.monitoring(!o.data!.monitoring.metrics_enabled); reload(); return r.metrics_enabled ? "Metrics endpoint enabled." : "Metrics endpoint disabled."; })}>
        {o.data.monitoring.metrics_enabled ? "Disable metrics endpoint" : "Enable metrics endpoint"}</button>}
    </section>

    <section className="inm-card" aria-label="REST API access">
      <h2 className="mb-2 font-semibold">REST API access tokens</h2>
      <p className="text-xs text-slate-500">Tokens are read-only (GET), expire, can be revoked and never reach credentials, users, tokens or integrations. A metrics-only token can read only the scrape endpoint.</p>
      {newToken && <div role="status" className="my-2 rounded border border-amber-400 p-2 text-sm">New token (shown once): <code className="break-all">{newToken}</code></div>}
      <table className="inm-table text-sm"><thead><tr><th>Name</th><th>Prefix</th><th>Scopes</th><th>Expires</th><th>Last used</th><th>State</th><th /></tr></thead>
        <tbody>{(o.data?.api_tokens ?? []).map(t => <tr key={t.id}><td>{t.name}</td><td className="font-mono text-xs">{t.prefix}…</td><td>{t.scopes.join(", ")}</td>
          <td className="text-xs">{t.expires_at?.slice(0, 10)}</td><td className="text-xs">{t.last_used_at ? `${ago(t.last_used_at)} from ${t.last_used_ip ?? "?"}` : "never"}</td>
          <td><Pill value={t.state} tone={t.state === "ACTIVE" ? "good" : "none"} /></td>
          <td>{admin && t.state === "ACTIVE" && <button className={btn2} disabled={act.busy} onClick={() => act.run(async () => { await integ.revokeToken(t.id); reload(); return `${t.name} revoked.`; })}>Revoke</button>}</td></tr>)}</tbody></table>
      {admin && <form className="mt-2 flex flex-wrap items-center gap-2" aria-label="New API token" onSubmit={e => { e.preventDefault(); act.run(async () => {
        const r = await integ.createToken(tok); setNewToken(r.token); setTok({ ...tok, name: "" }); reload(); return r.note; }); }}>
        <input aria-label="Token name" className={inputCls} placeholder="Token name" value={tok.name} onChange={e => setTok({ ...tok, name: e.target.value })} />
        {["read", "metrics"].map(s => <label key={s} className="flex items-center gap-1 text-xs"><input type="checkbox" checked={tok.scopes.includes(s)}
          onChange={e => setTok({ ...tok, scopes: e.target.checked ? [...tok.scopes, s] : tok.scopes.filter(x => x !== s) })} /> {s}</label>)}
        <input aria-label="Token lifetime days" className={`${inputCls} w-24`} value={tok.days} onChange={e => setTok({ ...tok, days: Number(e.target.value.replace(/\D/g, "")) || 1 })} />
        <button className={btn} type="submit" disabled={act.busy || !tok.name.trim() || !tok.scopes.length}>Create token</button>
      </form>}
    </section>

    <section className="inm-card" aria-label="Integration audit history">
      <h2 className="mb-2 font-semibold">Audit history</h2>
      <table className="inm-table text-xs"><thead><tr><th>When</th><th>Action</th><th>By</th><th>Detail</th></tr></thead>
        <tbody>{(audit.data ?? []).slice(0, 50).map((a, i) => <tr key={i}><td>{a.at?.replace("T", " ").slice(0, 19)}</td><td>{a.action}</td><td>{a.by}</td>
          <td className={a.success ? "" : "text-rose-600"}>{a.detail}</td></tr>)}</tbody></table>
      {!audit.data?.length && <p className="text-xs text-slate-500">No integration changes recorded.</p>}
    </section>
  </div>;
}
