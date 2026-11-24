// Typed client for wireless operations: cluster fleet, auto-onboarding, AP health report, client connectivity,
// diagnosis, incidents and cluster-scoped optimization proposals.
import { request } from "./api";

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const enc = encodeURIComponent;
const qs = (o: Record<string, string | number | boolean | null | undefined>) => {
  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "");
  return p.length ? "?" + p.map(([k, v]) => `${k}=${enc(String(v))}`).join("&") : "";
};

export type Tier = "GOLD" | "SILVER" | "BRONZE" | "UNRATED";
interface ClientExperience { score: number; band: string; scored: number; connected: number; tier: Tier; detail: string }
export interface RadioInfo { channel: string | null; util_percent: number | null; noise_floor_dbm: number | null; tx_power_dbm: number | null }
export interface FleetAp {
  device_id: string; name: string; serial: string; model: string | null; firmware: string | null; ip: string | null;
  mac: string | null; source_type: string; site: string | null; cluster_key: string; cluster: string | null; is_vc: boolean;
  state: string; state_reason: string; last_seen: string | null; clients: number | null; uptime_seconds: number | null;
  cpu_percent: number | null; memory_percent: number | null; radio_2g: RadioInfo; radio_5g: RadioInfo;
  exp_score: number | null; tier: Tier; condition_tier: Tier | null; condition_triggers: string[];
  client_experience: ClientExperience | null;
  event_tier: Tier | null; event_trigger: string | null; unavailable_inputs: string[]; transport: string | null; guidance: string;
}
export interface FleetCluster {
  key: string; name: string; vc_host: string | null; simulated: boolean; enabled: boolean | null; last_poll_at: string | null;
  last_poll_ok: boolean | null; last_poll_error: string | null; transport: string | null; rest_state: string | null;
  aps_total: number; aps_online: number; states: Record<string, number>; tiers: Record<string, number>; clients: number;
  util_2g_avg: number | null; util_5g_avg: number | null;
}
export interface Fleet {
  generated_at: string; clusters: FleetCluster[]; aps: FleetAp[];
  summary: { aps_total: number; states: Record<string, number>; tiers: Record<string, number>; monitored: number;
    unlinked: number; attention: number; clients: number; clusters_polled_ok: number; clusters_failing: number;
    health_report_at: string | null };
}
export interface HealthRow {
  ap: string; device_id?: string; site?: string; cluster?: string; state?: string; tier: Tier; trigger: string;
  client_experience?: ClientExperience | null;
  events: Record<string, number> | null; total_events: number | null; negative: number | null; unique_clients: number | null;
  event_tier: Tier | null; condition_tier: Tier | null; condition_triggers: string[]; unavailable_inputs: string[];
  interpretation: string; action: string; top_client?: string | null; top_reject_ssid_band?: string | null;
  reject_hours?: { hour: number; count: number }[]; clients_now?: number | null;
  util_2g_percent?: number | null; util_5g_percent?: number | null;
}
export interface HealthReport {
  id: string; generated_at: string; window_hours: number | null; window_start: string; window_end: string; scope: string;
  cluster: string | null; source: string; notes: string[]; label?: string;
  tiers: Record<Tier, number>; metrics: Record<string, number>; executive_summary: string[];
  method: { negative_categories: string[]; silver: Record<string, number>; bronze: Record<string, number>;
    condition_silver: Record<string, number>; condition_bronze: Record<string, number> };
  rows: HealthRow[]; movement?: { ap: string; from: Tier; to: Tier; direction: string }[];
  compared_to?: { id: string; label: string; generated_at: string };
}
export interface Snapshot { id: string; label: string; generated_at: string; scope: string; source: string; tiers: Record<Tier, number>; saved_by: string }
export interface Finding { id: string; severity: "high" | "medium" | "low" | "info"; title: string; why: string; actions: string[];
  owner: string; confidence: string; proposal: { operation: string; band: string; from: number | null; to: number | null; power?: number; arm_managed?: boolean; basis?: string } | null }
