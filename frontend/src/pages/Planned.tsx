import { PlannedNotice } from "../components/inm";

export function Onboarding() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Onboarding</h1>
      <PlannedNotice phase={2} title="Credential profiles and IP/CIDR discovery (read-only)">
        Vendor or auto-detect, approved ranges only, credential check, identity/serial de-duplication, site assignment and review.
        First targets: Aruba Instant AP-505/AP-535 and CX 6100/6200 on approved lab devices. Until then, legacy single-cluster
        registration remains on the Sites page (live mode is disabled in the Phase 1 demo profile).
      </PlannedNotice>
    </div>
  );
}

export function ChangeControl() {
  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Change control</h1>
      <PlannedNotice phase={6} title="Templates, independent approval and audited writes">
        Global → vendor/model → site → device inheritance, four-eyes approval (self-approval denied), verified pre/post backups,
        device locks, readback and rollback evidence. RF policy allows 20/40 MHz only; 80 MHz is blocked.
        The Configuration page can already read and plan SSID/VLAN/AP-name changes; applying them is locked in this release.
      </PlannedNotice>
    </div>
  );
}
