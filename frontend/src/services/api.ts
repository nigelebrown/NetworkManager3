// Thin typed API client. The token lives in sessionStorage (cleared when the tab closes).
const KEY = "sims_session";

export type Role = "admin" | "operator" | "viewer" | "auditor" | "approver";
export interface Session { access_token: string; username: string; role: Role; expires_at: string; must_change_password?: boolean }

export const getSession = (): Session | null => {
  try { const s = sessionStorage.getItem(KEY); return s ? JSON.parse(s) : null; } catch { return null; }
};
export const setSession = (s: Session | null) => {
  try { s ? sessionStorage.setItem(KEY, JSON.stringify(s)) : sessionStorage.removeItem(KEY); } catch { /* ignore */ }
};

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const s = getSession();
  const headers: Record<string, string> = { ...(init.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...(init.headers as Record<string, string>) };
  if (s) headers.Authorization = `Bearer ${s.access_token}`;
  const resp = await fetch(`/api${path}`, { ...init, headers });
  if (resp.status === 401 && s) { setSession(null); window.location.href = "/login"; }
  if (!resp.ok) {
    let msg = resp.statusText;
    try { const j = await resp.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    if (resp.status === 403 && msg === "password_change_required" && s) {
      setSession({ ...s, must_change_password: true }); window.location.href = "/change-password";
    }
    throw new ApiError(resp.status, msg);
  }
  return resp.json();
}

export interface Summary {
  mode: "demo" | "live"; simulated: boolean; sites_total: number;
  aps_total: number; aps_up: number; aps_down: number; clients_total: number;
  avg_snr_db: number | null; client_quality: Record<string, number>;
  avg_channel_utilization_percent: number | null; health_score: number | null;
  user_experience_score: number | null; user_experience: ExpSummary; alerts_active: number;
  alerts_by_severity: Record<string, number>; firmware_versions: Record<string, number>;
  last_poll_at: string | null;
}
export interface HistoryPoint { ts: string; clients: number; aps_up: number; aps_total: number; avg_snr_db: number | null; avg_util_percent: number | null }
export interface Radio {
  index: number; channel: string; band: string | null; tx_power_dbm: number | null;
  util_percent?: number | null; noise_floor_dbm?: number | null;  // absent = not measured
}
export interface AP {
  id: number; site_id: number; site_name: string | null; name: string; mac: string; ip: string | null;
  model: string | null; serial: string | null; firmware: string | null; status: string;
  uptime_seconds: number | null; cpu_percent: number | null; memory_percent: number | null;
  client_count: number | null; radios: Radio[] | null; simulated: boolean; last_seen: string | null;
}
export interface ExpSummary { score: number | null; band: string; good: number; fair: number; poor: number; unscored: number }
export interface Client {
  id: number; site_name: string | null; mac: string; hostname: string | null; ip: string | null;
  ap_name: string | null; ssid: string | null; vlan: number | null; band: string | null; channel: string | null;
  rssi_dbm: number | null; snr_db: number | null; speed_mbps: number | null; quality: string | null;
  auth_state: string | null; exp_score: number | null; exp_band: string | null; simulated: boolean; last_seen: string;
}
export interface Site {
  id: number; name: string; group: string | null; description: string | null; vc_host: string | null;
  vc_port: number; simulated: boolean; last_poll_at: string | null; last_poll_ok: boolean | null;
  last_poll_error: string | null; ap_count: number;
}
export interface Alert {
  id: number; kind: string; severity: string; message: string; status: string; raised_at: string;
  acknowledged_by: string | null; resolved_at: string | null; simulated: boolean;
}
export interface SystemStatus { mode: string; simulated_data: boolean; config_changes_enabled: boolean; tls_verification: boolean; poll_interval_seconds: number; phase: number; device_write_lock?: boolean; inm_profile?: string; role?: string }

const q = (o: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams();
  Object.entries(o).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") p.set(k, String(v)); });
  const s = p.toString(); return s ? `?${s}` : "";
};

export const api = {
  login: (username: string, password: string) =>
    request<Session>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request<{ ok: boolean }>("/auth/logout", { method: "POST" }),
  changePassword: (current_password: string, new_password: string) =>
    request<Session & { ok: boolean }>("/auth/change-password", { method: "POST", body: JSON.stringify({ current_password, new_password }) }),
  status: () => request<SystemStatus>("/system/status"),
  summary: (site_id?: number) => request<Summary>(`/dashboard/summary${q({ site_id })}`),
  history: (hours: number, site_id?: number) => request<HistoryPoint[]>(`/dashboard/history${q({ hours, site_id })}`),
  aps: (site_id?: number) => request<AP[]>(`/aps${q({ site_id })}`),
  clients: (p: { site_id?: number; q?: string; quality?: string }) => request<Client[]>(`/clients${q(p)}`),
  sites: () => request<Site[]>("/sites"),
  createSite: (b: Record<string, unknown>) => request<Site>("/sites", { method: "POST", body: JSON.stringify(b) }),
  testSite: (id: number) => request<{ ok: boolean; error?: string; aps_found?: number; clients_found?: number; firmware?: string; warnings?: string[] }>(`/sites/${id}/test`, { method: "POST" }),
  deleteAp: (id: number) => request<{ deleted: boolean }>(`/aps/${id}`, { method: "DELETE" }),
  deleteSite: (id: number) => request<{ deleted: boolean }>(`/sites/${id}`, { method: "DELETE" }),
  pollSite: (id: number) => request<{ ok: boolean; error: string | null }>(`/sites/${id}/poll`, { method: "POST" }),
  alerts: (status?: string) => request<Alert[]>(`/alerts${q({ status })}`),
  ackAlert: (id: number) => request<Alert>(`/alerts/${id}/ack`, { method: "POST" }),
  resolveAlert: (id: number) => request<Alert>(`/alerts/${id}/resolve`, { method: "POST" }),
};


// ---- configuration management ----
export interface SsidRow { profile: string; essid: string | null; vlan: number | string | null; opmode: string | null; rf_band: string | null; type: string | null; enabled: boolean }
export interface SsidView { backup_id: number | null; taken_at: string | null; simulated: boolean; ssids: SsidRow[]; vlans: number[] }
export interface ChangePlan {
  target: { site: string; controller: string; ap?: string; ap_ip?: string };
  creating?: boolean; before: Record<string, unknown> | null; after: Record<string, unknown>;
  request: { method: string; path: string; body: unknown }; rollback: string; warnings: string[];
  backup_id: number; verification: string; confirm_phrase: string;
}
export interface Change { id: number; site_id: number; kind: string; summary: string; status: string; plan: ChangePlan; created_by: string; created_at: string; expires_at: string; applied_at: string | null; result: string | null; rollback_of: number | null }
export interface ApplyResult { ok: boolean; status: string; message: string; problems: string[]; change: Change }
export interface BackupMeta { id: number; kind: string; taken_at: string; taken_by: string | null; sha256: string; simulated: boolean; ssid_count: number }
export interface SsidInput { profile: string; essid?: string; vlan?: number; opmode?: string; passphrase?: string; rf_band?: string; enabled?: boolean }
export interface MoveResult { moved: boolean; from: string; to: string; seen_in_destination: boolean; config_pulled: boolean; warnings: string[] }

const post = <T,>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const configApi = {
  ssids: (siteId: number) => request<SsidView>(`/config/sites/${siteId}/ssids`),
  pull: (siteId: number) => post<SsidView>(`/config/sites/${siteId}/pull`),
  backups: (siteId: number) => request<BackupMeta[]>(`/config/sites/${siteId}/backups`),
  backup: (id: number) => request<BackupMeta & { text: string }>(`/config/backups/${id}`),
  diff: (a: number, b: number) => request<{ identical: boolean; diff: string }>(`/config/backups/diff?a=${a}&b=${b}`),
  changes: (siteId: number) => request<Change[]>(`/config/changes?site_id=${siteId}`),
  planSsid: (siteId: number, body: SsidInput) => post<Change>(`/config/sites/${siteId}/ssid/plan`, body),
  planRename: (siteId: number, apId: number, newName: string) => post<Change>(`/config/sites/${siteId}/ap-rename/plan`, { ap_id: apId, new_name: newName }),
  apply: (id: number, confirmText: string) => post<ApplyResult>(`/config/changes/${id}/apply`, { confirm_text: confirmText }),
  approve: (id: number) => post<Change>(`/config/changes/${id}/approve`, {}),
  rollbackPlan: (id: number) => post<Change>(`/config/changes/${id}/rollback-plan`),
  cancel: (id: number) => post<Change>(`/config/changes/${id}/cancel`),
  moveAp: (apId: number, targetSiteId: number) => post<MoveResult>(`/aps/${apId}/move`, { target_site_id: targetSiteId, confirm: true }),
};

// ---- REST API onboarding ----
export interface RestCheck {
  rest_enabled: boolean | null; detail: string; writes_enabled: boolean; confirm_phrase: string; rollback: string; commands: string[];
  ssh?: { fingerprint?: string; key_type?: string; legacy_algorithms?: boolean; error?: string };
}
export interface RestEnableResult { ok: boolean; rest_enabled: boolean; message: string; steps: { command: string; ok: boolean }[]; rollback: string }
export const restApi = {
  check: (siteId: number) => post<RestCheck>(`/sites/${siteId}/rest-api/check`),
  enable: (siteId: number, hostKey: string, confirmText: string) =>
    post<RestEnableResult>(`/sites/${siteId}/rest-api/enable`, { host_key_sha256: hostKey, confirm_text: confirmText }),
};


// ---- Digital experience ----
export interface ExpInput { value: string; subscore: number; source: string; weight: number }
export interface ExpResult { score: number | null; band: string; coverage: number; partial: boolean; unavailable: Record<string, string>; inputs: Record<string, ExpInput> }
export interface ExpOverview {
  summary: ExpSummary; simulated: boolean; bands: { good_min: number; fair_min: number };
  worst_clients: { id: number; mac: string; hostname: string | null; site_name: string | null; ap_name: string | null; ssid: string | null; score: number; band: string }[];
  aps: { id: number; name: string; site_name: string | null; status: string; score: number | null; band: string | null; client_count: number | null }[];
}
export interface ExpPoint { ts: string; score: number | null; good: number; fair: number; poor: number }
export interface ExpEvent { id: number; ts: string; kind: string; mac: string; ap_from: string | null; ap_to: string | null; ssid: string | null; simulated: boolean }
export type ExpConfig = Record<string, unknown>;
const qs = (o: Record<string, unknown>) => { const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== ""); return p.length ? "?" + p.map(([k, v]) => `${k}=${encodeURIComponent(String(v))}`).join("&") : ""; };
export const expApi = {
  overview: (site?: number) => request<ExpOverview>(`/experience/overview${qs({ site_id: site })}`),
  history: (hours: number, site?: number) => request<ExpPoint[]>(`/experience/history${qs({ hours, site_id: site })}`),
  client: (id: number) => request<{ id: number; mac: string; ap_name: string | null; ssid: string | null; connected: boolean; result: ExpResult }>(`/experience/clients/${id}`),
  events: (site?: number) => request<{ inferred: boolean; events: ExpEvent[] }>(`/experience/events${qs({ site_id: site, limit: 50 })}`),
  config: () => request<ExpConfig>("/experience/config"),
  saveConfig: (c: ExpConfig) => request<ExpConfig>("/experience/config", { method: "PUT", body: JSON.stringify(c) }),
  resetConfig: () => request<ExpConfig>("/experience/config", { method: "DELETE" }),
};

