import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import toast from "react-hot-toast";
import {
  LuCar,
  LuCircleX,
  LuEye,
  LuFilter,
  LuPencil,
  LuPlus,
  LuSearch,
  LuShieldAlert,
  LuShieldCheck,
  LuTrash2,
} from "react-icons/lu";

import StatusBadge from "../components/StatusBadge";
import Pagination from "../components/Pagination";
import EmptyState from "../components/EmptyState";
import ConfirmDialog from "../components/ConfirmDialog";
import VehicleFormModal from "../components/VehicleFormModal";
import { SkeletonRow } from "../components/Loader";

import {
  listVehicles,
  deleteVehicle,
} from "../services/vehicleService";

import {
  useReferenceData,
  departmentName,
} from "../hooks/useReferenceData";

import {
  AUTHORIZATION_STATUSES,
  VEHICLE_TYPES,
  OWNER_TYPES,
} from "../utils/constants";

import { resolveMediaUrl } from "../api/axios";

const PAGE_SIZE = 10;

export default function Vehicles() {
  const navigate = useNavigate();
  const { departments } = useReferenceData();

  const [vehicles, setVehicles] = useState([]);
  const [count, setCount] = useState(0);

  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [status, setStatus] = useState("");
  const [vehicleType, setVehicleType] = useState("");
  const [ownerType, setOwnerType] = useState("");

  const [showFilters, setShowFilters] = useState(false);
  const [loading, setLoading] = useState(true);

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState(null);

  const [toDelete, setToDelete] = useState(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setPage(1);
    }, 400);

    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    loadVehicles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, debouncedSearch, status, vehicleType, ownerType]);

  async function loadVehicles() {
    setLoading(true);

    try {
      const data = await listVehicles({
        page,
        page_size: PAGE_SIZE,

        // Partial search:
        // KA, KA01, HR51, Rahul, Honda, etc.
        search: debouncedSearch || undefined,

        // Blank status means show both authorized and unauthorized.
        authorization_status: status || undefined,
        vehicle_type: vehicleType || undefined,
        owner_type: ownerType || undefined,
      });

      const results = Array.isArray(data)
        ? data
        : data?.results || [];

      setVehicles(results);

      setCount(
        Array.isArray(data)
          ? data.length
          : data?.count || 0
      );
    } catch (error) {
      console.error("Could not load vehicles:", error);
      toast.error("Could not load vehicles");
      setVehicles([]);
      setCount(0);
    } finally {
      setLoading(false);
    }
  }

  async function confirmDelete() {
    if (!toDelete?.id) {
      return;
    }

    setDeleting(true);

    try {
      await deleteVehicle(toDelete.id);

      toast.success("Vehicle removed successfully");
      setToDelete(null);

      if (vehicles.length === 1 && page > 1) {
        setPage((currentPage) => currentPage - 1);
      } else {
        await loadVehicles();
      }
    } catch (error) {
      console.error("Could not delete vehicle:", error);
      toast.error("Could not delete vehicle");
    } finally {
      setDeleting(false);
    }
  }

  function openAddVehicle() {
    setEditing(null);
    setFormOpen(true);
  }

  function openEditVehicle(vehicle) {
    setEditing(vehicle);
    setFormOpen(true);
  }

  function closeVehicleForm() {
    setFormOpen(false);
    setEditing(null);
  }

  async function handleVehicleSaved() {
    closeVehicleForm();
    await loadVehicles();
  }

  function clearFilters() {
    setSearch("");
    setDebouncedSearch("");
    setStatus("");
    setVehicleType("");
    setOwnerType("");
    setPage(1);
  }

  function openVehicleDetails(vehicleId) {
    navigate(`/vehicles/${vehicleId}`);
  }

  const hasActiveFilters =
    search ||
    status ||
    vehicleType ||
    ownerType;

  const activeFilterCount = [
    status,
    vehicleType,
    ownerType,
  ].filter(Boolean).length;

  const pageAuthorized = useMemo(
    () =>
      vehicles.filter(
        (vehicle) =>
          vehicle.authorization_status === "AUTHORIZED"
      ).length,
    [vehicles]
  );

  const pageUnauthorized = useMemo(
    () =>
      vehicles.filter(
        (vehicle) =>
          vehicle.authorization_status !== "AUTHORIZED"
      ).length,
    [vehicles]
  );

  return (
    <div className="space-y-6 animate-fadeIn">
      {/* Heading */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="font-display text-2xl font-bold text-ink-950">
            Vehicle Management
          </h1>

          <p className="mt-1 text-sm text-slate-500">
            Search registered vehicles using state code,
            plate number, owner name, or vehicle details.
          </p>
        </div>

        <button
          type="button"
          onClick={openAddVehicle}
          className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-brand-700"
        >
          <LuPlus size={18} />
          Add Vehicle
        </button>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <SummaryCard
          title="Total Matching Vehicles"
          value={count}
          icon={LuCar}
          tone="blue"
        />

        <SummaryCard
          title="Authorized on This Page"
          value={pageAuthorized}
          icon={LuShieldCheck}
          tone="green"
        />

        <SummaryCard
          title="Unauthorized on This Page"
          value={pageUnauthorized}
          icon={LuShieldAlert}
          tone="red"
        />
      </div>

      {/* Search and filters */}
      <div className="rounded-2xl border border-slate-100 bg-white p-4 shadow-card">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <div className="relative min-w-0 flex-1">
            <LuSearch
              className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              size={18}
            />

            <input
              type="text"
              value={search}
              onChange={(event) =>
                setSearch(event.target.value.toUpperCase())
              }
              placeholder="Search KA, KA01, HR51, plate number, owner..."
              className="w-full rounded-xl border border-slate-200 bg-white py-3 pl-10 pr-10 text-sm outline-none transition focus:border-brand-400 focus:ring-4 focus:ring-brand-100"
            />

            {search && (
              <button
                type="button"
                onClick={() => setSearch("")}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 transition hover:text-slate-700"
                aria-label="Clear search"
              >
                <LuCircleX size={18} />
              </button>
            )}
          </div>

          <button
            type="button"
            onClick={() =>
              setShowFilters((current) => !current)
            }
            className={`inline-flex items-center justify-center gap-2 rounded-xl border px-4 py-3 text-sm font-semibold transition ${
              showFilters || activeFilterCount > 0
                ? "border-brand-200 bg-brand-50 text-brand-700"
                : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
            }`}
          >
            <LuFilter size={17} />
            Filters

            {activeFilterCount > 0 && (
              <span className="grid h-5 min-w-5 place-items-center rounded-full bg-brand-600 px-1 text-[10px] text-white">
                {activeFilterCount}
              </span>
            )}
          </button>

          {hasActiveFilters && (
            <button
              type="button"
              onClick={clearFilters}
              className="inline-flex items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold text-red-600 transition hover:bg-red-50"
            >
              <LuCircleX size={17} />
              Clear
            </button>
          )}
        </div>

        {showFilters && (
          <div className="mt-4 grid gap-3 border-t border-slate-100 pt-4 sm:grid-cols-2 xl:grid-cols-3">
            <FilterSelect
              label="Authorization Status"
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setPage(1);
              }}
            >
              <option value="">
                All Statuses
              </option>

              {AUTHORIZATION_STATUSES.map((option) => (
                <option
                  key={option.value}
                  value={option.value}
                >
                  {option.label}
                </option>
              ))}
            </FilterSelect>

            <FilterSelect
              label="Vehicle Type"
              value={vehicleType}
              onChange={(event) => {
                setVehicleType(event.target.value);
                setPage(1);
              }}
            >
              <option value="">
                All Vehicle Types
              </option>

              {VEHICLE_TYPES.map((option) => (
                <option
                  key={option.value}
                  value={option.value}
                >
                  {option.label}
                </option>
              ))}
            </FilterSelect>

            <FilterSelect
              label="Owner Type"
              value={ownerType}
              onChange={(event) => {
                setOwnerType(event.target.value);
                setPage(1);
              }}
            >
              <option value="">
                All Owner Types
              </option>

              {OWNER_TYPES.map((option) => (
                <option
                  key={option.value}
                  value={option.value}
                >
                  {option.label}
                </option>
              ))}
            </FilterSelect>
          </div>
        )}

        {debouncedSearch && (
          <div className="mt-4 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700">
            Showing all matching authorized and unauthorized
            vehicles for{" "}
            <span className="font-bold">
              “{debouncedSearch}”
            </span>
          </div>
        )}
      </div>

      {/* Vehicles table */}
      <div className="overflow-hidden rounded-2xl border border-slate-100 bg-white shadow-card">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-5 py-4">
          <div>
            <h2 className="font-display font-semibold text-ink-950">
              Registered Vehicles
            </h2>

            <p className="mt-1 text-xs text-slate-400">
              Click a vehicle row or plate number to view
              complete details.
            </p>
          </div>

          <span className="rounded-full bg-slate-100 px-3 py-1.5 text-xs font-semibold text-slate-600">
            {count} vehicle{count === 1 ? "" : "s"}
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[950px] text-sm">
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
                  Department
                </th>

                <th className="px-5 py-3 font-semibold">
                  Type
                </th>

                <th className="px-5 py-3 font-semibold">
                  Status
                </th>

                <th className="px-5 py-3 text-right font-semibold">
                  Actions
                </th>
              </tr>
            </thead>

            <tbody className="divide-y divide-slate-100">
              {loading &&
                Array.from({ length: 6 }).map((_, index) => (
                  <SkeletonRow
                    key={index}
                    cols={7}
                  />
                ))}

              {!loading &&
                vehicles.map((vehicle) => (
                  <tr
                    key={vehicle.id}
                    onClick={() =>
                      openVehicleDetails(vehicle.id)
                    }
                    className="cursor-pointer transition hover:bg-slate-50"
                  >
                    <td className="px-5 py-4">
                      <div className="flex items-center gap-3">
                        <div className="grid h-12 w-12 shrink-0 place-items-center overflow-hidden rounded-xl border border-slate-100 bg-slate-100">
                          {vehicle.vehicle_image ? (
                            <img
                              src={resolveMediaUrl(
                                vehicle.vehicle_image
                              )}
                              alt={
                                vehicle.registration_number ||
                                "Vehicle"
                              }
                              className="h-full w-full object-cover"
                            />
                          ) : (
                            <LuCar
                              size={21}
                              className="text-slate-300"
                            />
                          )}
                        </div>

                        <div className="min-w-0">
                          <p className="max-w-[170px] truncate font-semibold text-ink-950">
                            {vehicle.vehicle_company ||
                            vehicle.vehicle_model
                              ? `${vehicle.vehicle_company || ""} ${
                                  vehicle.vehicle_model || ""
                                }`.trim()
                              : "Vehicle details unavailable"}
                          </p>

                          <p className="mt-0.5 text-xs capitalize text-slate-400">
                            {vehicle.color || "Color unavailable"}
                          </p>
                        </div>
                      </div>
                    </td>

                    <td className="px-5 py-4">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          openVehicleDetails(vehicle.id);
                        }}
                        className="rounded-lg bg-slate-100 px-3 py-2 font-display font-extrabold uppercase tracking-wider text-ink-950 transition hover:bg-brand-50 hover:text-brand-700"
                      >
                        {vehicle.registration_number || "—"}
                      </button>
                    </td>

                    <td className="px-5 py-4">
                      <p className="font-medium text-slate-700">
                        {vehicle.owner_name || "—"}
                      </p>

                      <p className="mt-0.5 text-xs capitalize text-slate-400">
                        {vehicle.owner_type
                          ?.replaceAll("_", " ")
                          .toLowerCase() || "Owner type unavailable"}
                      </p>
                    </td>

                    <td className="px-5 py-4 text-slate-600">
                      {departmentName(
                        departments,
                        vehicle.department
                      )}
                    </td>

                    <td className="px-5 py-4 capitalize text-slate-600">
                      {vehicle.vehicle_type
                        ?.replaceAll("_", " ")
                        .toLowerCase() || "—"}
                    </td>

                    <td className="px-5 py-4">
                      <StatusBadge
                        status={
                          vehicle.authorization_status ||
                          "UNAUTHORIZED"
                        }
                      />
                    </td>

                    <td className="px-5 py-4">
                      <div
                        className="flex items-center justify-end gap-1"
                        onClick={(event) =>
                          event.stopPropagation()
                        }
                      >
                        <ActionButton
                          title="View vehicle details"
                          onClick={() =>
                            openVehicleDetails(vehicle.id)
                          }
                        >
                          <LuEye size={17} />
                        </ActionButton>

                        <ActionButton
                          title="Edit vehicle"
                          onClick={() =>
                            openEditVehicle(vehicle)
                          }
                        >
                          <LuPencil size={17} />
                        </ActionButton>

                        <ActionButton
                          title="Delete vehicle"
                          danger
                          onClick={() =>
                            setToDelete(vehicle)
                          }
                        >
                          <LuTrash2 size={17} />
                        </ActionButton>
                      </div>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>

          {!loading && vehicles.length === 0 && (
            <div className="py-10">
              <EmptyState
                icon={LuCar}
                title="No vehicles found"
                message={
                  debouncedSearch
                    ? `No authorized or unauthorized vehicle matched "${debouncedSearch}".`
                    : "Try changing the filters or add a new vehicle."
                }
              />
            </div>
          )}
        </div>

        {!loading && vehicles.length > 0 && (
          <div className="border-t border-slate-100 px-4">
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={count}
              onPageChange={setPage}
            />
          </div>
        )}
      </div>

      <VehicleFormModal
        open={formOpen}
        vehicle={editing}
        onClose={closeVehicleForm}
        onSaved={handleVehicleSaved}
      />

      <ConfirmDialog
        open={Boolean(toDelete)}
        title="Delete this vehicle?"
        message={
          toDelete
            ? `${toDelete.registration_number} will be permanently removed from the system.`
            : ""
        }
        loading={deleting}
        onCancel={() => setToDelete(null)}
        onConfirm={confirmDelete}
      />
    </div>
  );
}

function SummaryCard({
  title,
  value,
  icon: Icon,
  tone = "blue",
}) {
  const tones = {
    blue: "border-blue-100 bg-blue-50 text-blue-600",
    green:
      "border-emerald-100 bg-emerald-50 text-emerald-600",
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

function FilterSelect({
  label,
  value,
  onChange,
  children,
}) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-slate-500">
        {label}
      </span>

      <select
        value={value}
        onChange={onChange}
        className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none transition focus:border-brand-400 focus:ring-4 focus:ring-brand-100"
      >
        {children}
      </select>
    </label>
  );
}

function ActionButton({
  children,
  onClick,
  title,
  danger = false,
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className={`grid h-9 w-9 place-items-center rounded-lg transition ${
        danger
          ? "text-slate-400 hover:bg-red-50 hover:text-red-600"
          : "text-slate-400 hover:bg-brand-50 hover:text-brand-600"
      }`}
    >
      {children}
    </button>
  );
}