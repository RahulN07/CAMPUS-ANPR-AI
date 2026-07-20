import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { LuUpload } from "react-icons/lu";

import Modal from "./Modal";
import { TextField, SelectField } from "./FormField";

import {
  OWNER_TYPES,
  VEHICLE_TYPES,
  FUEL_TYPES,
  AUTHORIZATION_STATUSES,
  VEHICLE_COLORS,
} from "../utils/constants";

import { useReferenceData } from "../hooks/useReferenceData";

import {
  createVehicle,
  updateVehicle,
  getVehicleCompanies,
  getVehicleModels,
} from "../services/vehicleService";

import { resolveMediaUrl } from "../api/axios";

const EMPTY = {
  owner_name: "",
  owner_email: "",
  owner_phone: "",
  owner_type: "STUDENT",
  department: "",

  vehicle_company: "",
  vehicle_model: "",
  vehicle_type: "TWO_WHEELER",

  color: "",
  fuel_type: "PETROL",
  registration_number: "",

  registration_date: "",
  valid_from: "",
  valid_until: "",

  authorization_status: "PENDING",
};

export default function VehicleFormModal({
  open,
  onClose,
  vehicle,
  onSaved,
}) {
  const { departments } = useReferenceData();

  const [form, setForm] = useState(EMPTY);

  const [companies, setCompanies] = useState([]);
  const [vehicleModels, setVehicleModels] = useState([]);

  const [selectedCompanyId, setSelectedCompanyId] = useState("");

  const [loadingCompanies, setLoadingCompanies] = useState(false);
  const [loadingModels, setLoadingModels] = useState(false);

  const [imageFile, setImageFile] = useState(null);
  const [preview, setPreview] = useState(null);

  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState({});

  useEffect(() => {
    if (!open) return;

    initializeForm();
  }, [open, vehicle]);

  async function initializeForm() {
    setErrors({});
    setImageFile(null);
    setSelectedCompanyId("");
    setVehicleModels([]);

    if (vehicle) {
      const vehicleType =
        vehicle.vehicle_type || "TWO_WHEELER";

      const editForm = {
        owner_name: vehicle.owner_name || "",
        owner_email: vehicle.owner_email || "",
        owner_phone: vehicle.owner_phone || "",
        owner_type: vehicle.owner_type || "STUDENT",
        department: vehicle.department || "",

        vehicle_company: vehicle.vehicle_company || "",
        vehicle_model: vehicle.vehicle_model || "",
        vehicle_type: vehicleType,

        color: vehicle.color || "",
        fuel_type: vehicle.fuel_type || "PETROL",
        registration_number:
          vehicle.registration_number || "",

        registration_date:
          vehicle.registration_date || "",
        valid_from: vehicle.valid_from || "",
        valid_until: vehicle.valid_until || "",

        authorization_status:
          vehicle.authorization_status || "PENDING",
      };

      setForm(editForm);
      setPreview(resolveMediaUrl(vehicle.vehicle_image));

      const loadedCompanies =
        await loadCompanies(vehicleType, false);

      const matchingCompany = loadedCompanies.find(
        (company) =>
          company.name.toLowerCase() ===
          String(vehicle.vehicle_company || "").toLowerCase()
      );

      if (matchingCompany) {
        setSelectedCompanyId(String(matchingCompany.id));

        await loadModels(
          matchingCompany.id,
          vehicle.vehicle_model
        );
      }
    } else {
      setForm(EMPTY);
      setPreview(null);

      await loadCompanies(
        EMPTY.vehicle_type,
        false
      );
    }
  }

  async function loadCompanies(
    vehicleType,
    resetFields = true
  ) {
    try {
      setLoadingCompanies(true);

      const data =
        await getVehicleCompanies(vehicleType);

      const list = Array.isArray(data)
        ? data
        : data?.results || [];

      setCompanies(list);

      if (resetFields) {
        setSelectedCompanyId("");
        setVehicleModels([]);

        setForm((previous) => ({
          ...previous,
          vehicle_company: "",
          vehicle_model: "",
        }));
      }

      return list;
    } catch (error) {
      console.error(
        "Failed to load vehicle companies:",
        error
      );

      setCompanies([]);
      toast.error("Could not load vehicle companies.");

      return [];
    } finally {
      setLoadingCompanies(false);
    }
  }

  async function loadModels(
    companyId,
    selectedModelName = ""
  ) {
    if (!companyId) {
      setVehicleModels([]);
      return [];
    }

    try {
      setLoadingModels(true);

      const data = await getVehicleModels(companyId);

      const list = Array.isArray(data)
        ? data
        : data?.results || [];

      setVehicleModels(list);

      setForm((previous) => ({
        ...previous,
        vehicle_model: selectedModelName || "",
      }));

      return list;
    } catch (error) {
      console.error(
        "Failed to load vehicle models:",
        error
      );

      setVehicleModels([]);
      toast.error("Could not load vehicle models.");

      return [];
    } finally {
      setLoadingModels(false);
    }
  }

  function update(key, value) {
    setForm((previous) => ({
      ...previous,
      [key]: value,
    }));
  }

  async function handleVehicleTypeChange(event) {
    const value = event.target.value;

    setForm((previous) => ({
      ...previous,
      vehicle_type: value,
      vehicle_company: "",
      vehicle_model: "",
    }));

    setSelectedCompanyId("");
    setVehicleModels([]);

    await loadCompanies(value, false);
  }

  async function handleCompanyChange(event) {
    const companyId = event.target.value;

    setSelectedCompanyId(companyId);

    if (!companyId) {
      setVehicleModels([]);

      setForm((previous) => ({
        ...previous,
        vehicle_company: "",
        vehicle_model: "",
      }));

      return;
    }

    const selectedCompany = companies.find(
      (company) =>
        String(company.id) === String(companyId)
    );

    setForm((previous) => ({
      ...previous,
      vehicle_company: selectedCompany?.name || "",
      vehicle_model: "",
    }));

    await loadModels(companyId);
  }

  function onImagePick(event) {
    const file = event.target.files?.[0];

    if (!file) return;

    setImageFile(file);
    setPreview(URL.createObjectURL(file));
  }

  function createPayload() {
    const data = new FormData();

    Object.entries(form).forEach(([key, value]) => {
      if (
        value !== null &&
        value !== undefined &&
        value !== ""
      ) {
        data.append(key, value);
      }
    });

    if (!form.department) {
      data.append("department", "");
    }

    if (imageFile) {
      data.append("vehicle_image", imageFile);
    }

    return data;
  }

  async function handleSubmit(event) {
    event.preventDefault();

    setSaving(true);
    setErrors({});

    if (!form.vehicle_company) {
      toast.error("Please select a vehicle company.");
      setSaving(false);
      return;
    }

    if (!form.vehicle_model) {
      toast.error("Please select a vehicle model.");
      setSaving(false);
      return;
    }

    if (
      form.valid_from &&
      form.valid_until &&
      form.valid_until < form.valid_from
    ) {
      setErrors({
        valid_until: [
          "Valid Until cannot be earlier than Valid From.",
        ],
      });

      toast.error("Please check the validity dates.");
      setSaving(false);
      return;
    }

    try {
      const payload = createPayload();

      if (vehicle) {
        await updateVehicle(vehicle.id, payload);
        toast.success("Vehicle updated successfully.");
      } else {
        await createVehicle(payload);
        toast.success("Vehicle added successfully.");
      }

      await onSaved?.();
      onClose();
    } catch (error) {
      const data = error?.response?.data;

      console.error("Vehicle save error:", error);

      if (data && typeof data === "object") {
        setErrors(data);
        toast.error("Please fix the highlighted fields.");
      } else {
        toast.error("Could not save vehicle.");
      }
    } finally {
      setSaving(false);
    }
  }

  const err = (key) => {
    const value = errors[key];

    if (Array.isArray(value)) {
      return value[0];
    }

    return value;
  };

  const companyOptions = companies.map((company) => ({
    value: String(company.id),
    label: company.name,
  }));

  const modelOptions = vehicleModels.map(
    (vehicleModel) => ({
      value: vehicleModel.name,
      label: vehicleModel.name,
    })
  );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={vehicle ? "Edit Vehicle" : "Add New Vehicle"}
      subtitle={
        vehicle
          ? `Editing ${vehicle.registration_number}`
          : "Register a new vehicle in the system"
      }
      width="max-w-3xl"
    >
      <form
        onSubmit={handleSubmit}
        className="space-y-6"
      >
        <div className="flex items-center gap-4">
          <div className="grid h-20 w-28 shrink-0 place-items-center overflow-hidden rounded-xl border border-slate-200 bg-slate-100">
            {preview ? (
              <img
                src={preview}
                alt="Vehicle"
                className="h-full w-full object-cover"
              />
            ) : (
              <LuUpload
                className="text-slate-300"
                size={22}
              />
            )}
          </div>

          <label className="text-sm">
            <span className="inline-block cursor-pointer rounded-lg border border-slate-200 px-3.5 py-2 font-medium text-slate-600 hover:bg-slate-50">
              Upload vehicle photo
            </span>

            <input
              type="file"
              accept="image/*"
              onChange={onImagePick}
              className="hidden"
            />
          </label>
        </div>

        <div>
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Owner Information
          </p>

          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              label="Owner Name"
              required
              value={form.owner_name}
              onChange={(event) =>
                update("owner_name", event.target.value)
              }
              error={err("owner_name")}
            />

            <SelectField
              label="Owner Type"
              required
              options={OWNER_TYPES}
              value={form.owner_type}
              onChange={(event) =>
                update("owner_type", event.target.value)
              }
              error={err("owner_type")}
            />

            <TextField
              label="Email"
              type="email"
              value={form.owner_email}
              onChange={(event) =>
                update("owner_email", event.target.value)
              }
              error={err("owner_email")}
            />

            <TextField
              label="Phone"
              value={form.owner_phone}
              onChange={(event) =>
                update("owner_phone", event.target.value)
              }
              error={err("owner_phone")}
            />

            <SelectField
              label="Department"
              placeholder="Select department"
              options={departments.map((department) => ({
                value: String(department.id),
                label:
                  department.display_name ||
                  department.name,
              }))}
              value={
                form.department
                  ? String(form.department)
                  : ""
              }
              onChange={(event) =>
                update("department", event.target.value)
              }
              error={err("department")}
            />
          </div>
        </div>

        <div>
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Vehicle Information
          </p>

          <div className="grid gap-4 sm:grid-cols-2">
            <SelectField
              label="Vehicle Type"
              required
              options={VEHICLE_TYPES}
              value={form.vehicle_type}
              onChange={handleVehicleTypeChange}
              error={err("vehicle_type")}
            />

            <SelectField
              label="Company"
              required
              placeholder={
                loadingCompanies
                  ? "Loading companies..."
                  : "Select company"
              }
              options={companyOptions}
              value={selectedCompanyId}
              onChange={handleCompanyChange}
              disabled={loadingCompanies}
              error={err("vehicle_company")}
            />

            <SelectField
              label="Model"
              required
              placeholder={
                loadingModels
                  ? "Loading models..."
                  : selectedCompanyId
                  ? "Select model"
                  : "Select company first"
              }
              options={modelOptions}
              value={form.vehicle_model}
              onChange={(event) =>
                update(
                  "vehicle_model",
                  event.target.value
                )
              }
              disabled={
                !selectedCompanyId || loadingModels
              }
              error={err("vehicle_model")}
            />

            <SelectField
              label="Fuel Type"
              required
              options={FUEL_TYPES}
              value={form.fuel_type}
              onChange={(event) =>
                update("fuel_type", event.target.value)
              }
              error={err("fuel_type")}
            />

            <SelectField
              label="Color"
              required
              options={VEHICLE_COLORS}
              value={form.color}
              onChange={(event) =>
                update("color", event.target.value)
              }
              error={err("color")}
            />

            <TextField
              label="Registration Number"
              required
              placeholder="KA25AB1234"
              value={form.registration_number}
              onChange={(event) =>
                update(
                  "registration_number",
                  event.target.value
                    .toUpperCase()
                    .replace(/\s/g, "")
                    .replace(/-/g, "")
                )
              }
              error={err("registration_number")}
            />
          </div>
        </div>

        <div>
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Registration & Validity
          </p>

          <div className="grid gap-4 sm:grid-cols-3">
            <TextField
              label="Registration Date"
              type="date"
              required
              value={form.registration_date}
              onChange={(event) =>
                update(
                  "registration_date",
                  event.target.value
                )
              }
              error={err("registration_date")}
            />

            <TextField
              label="Valid From"
              type="date"
              required
              value={form.valid_from}
              onChange={(event) =>
                update("valid_from", event.target.value)
              }
              error={err("valid_from")}
            />

            <TextField
              label="Valid Until"
              type="date"
              required
              value={form.valid_until}
              min={form.valid_from || undefined}
              onChange={(event) =>
                update("valid_until", event.target.value)
              }
              error={err("valid_until")}
            />
          </div>

          <div className="mt-4">
            <SelectField
              label="Access Status"
              options={AUTHORIZATION_STATUSES}
              value={form.authorization_status}
              onChange={(event) =>
                update(
                  "authorization_status",
                  event.target.value
                )
              }
              className="sm:max-w-xs"
              error={err("authorization_status")}
            />
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-100 pt-4">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-lg px-4 py-2.5 text-sm font-medium text-slate-600 hover:bg-slate-100 disabled:opacity-50"
          >
            Cancel
          </button>

          <button
            type="submit"
            disabled={saving}
            className="rounded-lg bg-brand-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-brand-700 disabled:opacity-60"
          >
            {saving
              ? "Saving..."
              : vehicle
              ? "Save Changes"
              : "Add Vehicle"}
          </button>
        </div>
      </form>
    </Modal>
  );
}