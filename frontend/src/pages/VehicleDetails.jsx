import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import toast from "react-hot-toast";
import { LuArrowLeft, LuCar, LuPencil, LuTrash2, LuMail, LuPhone, LuCalendar } from "react-icons/lu";
import StatusBadge from "../components/StatusBadge";
import ConfirmDialog from "../components/ConfirmDialog";
import VehicleFormModal from "../components/VehicleFormModal";
import { PageLoader } from "../components/Loader";
import EmptyState from "../components/EmptyState";
import { getVehicle, deleteVehicle } from "../services/vehicleService";
import { listRecords } from "../services/recordService";
import { useReferenceData, departmentName, gateName } from "../hooks/useReferenceData";
import { resolveMediaUrl } from "../api/axios";
import { formatDate, formatDateTime } from "../utils/format";
import { OWNER_TYPES, labelFor } from "../utils/constants";

function InfoRow({ label, value }) {
  return (
    <div>
      <p className="text-xs text-slate-400 mb-0.5">{label}</p>
      <p className="text-sm font-medium text-ink-950">{value ?? "—"}</p>
    </div>
  );
}

export default function VehicleDetails() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { departments, gates } = useReferenceData();

  const [vehicle, setVehicle] = useState(null);
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const data = await getVehicle(id);
      setVehicle(data);
      const recordsData = await listRecords({ vehicle: id, page_size: 8, ordering: "-timestamp" }).catch(() => null);
      const results = Array.isArray(recordsData) ? recordsData : recordsData?.results || [];
      setHistory(results);
    } catch {
      toast.error("Could not load vehicle");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function confirmDelete() {
    setDeleting(true);
    try {
      await deleteVehicle(id);
      toast.success("Vehicle deleted");
      navigate("/vehicles");
    } catch {
      toast.error("Could not delete vehicle");
      setDeleting(false);
    }
  }

  if (loading) return <PageLoader label="Loading vehicle…" />;
  if (!vehicle) return <EmptyState title="Vehicle not found" />;

  return (
    <div className="space-y-5 animate-fadeIn">
      <button
        onClick={() => navigate("/vehicles")}
        className="flex items-center gap-1.5 text-sm font-medium text-slate-500 hover:text-ink-950"
      >
        <LuArrowLeft size={16} /> Back to Vehicles
      </button>

      <div className="grid lg:grid-cols-3 gap-6">
        <div className="space-y-4">
          <div className="bg-white rounded-2xl shadow-card border border-slate-100 overflow-hidden">
            <div className="aspect-[4/3] bg-slate-100 grid place-items-center">
              {vehicle.vehicle_image ? (
                <img
                  src={resolveMediaUrl(vehicle.vehicle_image)}
                  alt={vehicle.registration_number}
                  className="w-full h-full object-cover"
                />
              ) : (
                <LuCar className="text-slate-300" size={48} />
              )}
            </div>
            <div className="p-4">
              <p className="font-display font-bold text-xl tracking-wide text-ink-950">
                {vehicle.registration_number}
              </p>
              <p className="text-sm text-slate-500">{vehicle.vehicle_company} {vehicle.vehicle_model}</p>
              <div className="mt-3">
                <StatusBadge status={vehicle.authorization_status} />
              </div>
            </div>
          </div>

          <div className="flex gap-2">
            <button
              onClick={() => setEditOpen(true)}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700"
            >
              <LuPencil size={16} /> Edit Vehicle
            </button>
            <button
              onClick={() => setDeleteOpen(true)}
              className="flex-1 flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-red-50 text-red-600 text-sm font-semibold hover:bg-red-100"
            >
              <LuTrash2 size={16} /> Delete
            </button>
          </div>
        </div>

        <div className="lg:col-span-2 space-y-6">
          <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-6">
            <h3 className="font-display font-semibold text-ink-950 mb-5">Owner Information</h3>
            <div className="grid sm:grid-cols-2 gap-5">
              <InfoRow label="Owner Name" value={vehicle.owner_name} />
              <InfoRow label="Owner Type" value={labelFor(OWNER_TYPES, vehicle.owner_type)} />
              <InfoRow
                label="Email"
                value={
                  vehicle.owner_email ? (
                    <span className="flex items-center gap-1.5"><LuMail size={14} className="text-slate-400" />{vehicle.owner_email}</span>
                  ) : "—"
                }
              />
              <InfoRow
                label="Phone"
                value={
                  vehicle.owner_phone ? (
                    <span className="flex items-center gap-1.5"><LuPhone size={14} className="text-slate-400" />{vehicle.owner_phone}</span>
                  ) : "—"
                }
              />
              <InfoRow label="Department" value={departmentName(departments, vehicle.department)} />
            </div>
          </div>

          <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-6">
            <h3 className="font-display font-semibold text-ink-950 mb-5">Vehicle Information</h3>
            <div className="grid sm:grid-cols-2 gap-5">
              <InfoRow label="Company" value={vehicle.vehicle_company} />
              <InfoRow label="Model" value={vehicle.vehicle_model} />
              <InfoRow label="Vehicle Type" value={vehicle.vehicle_type?.replace("_", " ")} />
              <InfoRow label="Fuel Type" value={vehicle.fuel_type} />
              <InfoRow label="Color" value={vehicle.color} />
              <InfoRow label="Registration Date" value={formatDate(vehicle.registration_date)} />
              <InfoRow label="Valid From" value={formatDate(vehicle.valid_from)} />
              <InfoRow label="Valid Until" value={formatDate(vehicle.valid_until)} />
            </div>
          </div>

          <div className="bg-white rounded-2xl shadow-card border border-slate-100 p-6">
            <h3 className="font-display font-semibold text-ink-950 mb-5">Recent Activity</h3>
            {history.length === 0 ? (
              <EmptyState title="No activity yet" message="Entry and exit records for this vehicle will appear here." />
            ) : (
              <ul className="divide-y divide-slate-100">
                {history.map((r) => (
                  <li key={r.id} className="py-3 flex items-center justify-between gap-4">
                    <div className="flex items-center gap-2 text-sm text-slate-600">
                      <LuCalendar size={15} className="text-slate-400" />
                      {formatDateTime(r.timestamp)}
                      <span className="text-slate-300">·</span>
                      {gateName(gates, r.gate)}
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <StatusBadge status={r.direction} />
                      <StatusBadge status={r.was_authorized ? "AUTHORIZED" : "UNAUTHORIZED"} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>

      <VehicleFormModal open={editOpen} vehicle={vehicle} onClose={() => setEditOpen(false)} onSaved={load} />

      <ConfirmDialog
        open={deleteOpen}
        title="Delete this vehicle?"
        message={`${vehicle.registration_number} will be permanently removed from the system.`}
        loading={deleting}
        onCancel={() => setDeleteOpen(false)}
        onConfirm={confirmDelete}
      />
    </div>
  );
}
