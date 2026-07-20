import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import {
  LuBell,
  LuShieldAlert,
  LuShieldCheck,
  LuClock,
  LuMegaphone,
  LuCheck,
  LuCheckCheck,
} from "react-icons/lu";

import EmptyState from "../components/EmptyState";
import { PageLoader } from "../components/Loader";
import { listNotifications, markAsRead, markAllAsRead } from "../services/notificationService";
import { timeAgo } from "../utils/format";

const ICONS = {
  UNAUTHORIZED_VEHICLE: { icon: LuShieldAlert, tone: "bg-red-50 text-red-500" },
  AUTHORIZED_ENTRY: { icon: LuShieldCheck, tone: "bg-emerald-50 text-emerald-500" },
  EXPIRED_VEHICLE: { icon: LuClock, tone: "bg-orange-50 text-orange-500" },
  EXPIRED_AUTHORIZATION: { icon: LuClock, tone: "bg-orange-50 text-orange-500" },
  SYSTEM_ALERT: { icon: LuMegaphone, tone: "bg-brand-50 text-brand-600" },
};

const FILTERS = [
  { value: "", label: "All" },
  { value: "false", label: "Unread" },
];

export default function Notifications() {
  const [notifications, setNotifications] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");

  useEffect(() => {
    load();
  }, []);

  async function load() {
    try {
      setLoading(true);
      const data = await listNotifications({ page_size: 100, ordering: "-created_at" });
      setNotifications(Array.isArray(data) ? data : data.results || []);
    } catch (error) {
      console.error(error);
      toast.error("Couldn't load notifications.");
    } finally {
      setLoading(false);
    }
  }

  async function handleMarkRead(n) {
    if (n.is_read) return;
    setNotifications((prev) => prev.map((x) => (x.id === n.id ? { ...x, is_read: true } : x)));
    try {
      await markAsRead(n.id);
    } catch {
      toast.error("Couldn't update notification.");
    }
  }

  async function handleMarkAll() {
    const unread = notifications.filter((n) => !n.is_read);
    if (!unread.length) return;
    setNotifications((prev) => prev.map((n) => ({ ...n, is_read: true })));
    try {
      await markAllAsRead(unread);
      toast.success("All notifications marked as read.");
    } catch {
      toast.error("Couldn't mark all as read.");
      load();
    }
  }

  const visible = notifications.filter((n) => (filter === "false" ? !n.is_read : true));
  const unreadCount = notifications.filter((n) => !n.is_read).length;

  if (loading) return <PageLoader label="Loading notifications…" />;

  return (
    <div className="space-y-5 animate-fadeIn">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 bg-white rounded-lg border border-slate-200 p-1">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setFilter(f.value)}
              className={`px-3.5 py-1.5 rounded-md text-sm font-medium transition-colors ${
                filter === f.value ? "bg-brand-600 text-white" : "text-slate-500 hover:bg-slate-50"
              }`}
            >
              {f.label} {f.value === "false" && unreadCount > 0 && `(${unreadCount})`}
            </button>
          ))}
        </div>

        <button
          onClick={handleMarkAll}
          disabled={unreadCount === 0}
          className="flex items-center gap-2 text-sm font-medium text-brand-600 hover:underline disabled:opacity-40 disabled:no-underline"
        >
          <LuCheckCheck size={16} /> Mark all as read
        </button>
      </div>

      <div className="bg-white rounded-2xl shadow-card border border-slate-100">
        {visible.length === 0 ? (
          <EmptyState icon={LuBell} title="Nothing to see here" message="You're all caught up." />
        ) : (
          <ul className="divide-y divide-slate-50">
            {visible.map((n) => {
              const meta = ICONS[n.notification_type] || ICONS.SYSTEM_ALERT;
              const Icon = meta.icon;
              return (
                <li
                  key={n.id}
                  onClick={() => handleMarkRead(n)}
                  className={`flex items-start gap-3.5 px-5 py-4 cursor-pointer transition-colors hover:bg-slate-50 ${
                    !n.is_read ? "bg-brand-50/40" : ""
                  }`}
                >
                  <div className={`h-9 w-9 rounded-full grid place-items-center shrink-0 ${meta.tone}`}>
                    <Icon size={16} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="text-sm font-semibold text-ink-950">{n.title}</p>
                      {!n.is_read && <span className="h-1.5 w-1.5 rounded-full bg-brand-600 shrink-0" />}
                    </div>
                    <p className="text-sm text-slate-500 mt-0.5">{n.message}</p>
                    <p className="text-[11px] text-slate-400 mt-1">{timeAgo(n.created_at)}</p>
                  </div>
                  {!n.is_read && (
                    <span className="flex items-center gap-1 text-[11px] font-medium text-brand-600 shrink-0">
                      <LuCheck size={13} /> Mark read
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