export interface Diagnosis { subject: Record<string, unknown>; headline: string; findings: Finding[];
  proposals: Finding["proposal"][]; method: string;
  arm?: { state: string; observed_at?: string; collection_state?: string; last_attempt_at?: string;
    transport?: string; unavailable?: string[]; errors?: Record<string,string>;
    channels: { band: string; channel: number; util_percent: number; quality_percent: number;
      retry_percent: number; noise_floor_dbm: number; interference_index: number | null }[];
    assignments: { interface: string; band: string; channel: number; power_dbm: number }[] };
  baseline?: { available: boolean; source_name?: string; scope?: string; reason?: string;
    reference_channels_5g?: number[]; observed_channels_5g?: number[];
    differences?: { setting: string; reference: unknown; observed: unknown; status: string }[] };
  ai?: { available: boolean; text?: string; model?: string; reason?: string };
  checked?: Record<string, string | number | null> }
export interface WClient {
  id: number; mac: string; hostname: string | null; ip: string | null; cluster_key: string; cluster: string; ap: string | null;
  ssid: string | null; vlan: number | null; band: string | null; channel: string | null; snr_db: number | null; rssi_dbm: number | null;
  speed_mbps: number | null; quality: string; exp_score: number | null; exp_band: string | null; connected: boolean;
  connected_seconds: number | null; first_seen: string | null; last_seen: string | null; connects: number; disconnects: number;
  roams: number; issues: string[]; simulated: boolean;
}
export interface Breakdown { name: string; clients: number; connected: number; poor: number; disconnects: number; roams: number; avg_snr_db: number | null }
export interface ClientView {
  generated_at: string; window_hours: number; clusters: { key: string; name: string }[];
  summary: { clients_seen: number; connected: number; quality: Record<string, number>; avg_snr_db: number | null;
    bands: Record<string, number>; connects: number; disconnects: number; roams: number; with_issues: number };
  by_cluster: Breakdown[]; by_ap: Breakdown[]; by_ssid: Breakdown[]; clients: WClient[]; evidence_note: string;
}
export interface Journey { id: number; mac: string; hostname: string | null; cluster: string;
  current: Record<string, string | number | boolean | null>; score: { score: number | null; band: string; inputs: Record<string, unknown>[] };
  events: { ts: string; kind: string; ap_from: string | null; ap_to: string | null; ssid: string | null }[];
  aps_visited: { ap: string; times: number }[]; window_hours: number }
export interface Incident {
  id: string; device_id: string; device: string | null; family: string | null; site: string | null; rule_id: string;
  severity: string; message: string; value: number | null; threshold: number | null; state: string; suppressed: boolean;
  entity: string | null; source_type: string; opened_at: string; last_seen_at: string; resolved_at: string | null;
  acked_by: string | null; assignee: string | null; assigned_at: string | null;
  ticket: { system: string; reference: string; url: string | null; closed?: boolean; closed_by?: string } | null;
  resolution: { type: string; by: string; at: string; note: string; ticket_needs_closure?: boolean; closure_note?: string } | null;
  recurrence_of: { id: string; resolved_by: string; resolved_at: string } | null;
  timeline: { at: string; by: string; kind: string; text: string }[]; age_minutes: number;
}
export interface Insight {
  device_id: string; family: string; state: { state: string; state_reason: string; freshness: string };
  health: { score: number | null; band: string; reasons: string[]; inputs: string[] };
  metrics: { metric: string; label: string; unit: string; value: number; observed_at: string }[];
  series_available: string[]; ap: FleetAp | null;
  clients: { id: number; mac: string; hostname: string | null; ssid: string | null; band: string | null; channel: string | null;
    snr_db: number | null; speed_mbps: number | null; quality: string | null; exp_score: number | null }[];
  client_bands?: Record<string, number>; ap_extras?: ApExtras; incidents: Incident[]; config: { captured: boolean; captured_at: string | null };
}
export interface ClusterOpt {
  cluster: FleetCluster; platform_blockers: string[];
  aps: { device_id: string; name: string; serial: string; state: string; tier: Tier; radio_2g: RadioInfo; radio_5g: RadioInfo;
    clients: number | null; enrollment: { id: string; mode: string; status: string; status_reason: string | null; requester: string;
      approver: string | null; expires_at: string | null } | null;
    headline: string; findings: Finding[]; proposals: Finding["proposal"][]; blockers: string[]; eligible: boolean }[];
  summary: { aps: number; with_proposals: number; enrolled: number; eligible: number };
}

