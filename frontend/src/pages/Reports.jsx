import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { LuDownload, LuFileChartColumn, LuTrendingUp, LuShieldAlert, LuTarget } from "react-icons/lu";

import { listRecords } from "../services/recordService";
import BarChart from "../components/BarChart";
import { PageLoader } from "../components/Loader";
import EmptyState from "../components/EmptyState";
import { toISODate, csvDownload, formatDate } from "../utils/format";

function lastNDays(n) {
  const days = [];
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    days.push(d);
  }
  return days;
}

export default function Reports() {
  const [dateFrom, setDateFrom] = useState(toISODate(lastNDays(7)[0]));
  const [dateTo, setDateTo] = useState(toISODate(new Date()));
  const [loading, setLoading] = useState(true);
  const [records, setRecords] = useState([]);
  const [truncated, setTruncated] = useState(false);

  useEffect(() => {
    generate();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function generate() {
    try {
      setLoading(true);
      const data = await listRecords({
        date_from: dateFrom,
        date_to: dateTo,
        page_size: 500,
        ordering: "-timestamp",
      });
      setRecords(data.results || []);
      setTruncated((data.count || 0) > 500);
    } catch (error) {
      console.error(error);
      toast.error("Couldn't generate the report.");
    } finally {
      setLoading(false);
    }
  }

  const totalEntries = records.filter((r) => r.direction === "ENTRY").length;
  const totalExits = records.filter((r) => r.direction === "EXIT").length;
  const unauthorized = records.filter((r) => !r.was_authorized).length;
  const avgConfidence = records.length
    ? Math.round((records.reduce((sum, r) => sum + (r.confidence_score || 0), 0) / records.length) * 100)
    : 0;

  const days = [];
  const start = new Date(dateFrom);
  const end = new Date(dateTo);
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    days.push(new Date(d));
  }
  const chartData = days.slice(-14).map((d) => {
    const iso = toISODate(d);
    const dayRecords = records.filter((r) => r.timestamp?.slice(0, 10) === iso);
    return {
      label: d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }),
      a: dayRecords.filter((r) => r.direction === "ENTRY").length,
      b: dayRecords.filter((r) => r.direction === "EXIT").length,
    };
  });

  const plateCounts = {};
  records.forEach((r) => {
    const key = r.detected_plate_text || "Unknown";
    plateCounts[key] = (plateCounts[key] || 0) + 1;
  });
  const topVehicles = Object.entries(plateCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  function handleExportCSV() {
    if (!records.length) {
      toast.error("Nothing to export for this range.");
      return;
    }
    const rows = records.map((r) => ({
      Plate: r.detected_plate_text,
      Owner: r.vehicle_detail?.owner_name || "",
      Gate: r.gate_name || "",
      Direction: r.direction,
      Timestamp: r.timestamp,
      Status: r.was_authorized ? "Authorized" : "Unauthorized",
      Confidence: Math.round((r.confidence_score || 0) * 100) + "%",
    }));
    csvDownload(`anpr-report_${dateFrom}_to_${dateTo}.csv`, rows);
  }

  function handlePrint() {
    window.print();
  }

  return (
    <div className="space-y-5 animate-fadeIn">
      <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-4 flex flex-col sm:flex-row sm:items-center gap-3">
        <input
          type="date"
          value={dateFrom}
          onChange={(e) => setDateFrom(e.target.value)}
          className="px-3 py-2.5 rounded-lg border border-slate-200 text-sm focus-ring"
        />
        <span className="text-slate-400 text-sm hidden sm:block">to</span>
        <input
          type="date"
          value={dateTo}
          onChange={(e) => setDateTo(e.target.value)}
          className="px-3 py-2.5 rounded-lg border border-slate-200 text-sm focus-ring"
        />
        <button
          onClick={generate}
          className="px-4 py-2.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium transition-colors"
        >
          Generate Report
        </button>

        <div className="sm:ml-auto flex items-center gap-2">
          <button
            onClick={handleExportCSV}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium transition-colors"
          >
            <LuDownload size={15} /> Download CSV
          </button>
          <button
            onClick={handlePrint}
            className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-ink-950 hover:bg-ink-900 text-white text-sm font-medium transition-colors"
          >
            <LuDownload size={15} /> Download PDF
          </button>
        </div>
      </div>

      {loading ? (
        <PageLoader label="Crunching the numbers…" />
      ) : (
        <>
          {truncated && (
            <p className="text-xs text-amber-600 bg-amber-50 border border-amber-100 rounded-lg px-3.5 py-2">
              This range has more than 500 records — the report below is based on the most recent 500.
            </p>
          )}

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <ReportStat icon={LuTrendingUp} label="Total Entries" value={totalEntries} tone="blue" />
            <ReportStat icon={LuTrendingUp} label="Total Exits" value={totalExits} tone="green" />
            <ReportStat icon={LuShieldAlert} label="Unauthorized Access" value={unauthorized} tone="red" />
            <ReportStat icon={LuTarget} label="Avg. Detection Confidence" value={`${avgConfidence}%`} tone="purple" />
          </div>

          <div className="grid lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 bg-white rounded-2xl shadow-card border border-slate-100 p-5">
              <h3 className="font-display font-semibold text-ink-950 mb-1">Vehicle Movement</h3>
              <p className="text-xs text-slate-400 mb-4">Entries vs. exits (last 14 days of range)</p>
              {chartData.every((d) => d.a === 0 && d.b === 0) ? (
                <EmptyState icon={LuFileChartColumn} title="No activity in this range" />
              ) : (
                <BarChart data={chartData} seriesA="Entries" seriesB="Exits" />
              )}
            </div>

            <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-5">
              <h3 className="font-display font-semibold text-ink-950 mb-4">Top Vehicles</h3>
              {topVehicles.length === 0 ? (
                <EmptyState title="No data yet" />
              ) : (
                <ul className="space-y-3">
                  {topVehicles.map(([plate, count], idx) => (
                    <li key={plate} className="flex items-center gap-3">
                      <span className="h-7 w-7 rounded-lg bg-slate-100 text-slate-500 text-xs font-semibold grid place-items-center shrink-0">
                        {idx + 1}
                      </span>
                      <span className="flex-1 text-sm font-medium text-ink-950 truncate">{plate}</span>
                      <span className="text-xs text-slate-400">{count} entries</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-display font-semibold text-ink-950">Report Summary</h3>
              <span className="text-xs text-slate-400">
                {formatDate(dateFrom)} – {formatDate(dateTo)}
              </span>
            </div>
            <p className="text-sm text-slate-500 leading-relaxed">
              Between {formatDate(dateFrom)} and {formatDate(dateTo)}, the system logged{" "}
              <strong className="text-ink-950">{records.length}</strong> gate events across{" "}
              <strong className="text-ink-950">{totalEntries}</strong> entries and{" "}
              <strong className="text-ink-950">{totalExits}</strong> exits.{" "}
              <strong className="text-ink-950">{unauthorized}</strong> of those were flagged unauthorized, with an
              average plate-detection confidence of <strong className="text-ink-950">{avgConfidence}%</strong>.
            </p>
          </div>
        </>
      )}
    </div>
  );
}

function ReportStat({ icon: Icon, label, value, tone }) {
  const tones = {
    blue: "bg-brand-50 text-brand-600",
    green: "bg-emerald-50 text-emerald-600",
    red: "bg-red-50 text-red-500",
    purple: "bg-violet-50 text-violet-600",
  };
  return (
    <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-5">
      <div className={`h-9 w-9 rounded-lg grid place-items-center mb-3 ${tones[tone]}`}>
        <Icon size={17} />
      </div>
      <p className="text-2xl font-display font-bold text-ink-950">{value}</p>
      <p className="text-xs text-slate-500 mt-1">{label}</p>
    </div>
  );
}
