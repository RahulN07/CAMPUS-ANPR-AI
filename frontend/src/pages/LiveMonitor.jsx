import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import Webcam from "react-webcam";
import toast from "react-hot-toast";
import {
  LuBuilding2,
  LuCamera,
  LuCar,
  LuCheckCheck,
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

import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";
import { SelectField } from "../components/FormField";
import { useReferenceData } from "../hooks/useReferenceData";
import { detectPlate, recentDetections } from "../services/anprService";
import { formatTime, formatDateTime } from "../utils/format";
import { resolveMediaUrl } from "../api/axios";

const DETECTION_INTERVAL_MS = 2500;
const DUPLICATE_WINDOW_MS = 30000;
const NO_PLATE_MESSAGE_MS = 2500;

/**
 * Converts a react-webcam screenshot data URL into a File the backend
 * can accept as multipart/form-data, without round-tripping through
 * fetch().
 */
function dataUrlToFile(dataUrl, filename = "frame.jpg") {
  const [header, base64] = dataUrl.split(",");
  const mimeMatch = header.match(/:(.*?);/);
  const mime = mimeMatch ? mimeMatch[1] : "image/jpeg";

  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);

  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }

  return new File([bytes], filename, { type: mime });
}

/**
 * Normalizes a DetectPlateView response into the same shape as the
 * records returned by GET recent-detections/ (EntryExitRecordSerializer),
 * so the recent-detections grid can render both without branching.
 */
function toRecentRecord(detection) {
  const attributes = detection.detected_vehicle_attributes || {};

  return {
    id: detection.record_id,
    detected_plate_text: detection.normalized_plate || detection.detected_plate,
    timestamp: detection.timestamp,
    captured_image: detection.captured_image,
    was_authorized: detection.was_authorized,
    confidence_score: detection.confidence_score,
    direction: detection.direction,
    detected_vehicle_type: attributes.vehicle_type,
    vehicle_color: attributes.vehicle_color,
    detected_vehicle_company: attributes.vehicle_company,
    detected_vehicle_model: attributes.vehicle_model,
  };
}

