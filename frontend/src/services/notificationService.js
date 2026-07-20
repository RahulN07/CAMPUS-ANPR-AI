import api from "../api/axios";

export const listNotifications = async (params = {}) => {
  const response = await api.get("notifications/", { params });
  return response.data;
};

export const markAsRead = async (id) => {
  const response = await api.patch(`notifications/${id}/`, { is_read: true });
  return response.data;
};

export const markAllAsRead = async (notifications) => {
  await Promise.all(
    notifications.filter((n) => !n.is_read).map((n) => markAsRead(n.id))
  );
};

export const deleteNotification = async (id) => {
  await api.delete(`notifications/${id}/`);
};
