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
import { ChangeControl } from "./pages/Planned";
import DeviceIntake from "./pages/DeviceIntake";
import Recovery from "./pages/Recovery";
import Switches from "./pages/Switches";
import SystemHealth from "./pages/SystemHealth";
import Users from "./pages/Users";

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
        <Route index element={<Dashboard />} />
        <Route path="aps" element={<APs />} />
        <Route path="clients" element={<Clients />} />
        <Route path="experience" element={<Experience />} />
        <Route path="assistant" element={<Assistant />} />
        <Route path="config" element={<Configuration />} />
        <Route path="firmware" element={<Firmware />} />
        <Route path="sites" element={<Sites />} />
        <Route path="alerts" element={<Alerts />} />
        {/* INM additions */}
        <Route path="system" element={<SystemHealth />} />
        <Route path="switches" element={<Switches />} />
        <Route path="logs" element={<Logs />} />
        <Route path="audit" element={<Audit />} />
        <Route path="users" element={<Users />} />
        <Route path="recovery" element={<Recovery />} />
        <Route path="ai-learning" element={<AiLearning />} />
        <Route path="onboarding" element={<DeviceIntake />} />
        <Route path="changes" element={<ChangeControl />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
