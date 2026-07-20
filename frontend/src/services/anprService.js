import api from "../api/axios";

/**
 * Sends one frame (manual upload OR automatic webcam tick) to the
 * ANPR detect endpoint.
 *
 * `direction` is intentionally optional now: the automatic webcam
 * workflow never sends it and lets the backend decide ENTRY/EXIT.
 * Pass it explicitly only for flows that still need to force a
 * direction (e.g. a manual upload form, if one exists elsewhere).
 */
export const detectPlate = async ({ image, gate, source = "WEBCAM", direction }) => {
  const fd = new FormData();
  fd.append("image", image);
  fd.append("source", source);
  if (gate) fd.append("gate", gate);
  if (direction) fd.append("direction", direction);

  const response = await api.post("anpr/detect/", fd);
  return response.data;
};

export const recentDetections = async (limit = 10) => {
  const response = await api.get("anpr/recent-detections/", { params: { limit } });
  return response.data;
};