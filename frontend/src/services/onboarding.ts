import { request } from "./api";

export type CredentialProfile = {
  id: string; name: string; device_family: "access_point" | "switch";
  protocol: string; username: string; description: string | null; enabled: boolean;
  has_password: boolean; created_by: string; created_at: string | null;
  last_test_status: string | null; last_tested_at: string | null;
};
export type FoundDevice = {
  management_ip: string; status: "FOUND" | "FAILED"; family?: "access_point" | "switch";
  hostname?: string; model?: string; serial_number?: string; firmware?: string | null;
  existing_device_id?: string | null; enrollable: boolean; result_note: string;
};
export type DiscoveryJob = {
  id: string; status: string; results: FoundDevice[];
  site_name: string; credential_id: string; device_writes: "LOCKED";
};
export type Registered = {
  registered: { device_id: string; hostname: string; serial_number: string; config_status: string }[];
  device_writes: "LOCKED"; next_step: string;
};
export type DeviceAcceptance = {
  device_id: string; family: "access_point" | "switch"; reported_model: string | null;
  recognized_pilot_model: string | null; reported_firmware: string | null;
  inventory_evidence: "REST_DISCOVERY_RECORDED" | "MANUAL_ENTRY";
  last_observed_at: string | null;
  credential: { bound: boolean; enabled: boolean; profile_id: string | null; last_test_status: string | null };
  baseline: { id: string | null; captured_at: string | null;
    source: string | null; completeness: string; warning: string | null };
  stage: "MANUAL_REGISTERED" | "REST_DISCOVERED" | "BASELINE_CAPTURED" | "DRAFTED";
  draft_count: number; field_checks: {field: string; status: "OBSERVED" | "NOT_VERIFIED"; detail: string}[];
  operator_pilot_eligible: boolean; hardware_certified: false;
  device_writes: "LOCKED" | "UNSAFE"; remaining_actions: string[];
};
export type FleetAcceptance = {
  device_count: number; limit: number; hardware_certified: 0; device_writes: "LOCKED" | "UNSAFE";
  stages: Record<string, number>;
  devices: {device_id: string; family: string; reported_model: string | null;
    recognized_pilot_model: string | null; inventory_evidence: string;
    stage: string; hardware_certified: false}[];
};

export type ConfigData = {
  device_id: string; credential_id: string | null; hardware_writes: "LOCKED";
  baseline: {
    id: string; captured_at: string; completeness: string;
    config: { vlans?: (number | { id: number; name: string })[];
      ssids?: Record<string, unknown>[]; interfaces?: Record<string, unknown>[] };
    warning: string | null;
  } | null;
  drafts: { id: string; state: "DRAFT"; created_at: string;
    proposed: Record<string, unknown> }[];
};

const enc = (v: string) => encodeURIComponent(v);
const opts = (obj: unknown) => ({ method: "POST", body: JSON.stringify(obj) });
export const onboardingApi = {
  credentials: () => request<CredentialProfile[]>("/v1/onboarding/credentials"),
  acceptance: (id: string) => request<DeviceAcceptance>(`/v1/onboarding/devices/${enc(id)}/acceptance`),
  fleetAcceptance: () => request<FleetAcceptance>("/v1/onboarding/acceptance"),
  createCredential: (body: {
    name: string; device_family: "access_point" | "switch"; username: string;
    password: string; description?: string | null;
  }) => request<CredentialProfile>("/v1/onboarding/credentials", opts(body)),
  rotateCredential: (id: string, body: { password: string; username?: string }) =>
    request<CredentialProfile>(`/v1/onboarding/credentials/${enc(id)}/rotate`, opts(body)),
  disableCredential: (id: string) =>
    request<CredentialProfile>(`/v1/onboarding/credentials/${enc(id)}/disable`, opts({})),
  testCredential: (id: string, ip: string) =>
    request<{ status: string; model: string | null; hostname: string | null }>(
      `/v1/onboarding/credentials/${enc(id)}/test`, opts({ management_ip: ip })),
  discover: (body: { credential_id: string; scope: string; site_name: string }) =>
    request<DiscoveryJob>("/v1/onboarding/discover", opts(body)),
  discovery: (id: string) => request<DiscoveryJob>(`/v1/onboarding/discoveries/${enc(id)}`),
  register: (id: string, indices: number[]) =>
    request<Registered>(`/v1/onboarding/discoveries/${enc(id)}/register`, opts({ indices })),
  capture: (id: string, credential_id?: string) =>
    request<{ snapshot_id: string; completeness: string; config: Record<string, unknown>;
      warning: string | null; device_writes: "LOCKED" }>(
      `/v1/onboarding/devices/${enc(id)}/capture`, opts({ credential_id: credential_id || null })),
  deviceConfig: (id: string) =>
    request<ConfigData>(`/v1/onboarding/devices/${enc(id)}/config`),
  saveDraft: (id: string, proposed: Record<string, unknown>) =>
    request<{ id: string; state: "DRAFT"; hardware_writes: "LOCKED" }>(
      `/v1/onboarding/devices/${enc(id)}/drafts`, opts({ proposed })),
};
