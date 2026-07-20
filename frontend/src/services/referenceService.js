import api from "../api/axios";

export const listDepartments = async () => {
  const response = await api.get("access/departments/");
  const data = response.data;
  return Array.isArray(data) ? data : data.results || [];
};

export const listGates = async () => {
  const response = await api.get("access/gates/");
  const data = response.data;
  return Array.isArray(data) ? data : data.results || [];
};

export const getSystemSettings = async () => {
  const response = await api.get("access/settings/");
  return response.data;
};

export const updateSystemSettings = async (payload) => {
  const response = await api.patch("access/settings/", payload);
  return response.data;
};
