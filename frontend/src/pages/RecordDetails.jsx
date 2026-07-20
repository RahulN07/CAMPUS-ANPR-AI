import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import toast from "react-hot-toast";

import {
  LuArrowLeft,
  LuBuilding2,
  LuCalendarClock,
  LuCamera,
  LuCar,
  LuCheck,
  LuChevronRight,
  LuCircleAlert,
  LuClock3,
  LuGauge,
  LuLogIn,
  LuLogOut,
  LuMail,
  LuMapPin,
  LuPhone,
  LuRefreshCw,
  LuScanLine,
  LuShieldAlert,
  LuShieldCheck,
  LuUser,
  LuX,
} from "react-icons/lu";

import { getRecord } from "../services/recordService";
import { resolveMediaUrl } from "../api/axios";
import { Spinner } from "../components/Loader";

export default function RecordDetails() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [record, setRecord] = useState(null);
  const [loading, setLoading] = useState(true);
  const [imageOpen, setImageOpen] = useState(false);

  async function loadRecord() {
    try {
      setLoading(true);

      const data = await getRecord(id);
      setRecord(data);
    } catch (error) {
      console.error("Failed to load detection record:", error);
      toast.error("Unable to load detection details.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadRecord();
  }, [id]);

  if (loading) {
    return (
      <div className="grid min-h-[60vh] place-items-center">
        <div className="flex flex-col items-center gap-3">
          <Spinner size={26} />
          <p className="text-sm text-slate-500">
            Loading detection details...
          </p>
        </div>
      </div>
    );
  }

  if (!record) {
    return (
      <div className="grid min-h-[60vh] place-items-center">
        <div className="max-w-md text-center">
          <div className="mx-auto grid h-16 w-16 place-items-center rounded-2xl bg-red-50 text-red-500">
            <LuCircleAlert size={30} />
          </div>

          <h2 className="mt-5 text-xl font-bold text-slate-900">
            Detection record not found
          </h2>

          <p className="mt-2 text-sm text-slate-500">
            The selected ANPR record may have been removed or is unavailable.
          </p>

          <button
            type="button"
            onClick={() => navigate("/entry-exit-logs")}
            className="mt-6 inline-flex items-center gap-2 rounded-xl bg-brand-600 px-5 py-3 text-sm font-semibold text-white transition hover:bg-brand-700"
          >
            <LuArrowLeft size={17} />
            Back to Logs
          </button>
        </div>
      </div>
    );
  }

  const plate = getPlate(record);
  const authorized = isAuthorized(record);
  const image = getRecordImage(record);
  const vehicle = getVehicle(record);

  const ownerName =
    record.owner_name ||
    vehicle?.owner_name ||
    "Unknown";

  const ownerType =
    record.owner_type ||
    vehicle?.owner_type ||
    "Not available";

  const ownerEmail =
    record.owner_email ||
    vehicle?.owner_email ||
    "Not available";

  const ownerPhone =
    record.owner_phone ||
    vehicle?.owner_phone ||
    "Not available";

  const department =
    getDepartmentName(record, vehicle);

  const vehicleCompany =
    record.vehicle_company ||
    vehicle?.vehicle_company ||
    vehicle?.company_name ||
    "Unknown";

  const vehicleModel =
    record.vehicle_model ||
    vehicle?.vehicle_model ||
    vehicle?.model_name ||
    "Unknown";

  const vehicleType =
    record.vehicle_type ||
    vehicle?.vehicle_type ||
    "Unknown";

  const vehicleColor =
    record.color ||
    vehicle?.color ||
    "Unknown";

  const direction = getDirection(record);
  const gate = getGateName(record);
  const confidence = getConfidence(record);
  const detectionSource = formatLabel(
    record.detection_source ||
      record.source ||
      "ANPR"
  );

  const detectedAt = getTimestamp(record);

  return (
    <>
      <div className="space-y-6 animate-fadeIn">
        {/* Breadcrumb and actions */}
        <div className="flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
          <div>
            <div className="flex flex-wrap items-center gap-2 text-sm text-slate-400">
              <Link
                to="/entry-exit-logs"
                className="transition hover:text-brand-600"
              >
                Entry / Exit Logs
              </Link>

              <LuChevronRight size={15} />

              <span className="text-slate-600">
                Detection #{record.id}
              </span>
            </div>

            <h1 className="mt-2 font-display text-2xl font-bold text-ink-950">
              Detection Details
            </h1>

            <p className="mt-1 text-sm text-slate-500">
              Complete ANPR detection and vehicle access information.
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => navigate(-1)}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50"
            >
              <LuArrowLeft size={17} />
              Back
            </button>

            <button
              type="button"
              onClick={loadRecord}
              className="inline-flex items-center gap-2 rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
            >
              <LuRefreshCw size={17} />
              Refresh
            </button>
          </div>
        </div>

        {/* Main summary */}
        <div className="grid gap-6 xl:grid-cols-[1.3fr_0.7fr]">
          {/* Captured image */}
          <section className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-card">
            <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
              <div>
                <h2 className="font-display font-semibold text-ink-950">
                  Captured Snapshot
                </h2>

                <p className="mt-1 text-xs text-slate-400">
                  Image captured during the ANPR detection.
                </p>
              </div>

              <div className="grid h-10 w-10 place-items-center rounded-xl bg-brand-50 text-brand-600">
                <LuCamera size={20} />
              </div>
            </div>

            <div className="p-5">
              <button
                type="button"
                onClick={() => image && setImageOpen(true)}
                disabled={!image}
                className="group relative block aspect-video w-full overflow-hidden rounded-2xl bg-slate-100 disabled:cursor-default"
              >
                {image ? (
                  <>
                    <img
                      src={image}
                      alt={`Detection ${plate}`}
                      className="h-full w-full object-contain transition duration-300 group-hover:scale-[1.02]"
                    />

                    <div className="absolute inset-0 grid place-items-center bg-black/0 transition group-hover:bg-black/10">
                      <span className="translate-y-2 rounded-full bg-black/70 px-4 py-2 text-xs font-semibold text-white opacity-0 transition group-hover:translate-y-0 group-hover:opacity-100">
                        View full image
                      </span>
                    </div>
                  </>
                ) : (
                  <div className="grid h-full place-items-center">
                    <div className="text-center">
                      <LuCamera
                        size={38}
                        className="mx-auto text-slate-300"
                      />

                      <p className="mt-3 text-sm font-medium text-slate-500">
                        No captured image
                      </p>

                      <p className="mt-1 text-xs text-slate-400">
                        An image was not saved for this detection.
                      </p>
                    </div>
                  </div>
                )}
              </button>
            </div>
          </section>

          {/* Plate summary */}
          <section className="rounded-2xl border border-slate-100 bg-white p-5 shadow-card">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                  Detected Number Plate
                </p>

                <div className="mt-3 inline-flex rounded-xl border-2 border-slate-900 bg-white px-5 py-3 shadow-sm">
                  <span className="font-display text-2xl font-black uppercase tracking-[0.15em] text-slate-950">
                    {plate}
                  </span>
                </div>
              </div>

              <AuthorizationBadge authorized={authorized} />
            </div>

            <div className="mt-6 space-y-4">
              <SummaryLine
                icon={direction === "EXIT" ? LuLogOut : LuLogIn}
                label="Direction"
                value={formatLabel(direction)}
              />

              <SummaryLine
                icon={LuMapPin}
                label="Gate"
                value={gate}
              />

              <SummaryLine
                icon={LuCalendarClock}
                label="Detected At"
                value={formatDateTime(detectedAt)}
              />

              <SummaryLine
                icon={LuGauge}
                label="Confidence"
                value={confidence}
              />

              <SummaryLine
                icon={LuScanLine}
                label="Detection Source"
                value={detectionSource}
              />
            </div>
          </section>
        </div>

        {/* Information cards */}
        <div className="grid gap-6 xl:grid-cols-3">
          <InformationCard
            title="Detection Information"
            icon={LuScanLine}
          >
            <InfoRow
              label="Record ID"
              value={`#${record.id}`}
            />

            <InfoRow
              label="Plate Number"
              value={plate}
              monospace
            />

            <InfoRow
              label="Direction"
              value={formatLabel(direction)}
            />

            <InfoRow
              label="Gate"
              value={gate}
            />

            <InfoRow
              label="Detection Time"
              value={formatDateTime(detectedAt)}
            />

            <InfoRow
              label="Confidence Score"
              value={confidence}
            />

            <InfoRow
              label="Detection Source"
              value={detectionSource}
            />

            <InfoRow
              label="Access Result"
              value={authorized ? "Authorized" : "Unauthorized"}
              valueClassName={
                authorized
                  ? "text-emerald-600"
                  : "text-red-600"
              }
            />
          </InformationCard>

          <InformationCard
            title="Owner Information"
            icon={LuUser}
          >
            <InfoRow
              icon={LuUser}
              label="Owner Name"
              value={ownerName}
            />

            <InfoRow
              icon={LuBuilding2}
              label="Owner Type"
              value={formatLabel(ownerType)}
            />

            <InfoRow
              icon={LuBuilding2}
              label="Department"
              value={department}
            />

            <InfoRow
              icon={LuMail}
              label="Email"
              value={ownerEmail}
            />

            <InfoRow
              icon={LuPhone}
              label="Phone"
              value={ownerPhone}
            />
          </InformationCard>

          <InformationCard
            title="Vehicle Information"
            icon={LuCar}
          >
            <InfoRow
              label="Registration"
              value={
                vehicle?.registration_number ||
                record.registration_number ||
                plate
              }
              monospace
            />

            <InfoRow
              label="Company"
              value={vehicleCompany}
            />

            <InfoRow
              label="Model"
              value={vehicleModel}
            />

            <InfoRow
              label="Vehicle Type"
              value={formatLabel(vehicleType)}
            />

            <InfoRow
              label="Color"
              value={formatLabel(vehicleColor)}
            />

            <InfoRow
              label="Registration Status"
              value={
                vehicle || record.vehicle_id
                  ? "Registered"
                  : "Not Registered"
              }
              valueClassName={
                vehicle || record.vehicle_id
                  ? "text-emerald-600"
                  : "text-orange-600"
              }
            />
          </InformationCard>
        </div>

        {/* Access decision */}
        <section
          className={`rounded-2xl border p-5 ${
            authorized
              ? "border-emerald-200 bg-emerald-50"
              : "border-red-200 bg-red-50"
          }`}
        >
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-4">
              <div
                className={`grid h-12 w-12 shrink-0 place-items-center rounded-xl ${
                  authorized
                    ? "bg-emerald-100 text-emerald-700"
                    : "bg-red-100 text-red-700"
                }`}
              >
                {authorized ? (
                  <LuShieldCheck size={24} />
                ) : (
                  <LuShieldAlert size={24} />
                )}
              </div>

              <div>
                <h3
                  className={`font-display text-lg font-bold ${
                    authorized
                      ? "text-emerald-900"
                      : "text-red-900"
                  }`}
                >
                  {authorized
                    ? "Vehicle access was authorized"
                    : "Unauthorized vehicle detected"}
                </h3>

                <p
                  className={`mt-1 text-sm ${
                    authorized
                      ? "text-emerald-700"
                      : "text-red-700"
                  }`}
                >
                  {authorized
                    ? `${plate} matched a registered and authorized vehicle.`
                    : `${plate} did not match an authorized vehicle at the time of detection.`}
                </p>
              </div>
            </div>

            {vehicle?.id && (
              <button
                type="button"
                onClick={() =>
                  navigate(`/vehicles/${vehicle.id}`)
                }
                className={`inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold transition ${
                  authorized
                    ? "bg-emerald-700 text-white hover:bg-emerald-800"
                    : "bg-red-700 text-white hover:bg-red-800"
                }`}
              >
                <LuCar size={17} />
                View Registered Vehicle
              </button>
            )}
          </div>
        </section>
      </div>

      {/* Full image modal */}
      {imageOpen && image && (
        <div
          className="fixed inset-0 z-50 grid place-items-center bg-black/80 p-4"
          onClick={() => setImageOpen(false)}
        >
          <button
            type="button"
            onClick={() => setImageOpen(false)}
            className="absolute right-5 top-5 grid h-11 w-11 place-items-center rounded-full bg-white/10 text-white transition hover:bg-white/20"
            aria-label="Close image"
          >
            <LuX size={23} />
          </button>

          <img
            src={image}
            alt={`Full detection ${plate}`}
            onClick={(event) => event.stopPropagation()}
            className="max-h-[90vh] max-w-[95vw] rounded-xl object-contain shadow-2xl"
          />
        </div>
      )}
    </>
  );
}

