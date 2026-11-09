import { request } from "./api";

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
const enc = encodeURIComponent;

export type WfStep = { step: string; status: "done" | "current" | "pending" };
export type WfDevice = { device_id: string; hostname: string | null; serial_number: string; model: string | null;
  firmware: string | null; source_type: string; state: string; state_reason: string; monitored: boolean };
export type WfPlan = { plan_id: string; device_id: string; hostname: string | null; state: string; operations: number;
  approver: string | null; execution: { id: string; outcome: string; environment: string } | null };
export type FoundRow = { management_ip: string; status: string; hostname?: string; model?: string;
  serial_number?: string; firmware?: string | null; existing_device_id?: string | null; enrollable: boolean;
  result_note: string };
export type Progress = { total: number; done: number; percent: number; found: number; failed: number; errors: number;
  attempts: number; lease_owner: string | null; heartbeat_at: string | null };
export type Workflow = {
  id: string; title: string; state: string; step: string; mode: string | null;
  site: { id: string | null; name: string | null }; device_family: "switch" | "access_point";
  credential_id: string | null; steps: WfStep[];
  discovery: { id: string; status: string; results: FoundRow[]; progress: Progress | null } | null;
  devices: WfDevice[]; plans: WfPlan[]; plan_progress: Record<string, number>;
  template_scope: { group_id?: string | null }; outcome: Record<string, any>;
  history: { at: string; actor: string; step: string; detail: string }[];
};
export type DevicePreview = { device_id: string; hostname: string | null; templates: { name: string; scope: string }[];
  operations: Record<string, unknown>[] | null; reason: string | null; certification: Record<string, string>;
  port_roles: Record<string, string>; provenance: Record<string, string> };
export type Capability = { operation: string; software: string; certification: string; executable_now: boolean;
  reason: string };
export type Capabilities = { device_id: string; adapter: string; adapter_implemented: boolean; model_family: string;
  firmware_train: string; environment: string; hardware_certified: boolean;
  write_posture: { hardware_writes: string; global_write_lock: boolean; config_changes_allowed: boolean };
  operations: Capability[]; read_operations: { op: string; status: string; reason?: string }[] };
export type Journal = { id: string; execution_id: string; plan_id: string; device_id: string; environment: string;
  state: string; strategy: string; last_step: string | null; owner: string | null; heartbeat_at: string | null;
  created_at: string | null; resolved_by: string | null; notes: string | null };
export type Group = { id: string; name: string; family: string; description: string | null; members: string[] };
export type Batch = { batch_id: string; plans: WfPlan[]; progress: Record<string, number> };
export type SeriesPoint = { t: string; v: number | null; q: string; src: string };

export const wf = {
  list: () => request<{ id: string; title: string; step: string; state: string; site: string; devices: number;
    updated_at: string }[]>("/v1/workflows"),
  get: (id: string) => request<Workflow>(`/v1/workflows/${enc(id)}`),
  start: (b: { title: string; device_family: string; site_name?: string; site_id?: string }) => post<Workflow>("/v1/workflows", b),
  credential: (id: string, credential_id: string) => post<Workflow>(`/v1/workflows/${enc(id)}/credential`, { credential_id }),
  discover: (id: string, scope: string, rate_per_s = 16) => post<Workflow>(`/v1/workflows/${enc(id)}/discover`, { scope, rate_per_s }),
  register: (id: string, indices: number[]) => post<Workflow>(`/v1/workflows/${enc(id)}/register`, { indices }),
  attach: (id: string, device_ids: string[]) => post<Workflow>(`/v1/workflows/${enc(id)}/attach`, { device_ids }),
  baseline: (id: string) => post<Workflow>(`/v1/workflows/${enc(id)}/baseline`),
  templates: (id: string, b: { group_id?: string | null; new_group_name?: string | null }) =>
    post<Workflow>(`/v1/workflows/${enc(id)}/templates`, b),
  preview: (id: string) => request<{ devices: DevicePreview[] }>(`/v1/workflows/${enc(id)}/preview`),
  decide: (id: string, mode: "NOW" | "LATER") => post<Workflow>(`/v1/workflows/${enc(id)}/decide`, { mode }),
  execute: (id: string) => post<Workflow>(`/v1/workflows/${enc(id)}/execute`),
  monitor: (id: string) => post<Workflow>(`/v1/workflows/${enc(id)}/monitor`),
  resume: (id: string) => post<Workflow>(`/v1/workflows/${enc(id)}/resume`),
  cancel: (id: string) => post<Workflow>(`/v1/workflows/${enc(id)}/cancel`),
  discoveryControl: (jobId: string, action: "pause" | "resume" | "cancel" | "retry") =>
    post(`/v1/onboarding/discoveries/${enc(jobId)}/control/${action}`),
};

