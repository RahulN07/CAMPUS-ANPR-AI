import { NavLink } from "react-router-dom";
import {
  LuLayoutDashboard,
  LuVideo,
  LuCar,
  LuArrowLeftRight,
  LuUsers,
  LuSearch,
  LuFileChartColumn,
  LuBell,
  LuSettings,
  LuShieldCheck,
  LuLogOut,
  LuX,
} from "react-icons/lu";
import { useAuth } from "../context/AuthContext";

const NAV_ITEMS = [
  { name: "Dashboard", path: "/dashboard", icon: LuLayoutDashboard },
  { name: "Live Monitor", path: "/live-monitor", icon: LuVideo },
  { name: "Vehicles", path: "/vehicles", icon: LuCar },
  { name: "Entry / Exit Logs", path: "/entry-exit-logs", icon: LuArrowLeftRight },
  { name: "Access Management", path: "/access-management", icon: LuUsers, adminOnly: true },
  { name: "Search Vehicle", path: "/search-vehicle", icon: LuSearch },
  { name: "Reports", path: "/reports", icon: LuFileChartColumn },
  { name: "Notifications", path: "/notifications", icon: LuBell },
  { name: "Settings", path: "/settings", icon: LuSettings, adminOnly: true },
];

export default function Sidebar({ open, onClose }) {
  const { user, logout } = useAuth();
  const isAdmin = user?.role === "ADMIN";

  return (
    <>
      {open && (
        <div
          className="fixed inset-0 z-30 bg-ink-950/50 lg:hidden"
          onClick={onClose}
        />
      )}

      <aside
        className={`fixed z-40 inset-y-0 left-0 w-64 shrink-0 bg-ink-950 text-slate-300 flex flex-col
        transition-transform duration-200 lg:static lg:translate-x-0
        ${open ? "translate-x-0" : "-translate-x-full"}`}
      >
        <div className="flex items-center gap-3 px-5 h-16 border-b border-white/10">
          <div className="h-9 w-9 rounded-lg bg-gradient-to-br from-brand-400 to-brand-700 grid place-items-center shadow-soft">
            <LuShieldCheck className="text-white" size={18} />
          </div>
          <div className="leading-tight">
            <p className="text-white font-display font-semibold text-sm tracking-wide">CAMPUS</p>
            <p className="text-[10px] text-slate-400 tracking-[0.18em]">SECURITY SYSTEM</p>
          </div>
          <button onClick={onClose} className="ml-auto lg:hidden text-slate-400 hover:text-white">
            <LuX size={20} />
          </button>
        </div>

        <nav className="flex-1 overflow-y-auto sidebar-scroll py-3 px-3 space-y-0.5">
          {NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin).map((item) => {
            const Icon = item.icon;
            return (
              <NavLink
                key={item.path}
                to={item.path}
                onClick={onClose}
                className={({ isActive }) =>
                  `flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-brand-600 text-white shadow-soft"
                      : "text-slate-300 hover:bg-white/5 hover:text-white"
                  }`
                }
              >
                <Icon size={18} className="shrink-0" />
                <span className="truncate">{item.name}</span>
              </NavLink>
            );
          })}
        </nav>

        <div className="p-3 border-t border-white/10">
          <button
            onClick={logout}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-slate-300 hover:bg-red-500/10 hover:text-red-400 transition-colors"
          >
            <LuLogOut size={18} />
            Logout
          </button>
        </div>
      </aside>
    </>
  );
}