function InformationCard({ title, icon: Icon, children }) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-card">
      <div className="flex items-center gap-3 border-b border-slate-100 px-5 py-4">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-brand-50 text-brand-600">
          <Icon size={18} />
        </div>

        <h2 className="font-display font-semibold text-ink-950">
          {title}
        </h2>
      </div>

      <div className="divide-y divide-slate-100 px-5">
        {children}
      </div>
    </section>
  );
}

function InfoRow({
  icon: Icon,
  label,
  value,
  monospace = false,
  valueClassName = "",
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-3.5">
      <div className="flex items-center gap-2 text-sm text-slate-500">
        {Icon && (
          <Icon
            size={15}
            className="shrink-0 text-slate-400"
          />
        )}

        <span>{label}</span>
      </div>

      <span
        className={`max-w-[60%] break-words text-right text-sm font-semibold text-slate-800 ${
          monospace
            ? "font-display uppercase tracking-wider"
            : ""
        } ${valueClassName}`}
      >
        {value || "Not available"}
      </span>
    </div>
  );
}

function SummaryLine({ icon: Icon, label, value }) {
  return (
    <div className="flex items-center gap-3">
      <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500">
        <Icon size={17} />
      </div>

      <div className="min-w-0">
        <p className="text-xs text-slate-400">
          {label}
        </p>

        <p className="truncate text-sm font-semibold text-slate-800">
          {value}
        </p>
      </div>
    </div>
  );
}

