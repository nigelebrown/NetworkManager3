import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router";
import Layout from "./components/Layout";
import { getSession } from "./services/api";
import Login from "./pages/Login";
import ChangePassword from "./pages/ChangePassword";
const PortGroups = lazy(() => import("./pages/PortGroups"));
const Optics = lazy(() => import("./pages/Optics"));
const InventoryImport = lazy(() => import("./pages/InventoryImport"));
const Alerts = lazy(() => import("./pages/Alerts"));
const Assistant = lazy(() => import("./pages/Assistant"));
const APs = lazy(() => import("./pages/APs"));
const ApFleet = lazy(() => import("./pages/ApFleet"));
const ApHealthReport = lazy(() => import("./pages/ApHealthReport"));
const ClientConnectivity = lazy(() => import("./pages/ClientConnectivity"));
const DiscoveryRules = lazy(() => import("./pages/DiscoveryRules"));
const Clients = lazy(() => import("./pages/Clients"));
const Configuration = lazy(() => import("./pages/Configuration"));
const Firmware = lazy(() => import("./pages/Firmware"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Experience = lazy(() => import("./pages/Experience"));
const Sites = lazy(() => import("./pages/Sites"));
const AiLearning = lazy(() => import("./pages/AiLearning"));
const Audit = lazy(() => import("./pages/Audit"));
const Logs = lazy(() => import("./pages/Logs"));
const DeviceIntake = lazy(() => import("./pages/DeviceIntake"));
const GuidedOnboarding = lazy(() => import("./pages/GuidedOnboarding"));
const DeviceConfig = lazy(() => import("./pages/DeviceConfig"));
const ConfigTemplates = lazy(() => import("./pages/ConfigTemplates"));
const Phase4Insights = lazy(() => import("./pages/Phase4Insights"));
const DiscoveryRanges = lazy(() => import("./pages/DiscoveryRanges"));
const PrinterConsumables = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterConsumables })));
const PrinterInventory = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterInventory })));
const PrinterLive = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterLive })));
const PrinterMaintenance = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterMaintenance })));
const PrinterModels = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterModels })));
const PrinterOverview = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterOverview })));
const PrinterProfiles = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterProfiles })));
const PrinterPurchasing = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterPurchasing })));
const PrinterRecommendations = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterRecommendations })));
const PrinterReports = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterReports })));
const PrinterUtilization = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrinterUtilization })));
const PrintJobs = lazy(() => import("./pages/PrinterSuite").then(m => ({ default: m.PrintJobs })));
const PrinterDevice = lazy(() => import("./pages/PrinterDevice"));
const AiRules = lazy(() => import("./pages/Intelligence").then(m => ({ default: m.AiRules })));
const IntelRecommendations = lazy(() => import("./pages/Intelligence").then(m => ({ default: m.IntelRecommendations })));
const OperationalAnalytics = lazy(() => import("./pages/Intelligence").then(m => ({ default: m.OperationalAnalytics })));
const Integrations = lazy(() => import("./pages/Integrations"));
const PredictiveHealth = lazy(() => import("./pages/Intelligence").then(m => ({ default: m.PredictiveHealth })));
const DeviceDetails = lazy(() => import("./pages/DeviceDetails"));
const Recovery = lazy(() => import("./pages/Recovery"));
const Switches = lazy(() => import("./pages/Switches"));
const SystemHealth = lazy(() => import("./pages/SystemHealth"));
const Users = lazy(() => import("./pages/Users"));
const NocDashboard = lazy(() => import("./pages/NocDashboard"));
const ExecutiveDashboard = lazy(() => import("./pages/ExecutiveDashboard"));
const Compliance = lazy(() => import("./pages/Compliance"));
const Certification = lazy(() => import("./pages/Certification"));
const Deployments = lazy(() => import("./pages/Deployments"));
const Notifications = lazy(() => import("./pages/Notifications"));
const Topology = lazy(() => import("./pages/Topology"));
const DeviceAlerts = lazy(() => import("./pages/DeviceAlerts"));
const Device360 = lazy(() => import("./pages/Device360"));
const DeviceInventory = lazy(() => import("./pages/DeviceInventory"));
const Collection = lazy(() => import("./pages/Collection"));
const ChangePlans = lazy(() => import("./pages/ChangePlans"));
const Vault = lazy(() => import("./pages/Vault"));
const Accountability = lazy(() => import("./pages/Accountability"));
const Replacement = lazy(() => import("./pages/Replacement"));
const LearningCentre = lazy(() => import("./pages/LearningCentre"));
const Optimization = lazy(() => import("./pages/Optimization"));
const Platform = lazy(() => import("./pages/Platform"));
const DataManagement = lazy(() => import("./pages/DataManagement"));
const WireframeWorkspace = lazy(() => import("./pages/WireframeWorkspaces"));

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
    <Suspense fallback={<div className="p-6 text-sm text-slate-500" role="status">Loading…</div>}>
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/change-password" element={<WithSession><ChangePassword /></WithSession>} />
      <Route element={<Protected><Layout /></Protected>}>
        {/* Preserved SIMS screens (Phase 1 parity) */}
        <Route index element={<NocDashboard />} />
        <Route path="wifi" element={<Dashboard />} />
        <Route path="aps" element={<ApFleet mode="aps" />} />
        <Route path="aps/controller" element={<APs />} />
        <Route path="aps/fleet" element={<Navigate to="/aps" replace />} />
        <Route path="ap-health" element={<ApHealthReport />} />
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
        <Route path="ai-learning" element={<AiRules />} />
        <Route path="ai-site-rules" element={<AiLearning />} />
        <Route path="phase4-insights" element={<Phase4Insights />} />
        <Route path="onboarding" element={<GuidedOnboarding />} />
        <Route path="onboarding/import" element={<InventoryImport />} />
        <Route path="onboarding/manual" element={<DeviceIntake />} />
        <Route path="devices/:id/config" element={<DeviceConfig />} />
        <Route path="config-templates" element={<ConfigTemplates />} />
        <Route path="discovery" element={<DiscoveryRules />} />
        <Route path="discovery/planner" element={<DiscoveryRanges />} />
        <Route path="printers" element={<PrinterOverview />} />
        <Route path="printers/inventory" element={<PrinterInventory />} />
        <Route path="printers/live" element={<PrinterLive />} />
        <Route path="printers/utilization" element={<PrinterUtilization />} />
        <Route path="printers/consumables" element={<PrinterConsumables />} />
        <Route path="printers/maintenance" element={<PrinterMaintenance />} />
        <Route path="printers/models" element={<PrinterModels />} />
        <Route path="printers/recommendations" element={<PrinterRecommendations />} />
        <Route path="printers/reports" element={<PrinterReports />} />
        <Route path="printers/purchasing" element={<PrinterPurchasing />} />
        <Route path="printers/print-jobs" element={<PrintJobs />} />
        <Route path="printers/profiles" element={<PrinterProfiles />} />
        <Route path="printers/:id" element={<PrinterDevice />} />
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
        <Route path="rf-health" element={<ApFleet mode="rf" />} />
        <Route path="port-groups" element={<PortGroups />} />
        <Route path="optics" element={<Optics />} />
        <Route path="port-health" element={<WireframeWorkspace kind="ports" />} />
        <Route path="client-journeys" element={<ClientConnectivity />} />
        <Route path="approvals" element={<WireframeWorkspace kind="approvals" />} />
        <Route path="recommendations" element={<IntelRecommendations />} />
        <Route path="predictive" element={<PredictiveHealth />} />
        <Route path="analytics" element={<OperationalAnalytics />} />
        <Route path="credentials" element={<WireframeWorkspace kind="credentials" />} />
        <Route path="integrations" element={<Integrations />} />
        <Route path="switch-config" element={<WireframeWorkspace kind="switchconfig" />} />
        <Route path="ssid-policies" element={<WireframeWorkspace kind="ssid" />} />
        <Route path="device-360" element={<WireframeWorkspace kind="device360" />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </Suspense>
  );
}
