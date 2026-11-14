import { Link } from "react-router-dom";
import BulkDeviceImport from "../components/BulkDeviceImport";
import { Card } from "../components/ui";
import { PageHeader } from "../components/viz";
import { getSession } from "../services/api";

export default function InventoryImport() {
  const admin = getSession()?.role === "admin";
  return <div className="space-y-4">
    <PageHeader eyebrow="Sites & inventory" title="Import Aruba inventory"
      subtitle="Upload an AP or switch export, review sites and management addresses, then confirm the import." />
    <Link className="text-sm text-blue-700 underline" to="/inventory">Open Device Inventory</Link>
    {admin ? <Card title="Upload and review CSV">
      <BulkDeviceImport />
    </Card> : <Card>Administrator access is required to import devices.</Card>}
  </div>;
}
