import { useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";

import {
  LuArrowRight,
  LuBuilding2,
  LuCamera,
  LuCar,
  LuClock3,
  LuMapPin,
  LuSearch,
  LuShieldAlert,
  LuShieldCheck,
  LuUser,
  LuX,
} from "react-icons/lu";

import { listVehicles } from "../services/vehicleService";
import { listRecords } from "../services/recordService";

import StatusBadge from "../components/StatusBadge";
import EmptyState from "../components/EmptyState";
import { Spinner } from "../components/Loader";

import { resolveMediaUrl } from "../api/axios";
import { labelFor, VEHICLE_TYPES } from "../utils/constants";

const SEARCH_LIMIT = 100;

export default function SearchVehicle() {
  const navigate = useNavigate();

  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);

  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const [searchedQuery, setSearchedQuery] = useState("");

  async function handleSearch(event) {
    event.preventDefault();

    const cleanedQuery = normalizeSearch(query);

    if (!cleanedQuery) {
      toast.error("Enter a plate number or owner name");
      return;
    }

    setLoading(true);
    setSearched(true);
    setSearchedQuery(cleanedQuery);

    try {
      /*
       * Search both:
       * 1. Registered Vehicle table
       * 2. EntryExitRecord table
       */
      const [vehicleResponse, recordResponse] =
        await Promise.allSettled([
          listVehicles({
            search: cleanedQuery,
            page_size: SEARCH_LIMIT,
          }),

          listRecords({
            search: cleanedQuery,
            page_size: SEARCH_LIMIT,
            ordering: "-timestamp",
          }),
        ]);

      const registeredVehicles =
        vehicleResponse.status === "fulfilled"
          ? extractResults(vehicleResponse.value)
          : [];

      const detectionRecords =
        recordResponse.status === "fulfilled"
          ? extractResults(recordResponse.value)
          : [];

      if (
        vehicleResponse.status === "rejected" &&
        recordResponse.status === "rejected"
      ) {
        throw new Error("Both search requests failed");
      }

      const combinedResults = combineSearchResults(
        registeredVehicles,
        detectionRecords
      );

      setResults(combinedResults);
    } catch (error) {
      console.error("Vehicle search failed:", error);
      setResults([]);
      toast.error("Search failed. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function clearSearch() {
    setQuery("");
    setResults([]);
    setSearched(false);
    setSearchedQuery("");
  }

  function openResult(result) {
    if (result.registered && result.vehicleId) {
      navigate(`/vehicles/${result.vehicleId}`);
      return;
    }

    /*
     * The project currently does not have a confirmed detection-details route.
     * Open the Entry/Exit Logs page for detected-only vehicles.
     */
    if (result.recordId) {
      navigate(`/records/${result.recordId}`);
      return;
    }

    navigate("/entry-exit-logs");
    }

  const registeredCount = results.filter(
    (result) => result.registered
  ).length;

  const detectedOnlyCount = results.filter(
    (result) => !result.registered
  ).length;

  const authorizedCount = results.filter(
    (result) => result.authorized
  ).length;

  const unauthorizedCount = results.filter(
    (result) => !result.authorized
  ).length;

  return (
    <div className="space-y-6 animate-fadeIn">
      {/* Page heading */}
      <div>
        <h1 className="font-display text-2xl font-bold text-ink-950">
          Search Vehicle
        </h1>

        <p className="mt-1 text-sm text-slate-500">
          Search registered vehicles and ANPR detection
          records using a state code, plate number, or owner
          name.
        </p>
      </div>

      {/* Search area */}
      <div className="rounded-2xl border border-slate-100 bg-white p-5 shadow-card">
        <form
          onSubmit={handleSearch}
          className="flex flex-col gap-3 sm:flex-row"
        >
          <div className="relative min-w-0 flex-1">
            <LuSearch
              size={19}
              className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-400"
            />

            <input
              autoFocus
              type="text"
              value={query}
              onChange={(event) =>
                setQuery(event.target.value.toUpperCase())
              }
              placeholder="Search KA, KL, HR51, KA14EY8151 or owner name"
              className="w-full rounded-xl border border-slate-200 bg-white py-3.5 pl-11 pr-11 text-sm text-ink-950 outline-none transition placeholder:text-slate-400 focus:border-brand-400 focus:ring-4 focus:ring-brand-100"
            />

            {query && (
              <button
                type="button"
                onClick={clearSearch}
                className="absolute right-4 top-1/2 grid -translate-y-1/2 place-items-center text-slate-400 transition hover:text-slate-700"
                aria-label="Clear search"
              >
                <LuX size={18} />
              </button>
            )}
          </div>

          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="inline-flex min-w-28 items-center justify-center gap-2 rounded-xl bg-brand-600 px-6 py-3.5 text-sm font-semibold text-white transition hover:bg-brand-700 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loading ? (
              <>
                <Spinner
                  size={16}
                  className="text-white"
                />
                Searching
              </>
            ) : (
              <>
                <LuSearch size={17} />
                Search
              </>
            )}
          </button>
        </form>

        <div className="mt-4 flex flex-wrap gap-2 text-xs text-slate-500">
          <SearchExample text="KA" />
          <SearchExample text="KL" />
          <SearchExample text="HR51" />
          <SearchExample text="KA14EY8151" />
        </div>
      </div>

      {/* Search summary */}
      {searched && !loading && (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <SummaryCard
              title="Total Results"
              value={results.length}
              icon={LuCar}
              tone="blue"
            />

            <SummaryCard
              title="Registered"
              value={registeredCount}
              icon={LuShieldCheck}
              tone="green"
            />

            <SummaryCard
              title="Detected Only"
              value={detectedOnlyCount}
              icon={LuCamera}
              tone="orange"
            />

            <SummaryCard
              title="Unauthorized"
              value={unauthorizedCount}
              icon={LuShieldAlert}
              tone="red"
            />
          </div>

          <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700">
            Found{" "}
            <span className="font-bold">
              {results.length}
            </span>{" "}
            unique vehicle
            {results.length === 1 ? "" : "s"} matching{" "}
            <span className="font-bold">
              “{searchedQuery}”
            </span>
            . Results include registered vehicles and ANPR
            detections.
          </div>
        </>
      )}

      {/* Results */}
      {searched && !loading && (
        <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
            <div>
              <h2 className="font-display font-semibold text-ink-950">
                Search Results
              </h2>

              <p className="mt-1 text-xs text-slate-400">
                Registered vehicles open the full vehicle
                details page.
              </p>
            </div>

            <div className="flex flex-wrap gap-2">
              <ResultCountBadge
                label="Authorized"
                value={authorizedCount}
              />

              <ResultCountBadge
                label="Unauthorized"
                value={unauthorizedCount}
              />
            </div>
          </div>

          {results.length === 0 ? (
            <div className="py-12">
              <EmptyState
                icon={LuCar}
                title="No vehicle matched"
                message={`No registered vehicle or ANPR detection matched "${searchedQuery}". Try a shorter value such as KA, KL, HR or MH.`}
              />
            </div>
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden overflow-x-auto lg:block">
                <table className="w-full min-w-[1050px] text-sm">
                  <thead className="bg-slate-50/80">
                    <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                      <th className="px-5 py-3 font-semibold">
                        Vehicle
                      </th>

                      <th className="px-5 py-3 font-semibold">
                        Number Plate
                      </th>

                      <th className="px-5 py-3 font-semibold">
                        Owner
                      </th>

                      <th className="px-5 py-3 font-semibold">
                        Source
                      </th>

                      <th className="px-5 py-3 font-semibold">
                        Last Seen
                      </th>

                      <th className="px-5 py-3 font-semibold">
                        Status
                      </th>

                      <th className="px-5 py-3 text-right font-semibold">
                        Action
                      </th>
                    </tr>
                  </thead>

                  <tbody className="divide-y divide-slate-100">
                    {results.map((result) => (
                      <SearchResultRow
                        key={result.key}
                        result={result}
                        onOpen={() => openResult(result)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Mobile cards */}
              <div className="divide-y divide-slate-100 lg:hidden">
                {results.map((result) => (
                  <MobileSearchCard
                    key={result.key}
                    result={result}
                    onOpen={() => openResult(result)}
                  />
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {!searched && !loading && (
        <div className="rounded-2xl border border-dashed border-slate-200 bg-white py-16">
          <EmptyState
            icon={LuSearch}
            title="Search all vehicle records"
            message="Enter a state code such as KA or KL, a full number plate, or an owner name."
          />
        </div>
      )}
    </div>
  );
}

function SearchResultRow({ result, onOpen }) {
  return (
    <tr
      onClick={onOpen}
      className="cursor-pointer transition hover:bg-slate-50"
    >
      <td className="px-5 py-4">
        <VehicleImage result={result} />
      </td>

      <td className="px-5 py-4">
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onOpen();
          }}
          className="rounded-lg bg-slate-100 px-3 py-2 font-display font-extrabold uppercase tracking-wider text-ink-950 transition hover:bg-brand-50 hover:text-brand-700"
        >
          {result.plate}
        </button>
      </td>

      <td className="px-5 py-4">
        <p className="font-medium text-slate-700">
          {result.ownerName}
        </p>

        <p className="mt-0.5 text-xs text-slate-400">
          {result.departmentName}
        </p>
      </td>

      <td className="px-5 py-4">
        <SourceBadge registered={result.registered} />
      </td>

      <td className="px-5 py-4">
        <div className="space-y-1">
          <div className="flex items-center gap-1.5 text-sm text-slate-600">
            <LuClock3
              size={14}
              className="text-slate-400"
            />
            {result.lastSeen}
          </div>

          {result.gateName !== "—" && (
            <div className="flex items-center gap-1.5 text-xs text-slate-400">
              <LuMapPin size={13} />
              {result.gateName}
            </div>
          )}
        </div>
      </td>

      <td className="px-5 py-4">
        <StatusBadge
          status={
            result.authorized
              ? "AUTHORIZED"
              : "UNAUTHORIZED"
          }
        />
      </td>

      <td className="px-5 py-4">
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation();
            onOpen();
          }}
          className="ml-auto flex items-center gap-2 rounded-lg px-3 py-2 text-sm font-semibold text-brand-600 transition hover:bg-brand-50"
        >
          {result.registered
            ? "View Details"
            : "View Logs"}

          <LuArrowRight size={16} />
        </button>
      </td>
    </tr>
  );
}

function MobileSearchCard({ result, onOpen }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="w-full p-4 text-left transition hover:bg-slate-50"
    >
      <div className="flex items-start gap-3">
        <VehicleImage result={result} />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="font-display font-extrabold uppercase tracking-wider text-ink-950">
              {result.plate}
            </p>

            <StatusBadge
              status={
                result.authorized
                  ? "AUTHORIZED"
                  : "UNAUTHORIZED"
              }
            />
          </div>

          <p className="mt-2 text-sm font-medium text-slate-700">
            {result.ownerName}
          </p>

          <p className="mt-0.5 text-xs text-slate-400">
            {result.departmentName}
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <SourceBadge
              registered={result.registered}
            />

            <span className="flex items-center gap-1 text-xs text-slate-500">
              <LuClock3 size={13} />
              {result.lastSeen}
            </span>
          </div>
        </div>

        <LuArrowRight
          size={17}
          className="mt-1 shrink-0 text-slate-300"
        />
      </div>
    </button>
  );
}

function VehicleImage({ result }) {
  return (
    <div className="flex items-center gap-3">
      <div className="grid h-12 w-12 shrink-0 place-items-center overflow-hidden rounded-xl border border-slate-100 bg-slate-100">
        {result.image ? (
          <img
            src={result.image}
            alt={result.plate}
            className="h-full w-full object-cover"
            onError={(event) => {
              event.currentTarget.style.display = "none";
            }}
          />
        ) : result.registered ? (
          <LuCar
            size={21}
            className="text-slate-300"
          />
        ) : (
          <LuCamera
            size={21}
            className="text-slate-300"
          />
        )}
      </div>

      <div className="hidden min-w-0 xl:block">
        <p className="max-w-[160px] truncate font-medium text-ink-950">
          {result.vehicleDescription}
        </p>

        <p className="mt-0.5 text-xs text-slate-400">
          {result.vehicleType}
        </p>
      </div>
    </div>
  );
}

function SourceBadge({ registered }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${
        registered
          ? "border-blue-200 bg-blue-50 text-blue-700"
          : "border-orange-200 bg-orange-50 text-orange-700"
      }`}
    >
      {registered ? (
        <LuCar size={13} />
      ) : (
        <LuCamera size={13} />
      )}

      {registered
        ? "Registered"
        : "Detection Only"}
    </span>
  );
}

function SummaryCard({
  title,
  value,
  icon: Icon,
  tone,
}) {
  const tones = {
    blue: "border-blue-100 bg-blue-50 text-blue-600",
    green:
      "border-emerald-100 bg-emerald-50 text-emerald-600",
    orange:
      "border-orange-100 bg-orange-50 text-orange-600",
    red: "border-red-100 bg-red-50 text-red-600",
  };

  return (
    <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-card">
      <div className="flex items-center justify-between gap-4">
        <div>
          <p className="text-xs font-medium text-slate-500">
            {title}
          </p>

          <p className="mt-1 font-display text-2xl font-black text-ink-950">
            {value}
          </p>
        </div>

        <div
          className={`grid h-11 w-11 place-items-center rounded-xl border ${
            tones[tone] || tones.blue
          }`}
        >
          <Icon size={21} />
        </div>
      </div>
    </div>
  );
}

function ResultCountBadge({ label, value }) {
  return (
    <span className="rounded-full bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-600">
      {label}: {value}
    </span>
  );
}

function SearchExample({ text }) {
  return (
    <span className="rounded-lg bg-slate-100 px-2.5 py-1">
      Try: {text}
    </span>
  );
}

/*
 * Converts the DRF response into an array.
 * Supports paginated and non-paginated responses.
 */
function extractResults(response) {
  if (Array.isArray(response)) {
    return response;
  }

  if (Array.isArray(response?.results)) {
    return response.results;
  }

  return [];
}

/*
 * Combine registered vehicles and detection records.
 *
 * Only one result is shown for each unique plate.
 * When the same plate exists in both tables, registered
 * vehicle details are used and the latest detection is attached.
 */
function combineSearchResults(vehicles, records) {
  const resultsByPlate = new Map();

  records.forEach((record) => {
    const plate = getRecordPlate(record);

    if (!plate) {
      return;
    }

    const normalizedPlate = normalizePlate(plate);
    const current = resultsByPlate.get(normalizedPlate);

    if (
      !current ||
      getRecordDate(record) >
        getRecordDate(current.record)
    ) {
      resultsByPlate.set(normalizedPlate, {
        key: `record-${normalizedPlate}`,
        plate: normalizedPlate,
        registered: false,
        authorized: Boolean(
          record.was_authorized ||
            record.authorization_status ===
              "AUTHORIZED"
        ),
        vehicleId:
          record.vehicle?.id ||
          record.vehicle_id ||
          (typeof record.vehicle === "number"
            ? record.vehicle
            : null),
        recordId: record.id,
        ownerName:
          record.owner_name ||
          record.vehicle?.owner_name ||
          "Unknown",
        departmentName:
          record.department_name ||
          record.vehicle?.department_name ||
          "Not registered",
        vehicleDescription:
          record.vehicle
            ? `${record.vehicle.vehicle_company || ""} ${
                record.vehicle.vehicle_model || ""
              }`.trim() || "Detected vehicle"
            : "Detected vehicle",
        vehicleType: cleanLabel(
          record.vehicle_type ||
            record.vehicle?.vehicle_type ||
            "Unknown type"
        ),
        image: getRecordImage(record),
        lastSeen: formatRecordDate(record),
        gateName: getGateName(record),
        record,
      });
    }
  });

  vehicles.forEach((vehicle) => {
    const plate = normalizePlate(
      vehicle.registration_number
    );

    if (!plate) {
      return;
    }

    const detectionResult =
      resultsByPlate.get(plate);

    const vehicleImage = vehicle.vehicle_image
      ? resolveMediaUrl(vehicle.vehicle_image)
      : null;

    resultsByPlate.set(plate, {
      key: `vehicle-${vehicle.id}`,
      plate,
      registered: true,
      authorized:
        vehicle.authorization_status ===
        "AUTHORIZED",
      vehicleId: vehicle.id,
      recordId: detectionResult?.recordId || null,
      ownerName:
        vehicle.owner_name || "Unknown",
      departmentName:
        vehicle.department_name ||
        vehicle.department?.name ||
        vehicle.department_detail?.name ||
        "Department unavailable",
      vehicleDescription:
        `${vehicle.vehicle_company || ""} ${
          vehicle.vehicle_model || ""
        }`.trim() || "Registered vehicle",
      vehicleType:
        labelFor(
          VEHICLE_TYPES,
          vehicle.vehicle_type
        ) ||
        cleanLabel(vehicle.vehicle_type) ||
        "Unknown type",
      image:
        vehicleImage ||
        detectionResult?.image ||
        null,
      lastSeen:
        detectionResult?.lastSeen ||
        "No detections yet",
      gateName:
        detectionResult?.gateName || "—",
      record: detectionResult?.record || null,
      vehicle,
    });
  });

  return Array.from(resultsByPlate.values()).sort(
    (first, second) => {
      /*
       * Registered vehicles appear first.
       * Inside each group, sort by plate number.
       */
      if (
        first.registered !== second.registered
      ) {
        return first.registered ? -1 : 1;
      }

      return first.plate.localeCompare(
        second.plate
      );
    }
  );
}

function getRecordPlate(record) {
  return (
    record.detected_plate_text ||
    record.detected_plate ||
    record.number_plate ||
    record.registration_number ||
    record.vehicle?.registration_number ||
    ""
  );
}

function getRecordImage(record) {
  const image =
    record.captured_image ||
    record.vehicle_image ||
    record.plate_image ||
    record.image ||
    null;

  return image ? resolveMediaUrl(image) : null;
}

function getGateName(record) {
  if (typeof record.gate === "object") {
    return (
      record.gate?.name ||
      record.gate?.gate_name ||
      "—"
    );
  }

  return (
    record.gate_name ||
    record.gate_display ||
    record.gate ||
    "—"
  );
}

function getRecordDate(record) {
  const value =
    record.timestamp ||
    record.entry_time ||
    record.exit_time ||
    record.created_at;

  if (!value) {
    return 0;
  }

  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? 0
    : date.getTime();
}

function formatRecordDate(record) {
  const value =
    record.timestamp ||
    record.entry_time ||
    record.exit_time ||
    record.created_at;

  if (!value) {
    return "Time unavailable";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "Time unavailable";
  }

  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function normalizeSearch(value) {
  return value.trim().toUpperCase();
}

function normalizePlate(value) {
  return String(value || "")
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "");
}

function cleanLabel(value) {
  if (!value) {
    return "";
  }

  return String(value)
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (character) =>
      character.toUpperCase()
    );
}