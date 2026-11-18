import { useLocation, useNavigate } from "react-router-dom";

export default function DashboardSwitcher() {
  const location = useLocation(), navigate = useNavigate();
  return <label className="flex items-center gap-2 text-sm text-slate-600">Dashboard
    <select className="inm-form-control" value={location.pathname} onChange={e => navigate(e.target.value)}>
      <option value="/executive">Executive experience</option>
      <option value="/wifi">Wireless performance</option>
      <option value="/experience">Digital experience</option>
      <option value="/">Network command centre</option>
    </select>
  </label>;
}
