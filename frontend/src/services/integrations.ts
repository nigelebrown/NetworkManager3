import { request } from "./api";

const B = "/v1/integrations";
const post = <T,>(p: string, b: unknown = {}) => request<T>(p, { method: "POST", body: JSON.stringify(b) });

export type Connection = { id: string; name: string; kind: string; kind_label: string; target: string; config: Record<string, unknown>;
  secret_configured: boolean; min_severity: string; include_demo: boolean; enabled: boolean; health: string;
  last_test_at: string | null; last_test_ok: boolean | null; created_by: string;
  deliveries: { by_state: Record<string, number>; last_delivery_at: string | null; last_error: string | null } };
export type ApiTokenRow = { id: string; name: string; prefix: string; scopes: string[]; owner: string; created_at: string;
  expires_at: string; last_used_at: string | null; last_used_ip: string | null; state: string };
export type Delivery = { id: string; kind: string; state: string; attempts: number; last_error: string | null; created_at: string | null; message?: string };
export type ConnectionIn = { name: string; kind: string; target: string; config: Record<string, unknown>; secret?: string;
  clear_secret?: boolean; min_severity: string; include_demo: boolean; enabled: boolean };

export const integ = {
  overview: () => request<{ connections: Connection[]; kinds: Record<string, string>; api_tokens: ApiTokenRow[];
    monitoring: { metrics_enabled: boolean; path: string; auth: string }; policy: string }>(B),
  create: (b: ConnectionIn) => post<Connection>(`${B}/connections`, b),
  update: (id: string, b: ConnectionIn) => request<Connection>(`${B}/connections/${id}`, { method: "PUT", body: JSON.stringify(b) }),
  action: (id: string, a: "enable" | "disable" | "test") => post<Connection & { delivered?: boolean; error?: string | null; connection?: Connection }>(`${B}/connections/${id}/${a}`),
  remove: (id: string) => request<void>(`${B}/connections/${id}`, { method: "DELETE" }),
  deliveries: (id: string) => request<Delivery[]>(`${B}/connections/${id}/deliveries`),
  retry: (id: string) => post<Delivery>(`${B}/deliveries/${id}/retry`),
  audit: () => request<{ at: string; action: string; by: string; target: string; detail: string; success: boolean }[]>(`${B}/audit`),
  createToken: (b: { name: string; scopes: string[]; days: number }) => post<ApiTokenRow & { token: string; note: string }>(`${B}/api-tokens`, b),
  revokeToken: (id: string) => request<ApiTokenRow>(`${B}/api-tokens/${id}`, { method: "DELETE" }),
  monitoring: (metrics_enabled: boolean) => request<{ metrics_enabled: boolean }>(`${B}/monitoring`, { method: "PUT", body: JSON.stringify({ metrics_enabled }) }),
};
