import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import {
  LuBuilding2,
  LuCamera,
  LuCar,
  LuClock,
  LuGauge,
  LuMapPin,
  LuPalette,
  LuScanLine,
  LuShieldAlert,
  LuShieldCheck,
  LuTriangleAlert,
  LuVideoOff,
} from "react-icons/lu";

import EmptyState from "../components/EmptyState";
import { SelectField } from "../components/FormField";
import StatusBadge from "../components/StatusBadge";
import { useLiveAnpr } from "../hooks/useLiveAnpr";
import { useReferenceData } from "../hooks/useReferenceData";
import { resolveMediaUrl } from "../api/axios";
import { recentDetections } from "../services/anprService";
import { formatDateTime, formatTime } from "../utils/format";


function normalizeStoredRecord(record) {
  const vehicle = record.vehicle_detail || {};
  const authorized = record.was_authorized === true;
  return {
    record_id: record.id,
    track_id: null,
    plate: record.detected_plate_text,
    authorization_status: authorized ? "AUTHORIZED" : "UNAUTHORIZED",
    authorized,
    confidence: Number(record.confidence_score || 0),
    owner: vehicle.owner_name || null,
    owner_type: vehicle.owner_type || null,
    department: vehicle.department_name || null,
    company: vehicle.vehicle_company || record.detected_vehicle_company || null,
    model: vehicle.vehicle_model || record.detected_vehicle_model || null,
    color: vehicle.color || record.vehicle_color || null,
    vehicle_type: vehicle.vehicle_type_display || record.detected_vehicle_type || null,
    gate: record.gate_name || null,
    direction: record.direction,
    timestamp: record.timestamp,
    captured_image: record.captured_image,
    plate_image: record.plate_image,
    diagnostic_only: false,
  };
}


function detectionIdentity(detection) {
  if (detection?.record_id) return `record:${detection.record_id}`;
  return [
    detection?.plate || "unknown",
    detection?.track_id ?? "track",
    detection?.published_at || detection?.timestamp || "time",
  ].join(":");
}


function timestampValue(detection) {
  const value = Date.parse(detection?.timestamp || detection?.published_at || "");
  return Number.isFinite(value) ? value : 0;
}


