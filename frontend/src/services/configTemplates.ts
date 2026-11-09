import { request } from "./api";

export type TemplateScope = "global" | "group" | "site" | "device";
export type TemplateFamily = "access_point" | "switch";
export type TemplateDoc = {
  vlans: number[];
  ssids?: { profile: string; essid: string; vlan: number | null; rf_band: "all" | "2.4" | "5.0";
    channel_width_mhz: 20 | 40; enabled: boolean }[];
  interfaces?: { name: string; admin_state: "up" | "down"; access_vlan: number | null; trunk_vlans: number[] }[];
  port_roles?: Record<string, Record<string, unknown>>;
  port_assignments?: { ports: string; role: string }[];
  system?: { ntp?: string[]; dns?: string[]; dns_domain?: string; syslog?: string[] };
};
export type Template = {
  id: string; name: string; scope: TemplateScope; family: TemplateFamily;
  site_id: string | null; device_id: string | null; group_id?: string | null; model_contains: string | null;
  priority: number; config: TemplateDoc; enabled: boolean; revision: number;
};
export type Preview = {
  device_id: string; family: TemplateFamily; source_type: string; operation?: string;
  templates: { id: string; name: string; scope: TemplateScope; priority: number; revision: number }[];
  effective: TemplateDoc; provenance: Record<string, string>; applied_to_hardware: false; note: string;
};

export const templateApi = {
  list: () => request<Template[]>("/v1/config-templates"),
  create: (payload: Omit<Template, "id" | "revision">) =>
    request<Template>("/v1/config-templates", { method: "POST", body: JSON.stringify(payload) }),
  update: (id: string, expected_revision: number, patch: { config?: TemplateDoc; enabled?: boolean; priority?: number }) =>
    request<Template>("/v1/config-templates/" + encodeURIComponent(id),
      { method: "PATCH", body: JSON.stringify({ expected_revision, ...patch }) }),
  preview: (device: string) => request<Preview>(
    "/v1/config-templates/devices/" + encodeURIComponent(device) + "/preview"),
  planPreview: (device: string) => request<{ operations: Record<string, unknown>[] | null; reason: string | null;
    certification: Record<string, string>; port_roles: Record<string, string>; templates: { name: string; scope: string }[] }>(
    "/v1/config-templates/devices/" + encodeURIComponent(device) + "/plan-preview"),
  saveDraft: (device: string) => request<{draft_id: string; state: "DRAFT"; hardware_writes: string}>(
    "/v1/config-templates/devices/" + encodeURIComponent(device) + "/draft", {method: "POST"}),
};
