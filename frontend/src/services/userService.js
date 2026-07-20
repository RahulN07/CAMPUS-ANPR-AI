import api from "../api/axios";

export const listUsers = async (params = {}) => {
  const response = await api.get("auth/users/", { params });
  return response.data;
};

export const getUser = async (id) => {
  const response = await api.get(`auth/users/${id}/`);
  return response.data;
};

export const createUser = async (payload) => {
  const response = await api.post("auth/users/", payload);
  return response.data;
};

export const updateUser = async (id, payload) => {
  const response = await api.patch(`auth/users/${id}/`, payload);
  return response.data;
};

export const deleteUser = async (id) => {
  await api.delete(`auth/users/${id}/`);
};