export default function LiveMonitor() {
  const { gates } = useReferenceData();
  const navigate = useNavigate();
  const [selectedGate, setSelectedGate] = useState("");
  const [storedRecords, setStoredRecords] = useState([]);

  const notifyDetection = useCallback((detection) => {
    const plate = detection.plate || "UNREADABLE";
    if (detection.authorized) {
      toast.success(`Access authorized — ${plate}`);
    } else {
      toast.error(`Unauthorized vehicle — ${plate}`);
    }
  }, []);

  const live = useLiveAnpr(selectedGate, {
    maximumEvents: 25,
    onDetection: notifyDetection,
  });

  useEffect(() => {
    let active = true;
    recentDetections(25)
      .then((data) => {
        if (!active) return;
        const records = Array.isArray(data) ? data : data?.results || [];
        setStoredRecords(records.map(normalizeStoredRecord));
      })
      .catch(() => {
        // Historical activity must never block the live transport.
      });
    return () => {
      active = false;
    };
  }, []);

  const activity = useMemo(() => {
    const merged = new Map();
    for (const detection of [...live.recentEvents, ...storedRecords]) {
      const key = detectionIdentity(detection);
      if (!merged.has(key)) merged.set(key, detection);
    }
    return [...merged.values()]
      .sort((first, second) => timestampValue(second) - timestampValue(first))
      .slice(0, 25);
  }, [live.recentEvents, storedRecords]);

  const selectedGateDetails = gates.find(
    (item) => String(item.id) === String(selectedGate),
  );
  const latest = live.latestDetection || activity[0] || null;
  const resultConfidence = Math.round(Number(latest?.confidence || 0) * 100);
  const needsReview = resultConfidence > 0 && resultConfidence < 75;

  const authorizationCounts = useMemo(
    () => ({
      authorized: activity.filter((item) => item.authorized).length,
      unauthorized: activity.filter((item) => !item.authorized).length,
    }),
    [activity],
  );

  function handleStart() {
    if (!selectedGate) {
      toast.error("Select a gate before starting live monitoring");
      return;
    }
    live.start();
  }

  function handleStop() {
    live.stop();
  }

  const connectionLabel = {
    idle: "Monitor Stopped",
    connecting: "Connecting",
    connected: "Transport Connected",
    disconnected: "Reconnecting",
    unauthorized: "Session Expired",
  }[live.connectionState] || "Disconnected";

  const pipelineLabel = {
    RUNNING: "CCTV Running",
    WARMING: "AI Warming Up",
    STOPPED: "CCTV Stopped",
    ERROR: "Pipeline Error",
    OFFLINE: "Waiting for CCTV",
    IDLE: "Monitor Stopped",
  }[live.pipelineState] || live.pipelineState;

  return (
    <div className="space-y-6 animate-fadeIn">
      <div className="flex flex-wrap items-center gap-3">
        <SelectField
          placeholder="Select a gate"
          options={gates.map((item) => ({ value: item.id, label: item.name }))}
          value={selectedGate}
          onChange={(event) => setSelectedGate(event.target.value)}
          disabled={live.monitoring}
          className="w-56"
        />

        {!live.monitoring ? (
          <button
            type="button"
            onClick={handleStart}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700"
          >
            <LuCamera size={16} />
            Connect Live Monitor
          </button>
        ) : (
          <button
            type="button"
            onClick={handleStop}
            className="inline-flex items-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-red-700"
          >
            <LuVideoOff size={16} />
            Disconnect Monitor
          </button>
        )}

        <div
          className={`ml-auto flex items-center gap-2 rounded-full border px-3 py-2 text-xs font-semibold ${
            live.connectionState === "connected"
              ? "border-emerald-100 bg-emerald-50 text-emerald-700"
              : live.monitoring
                ? "border-amber-100 bg-amber-50 text-amber-700"
                : "border-slate-100 bg-slate-50 text-slate-500"
          }`}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              live.connectionState === "connected"
                ? "bg-emerald-500 animate-pulse"
                : live.monitoring
                  ? "bg-amber-500 animate-pulse"
                  : "bg-slate-300"
            }`}
          />
          {connectionLabel}
        </div>
      </div>

      {live.error && (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
          <span className="flex items-center gap-2">
            <LuTriangleAlert size={16} className="shrink-0" />
            {live.error}
          </span>
          <button
            type="button"
            onClick={live.clearError}
            className="text-xs font-bold uppercase tracking-wide"
          >
            Dismiss
          </button>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <MonitorStatCard
          title="Processing FPS"
          value={live.metrics.fps.toFixed(1)}
          detail={`Target ${live.metrics.targetFps}`}
          icon={LuGauge}
          tone="blue"
        />
        <MonitorStatCard
          title="Vehicles in Frame"
          value={live.metrics.vehicleCount}
          detail={`${live.metrics.trackedCount} tracked`}
          icon={LuCar}
          tone="green"
        />
        <MonitorStatCard
          title="Frame Queue"
          value={`${live.metrics.frameQueueSize}/${live.metrics.frameQueueCapacity}`}
          detail={`${live.metrics.frameQueueDropped} dropped`}
          icon={LuScanLine}
          tone="amber"
        />
        <MonitorStatCard
          title="Vehicle Queue"
          value={`${live.metrics.vehicleQueueSize}/${live.metrics.vehicleQueueCapacity}`}
          detail={`${live.metrics.workerInFlight}/${live.metrics.workerCount} workers busy`}
          icon={LuClock}
          tone="red"
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          <section className="relative overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-card">
            {live.monitoring && (
              <div className="pointer-events-none absolute left-4 top-4 z-20 flex flex-wrap gap-2">
                <div
                  className={`flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold text-white backdrop-blur ${
                    live.isLive ? "bg-emerald-600/90" : "bg-slate-950/75"
                  }`}
                >
                  <span
                    className={`h-2 w-2 rounded-full ${
                      live.isLive ? "bg-white animate-pulse" : "bg-amber-400"
                    }`}
                  />
                  {pipelineLabel}
                </div>
                <div className="rounded-full bg-slate-950/75 px-3 py-1.5 text-xs font-medium text-white backdrop-blur">
                  {selectedGateDetails?.name || "Gate"}
                </div>
                {live.isLive && (
                  <div className="rounded-full bg-slate-950/75 px-3 py-1.5 text-xs font-medium text-white backdrop-blur">
                    {live.metrics.fps.toFixed(1)} FPS · {live.metrics.vehicleCount} vehicles
                  </div>
                )}
              </div>
            )}

            <div className="aspect-video w-full bg-slate-950">
              {live.frameUrl ? (
                <img
                  src={live.frameUrl}
                  alt={`Live ANPR feed for ${selectedGateDetails?.name || "gate"}`}
                  className="h-full w-full object-contain"
                />
              ) : (
                <div className="grid h-full w-full place-items-center px-6 text-slate-400">
                  <div className="max-w-md text-center">
                    {live.monitoring ? (
                      <LuScanLine size={36} className="mx-auto mb-3 animate-pulse text-blue-400" />
                    ) : (
                      <LuVideoOff size={36} className="mx-auto mb-3 opacity-60" />
                    )}
                    <p className="text-sm font-semibold text-slate-200">{pipelineLabel}</p>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      {live.monitoring
                        ? "The monitor is connected. Start the CCTV ANPR worker for this gate to receive annotated frames."
                        : "Select a gate and connect to its continuous CCTV pipeline."}
                    </p>
                  </div>
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-px border-t border-slate-800 bg-slate-800 text-xs sm:grid-cols-4">
              <StreamMetric label="Crossings" value={live.metrics.lineCrossings} />
              <StreamMetric label="Records saved" value={live.metrics.recordsSaved} />
              <StreamMetric label="Camera reconnects" value={live.metrics.reconnects} />
              <StreamMetric
                label="Transport"
                value={live.connectionState === "connected" ? "Healthy" : "Waiting"}
              />
            </div>
          </section>

          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-card">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <div>
                <h3 className="font-display font-semibold text-ink-950">Recent Activity</h3>
                <p className="mt-1 text-xs text-slate-400">
                  New vehicle events appear instantly without refreshing
                </p>
              </div>
              <div className="flex items-center gap-2 text-xs font-semibold">
                <span className="rounded-full bg-emerald-50 px-3 py-1 text-emerald-700">
                  {authorizationCounts.authorized} authorized
                </span>
                <span className="rounded-full bg-red-50 px-3 py-1 text-red-700">
                  {authorizationCounts.unauthorized} unauthorized
                </span>
              </div>
            </div>

            {activity.length === 0 ? (
              <EmptyState title="No detections yet" message="Recognized vehicles will appear here." />
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                {activity.slice(0, 10).map((detection, index) => {
                  const confidence = Math.round(Number(detection.confidence || 0) * 100);
                  const review = confidence > 0 && confidence < 75;
                  return (
                    <article
                      key={detectionIdentity(detection)}
                      onClick={() =>
                        detection.record_id && navigate(`/records/${detection.record_id}`)
                      }
                      className={`group relative overflow-hidden rounded-xl border bg-white transition duration-200 hover:-translate-y-1 hover:shadow-lg ${
                        detection.record_id ? "cursor-pointer" : "cursor-default"
                      } ${index === 0 ? "border-blue-300 ring-2 ring-blue-100" : "border-slate-100"}`}
                    >
                      {index === 0 && (
                        <span className="absolute left-2 top-2 z-10 rounded-full bg-blue-600 px-2 py-1 text-[9px] font-bold uppercase tracking-wide text-white shadow">
                          Latest
                        </span>
                      )}
                      <div className="aspect-video overflow-hidden bg-slate-100">
                        {detection.captured_image ? (
                          <img
                            src={resolveMediaUrl(detection.captured_image)}
                            alt={detection.plate || "Detected vehicle"}
                            className="h-full w-full object-cover transition duration-300 group-hover:scale-105"
                          />
                        ) : (
                          <div className="grid h-full w-full place-items-center text-slate-300">
                            <LuCar size={24} />
                          </div>
                        )}
                      </div>
                      <div className="space-y-2 p-3">
                        <div>
                          <p className="truncate text-sm font-extrabold tracking-wide text-ink-950">
                            {detection.plate || "UNREADABLE"}
                          </p>
                          <p className="mt-0.5 text-[11px] text-slate-400">
                            {formatTime(detection.timestamp || detection.published_at)}
                            {detection.direction ? ` · ${detection.direction}` : ""}
                          </p>
                        </div>
                        <div className="flex items-center justify-between gap-2">
                          {review ? (
                            <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] font-semibold text-amber-700">
                              <LuTriangleAlert size={10} /> Review
                            </span>
                          ) : (
                            <StatusBadge status={detection.authorization_status} />
                          )}
                          {confidence > 0 && (
                            <span className="text-[10px] font-semibold text-slate-400">
                              {confidence}%
                            </span>
                          )}
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </section>
        </div>

        <aside className="h-fit rounded-2xl border border-slate-100 bg-white p-5 shadow-card xl:sticky xl:top-6">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h3 className="font-display font-semibold text-ink-950">Latest Detection</h3>
              <p className="mt-1 text-xs text-slate-400">Most recent recognized vehicle</p>
            </div>
            <div
              className={`grid h-10 w-10 place-items-center rounded-xl ${
                latest
                  ? latest.authorized
                    ? "bg-emerald-50 text-emerald-600"
                    : "bg-red-50 text-red-600"
                  : "bg-slate-100 text-slate-400"
              }`}
            >
              <LuCar size={20} />
            </div>
          </div>

          {!latest ? (
            <EmptyState
              title="Waiting for a vehicle"
              message="Vehicle and owner details will appear after a line crossing."
            />
          ) : (
            <div className="space-y-5">
              <div
                className={`rounded-2xl border p-4 ${
                  latest.authorized
                    ? "border-emerald-100 bg-emerald-50/70"
                    : "border-red-100 bg-red-50/70"
                }`}
              >
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-400">
                  Number plate
                </p>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                  <p className="font-display text-2xl font-black tracking-wider text-ink-950">
                    {latest.plate || "UNREADABLE"}
                  </p>
                  <StatusBadge status={latest.authorization_status} />
                </div>
              </div>

              <div className="space-y-2">
                <VehicleInfoRow icon={LuCar} label="Vehicle type" value={latest.vehicle_type || "Unknown"} />
                <VehicleInfoRow icon={LuPalette} label="Color" value={latest.color || "Unknown"} />
                <VehicleInfoRow
                  icon={LuBuilding2}
                  label="Company / Model"
                  value={[latest.company, latest.model].filter(Boolean).join(" ") || "Unknown"}
                />
                <VehicleInfoRow icon={LuBuilding2} label="Owner" value={latest.owner || "No owner on record"} />
                <VehicleInfoRow icon={LuBuilding2} label="Department" value={latest.department || "Not registered"} />
                <VehicleInfoRow icon={LuMapPin} label="Gate" value={latest.gate || selectedGateDetails?.name || "—"} />
                <VehicleInfoRow
                  icon={LuClock}
                  label="Detected at"
                  value={formatDateTime(latest.timestamp || latest.published_at)}
                />
                <VehicleInfoRow
                  icon={LuScanLine}
                  label="Track / Direction"
                  value={`${latest.track_id ? `ID ${latest.track_id}` : "Stored record"} · ${latest.direction || "—"}`}
                />
              </div>

              <div>
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="font-medium text-slate-500">Plate confidence</span>
                  <span className="font-bold text-slate-700">{resultConfidence}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full transition-all duration-700 ${
                      needsReview
                        ? "bg-amber-500"
                        : latest.authorized
                          ? "bg-emerald-500"
                          : "bg-red-500"
                    }`}
                    style={{ width: `${Math.min(resultConfidence, 100)}%` }}
                  />
                </div>
              </div>

              {needsReview && (
                <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700">
                  <LuTriangleAlert size={16} className="mt-0.5 shrink-0" />
                  <span>This plate requires manual review because its recognition confidence is low.</span>
                </div>
              )}

              <div
                className={`flex items-center gap-2 rounded-xl border px-3 py-3 text-xs font-semibold ${
                  latest.authorized
                    ? "border-emerald-100 bg-emerald-50 text-emerald-700"
                    : "border-red-100 bg-red-50 text-red-700"
                }`}
              >
                {latest.authorized ? <LuShieldCheck size={16} /> : <LuShieldAlert size={16} />}
                {latest.authorized
                  ? "Authorized vehicle — access recorded"
                  : "Unknown or unauthorized vehicle — security review required"}
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}