export default function LiveMonitor() {
  const { gates } = useReferenceData();
  const navigate = useNavigate();

  // Refs
  const webcamRef = useRef(null);
  const intervalRef = useRef(null);
  const requestInProgressRef = useRef(false);
  const mountedRef = useRef(true);
  const lastDetectionRef = useRef({ plate: null, timestamp: 0 });
  const noPlateTimeoutRef = useRef(null);

  // State
  const [selectedGate, setSelectedGate] = useState("");
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraReady, setCameraReady] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [latestDetection, setLatestDetection] = useState(null);
  const [recentDetectionsList, setRecentDetectionsList] = useState([]);
  const [scanMessage, setScanMessage] = useState("");
  const [error, setError] = useState("");

  const loadRecent = useCallback(async () => {
    try {
      const data = await recentDetections(25);
      setRecentDetectionsList(Array.isArray(data) ? data : data.results || []);
    } catch {
      // Recent detections should not block the live monitor.
    }
  }, []);

  const stopCamera = useCallback(() => {
    if (intervalRef.current) {
      window.clearInterval(intervalRef.current);
      intervalRef.current = null;
    }

    if (noPlateTimeoutRef.current) {
      window.clearTimeout(noPlateTimeoutRef.current);
      noPlateTimeoutRef.current = null;
    }

    const stream = webcamRef.current?.video?.srcObject;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
    }

    requestInProgressRef.current = false;
    setCameraActive(false);
    setCameraReady(false);
    setDetecting(false);
    setScanMessage("");
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    loadRecent();

    return () => {
      mountedRef.current = false;
      stopCamera();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const captureAndDetect = useCallback(async () => {
    if (requestInProgressRef.current) return;
    if (!webcamRef.current) return;

    const screenshot = webcamRef.current.getScreenshot();
    if (!screenshot) return;

    requestInProgressRef.current = true;
    setDetecting(true);

    try {
      const frameFile = dataUrlToFile(screenshot);

      const data = await detectPlate({
        image: frameFile,
        gate: selectedGate || undefined,
        source: "WEBCAM",
      });

      if (!mountedRef.current) return;

      setError("");

      if (!data.success) {
        setScanMessage("No plate detected");

        if (noPlateTimeoutRef.current) window.clearTimeout(noPlateTimeoutRef.current);
        noPlateTimeoutRef.current = window.setTimeout(() => {
          if (mountedRef.current) setScanMessage("");
        }, NO_PLATE_MESSAGE_MS);

        return;
      }

      setScanMessage("");
      setLatestDetection(data);

      const plate = data.normalized_plate;
      const now = Date.now();

      const isDuplicate =
        data.duplicate_skipped ||
        (plate &&
          lastDetectionRef.current.plate === plate &&
          now - lastDetectionRef.current.timestamp < DUPLICATE_WINDOW_MS);

      if (!isDuplicate) {
        lastDetectionRef.current = { plate, timestamp: now };

        setRecentDetectionsList((current) => [toRecentRecord(data), ...current].slice(0, 25));

        if (data.was_authorized) {
          toast.success(`Access authorized \u2014 ${plate}`);
        } else {
          toast.error(`Unauthorized vehicle \u2014 ${plate}`);
        }
      }
    } catch (requestError) {
      console.error("Detection request failed:", requestError);
      if (mountedRef.current) {
        setError("Detection request failed. Still scanning\u2026");
      }
    } finally {
      requestInProgressRef.current = false;
      if (mountedRef.current) setDetecting(false);
    }
  }, [selectedGate]);

  function handleStartCamera() {
    if (!selectedGate) {
      toast.error("Select a gate before starting the camera");
      return;
    }

    setError("");
    setLatestDetection(null);
    setCameraActive(true);

    intervalRef.current = window.setInterval(() => {
      captureAndDetect();
    }, DETECTION_INTERVAL_MS);
  }

  function handleStopCamera() {
    stopCamera();
  }

  const statistics = useMemo(() => {
    const authorized = recentDetectionsList.filter((r) => r.was_authorized).length;
    const unauthorized = recentDetectionsList.filter((r) => !r.was_authorized).length;

    const needsReview = recentDetectionsList.filter((r) => {
      const confidence = Number(r.confidence_score || 0);
      return confidence > 0 && confidence < 0.75;
    }).length;

    return {
      total: recentDetectionsList.length,
      authorized,
      unauthorized,
      needsReview,
    };
  }, [recentDetectionsList]);

  const selectedGateDetails = gates.find((item) => String(item.id) === String(selectedGate));

  const resultAuthorized = latestDetection?.was_authorized === true;

  const resultConfidence = Math.round(Number(latestDetection?.confidence_score || 0) * 100);

  const attributes = latestDetection?.detected_vehicle_attributes;

  return (
    <div className="space-y-6 animate-fadeIn">
      {/* Top controls */}
      <div className="flex flex-wrap items-center gap-3">
        <SelectField
          placeholder="Select a gate"
          options={gates.map((item) => ({
            value: item.id,
            label: item.name,
          }))}
          value={selectedGate}
          onChange={(event) => setSelectedGate(event.target.value)}
          disabled={cameraActive}
          className="w-56"
        />

        {!cameraActive ? (
          <button
            type="button"
            onClick={handleStartCamera}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700"
          >
            <LuCamera size={16} />
            Start Camera
          </button>
        ) : (
          <button
            type="button"
            onClick={handleStopCamera}
            className="inline-flex items-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-red-700"
          >
            <LuVideoOff size={16} />
            Stop Camera
          </button>
        )}

        <div
          className={`ml-auto flex items-center gap-2 rounded-full border px-3 py-2 text-xs font-semibold ${
            cameraActive
              ? "border-emerald-100 bg-emerald-50 text-emerald-700"
              : "border-slate-100 bg-slate-50 text-slate-500"
          }`}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              cameraActive ? "bg-emerald-500 animate-pulse" : "bg-slate-300"
            }`}
          />
          {cameraActive ? "Scanning Automatically" : "Camera Stopped"}
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-700">
          <LuTriangleAlert size={16} className="shrink-0" />
          {error}
        </div>
      )}

      {/* Statistics */}
      <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
        <MonitorStatCard title="Recent Scans" value={statistics.total} icon={LuScanLine} tone="blue" />
        <MonitorStatCard title="Authorized" value={statistics.authorized} icon={LuShieldCheck} tone="green" />
        <MonitorStatCard title="Unauthorized" value={statistics.unauthorized} icon={LuShieldAlert} tone="red" />
        <MonitorStatCard title="Needs Review" value={statistics.needsReview} icon={LuTriangleAlert} tone="amber" />
      </div>

      {/* Main monitor */}
      <div className="grid gap-6 xl:grid-cols-3">
        <div className="space-y-6 xl:col-span-2">
          <div className="relative overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-card">
            {/* Camera information overlay */}
            {cameraActive && (
              <div className="pointer-events-none absolute left-4 top-4 z-20 flex flex-wrap gap-2">
                <div className="flex items-center gap-2 rounded-full bg-slate-950/75 px-3 py-1.5 text-xs font-semibold text-white backdrop-blur">
                  <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                  LIVE
                </div>

                <div className="rounded-full bg-slate-950/75 px-3 py-1.5 text-xs font-medium text-white backdrop-blur">
                  {selectedGateDetails?.name || "Gate"}
                </div>

                {detecting && (
                  <div className="hidden items-center gap-1.5 rounded-full bg-blue-600/90 px-3 py-1.5 text-xs font-medium text-white backdrop-blur sm:flex">
                    <LuScanLine size={13} className="animate-pulse" />
                    Analyzing frame
                  </div>
                )}
              </div>
            )}

            {scanMessage && cameraActive && (
              <div className="pointer-events-none absolute bottom-4 left-1/2 z-20 -translate-x-1/2 rounded-full bg-slate-950/75 px-4 py-1.5 text-xs font-medium text-white backdrop-blur">
                {scanMessage}
              </div>
            )}

            <div className="aspect-video w-full bg-slate-950">
              {cameraActive ? (
                <Webcam
                  ref={webcamRef}
                  audio={false}
                  screenshotFormat="image/jpeg"
                  videoConstraints={{ facingMode: "environment" }}
                  onUserMedia={() => setCameraReady(true)}
                  onUserMediaError={() => {
                    setError("Camera access denied or unavailable.");
                    stopCamera();
                  }}
                  className="h-full w-full object-cover"
                />
              ) : (
                <div className="grid h-full w-full place-items-center text-slate-500">
                  <div className="text-center">
                    <LuVideoOff size={32} className="mx-auto mb-2 opacity-60" />
                    <p className="text-sm font-medium">Camera stopped</p>
                    <p className="mt-1 text-xs text-slate-600">
                      Select a gate and press Start Camera
                    </p>
                  </div>
                </div>
              )}
            </div>

            {cameraActive && !cameraReady && (
              <div className="absolute inset-0 z-30 grid place-items-center bg-slate-950/60 backdrop-blur-[2px]">
                <p className="text-sm font-medium text-white">Starting camera\u2026</p>
              </div>
            )}
          </div>

          {/* Recent detections */}
          <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-card">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="font-display font-semibold text-ink-950">Recent Detections</h3>
                <p className="mt-1 text-xs text-slate-400">Latest ANPR activity from connected cameras</p>
              </div>

              <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-600">
                {recentDetectionsList.length} records
              </span>
            </div>

            {recentDetectionsList.length === 0 ? (
              <EmptyState title="No detections yet" message="Detected plates will appear here." />
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
                {recentDetectionsList.slice(0, 10).map((record, index) => {
                  const confidence = Math.round(Number(record.confidence_score || 0) * 100);
                  const needsReview = confidence > 0 && confidence < 75;

                  return (
                    <article
                      key={record.id ?? `${record.detected_plate_text}-${record.timestamp}`}
                      onClick={() => record.id && navigate(`/records/${record.id}`)}
                      className={`group relative cursor-pointer overflow-hidden rounded-xl border bg-white transition duration-200 hover:-translate-y-1 hover:shadow-lg ${
                        index === 0 ? "border-blue-300 ring-2 ring-blue-100" : "border-slate-100"
                      }`}
                    >
                      {index === 0 && (
                        <span className="absolute left-2 top-2 z-10 rounded-full bg-blue-600 px-2 py-1 text-[9px] font-bold uppercase tracking-wide text-white shadow">
                          New
                        </span>
                      )}

                      <div className="aspect-video overflow-hidden bg-slate-100">
                        {record.captured_image ? (
                          <img
                            src={resolveMediaUrl(record.captured_image)}
                            alt={record.detected_plate_text || "Detected vehicle"}
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
                          <p
                            className="truncate text-sm font-extrabold tracking-wide text-ink-950"
                            title={record.detected_plate_text}
                          >
                            {record.detected_plate_text || "UNREADABLE"}
                          </p>

                          <p className="mt-0.5 text-[11px] text-slate-400">
                            {formatTime(record.timestamp)}
                            {record.direction ? ` \u00b7 ${record.direction}` : ""}
                          </p>
                        </div>

                        <div className="flex items-center justify-between gap-2">
                          {needsReview ? (
                            <span className="inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-2 py-1 text-[10px] font-semibold text-amber-700">
                              <LuTriangleAlert size={10} />
                              Review
                            </span>
                          ) : (
                            <StatusBadge status={record.was_authorized ? "AUTHORIZED" : "UNAUTHORIZED"} />
                          )}

                          {confidence > 0 && (
                            <span className="text-[10px] font-semibold text-slate-400">{confidence}%</span>
                          )}
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {/* Detected vehicle panel */}
        <aside className="h-fit rounded-2xl border border-slate-100 bg-white p-5 shadow-card xl:sticky xl:top-6">
          <div className="mb-5 flex items-center justify-between">
            <div>
              <h3 className="font-display font-semibold text-ink-950">Detected Vehicle</h3>
              <p className="mt-1 text-xs text-slate-400">Latest scan result</p>
            </div>

            <div
              className={`grid h-10 w-10 place-items-center rounded-xl ${
                latestDetection?.success
                  ? resultAuthorized
                    ? "bg-emerald-50 text-emerald-600"
                    : "bg-red-50 text-red-600"
                  : "bg-slate-100 text-slate-400"
              }`}
            >
              <LuCar size={20} />
            </div>
          </div>

          {!latestDetection && !detecting && (
            <EmptyState
              title="Waiting for a scan"
              message="Start the camera to see vehicle details as plates are detected."
            />
          )}

          {latestDetection && latestDetection.success && (
            <div className="space-y-5">
              {/* Plate */}
              <div
                className={`rounded-2xl border p-4 ${
                  resultAuthorized ? "border-emerald-100 bg-emerald-50/70" : "border-red-100 bg-red-50/70"
                }`}
              >
                <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-slate-400">Number plate</p>

                <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
                  <p className="font-display text-2xl font-black tracking-wider text-ink-950">
                    {latestDetection.normalized_plate || "UNREADABLE"}
                  </p>

                  <StatusBadge status={latestDetection.authorization_status} />
                </div>
              </div>

              {/* Information */}
              <div className="space-y-2">
                <VehicleInfoRow
                  icon={LuCar}
                  label="Vehicle type"
                  value={attributes?.vehicle_type || "Unknown"}
                />

                <VehicleInfoRow icon={LuPalette} label="Color" value={attributes?.vehicle_color || "Unknown"} />

                <VehicleInfoRow
                  icon={LuBuilding2}
                  label="Company / Model"
                  value={
                    attributes?.vehicle_company && attributes.vehicle_company !== "Unknown"
                      ? `${attributes.vehicle_company} ${attributes.vehicle_model || ""}`.trim()
                      : "Unknown"
                  }
                />

                <VehicleInfoRow
                  icon={LuBuilding2}
                  label="Owner"
                  value={latestDetection.vehicle?.owner_name || "No owner on record"}
                />

                <VehicleInfoRow
                  icon={LuMapPin}
                  label="Gate"
                  value={selectedGateDetails?.name || "\u2014"}
                />

                <VehicleInfoRow icon={LuClock} label="Detected at" value={formatDateTime(new Date())} />

                <VehicleInfoRow icon={LuGauge} label="Plate confidence" value={`${resultConfidence}%`} />
              </div>

              {/* Confidence bar */}
              <div>
                <div className="mb-2 flex items-center justify-between text-xs">
                  <span className="font-medium text-slate-500">OCR confidence</span>
                  <span className="font-bold text-slate-700">{resultConfidence}%</span>
                </div>

                <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                  <div
                    className={`h-full rounded-full transition-all duration-700 ${
                      resultConfidence < 75
                        ? "bg-amber-500"
                        : resultAuthorized
                        ? "bg-emerald-500"
                        : "bg-red-500"
                    }`}
                    style={{ width: `${Math.min(resultConfidence, 100)}%` }}
                  />
                </div>
              </div>

              {resultConfidence > 0 && resultConfidence < 75 && (
                <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700">
                  <LuTriangleAlert size={16} className="mt-0.5 shrink-0" />
                  <span>This result has low confidence and should be reviewed before taking action.</span>
                </div>
              )}

              {latestDetection.duplicate_skipped ? (
                <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-3 text-xs font-semibold text-slate-500">
                  Duplicate scan \u2014 no new record created
                </div>
              ) : (
                latestDetection.record_created && (
                  <div className="flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 px-3 py-3 text-xs font-semibold text-emerald-700">
                    <LuCheckCheck size={16} />
                    {latestDetection.direction === "EXIT" ? "Exit recorded successfully" : "Entry recorded successfully"}
                  </div>
                )
              )}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

function MonitorStatCard({ title, value, icon: Icon, tone }) {
  const styles = {
    blue: "bg-blue-50 text-blue-600 border-blue-100",
    green: "bg-emerald-50 text-emerald-600 border-emerald-100",
    red: "bg-red-50 text-red-600 border-red-100",
    amber: "bg-amber-50 text-amber-600 border-amber-100",
  };

  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-card transition hover:-translate-y-0.5 hover:shadow-lg">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-slate-500">{title}</p>
          <p className="mt-1 font-display text-2xl font-black text-ink-950">{value}</p>
        </div>

        <div className={`grid h-11 w-11 place-items-center rounded-xl border ${styles[tone] || styles.blue}`}>
          <Icon size={21} />
        </div>
      </div>
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
        <p className="truncate text-sm font-semibold capitalize text-slate-700">{value}</p>
      </div>
    </div>
  );
}