export const wl = {
  collectArm: (id: string) => post<{state:string}>(`/v1/wireless/arm/${encodeURIComponent(id)}/collect`),
  diagnosisPlan: (id: string, finding_id: string, observed_at: string) => post<{id:string; state:string}>(`/v1/wireless/diagnose/ap/${encodeURIComponent(id)}/change-plan`, {finding_id, observed_at}),
  fleet: () => request<Fleet>("/v1/wireless/fleet"),
  scanKeys: (device_ids?: string[]) => post<{ scanned: number; results: { ip: string; fingerprint?: string; key_type?: string; error?: string; status?: string }[];
    pending: { ip: string; fingerprint: string; key_type: string; scanned_at: string; changed_from?: string | null }[] }>("/v1/wireless/autodiscover/scan-keys", { device_ids }),
  pendingKeys: () => request<{ ip: string; fingerprint: string; key_type: string; scanned_at: string; changed_from?: string | null }[]>("/v1/wireless/autodiscover/pending-keys"),
  approveKeys: (items: { ip: string; fingerprint: string }[]) => post<{ ip: string; approved: boolean; reason?: string }[]>(
    "/v1/wireless/autodiscover/approve-keys", { items, confirm: "APPROVE HOST KEYS" }),
  onboardingPolicy: () => request<{ enabled: boolean; credential_ids: string[] | null; auto_trust_new_hosts: boolean; auto_enable_rest: boolean }>("/v1/wireless/autodiscover/policy"),
  saveOnboardingPolicy: (body: { enabled: boolean; credential_ids: string[] | null; auto_trust_new_hosts: boolean; auto_enable_rest: boolean }) =>
    request("/v1/wireless/autodiscover/policy", { method: "PUT", body: JSON.stringify(body) }),
  onboardingProfiles: () => request<{ id: string; name: string; device_family: string; enabled: boolean }[]>("/v1/onboarding/credentials"),
  autodiscover: (auto_enable_rest: boolean, device_ids?: string[], credential_ids?: string[]) => post<{ state: string }>("/v1/wireless/autodiscover", { auto_enable_rest, device_ids, credential_ids, auto_trust_new_hosts: true }),
  autodiscoverStatus: () => request<{ state: string; done?: number; total?: number; clusters_saved?: number; counts?: Record<string, number>;
    clusters?: { name: string; vc_ip: string; members: number; transport: string; rest_state: string; first_poll_ok?: boolean; first_poll_error?: string | null }[];
    results?: { device_id: string; hostname: string; ip?: string; result: string; detail?: string | null }[]; error?: string;
    finished_at?: string }>("/v1/wireless/autodiscover/status"),
  report: (hours: number, cluster?: string) => request<HealthReport>(`/v1/wireless/health-report${qs({ hours, cluster })}`),
  importReport: (file: File, label: string, cluster?: string) => {
    const f = new FormData(); f.append("file", file); f.append("label", label); if (cluster) f.append("cluster", cluster);
    return request<HealthReport>("/v1/wireless/health-report/import", { method: "POST", body: f });
  },
  saveSnapshot: (hours: number, label: string, cluster?: string) => post<{ id: string; label: string }>("/v1/wireless/health-report/snapshots", { hours, label, cluster }),
  snapshots: () => request<Snapshot[]>("/v1/wireless/health-report/snapshots"),
  snapshot: (id: string, compareTo?: string) => request<HealthReport>(`/v1/wireless/health-report/snapshots/${enc(id)}${qs({ compare_to: compareTo })}`),
  deleteSnapshot: (id: string) => request<{ deleted: string }>(`/v1/wireless/health-report/snapshots/${enc(id)}`, { method: "DELETE" }),
  clients: (p: { hours: number; cluster?: string; ap?: string; ssid?: string; band?: string; quality?: string; q?: string; connected?: boolean }) =>
    request<ClientView>(`/v1/wireless/clients${qs(p)}`),
  journey: (id: number, hours: number) => request<Journey>(`/v1/wireless/clients/${id}/journey${qs({ hours })}`),
  diagnoseAp: (id: string, ai = false) => request<Diagnosis>(`/v1/wireless/diagnose/ap/${enc(id)}${qs({ ai })}`),
  diagnoseClient: (id: number, ai = false) => request<Diagnosis>(`/v1/wireless/diagnose/client/${id}${qs({ ai })}`),
  insight: (id: string) => request<Insight>(`/v1/wireless/devices/${enc(id)}/insight`),
  clusterOpt: (key: string) => request<ClusterOpt>(`/v1/wireless/optimization/cluster/${enc(key)}`),
  clusterPropose: (cluster: string, mode: string, device_ids?: string[]) =>
    post<{ proposals: number; note: string }>("/v1/wireless/optimization/cluster-proposal", { cluster, mode, device_ids }),
};

