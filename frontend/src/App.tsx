import InventoryImport from "./pages/InventoryImport";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { getSession } from "./services/api";
import Alerts from "./pages/Alerts";
import Assistant from "./pages/Assistant";
import APs from "./pages/APs";
import Clients from "./pages/Clients";
import Configuration from "./pages/Configuration";
import Firmware from "./pages/Firmware";
import Dashboard from "./pages/Dashboard";
import Experience from "./pages/Experience";
import Login from "./pages/Login";
import Sites from "./pages/Sites";
import AiLearning from "./pages/AiLearning";
import Audit from "./pages/Audit";
import ChangePassword from "./pages/ChangePassword";
import Logs from "./pages/Logs";
import DeviceIntake from "./pages/DeviceIntake";
import GuidedOnboarding from "./pages/GuidedOnboarding";
import DeviceConfig from "./pages/DeviceConfig";
import ConfigTemplates from "./pages/ConfigTemplates";
import Phase4Insights from "./pages/Phase4Insights";
import DiscoveryRanges from "./pages/DiscoveryRanges";
import { Printers, PrinterDetails } from "./pages/Printers";
import DeviceDetails from "./pages/DeviceDetails";
import Recovery from "./pages/Recovery";
import Switches from "./pages/Switches";
import SystemHealth from "./pages/SystemHealth";
import Users from "./pages/Users";
import NocDashboard from "./pages/NocDashboard";
import ExecutiveDashboard from "./pages/ExecutiveDashboard";
import Compliance from "./pages/Compliance";
import Certification from "./pages/Certification";
import Deployments from "./pages/Deployments";
import Notifications from "./pages/Notifications";
import Topology from "./pages/Topology";
import DeviceAlerts from "./pages/DeviceAlerts";
import Device360 from "./pages/Device360";
import DeviceInventory from "./pages/DeviceInventory";
import Collection from "./pages/Collection";
import ChangePlans from "./pages/ChangePlans";
import Vault from "./pages/Vault";
import Accountability from "./pages/Accountability";
import Replacement from "./pages/Replacement";
import LearningCentre from "./pages/LearningCentre";
import Optimization from "./pages/Optimization";
import Platform from "./pages/Platform";
import DataManagement from "./pages/DataManagement";
import WireframeWorkspace from "./pages/WireframeWorkspaces";

function Protected({ children }: { children: JSX.Element }) {
  const s = getSession();
  if (!s || new Date(s.expires_at) < new Date()) return <Navigate to="/login" replace />;
  if (s.must_change_password) return <Navigate to="/change-password" replace />;
  return children;
}

// Evaluated at render time (not when <App> first rendered), so a fresh login is seen immediately.
function WithSession({ children }: { children: JSX.Element }) {
  return getSession() ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/change-password" element={<WithSession><ChangePassword /></WithSession>} />
      <Route element={<Protected><Layout /></Protected>}>
        {/* Preserved SIMS screens (Phase 1 parity) */}
        <Route index element={<NocDashboard />} />
        <Route path="wifi" element={<Dashboard />} />
        <Route path="aps" element={<APs />} />
        <Route path="clients" element={<Clients />} />
        <Route path="experience" element={<Experience />} />
        <Route path="assistant" element={<Assistant />} />
        <Route path="config" element={<Configuration />} />
        <Route path="firmware" element={<Firmware />} />
        <Route path="sites" element={<Sites />} />
        <Route path="inventory" element={<DeviceInventory />} />
        <Route path="alerts" element={<Alerts />} />
        {/* INM additions */}
        <Route path="system" element={<SystemHealth />} />
        <Route path="switches" element={<Switches />} />
        <Route path="logs" element={<Logs />} />
        <Route path="audit" element={<Audit />} />
        <Route path="users" element={<Users />} />
        <Route path="recovery" element={<Recovery />} />
        <Route path="ai-learning" element={<AiLearning />} />
        <Route path="phase4-insights" element={<Phase4Insights />} />
        <Route path="onboarding" element={<GuidedOnboarding />} />
        <Route path="onboarding/import" element={<InventoryImport />} />
        <Route path="onboarding/manual" element={<DeviceIntake />} />
        <Route path="devices/:id/config" element={<DeviceConfig />} />
        <Route path="config-templates" element={<ConfigTemplates />} />
        <Route path="discovery" element={<DiscoveryRanges />} />
        <Route path="printers" element={<Printers />} />
        <Route path="printers/:id" element={<PrinterDetails />} />
        <Route path="devices/:id" element={<DeviceDetails />} />
        <Route path="changes" element={<ChangePlans />} />
        <Route path="executive" element={<ExecutiveDashboard />} />
        <Route path="compliance" element={<Compliance />} />
        <Route path="certification" element={<Certification />} />
        <Route path="deployments" element={<Deployments />} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="topology" element={<Topology />} />
        <Route path="device-alerts" element={<DeviceAlerts />} />
        <Route path="devices/:id/360" element={<Device360 />} />
        <Route path="collection" element={<Collection />} />
        <Route path="vault" element={<Vault />} />
        <Route path="accountability" element={<Accountability />} />
        <Route path="replace" element={<Replacement />} />
        <Route path="learning" element={<LearningCentre />} />
        <Route path="optimization" element={<Optimization />} />
        <Route path="platform" element={<Platform />} />
        <Route path="data" element={<DataManagement />} />
        {/* Approved full-system wireframe workspaces: reuse existing APIs and change safeguards. */}
        <Route path="rf-health" element={<WireframeWorkspace kind="rf" />} />
        <Route path="port-health" element={<WireframeWorkspace kind="ports" />} />
        <Route path="client-journeys" element={<WireframeWorkspace kind="journeys" />} />
        <Route path="approvals" element={<WireframeWorkspace kind="approvals" />} />
        <Route path="recommendations" element={<WireframeWorkspace kind="recommendations" />} />
        <Route path="predictive" element={<WireframeWorkspace kind="predictive" />} />
        <Route path="analytics" element={<WireframeWorkspace kind="analytics" />} />
        <Route path="credentials" element={<WireframeWorkspace kind="credentials" />} />
        <Route path="integrations" element={<WireframeWorkspace kind="integrations" />} />
        <Route path="switch-config" element={<WireframeWorkspace kind="switchconfig" />} />
        <Route path="ssid-policies" element={<WireframeWorkspace kind="ssid" />} />
        <Route path="device-360" element={<WireframeWorkspace kind="device360" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
