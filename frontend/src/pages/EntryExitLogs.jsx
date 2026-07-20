import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { LuFilter, LuDownload, LuSearch, LuArrowLeftRight } from "react-icons/lu";
import StatusBadge from "../components/StatusBadge";
import Pagination from "../components/Pagination";
import EmptyState from "../components/EmptyState";
import { SkeletonRow } from "../components/Loader";
import { listRecords } from "../services/recordService";
import { useReferenceData } from "../hooks/useReferenceData";
import { DIRECTIONS } from "../utils/constants";
import { formatDate, formatTime, csvDownload } from "../utils/format";

const PAGE_SIZE = 10;

export default function EntryExitLogs() {
  const { gates } = useReferenceData();
  const [records, setRecords] = useState([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);

  const [search, setSearch] = useState("");
  const [direction, setDirection] = useState("");
  const [gate, setGate] = useState("");
  const [status, setStatus] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [page, setPage] = useState(1);

  async function load() {
    setLoading(true);
    try {
      const data = await listRecords({
        page,
        page_size: PAGE_SIZE,
        search: search || undefined,
        direction: direction || undefined,
        gate: gate || undefined,
        was_authorized: status || undefined,
        date_from: startDate || undefined,
        date_to: endDate || undefined,
        ordering: "-timestamp",
      });
      setRecords(Array.isArray(data) ? data : data.results || []);
      setCount(Array.isArray(data) ? data.length : data.count || 0);
    } catch {
      toast.error("Could not load entry/exit logs");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, direction, gate, status, startDate, endDate]);

  useEffect(() => {
    const t = setTimeout(() => {
      setPage(1);
      load();
    }, 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  async function handleExport() {
    try {
      const data = await listRecords({
        page_size: 500,
        search: search || undefined,
        direction: direction || undefined,
        gate: gate || undefined,
        was_authorized: status || undefined,
        date_from: startDate || undefined,
        date_to: endDate || undefined,
        ordering: "-timestamp",
      });
      const all = Array.isArray(data) ? data : data.results || [];
      if (!all.length) {
        toast.error("Nothing to export for this filter");
        return;
      }
      csvDownload(
        "entry-exit-logs.csv",
        all.map((r) => ({
          Plate: r.detected_plate_text,
          Owner: r.vehicle_detail?.owner_name || "Unknown",
          Gate: r.gate_name || "—",
          Direction: r.direction,
          Timestamp: r.timestamp,
          Status: r.was_authorized ? "Authorized" : "Unauthorized",
          Confidence: Math.round((r.confidence_score || 0) * 100) + "%",
        }))
      );
    } catch {
      toast.error("Could not export logs");
    }
  }

  return (
    <div className="space-y-5 animate-fadeIn">
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative flex-1 min-w-[220px]">
          <LuSearch className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={17} />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by plate number…"
            className="w-full pl-10 pr-3 py-2.5 rounded-lg border border-slate-200 text-sm focus-ring bg-white"
          />
        </div>
        <button
          onClick={() => setShowFilters((s) => !s)}
          className={`flex items-center gap-2 px-3.5 py-2.5 rounded-lg text-sm font-medium border ${
            showFilters ? "bg-brand-50 border-brand-200 text-brand-700" : "border-slate-200 text-slate-600 bg-white"
          }`}
        >
          <LuFilter size={16} /> Filter
        </button>
        <button
          onClick={handleExport}
          className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-emerald-600 text-white text-sm font-semibold hover:bg-emerald-700"
        >
          <LuDownload size={16} /> Export
        </button>
      </div>

      {showFilters && (
        <div className="flex flex-wrap gap-3 bg-white p-4 rounded-xl border border-slate-100">
          <input
            type="date"
            value={startDate}
            onChange={(e) => { setStartDate(e.target.value); setPage(1); }}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
          />
          <input
            type="date"
            value={endDate}
            onChange={(e) => { setEndDate(e.target.value); setPage(1); }}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm"
          />
          <select
            value={gate}
            onChange={(e) => { setGate(e.target.value); setPage(1); }}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white"
          >
            <option value="">All Gates</option>
            {gates.map((g) => (
              <option key={g.id} value={g.id}>{g.name}</option>
            ))}
          </select>
          <select
            value={direction}
            onChange={(e) => { setDirection(e.target.value); setPage(1); }}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white"
          >
            <option value="">All Types</option>
            {DIRECTIONS.map((d) => (
              <option key={d.value} value={d.value}>{d.label}</option>
            ))}
          </select>
          <select
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1); }}
            className="rounded-lg border border-slate-200 px-3 py-2 text-sm bg-white"
          >
            <option value="">All Statuses</option>
            <option value="true">Authorized</option>
            <option value="false">Unauthorized</option>
          </select>
        </div>
      )}

      <div className="bg-white rounded-2xl shadow-card border border-slate-100 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-slate-50/70">
              <tr className="text-left text-slate-500 text-xs uppercase tracking-wide">
                <th className="px-4 py-3 font-semibold">#</th>
                <th className="px-4 py-3 font-semibold">Number Plate</th>
                <th className="px-4 py-3 font-semibold">Owner</th>
                <th className="px-4 py-3 font-semibold">Gate</th>
                <th className="px-4 py-3 font-semibold">Type</th>
                <th className="px-4 py-3 font-semibold">Entry Time</th>
                <th className="px-4 py-3 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading && Array.from({ length: 8 }).map((_, i) => <SkeletonRow key={i} cols={7} />)}

              {!loading &&
                records.map((r, idx) => (
                  <tr key={r.id} className="table-row-hover">
                    <td className="px-4 py-3 text-slate-400">{(page - 1) * PAGE_SIZE + idx + 1}</td>
                    <td className="px-4 py-3 font-semibold text-ink-950 tracking-wide">{r.detected_plate_text || "—"}</td>
                    <td className="px-4 py-3 text-slate-600">{r.vehicle_detail?.owner_name || "Unknown"}</td>
                    <td className="px-4 py-3 text-slate-600">{r.gate_name || "—"}</td>
                    <td className="px-4 py-3">
                      <StatusBadge status={r.direction} />
                    </td>
                    <td className="px-4 py-3 text-slate-500">
                      {formatDate(r.timestamp)}, {formatTime(r.timestamp)}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={r.was_authorized ? "AUTHORIZED" : "UNAUTHORIZED"} />
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>

          {!loading && records.length === 0 && (
            <EmptyState
              icon={LuArrowLeftRight}
              title="No activity found"
              message="Try adjusting your search or filters."
            />
          )}
        </div>

        {!loading && records.length > 0 && (
          <div className="px-4 border-t border-slate-100">
            <Pagination page={page} pageSize={PAGE_SIZE} total={count} onPageChange={setPage} />
          </div>
        )}
      </div>
    </div>
  );
}