function MonitorStatCard({ title, value, detail, icon: Icon, tone }) {
  const styles = {
    blue: "bg-blue-50 text-blue-600 border-blue-100",
    green: "bg-emerald-50 text-emerald-600 border-emerald-100",
    red: "bg-red-50 text-red-600 border-red-100",
    amber: "bg-amber-50 text-amber-600 border-amber-100",
  };
  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-card transition hover:-translate-y-0.5 hover:shadow-lg">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-medium text-slate-500">{title}</p>
          <p className="mt-1 truncate font-display text-2xl font-black text-ink-950">{value}</p>
          <p className="mt-1 truncate text-[11px] font-medium text-slate-400">{detail}</p>
        </div>
        <div className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl border ${styles[tone] || styles.blue}`}>
          <Icon size={21} />
        </div>
      </div>
    </div>
  );
}


function StreamMetric({ label, value }) {
  return (
    <div className="bg-slate-950 px-4 py-3 text-center">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 font-bold text-slate-200">{value}</p>
    </div>
  );
}


function VehicleInfoRow({ icon: Icon, label, value }) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-slate-100 bg-slate-50/70 p-3">
      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white text-slate-500 shadow-sm">
        <Icon size={16} />
      </div>
      <div className="min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">{label}</p>
        <p className="truncate text-sm font-semibold text-slate-700">{value}</p>
      </div>
    </div>
  );
}