function AuthorizationBadge({ authorized }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-bold ${
        authorized
          ? "border-emerald-200 bg-emerald-50 text-emerald-700"
          : "border-red-200 bg-red-50 text-red-700"
      }`}
    >
      {authorized ? (
        <LuCheck size={14} />
      ) : (
        <LuShieldAlert size={14} />
      )}

      {authorized ? "Authorized" : "Unauthorized"}
    </span>
  );
}

function getPlate(record) {
  return String(
    record.detected_plate_text ||
      record.detected_plate ||
      record.number_plate ||
      record.registration_number ||
      record.vehicle?.registration_number ||
      "UNKNOWN"
  )
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
}

function isAuthorized(record) {
  if (typeof record.was_authorized === "boolean") {
    return record.was_authorized;
  }

  const status = String(
    record.authorization_status ||
      record.status ||
      ""
  ).toUpperCase();

  return status === "AUTHORIZED";
}

function getVehicle(record) {
  return typeof record.vehicle === "object"
    ? record.vehicle
    : record.vehicle_details ||
        record.vehicle_detail ||
        null;
}

function getRecordImage(record) {
  const image =
    record.captured_image ||
    record.snapshot ||
    record.plate_image ||
    record.image ||
    record.vehicle_image ||
    null;

  return image ? resolveMediaUrl(image) : null;
}

function getGateName(record) {
  if (record.gate && typeof record.gate === "object") {
    return (
      record.gate.name ||
      record.gate.gate_name ||
      "Unknown Gate"
    );
  }

  return (
    record.gate_name ||
    record.gate_display ||
    record.gate ||
    "Unknown Gate"
  );
}

function getDirection(record) {
  return String(
    record.direction ||
      record.record_type ||
      "ENTRY"
  ).toUpperCase();
}

function getTimestamp(record) {
  return (
    record.timestamp ||
    record.entry_time ||
    record.exit_time ||
    record.created_at ||
    null
  );
}

function getConfidence(record) {
  const rawValue =
    record.confidence_score ??
    record.confidence ??
    record.ocr_confidence;

  if (
    rawValue === null ||
    rawValue === undefined ||
    rawValue === ""
  ) {
    return "Not available";
  }

  const numericValue = Number(rawValue);

  if (Number.isNaN(numericValue)) {
    return String(rawValue);
  }

  const percentage =
    numericValue <= 1
      ? numericValue * 100
      : numericValue;

  return `${percentage.toFixed(1)}%`;
}

function getDepartmentName(record, vehicle) {
  if (
    vehicle?.department &&
    typeof vehicle.department === "object"
  ) {
    return (
      vehicle.department.name ||
      vehicle.department.department_name ||
      "Not available"
    );
  }

  return (
    record.department_name ||
    vehicle?.department_name ||
    vehicle?.department ||
    "Not available"
  );
}

function formatDateTime(value) {
  if (!value) {
    return "Not available";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatLabel(value) {
  if (!value) {
    return "Not available";
  }

  return String(value)
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .toLowerCase()
    .replace(/\b\w/g, (character) =>
      character.toUpperCase()
    );
}