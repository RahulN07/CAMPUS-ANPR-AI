import api from "../api/axios";

export const getDashboardSummary = async () => {
  const response = await api.get("dashboard/");
  return response.data;
};