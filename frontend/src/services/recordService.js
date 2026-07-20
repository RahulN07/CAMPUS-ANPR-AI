import api from "../api/axios";

export const listRecords = async (params = {}) => {
  const response = await api.get("records/", { params });
  return response.data;
};

export const getRecord = async (id) => {
  const response = await api.get(`records/${id}/`);
  return response.data;
};

export const createRecord = async (payload) => {
  const response = await api.post("records/", payload);
  return response.data;
};

export const updateRecord = async (id, payload) => {
  const response = await api.patch(`records/${id}/`, payload);
  return response.data;
};

export const deleteRecord = async (id) => {
  await api.delete(`records/${id}/`);
};
