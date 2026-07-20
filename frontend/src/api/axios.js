import axios from "axios";

export const API_BASE_URL = "http://127.0.0.1:8000/api/";
export const MEDIA_BASE_URL = "http://127.0.0.1:8000";

const api = axios.create({
  baseURL: API_BASE_URL,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("access");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  if (!(config.data instanceof FormData)) {
    config.headers["Content-Type"] = "application/json";
  }
  return config;
});

let refreshing = null;

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    const status = error.response?.status;

    if (status === 401 && original && !original._retry && localStorage.getItem("refresh")) {
      original._retry = true;
      try {
        if (!refreshing) {
          refreshing = axios
            .post(`${API_BASE_URL}auth/refresh/`, {
              refresh: localStorage.getItem("refresh"),
            })
            .then((res) => {
              localStorage.setItem("access", res.data.access);
              return res.data.access;
            })
            .finally(() => {
              refreshing = null;
            });
        }
        const newAccess = await refreshing;
        original.headers.Authorization = `Bearer ${newAccess}`;
        return api(original);
      } catch (refreshError) {
        localStorage.removeItem("access");
        localStorage.removeItem("refresh");
        window.location.href = "/";
        return Promise.reject(refreshError);
      }
    }

    return Promise.reject(error);
  }
);

export function resolveMediaUrl(path) {
  if (!path) return null;
  if (path.startsWith("http")) return path;
  return `${MEDIA_BASE_URL}${path}`;
}

export default api;