// ---- local AI assistant (Ollama) ----
export interface AiStatus { enabled: boolean; url: string; model: string | null; reachable: boolean | null; models: string[]; model_installed: boolean | null; error: string | null }
export interface AiAnswer { kind: string; text: string; ai_generated: boolean; model: string; facts: Record<string, unknown>; simulated: boolean; tool?: string | null }
export const aiApi = {
  status: () => request<AiStatus>("/ai/status"),
  setModel: (model: string) => request<{ model: string }>("/ai/model", { method: "PUT", body: JSON.stringify({ model }) }),
  explainClient: (id: number) => post<AiAnswer>(`/ai/explain/client/${id}`),
  summary: (siteId: number | null, hours: number) => post<AiAnswer>("/ai/summary", { site_id: siteId, hours }),
  recommend: (siteId: number | null) => post<AiAnswer>("/ai/recommend", { site_id: siteId }),
  ask: (question: string, siteId: number | null) => post<AiAnswer>("/ai/ask", { question, site_id: siteId }),
};


// ---- Firmware compliance, images, certificate ----
export interface FwPolicy { target: string | null; mode: "minimum" | "exact"; sites: Record<string, string> }
export interface FwSiteReport {
  site_id: number; site_name: string; simulated: boolean; running: string | null; running_parsed: string | null;
  target: string | null; mode: string; status: "compliant" | "outdated" | "newer" | "unknown"; ap_count: number; ap_up: number; note: string;
}
export interface FwImage { id: number; filename: string; ap_class: string; version: string; size_bytes: number; sha256: string; uploaded_by: string; uploaded_at: string }
export interface FwChange { id: number; site_id: number; kind: string; summary: string; status: string; expires_at: string; plan: Record<string, any> }  // eslint-disable-line @typescript-eslint/no-explicit-any
export const fwApi = {
  compliance: () => request<{ policy: FwPolicy; sites: FwSiteReport[] }>("/firmware/compliance"),
  setPolicy: (p: FwPolicy) => post<{ policy: FwPolicy }>("/firmware/policy", p),
  images: () => request<FwImage[]>("/firmware/images"),
  upload: (file: File) => { const f = new FormData(); f.append("file", file, file.name); return request<FwImage>("/firmware/images", { method: "POST", body: f }); },
  deleteImage: (id: number) => request<{ deleted: string }>(`/firmware/images/${id}`, { method: "DELETE" }),
  planUpgrade: (siteId: number, imageIds: number[], allowDowngrade: boolean) =>
    post<FwChange>(`/firmware/sites/${siteId}/upgrade/plan`, { image_ids: imageIds, allow_downgrade: allowDowngrade }),
  planCert: (siteId: number, file: File, passphrase: string) => {
    const f = new FormData(); f.append("file", file, file.name); f.append("passphrase", passphrase);
    return request<FwChange>(`/firmware/sites/${siteId}/certificate/plan`, { method: "POST", body: f });
  },
};
