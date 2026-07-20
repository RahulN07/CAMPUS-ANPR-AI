import api from "../api/axios";

function toFormData(payload) {
  const fd = new FormData();

  Object.entries(payload).forEach(([key, value]) => {
    if (value === undefined || value === null) return;

    if (
      key === "vehicle_image" &&
      !(value instanceof File)
    ) {
      return;
    }

    fd.append(key, value);
  });

  return fd;
}

export const listVehicles = async (params = {}) => {
  const response = await api.get("vehicles/", {
    params,
  });

  return response.data;
};

export const getVehicle = async (id) => {
  const response = await api.get(
    `vehicles/${id}/`
  );

  return response.data;
};

export const createVehicle = async (payload) => {
  const hasImage =
    payload.vehicle_image instanceof File;

  const response = await api.post(
    "vehicles/",
    hasImage ? toFormData(payload) : payload
  );

  return response.data;
};

export const updateVehicle = async (
  id,
  payload
) => {
  const hasImage =
    payload.vehicle_image instanceof File;

  const response = await api.patch(
    `vehicles/${id}/`,
    hasImage ? toFormData(payload) : payload
  );

  return response.data;
};

export const deleteVehicle = async (id) => {
  await api.delete(`vehicles/${id}/`);
};

export const getVehicleCompanies = async (
  vehicleType
) => {
  const response = await api.get(
    "vehicles/companies/",
    {
      params: {
        vehicle_type: vehicleType,
      },
    }
  );

  return response.data;
};

export const getVehicleModels = async (
  companyId
) => {
  const response = await api.get(
    "vehicles/models/",
    {
      params: {
        company: companyId,
      },
    }
  );

  return response.data;
};