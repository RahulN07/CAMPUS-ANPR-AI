import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  LuCar,
  LuShieldCheck,
  LuShieldAlert,
  LuLogIn,
  LuLogOut,
  LuUsers,
  LuBellRing,
  LuPlus,
  LuSearch,
  LuFileChartColumn,
  LuArrowRight,
  LuScanLine,
} from "react-icons/lu";
import StatCard from "../components/StatCard";
import StatusBadge from "../components/StatusBadge";
import { PageLoader } from "../components/Loader";
import EmptyState from "../components/EmptyState";
import VehicleFormModal from "../components/VehicleFormModal";
import { getDashboardSummary } from "../services/dashboardService";
import { listRecords } from "../services/recordService";
import { listNotifications } from "../services/notificationService";
import { formatDateTime, timeAgo } from "../utils/format";

export default function Dashboard() {
  const [summary, setSummary] = useState(null);
  const [records, setRecords] = useState([]);
  const [activity, setActivity] = useState([]);
  const [unauthorizedCount, setUnauthorizedCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [addOpen, setAddOpen] = useState(false);

async function loadAll() {
  setLoading(true);

  try {
    const [summaryData, recordsData, notifData] = await Promise.all([
      getDashboardSummary(),

      listRecords({
        ordering: "-timestamp",
        page_size: 1000,
      }).catch(() => []),

      listNotifications().catch(() => []),
    ]);

    setSummary(summaryData);

    const recordResults = Array.isArray(recordsData)
      ? recordsData
      : recordsData.results || [];

    // Show only the latest 6 records in the dashboard table
    setRecords(recordResults.slice(0, 6));

    // Count all unauthorized ANPR detection records
    const unauthorizedRecords = recordResults.filter(
      (record) => record.was_authorized === false
    ).length;

    setUnauthorizedCount(unauthorizedRecords);

    const notifResults = Array.isArray(notifData)
      ? notifData
      : notifData.results || [];

    setActivity(notifResults.slice(0, 5));
  } catch (error) {
    console.error("Failed to load dashboard:", error);
  } finally {
    setLoading(false);
  }
}
  useEffect(() => {
    loadAll();
  }, []);

  if (loading || !summary) return <PageLoader label="Loading dashboard…" />;

  const latest = summary.recent_records?.[0];

  return (
    <div className="space-y-6 animate-fadeIn">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4">
        <StatCard title="Total Vehicles" value={summary.total_vehicles} icon={LuCar} tone="blue" />
        <StatCard title="Authorized" value={summary.authorized_vehicles} icon={LuShieldCheck} tone="green" />
        <StatCard title="Unauthorized" value={unauthorizedCount} icon={LuShieldAlert} tone="red" />
        <StatCard title="Today's Entries" value={summary.today_entries} icon={LuLogIn} tone="amber" />
        <StatCard title="Today's Exits" value={summary.today_exits} icon={LuLogOut} tone="purple" />
        <StatCard title="Active Users" value={summary.active_users} icon={LuUsers} tone="cyan" />
      </div>

      <div className="grid lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-display font-semibold text-ink-950">Live Vehicle Detection</h3>
              <Link to="/live-monitor" className="text-xs font-semibold text-brand-600 flex items-center gap-1 hover:underline">
                Open Live Monitor <LuArrowRight size={13} />
              </Link>
            </div>

            {latest ? (
              <div className="flex items-center gap-4 p-4 rounded-xl bg-slate-50 border border-slate-100">
                <div className="h-16 w-16 rounded-xl bg-gradient-to-br from-ink-800 to-ink-950 grid place-items-center shrink-0">
                  <LuScanLine className="text-brand-400" size={26} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="font-display font-bold text-lg tracking-wide text-ink-950">
                    {latest.detected_plate_text || "UNREADABLE"}
                  </p>
                  <p className="text-xs text-slate-500 mt-0.5">{formatDateTime(latest.timestamp)}</p>
                </div>
                <div className="text-right shrink-0">
                  <StatusBadge status={latest.was_authorized ? "AUTHORIZED" : "UNAUTHORIZED"} />
                  <p className="text-[11px] text-slate-400 mt-1.5">
                    {Math.round((latest.confidence_score || 0) * 100)}% confidence
                  </p>
                </div>
              </div>
            ) : (
              <EmptyState title="No detections yet" message="Live detections will appear here once the ANPR pipeline runs." />
            )}
          </div>

          <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-display font-semibold text-ink-950">Recent Entry Records</h3>
              <Link to="/entry-exit-logs" className="text-xs font-semibold text-brand-600 flex items-center gap-1 hover:underline">
                View All <LuArrowRight size={13} />
              </Link>
            </div>

            {records.length === 0 ? (
              <EmptyState title="No records yet" message="Entry and exit activity will show up here." />
            ) : (
              <div className="overflow-x-auto -mx-1">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-slate-400 text-xs uppercase tracking-wide">
                      <th className="px-3 py-2 font-medium">Plate Number</th>
                      <th className="px-3 py-2 font-medium">Direction</th>
                      <th className="px-3 py-2 font-medium">Time</th>
                      <th className="px-3 py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {records.map((r) => (
                      <tr key={r.id} className="table-row-hover">
                        <td className="px-3 py-3 font-semibold text-ink-950">{r.detected_plate_text || "—"}</td>
                        <td className="px-3 py-3 text-slate-600">{r.direction}</td>
                        <td className="px-3 py-3 text-slate-500">{timeAgo(r.timestamp)}</td>
                        <td className="px-3 py-3">
                          <StatusBadge status={r.was_authorized ? "AUTHORIZED" : "UNAUTHORIZED"} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-6">
          <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-5">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-display font-semibold text-ink-950">Recent Activity</h3>
              <Link to="/notifications" className="text-xs font-semibold text-brand-600 hover:underline">
                View All
              </Link>
            </div>
            {activity.length === 0 ? (
              <EmptyState title="All quiet" message="No recent notifications." />
            ) : (
              <ul className="space-y-3.5">
                {activity.map((n) => (
                  <li key={n.id} className="flex items-start gap-3">
                    <span
                      className={`mt-1 h-2 w-2 rounded-full shrink-0 ${
                        n.is_read ? "bg-slate-300" : "bg-brand-500"
                      }`}
                    />
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-ink-950 truncate">{n.title}</p>
                      <p className="text-xs text-slate-400">{timeAgo(n.created_at)}</p>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-5">
            <h3 className="font-display font-semibold text-ink-950 mb-4">Quick Actions</h3>
            <div className="space-y-2.5">
              <button
                onClick={() => setAddOpen(true)}
                className="w-full flex items-center gap-3 px-4 py-3 rounded-xl bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700"
              >
                <LuPlus size={17} /> Add New Vehicle
              </button>
              <Link
                to="/search-vehicle"
                className="w-full flex items-center gap-3 px-4 py-3 rounded-xl bg-slate-100 text-slate-700 text-sm font-semibold hover:bg-slate-200"
              >
                <LuSearch size={17} /> Search Vehicle
              </Link>
              <Link
                to="/reports"
                className="w-full flex items-center gap-3 px-4 py-3 rounded-xl bg-emerald-500 text-white text-sm font-semibold hover:bg-emerald-600"
              >
                <LuFileChartColumn size={17} /> Generate Report
              </Link>
            </div>
          </div>

          {summary.unread_notifications > 0 && (
            <div className="bg-amber-50 border border-amber-100 rounded-2xl p-4 flex items-start gap-3">
              <LuBellRing className="text-amber-500 shrink-0 mt-0.5" size={18} />
              <p className="text-sm text-amber-700">
                You have <span className="font-semibold">{summary.unread_notifications}</span> unread
                notification{summary.unread_notifications === 1 ? "" : "s"}.
              </p>
            </div>
          )}
        </div>
      </div>

      <VehicleFormModal open={addOpen} onClose={() => setAddOpen(false)} onSaved={loadAll} />
    </div>
  );
}
