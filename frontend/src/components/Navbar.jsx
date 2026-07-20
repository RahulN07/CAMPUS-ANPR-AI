import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { LuMenu, LuBell, LuChevronDown, LuLogOut, LuCircleUserRound } from "react-icons/lu";
import { useAuth } from "../context/AuthContext";
import { initials } from "../utils/format";
import { listNotifications } from "../services/notificationService";
import { unwrapList } from "../utils/api";

const TITLES = {
  "/dashboard": ["Dashboard", "Overview of your campus security system"],
  "/live-monitor": ["Live Monitor", "Real-time camera feed & ANPR"],
  "/vehicles": ["Vehicles", "Manage all registered vehicles"],
  "/entry-exit-logs": ["Entry / Exit Logs", "View entry and exit activities"],
  "/access-management": ["Access Management", "Manage access, permissions and users"],
  "/search-vehicle": ["Search Vehicle", "Find a vehicle by plate, owner or department"],
  "/reports": ["Reports", "Generate and download reports"],
  "/notifications": ["Notifications", "View all system notifications"],
  "/settings": ["Settings", "System settings and preferences"],
};

export default function Navbar({ onMenuClick }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [menuOpen, setMenuOpen] = useState(false);
  const [unread, setUnread] = useState(0);
  const menuRef = useRef(null);

  const base = "/" + location.pathname.split("/")[1];
  const [title, subtitle] =
    Object.entries(TITLES).find(([path]) => base.startsWith(path))?.[1] ||
    (location.pathname.startsWith("/vehicles/")
      ? ["Vehicle Details", "View and edit vehicle information"]
      : ["Campus Security System", ""]);

  useEffect(() => {
    let mounted = true;
    async function loadUnread() {
      try {
        const data = await listNotifications({ is_read: false });
        const { results } = unwrapList(data);
        if (mounted) setUnread(results.length);
      } catch {
        // silent - notifications badge is non-critical
      }
    }
    loadUnread();
    const id = setInterval(loadUnread, 30000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, [location.pathname]);

  useEffect(() => {
    function onClickOutside(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  const displayName =
    [user?.first_name, user?.last_name].filter(Boolean).join(" ") || user?.username || "User";
  const roleLabel = user?.role
    ? user.role.replace("_", " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase())
    : "";

  return (
    <header className="sticky top-0 z-20 h-16 bg-white/90 backdrop-blur border-b border-slate-200 px-4 lg:px-6 flex items-center justify-between gap-4">
      <div className="flex items-center gap-3 min-w-0">
        <button
          onClick={onMenuClick}
          className="lg:hidden shrink-0 h-9 w-9 grid place-items-center rounded-lg text-slate-500 hover:bg-slate-100"
        >
          <LuMenu size={20} />
        </button>
        <div className="min-w-0">
          <h1 className="text-lg font-display font-semibold text-ink-950 truncate">{title}</h1>
          {subtitle && <p className="text-xs text-slate-500 truncate hidden sm:block">{subtitle}</p>}
        </div>
      </div>

      <div className="flex items-center gap-2 sm:gap-4">
        <button
          onClick={() => navigate("/notifications")}
          className="relative h-10 w-10 grid place-items-center rounded-lg text-slate-500 hover:bg-slate-100 transition-colors"
          title="Notifications"
        >
          <LuBell size={19} />
          {unread > 0 && (
            <span className="absolute top-1.5 right-1.5 h-4 min-w-4 px-1 rounded-full bg-red-500 text-white text-[10px] font-semibold grid place-items-center">
              {unread > 9 ? "9+" : unread}
            </span>
          )}
        </button>

        <div className="h-8 w-px bg-slate-200 hidden sm:block" />

        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setMenuOpen((o) => !o)}
            className="flex items-center gap-2 pl-1 pr-2 py-1 rounded-lg hover:bg-slate-100 transition-colors"
          >
            <div className="h-9 w-9 rounded-full bg-gradient-to-br from-brand-400 to-brand-700 text-white grid place-items-center text-xs font-semibold">
              {initials(displayName)}
            </div>
            <div className="text-left hidden sm:block leading-tight">
              <p className="text-sm font-medium text-ink-950">{displayName}</p>
              <p className="text-[11px] text-slate-500">{roleLabel} Access</p>
            </div>
            <LuChevronDown size={16} className="text-slate-400 hidden sm:block" />
          </button>

          {menuOpen && (
            <div className="absolute right-0 mt-2 w-48 bg-white rounded-xl shadow-card border border-slate-100 py-1.5 animate-fadeIn">
              <button
                onClick={() => {
                  setMenuOpen(false);
                  navigate("/settings");
                }}
                className="w-full flex items-center gap-2 px-3.5 py-2 text-sm text-slate-600 hover:bg-slate-50"
              >
                <LuCircleUserRound size={16} /> My Profile
              </button>
              <button
                onClick={logout}
                className="w-full flex items-center gap-2 px-3.5 py-2 text-sm text-red-500 hover:bg-red-50"
              >
                <LuLogOut size={16} /> Logout
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
