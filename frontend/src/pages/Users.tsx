import { PageHeader } from "../components/viz";
import { FormEvent, useState } from "react";
import { Card, ErrorNote } from "../components/ui";
import { Table, btn, btnPrimary, input } from "../components/inm";
import { useLoad } from "../components/useLoad";
import { api, getSession } from "../services/api";
import { v1, type UserRow } from "../services/inm";

const ROLES = ["viewer", "operator", "auditor", "approver", "admin"];

export default function Users() {
  const users = useLoad(() => v1.users(), [], 0, ["users"]);
  const sites = useLoad(() => api.sites(), [], 0, ["sites"]);
  const [err, setErr] = useState<string | null>(null);
  const [form, setForm] = useState({ username: "", role: "viewer", temporary_password: "", site_scope: "all" });
  const me = getSession()?.username;
  const act = async (fn: () => Promise<unknown>) => { setErr(null); try { await fn(); users.reload(); } catch (e) { setErr(e instanceof Error ? e.message : "failed"); } };
  const create = (e: FormEvent) => { e.preventDefault(); act(async () => { await v1.createUser(form); setForm({ ...form, username: "", temporary_password: "" }); }); };
  const toggleGrant = (u: UserRow, siteId: number) =>
    act(() => v1.setGrants(u.id, u.site_grants.includes(siteId) ? u.site_grants.filter((s) => s !== siteId) : [...u.site_grants, siteId]));
  const reset = (u: UserRow) => { const p = prompt(`Temporary password for ${u.username} (12+ characters). They must change it at next sign-in.`); if (p) act(() => v1.resetPassword(u.id, p)); };
  return (
    <div className="space-y-4">
      <PageHeader eyebrow="Administration" title="Users & roles" subtitle="Named accounts, site grants, RBAC and mandatory first-login credential changes." />
      <p className="text-sm text-slate-400">Every person gets a named account (no shared technician identities). New and reset accounts must change their password at first sign-in. “Granted” scope limits a user to the sites ticked below, including live updates.</p>
      <ErrorNote error={err || users.error} />
      <Card title="Add user">
        <form onSubmit={create} className="flex flex-wrap items-end gap-2">
          <input className={input} placeholder="username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required minLength={3} />
          <select className={input} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select>
          <select className={input} value={form.site_scope} onChange={(e) => setForm({ ...form, site_scope: e.target.value })}><option value="all">all sites</option><option value="granted">granted sites only</option></select>
          <input className={input} type="password" placeholder="temporary password (12+)" value={form.temporary_password} onChange={(e) => setForm({ ...form, temporary_password: e.target.value })} required minLength={12} autoComplete="new-password" />
          <button className={btnPrimary}>Create</button>
        </form>
      </Card>
      <Card>
        <Table head={["User", "Role", "Active", "Scope", "Site grants", ""]}>
          {users.data?.map((u) => (
            <tr key={u.id}>
              <td className="px-2 py-2 font-medium">{u.username}{u.must_change_password && <span className="ml-2 text-[10px] text-amber-300">must change password</span>}</td>
              <td className="px-2 py-2"><select className={input} value={u.role} disabled={u.username === me} onChange={(e) => act(() => v1.patchUser(u.id, { role: e.target.value }))}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></td>
              <td className="px-2 py-2"><input type="checkbox" checked={u.is_active} disabled={u.username === me} onChange={(e) => act(() => v1.patchUser(u.id, { is_active: e.target.checked }))} /></td>
              <td className="px-2 py-2"><select className={input} value={u.site_scope} disabled={u.role === "admin"} onChange={(e) => act(() => v1.patchUser(u.id, { site_scope: e.target.value as "all" | "granted" }))}><option value="all">all</option><option value="granted">granted</option></select></td>
              <td className="px-2 py-2 text-xs">{u.site_scope === "granted" && u.role !== "admin" ? sites.data?.map((s) => (
                <label key={s.id} className="mr-2 inline-flex items-center gap-1"><input type="checkbox" checked={u.site_grants.includes(s.id)} onChange={() => toggleGrant(u, s.id)} />{s.name}</label>
              )) : <span className="text-slate-500">all sites</span>}</td>
              <td className="px-2 py-2 text-right"><button className={btn} onClick={() => reset(u)}>Reset password</button></td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}