export const inc = {
  list: (p: { state: string; assignee?: string; mine?: boolean; unassigned?: boolean; severity?: string }) =>
    request<Incident[]>(`/v1/incidents${qs(p)}`),
  summary: () => request<{ open: number; unassigned: number; mine: number; with_ticket: number; auto_recovered: number;
    manually_resolved: number; tickets_to_close: number; recurrences: number }>("/v1/incidents/summary"),
  assignees: () => request<{ username: string; role: string }[]>("/v1/incidents/assignees"),
  assign: (id: string, assignee: string | null, note = "") => post<Incident>(`/v1/incidents/${enc(id)}/assign`, { assignee, note }),
  ticket: (id: string, system: string, reference: string, url?: string) => post<Incident>(`/v1/incidents/${enc(id)}/ticket`, { system, reference, url: url || null }),
  note: (id: string, text: string) => post<Incident>(`/v1/incidents/${enc(id)}/note`, { text }),
  resolve: (id: string, resolution: string, note: string, ticket_reference?: string) =>
    post<Incident>(`/v1/incidents/${enc(id)}/resolve`, { resolution, note, ticket_reference: ticket_reference || null }),
  reopen: (id: string, text: string) => post<Incident>(`/v1/incidents/${enc(id)}/reopen`, { text }),
};

export const TIER_TONE = { GOLD: "good", SILVER: "fair", BRONZE: "bronze", UNRATED: "none" } as const;
export const STATE_TONE = (s: string): "good" | "fair" | "poor" | "none" =>
  s === "ONLINE" ? "good" : s === "OFFLINE" ? "poor" : s === "DEGRADED" || s === "STALE" ? "fair" : "none";
export const pct = (v: number | null | undefined) => v == null ? "—" : `${Math.round(v)}%`;

export type ApExtras = { observed_at: string | null; memory_free_mib: number | null; memory_basis: string | null;
  uplink: { interface: string | null; rx_bps: number | null; tx_bps: number | null; quality: string; interval_s?: number };
  neighbors: { aps: { total: number | null; by_band?: Record<string, number>; by_type?: Record<string, number>; reason?: string };
    stations: { total: number | null; by_band?: Record<string, number>; reason?: string }; source: string };
  signal_distribution: { by_band: Record<string, Record<string, number>>; source: string };
  client_match: { state: string; reason: string }; gaps: string[]; format_verification: string };
