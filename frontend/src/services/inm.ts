// Typed client for the versioned INM API (/api/v1). Legacy pages keep using services/api.ts unchanged.
import { request } from "./api";

export interface ServiceCheck { name: string; role: string; status: "UP" | "DEGRADED" | "DOWN" | "UNKNOWN"; latency_ms: number | null; version: string | null; detail: string; checked_at: string }
export interface Services { overall: "UP" | "DEGRADED" | "DOWN"; checked_at: string; services: ServiceCheck[]; websocket_gateway: { bus_connected: boolean; clients: number; cursor: number } }
export interface Trace {
  correlation_id: string; complete: boolean;
  management_db: { event_id: string; event_type: string; ingested_at: string; delivery_count: number; source_type: string }[];
  timeseries_db: { observed_at: string; metric_name: string; value: number | null; source_event_id: string }[];
  loki: { ts: string; line: string }[]; loki_error?: string;
  websocket: { seq: number; delivered_to_clients: number; topics: string[] } | null;
}
export interface Capability { operation: string; status: string; evidence: string | null }
export interface Device {
  id: string; source_type: "DEMO" | "REAL"; environment: string; vendor: string; family: "access_point" | "switch";
  model: string | null; serial_number: string; hostname: string | null; firmware: string | null; status: string; adapter: string;
  site_id: string | null; cluster_id: string | null; legacy_site_id: number | null; last_observed_at: string | null; capabilities?: Capability[];
}
export interface MetricPoint { observed_at: string; metric_name: string; value: number | null; unit: string; quality: string; source_type: string }
export interface EventRow { event_id: string; event_type: string; occurred_at: string; ingested_at: string; correlation_id: string; source_type: string; source_service: string; actor: string; summary: string | null; delivery_count: number; legacy_site_id: number | null }
export interface LogEntry { ts: string; labels: Record<string, string>; line: string; json: Record<string, unknown> | null; device?: Device | null; correlation?: "MAPPED" | "UNMAPPED"; unmapped_reason?: string | null }
export interface AuditRow { id: number; ts: string; actor: string | null; actor_role: string | null; actor_kind: string | null; action: string; target: string | null; detail: string | null; outcome: string; source_ip: string | null; request_id: string | null; correlation_id: string | null; session_id_hash: string | null; channel: string; prev_hash: string | null; event_hash: string | null }
export interface ChainResult { ok: boolean; checked: number; legacy_unchained: number; head?: string; broken_at_id?: number | null; reason?: string }
export interface Rec { id: string; created_at: string; site: string | null; engine: string; rule_id: string; rule_version: string; source_type: string; risk: string; title: string; explanation: string; evidence: Record<string, unknown>; input_window: string; approval_state: string; requested_by: string; trained_model: false }
export interface Snapshot { id: number; site_id: number; taken_at: string; kind: string; sha256: string; classification: string; restorable: false; reason: string }
export interface Recovery { vault: { status: string; phase: number; detail: string }; replacement_wizard: { status: string; phase: number }; restore_candidates: unknown[]; sanitized_snapshots: Snapshot[] }
export interface UserRow { id: number; username: string; role: string; is_active: boolean; site_scope: "all" | "granted"; site_grants: number[]; must_change_password: boolean; created_at: string }
export interface ReleaseRow { id: number; ts: string; operation: string; operator: string; version: string; git_commit: string | null; environment: string; outcome: string; detail: string | null }
export interface Features { device_writes: "LOCKED" | "ENABLED"; modules: { key: string; status: string; phase?: number; note?: string }[] }

const j = (body: unknown) => ({ body: JSON.stringify(body) });
export const v1 = {
  services: () => request<Services>("/v1/system/services"),
  features: () => request<Features>("/v1/system/features"),
  synthetic: () => request<{ correlation_id: string; event_id: string }>("/v1/system/synthetic-event", { method: "POST", ...j({}) }),
  trace: (cid: string) => request<Trace>(`/v1/system/synthetic-event/${cid}`),
  devices: (family?: "access_point" | "switch") => request<Device[]>(`/v1/devices${family ? `?family=${family}` : ""}`),
  metrics: (id: string, hours = 1) => request<{ points: MetricPoint[] }>(`/v1/devices/${id}/metrics?hours=${hours}`),
  events: (limit = 100) => request<EventRow[]>(`/v1/events?limit=${limit}`),
  logs: (stream: "events" | "syslog", contains: string, minutes: number) =>
    request<{ query: string; entries: LogEntry[] }>(`/v1/logs?stream=${stream}&minutes=${minutes}&contains=${encodeURIComponent(contains)}`),
  audit: (action?: string) => request<AuditRow[]>(`/v1/audit/events?limit=300${action ? `&action=${encodeURIComponent(action)}` : ""}`),
  verifyAudit: () => request<ChainResult>("/v1/audit/verify"),
  releases: () => request<ReleaseRow[]>("/v1/release/events"),
  analyze: () => request<{ created: number; engine: string; ruleset_version: string }>("/v1/ai/analyze", { method: "POST" }),
  recommendations: () => request<Rec[]>("/v1/recommendations"),
  recovery: () => request<Recovery>("/v1/recovery/snapshots"),
  users: () => request<UserRow[]>("/v1/users"),
  createUser: (b: { username: string; role: string; temporary_password: string; site_scope: string }) => request<UserRow>("/v1/users", { method: "POST", ...j(b) }),
  patchUser: (id: number, b: Partial<Pick<UserRow, "role" | "is_active" | "site_scope">>) => request<UserRow>(`/v1/users/${id}`, { method: "PATCH", ...j(b) }),
  setGrants: (id: number, site_ids: number[]) => request<UserRow>(`/v1/users/${id}/grants`, { method: "PUT", ...j({ site_ids }) }),
  resetPassword: (id: number, temporary_password: string) => request<{ ok: boolean }>(`/v1/users/${id}/reset-password`, { method: "POST", ...j({ temporary_password }) }),
};

export async function downloadAuditCsv(token: string) {
  const r = await fetch("/api/v1/audit/export", { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new Error(`Export failed (HTTP ${r.status})`);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = `inm-audit-${new Date().toISOString().slice(0, 19)}.csv`; a.click();
  URL.revokeObjectURL(url);
}