export type NotifyChannel = { id: string; name: string; kind: "webhook" | "email" | "syslog"; target: string;
  min_severity: string; escalate_after_minutes: number | null; escalation_channel_id: string | null;
  include_demo: boolean; enabled: boolean; created_by: string; created_at: string | null };
export type NotifyDelivery = { id: string; channel_id: string; alert_id: string | null; kind: string; state: string;
  attempts: number; last_error: string | null; correlation_id: string | null; created_at: string | null;
  next_attempt_at: string | null; sent_at: string | null; message: string | null; severity: string | null };

export const notify = {
  channels: () => request<NotifyChannel[]>("/v1/notifications/channels"),
  create: (b: Partial<NotifyChannel>) => post<NotifyChannel>("/v1/notifications/channels", b),
  remove: (id: string) => request<void>(`/v1/notifications/channels/${enc(id)}`, { method: "DELETE" }),
  test: (id: string) => post<{ delivered: boolean; error: string | null }>(`/v1/notifications/channels/${enc(id)}/test`),
  deliveries: (state?: string) => request<NotifyDelivery[]>(`/v1/notifications/deliveries${state ? `?state=${enc(state)}` : ""}`),
  retry: (id: string) => post<NotifyDelivery>(`/v1/notifications/deliveries/${enc(id)}/retry`),
};

export const rem = {
  capabilities: (id: string) => request<Capabilities>(`/v1/devices/${enc(id)}/capabilities`),
  matrix: () => request<{ adapters: Record<string, any>[]; certifications: { adapter: string; model_family: string;
    firmware_train: string; environment: string; operations: Record<string, string> }[]; note: string }>("/v1/capability-matrix"),
  portSeries: (id: string, port: string, metric: string, hours = 6) =>
    request<{ points: SeriesPoint[] }>(`/v1/devices/${enc(id)}/ports/${port}/series?metric=${enc(metric)}&hours=${hours}`),
  journal: (state?: string) => request<Journal[]>(`/v1/recovery/journal${state ? `?state=${enc(state)}` : ""}`),
  recover: (id: string) => post<{ recovered: boolean; steps: string[]; error?: string; journal: Journal }>(`/v1/recovery/journal/${enc(id)}/recover`),
  resolve: (id: string, note: string) => post<Journal>(`/v1/recovery/journal/${enc(id)}/resolve`, { note }),
  guided: (id: string) => request<{ automatic: boolean; summary: string; steps: string[] }>(`/v1/devices/${enc(id)}/guided-restore`),
  groups: () => request<Group[]>("/v1/device-groups"),
  createGroup: (b: { name: string; family: string; description?: string }) => post<Group>("/v1/device-groups", b),
  addMembers: (id: string, device_ids: string[]) => post<Group>(`/v1/device-groups/${enc(id)}/members`, { device_ids }),
  deploy: (b: { group_id?: string; device_ids?: string[] }) =>
    post<{ batch_id: string; devices: { device_id: string; plan_id: string | null; in_compliance?: boolean; error?: string }[] }>("/v1/deployments", b),
  batch: (id: string) => request<Batch>(`/v1/deployments/${enc(id)}`),
  executeBatch: (id: string, stop_on_failure = true) =>
    post<{ executed: { plan_id: string; outcome: string | null; decision: string; denial_reason: string | null }[];
      stopped_at: string | null; progress: Record<string, number> }>(`/v1/deployments/${enc(id)}/execute`, { stop_on_failure }),
  topology: () => request<{ nodes: { id: string; label: string; family: string; model: string | null; source_type: string | null; inventory: boolean }[];
    edges: { from: string; to: string; local_port: string; remote_port: string | null; link: string; poe_w: number | null }[]; note: string }>("/v1/topology"),
  relatedChanges: (alertId: string) => request<{ candidates: { plan_id: string; title: string | null; same_device: boolean; outcome: string;
    environment: string; minutes_before_alert: number; operations: string[] }[]; method: string; window_minutes: number }>(`/v1/alerts/${enc(alertId)}/related-changes`),
  outbox: () => request<{ pending: number; delivered: number; dlq: number; oldest_pending_at: string | null; mode: string }>("/v1/outbox/status"),
};
