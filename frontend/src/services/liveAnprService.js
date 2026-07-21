import axios from "axios";

import api, { API_BASE_URL } from "../api/axios";


const WS_PROTOCOL = "anpr.v1";
const NORMAL_CLOSE = 1000;
const UNAUTHORIZED_CLOSE = 4401;
const GATE_NOT_FOUND_CLOSE = 4404;
const HEARTBEAT_INTERVAL_MS = 20_000;
const HEARTBEAT_TIMEOUT_MS = 60_000;
const MAX_RECONNECT_MS = 15_000;


function positiveGateId(gateId) {
  const normalized = Number(gateId);
  if (!Number.isInteger(normalized) || normalized <= 0) {
    throw new Error("gateId must be a positive integer");
  }
  return normalized;
}


function accessToken() {
  return localStorage.getItem("access") || "";
}


async function refreshAccessToken() {
  const refresh = localStorage.getItem("refresh");
  if (!refresh) throw new Error("No refresh token is available");

  const url = new URL("auth/refresh/", API_BASE_URL).toString();
  const response = await axios.post(url, { refresh });
  const token = response.data?.access;
  if (!token) throw new Error("Token refresh did not return an access token");

  localStorage.setItem("access", token);
  return token;
}


export function buildGateWebSocketUrl(gateId) {
  const id = positiveGateId(gateId);
  const base = new URL(API_BASE_URL, window.location.origin);
  const protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${base.host}/ws/anpr/gates/${id}/`;
}


export async function fetchLatestGateFrame({ gateId, etag = "", signal } = {}) {
  const id = positiveGateId(gateId);
  const headers = {};
  if (etag) headers["If-None-Match"] = etag;

  const response = await api.get(`anpr/gates/${id}/live-frame/`, {
    headers,
    responseType: "blob",
    signal,
    validateStatus: (status) => [200, 204, 304].includes(status),
  });

  if (response.status === 304) {
    return { state: "unchanged", etag };
  }
  if (response.status === 204) {
    return { state: "offline", etag: "" };
  }

  return {
    state: "frame",
    blob: response.data,
    etag: response.headers.etag || "",
    sequence: response.headers["x-anpr-frame-sequence"] || "",
    publishedAt: response.headers["x-anpr-published-at"] || "",
    fps: Number(response.headers["x-anpr-fps"] || 0),
    vehicleCount: Number(response.headers["x-anpr-vehicle-count"] || 0),
    trackedCount: Number(response.headers["x-anpr-tracked-count"] || 0),
  };
}


export class GateFramePoller {
  constructor({
    gateId,
    fps = 10,
    onFrame = () => {},
    onState = () => {},
    onError = () => {},
  }) {
    this.gateId = positiveGateId(gateId);
    this.intervalMs = Math.max(100, Math.round(1000 / Math.max(1, Math.min(10, fps))));
    this.onFrame = onFrame;
    this.onState = onState;
    this.onError = onError;
    this.running = false;
    this.timer = null;
    this.controller = null;
    this.etag = "";
    this.objectUrl = "";
    this.failureCount = 0;
    this._visibilityHandler = () => {
      if (this.running && document.visibilityState === "visible") {
        this._schedule(0);
      }
    };
  }

  start() {
    if (this.running) return false;
    this.running = true;
    this.failureCount = 0;
    document.addEventListener("visibilitychange", this._visibilityHandler);
    this._schedule(0);
    return true;
  }

  stop() {
    if (!this.running && !this.timer && !this.controller) {
      this._revokeObjectUrl();
      return false;
    }
    this.running = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    if (this.controller) this.controller.abort();
    this.controller = null;
    document.removeEventListener("visibilitychange", this._visibilityHandler);
    this._revokeObjectUrl();
    return true;
  }

  async _poll() {
    if (!this.running || this.controller) return;
    const startedAt = performance.now();
    this.controller = new AbortController();

    try {
      const result = await fetchLatestGateFrame({
        gateId: this.gateId,
        etag: this.etag,
        signal: this.controller.signal,
      });
      if (!this.running) return;

      this.failureCount = 0;
      if (result.state === "frame") {
        this.etag = result.etag;
        const nextUrl = URL.createObjectURL(result.blob);
        const previousUrl = this.objectUrl;
        this.objectUrl = nextUrl;
        this._safeCall(this.onFrame, { ...result, url: nextUrl });
        if (previousUrl) URL.revokeObjectURL(previousUrl);
      } else if (result.state === "offline") {
        this.etag = "";
      }
      this._safeCall(this.onState, result);
    } catch (error) {
      if (error?.name !== "CanceledError" && error?.name !== "AbortError") {
        this.failureCount += 1;
        this._safeCall(this.onError, error);
      }
    } finally {
      this.controller = null;
      if (this.running) {
        const elapsed = performance.now() - startedAt;
        const visibleDelay = Math.max(0, this.intervalMs - elapsed);
        const hiddenDelay = 1000;
        const failureDelay = Math.min(5000, 250 * 2 ** this.failureCount);
        this._schedule(
          this.failureCount
            ? failureDelay
            : document.visibilityState === "hidden"
              ? hiddenDelay
              : visibleDelay,
        );
      }
    }
  }

  _schedule(delay) {
    if (!this.running) return;
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      this._poll();
    }, delay);
  }

  _revokeObjectUrl() {
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = "";
  }

  _safeCall(callback, value) {
    try {
      callback(value);
    } catch (error) {
      console.error("Live frame callback failed", error);
    }
  }
}


export class GateLiveSocket {
  constructor({
    gateId,
    onConnection = () => {},
    onSnapshot = () => {},
    onStatus = () => {},
    onDetection = () => {},
    onError = () => {},
    onAuthFailure = () => {},
  }) {
    this.gateId = positiveGateId(gateId);
    this.onConnection = onConnection;
    this.onSnapshot = onSnapshot;
    this.onStatus = onStatus;
    this.onDetection = onDetection;
    this.onError = onError;
    this.onAuthFailure = onAuthFailure;
    this.socket = null;
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
    this.shouldReconnect = false;
    this.reconnectAttempt = 0;
    this.lastPongAt = 0;
    this.authRecovery = null;
  }

  connect() {
    this.shouldReconnect = true;
    if (
      this.socket &&
      [WebSocket.OPEN, WebSocket.CONNECTING].includes(this.socket.readyState)
    ) {
      return false;
    }
    this._open();
    return true;
  }

  disconnect() {
    this.shouldReconnect = false;
    this._clearReconnect();
    this._stopHeartbeat();
    const socket = this.socket;
    this.socket = null;
    if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close(NORMAL_CLOSE, "client disconnect");
    }
  }

  requestStatus() {
    return this._send({ type: "get_status" });
  }

  _open() {
    if (!this.shouldReconnect) return;
    const token = accessToken();
    if (!token) {
      this._safeCall(this.onAuthFailure, new Error("Missing access token"));
      return;
    }

    this._clearReconnect();
    this._safeCall(this.onConnection, { state: "connecting" });
    const socket = new WebSocket(buildGateWebSocketUrl(this.gateId), [
      WS_PROTOCOL,
      token,
    ]);
    this.socket = socket;

    socket.onopen = () => {
      if (socket !== this.socket) return;
      this.reconnectAttempt = 0;
      this.lastPongAt = Date.now();
      this._safeCall(this.onConnection, { state: "connected" });
      this._startHeartbeat();
    };

    socket.onmessage = (event) => {
      if (socket !== this.socket) return;
      this._handleMessage(event.data);
    };

    socket.onerror = () => {
      if (socket === this.socket) {
        this._safeCall(this.onError, new Error("Live WebSocket error"));
      }
    };

    socket.onclose = (event) => {
      if (socket !== this.socket) return;
      this.socket = null;
      this._stopHeartbeat();
      this._safeCall(this.onConnection, {
        state: "disconnected",
        code: event.code,
        reason: event.reason,
      });

      if (!this.shouldReconnect || event.code === NORMAL_CLOSE) return;
      if (event.code === GATE_NOT_FOUND_CLOSE) {
        this.shouldReconnect = false;
        this._safeCall(this.onError, new Error("Gate is inactive or missing"));
        return;
      }
      if (event.code === UNAUTHORIZED_CLOSE) {
        this._recoverAuthentication();
        return;
      }
      this._scheduleReconnect();
    };
  }

  _handleMessage(raw) {
    let message;
    try {
      message = JSON.parse(raw);
    } catch {
      this._safeCall(this.onError, new Error("Invalid live WebSocket message"));
      return;
    }

    if (message.type === "pong") {
      this.lastPongAt = Date.now();
    } else if (message.type === "snapshot") {
      this._safeCall(this.onSnapshot, message.data || {});
    } else if (message.type === "status") {
      this._safeCall(this.onStatus, message.data || {});
    } else if (message.type === "detection") {
      this._safeCall(this.onDetection, message.data || {});
    } else if (message.type === "error") {
      this._safeCall(
        this.onError,
        new Error(message.error?.message || "Live WebSocket server error"),
      );
    }
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (Date.now() - this.lastPongAt > HEARTBEAT_TIMEOUT_MS) {
        this.socket?.close(4000, "heartbeat timeout");
        return;
      }
      this._send({ type: "ping" });
    }, HEARTBEAT_INTERVAL_MS);
  }

  _stopHeartbeat() {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = null;
  }

  _send(payload) {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(payload));
    return true;
  }

  _scheduleReconnect() {
    if (!this.shouldReconnect || this.reconnectTimer) return;
    const exponential = Math.min(
      MAX_RECONNECT_MS,
      500 * 2 ** this.reconnectAttempt,
    );
    const jitter = Math.round(Math.random() * 250);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this._open();
    }, exponential + jitter);
  }

  _clearReconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  _recoverAuthentication() {
    if (this.authRecovery) return;
    this.authRecovery = refreshAccessToken()
      .then(() => {
        if (this.shouldReconnect) this._open();
      })
      .catch((error) => {
        this.shouldReconnect = false;
        localStorage.removeItem("access");
        localStorage.removeItem("refresh");
        this._safeCall(this.onAuthFailure, error);
      })
      .finally(() => {
        this.authRecovery = null;
      });
  }

  _safeCall(callback, value) {
    try {
      callback(value);
    } catch (error) {
      console.error("Live WebSocket callback failed", error);
    }
  }
}
