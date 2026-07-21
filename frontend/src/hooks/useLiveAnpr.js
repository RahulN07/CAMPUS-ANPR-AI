import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  GateFramePoller,
  GateLiveSocket,
} from "../services/liveAnprService";


function normalizeGateId(gateId) {
  const id = Number(gateId);
  return Number.isInteger(id) && id > 0 ? id : null;
}


function eventIdentity(event) {
  if (event?.record_id) return `record:${event.record_id}`;
  return [
    "live",
    event?.plate || "",
    event?.track_id ?? "",
    event?.published_at || event?.timestamp || "",
  ].join(":");
}


function mergeRecentEvents(incoming, current, maximum) {
  const merged = [];
  const seen = new Set();

  for (const event of [...incoming, ...current]) {
    if (!event || typeof event !== "object") continue;
    const identity = eventIdentity(event);
    if (seen.has(identity)) continue;
    seen.add(identity);
    merged.push(event);
    if (merged.length >= maximum) break;
  }
  return merged;
}


export function useLiveAnpr(gateId, options = {}) {
  const maximumEvents = Math.max(1, Math.min(100, options.maximumEvents || 25));
  const normalizedGateId = normalizeGateId(gateId);
  const onDetectionRef = useRef(options.onDetection);
  const mountedRef = useRef(true);
  const socketRef = useRef(null);
  const pollerRef = useRef(null);

  const [monitoring, setMonitoring] = useState(false);
  const [connectionState, setConnectionState] = useState("idle");
  const [status, setStatus] = useState(null);
  const [frameUrl, setFrameUrl] = useState("");
  const [frameMetadata, setFrameMetadata] = useState(null);
  const [latestDetection, setLatestDetection] = useState(null);
  const [recentEvents, setRecentEvents] = useState([]);
  const [error, setError] = useState("");

  useEffect(() => {
    onDetectionRef.current = options.onDetection;
  }, [options.onDetection]);

  const stopFramePolling = useCallback(() => {
    pollerRef.current?.stop();
    if (mountedRef.current) {
      setFrameUrl("");
      setFrameMetadata(null);
    }
  }, []);

  const applyStatus = useCallback(
    (nextStatus) => {
      if (!mountedRef.current) return;
      const normalized = nextStatus && typeof nextStatus === "object" ? nextStatus : null;
      setStatus(normalized);

      if (normalized?.state === "RUNNING") {
        pollerRef.current?.start();
      } else if (["STOPPED", "ERROR"].includes(normalized?.state)) {
        stopFramePolling();
      }
    },
    [stopFramePolling],
  );

  const applyDetection = useCallback(
    (detection) => {
      if (!mountedRef.current || !detection || typeof detection !== "object") return;
      setLatestDetection(detection);
      setRecentEvents((current) =>
        mergeRecentEvents([detection], current, maximumEvents),
      );
      try {
        onDetectionRef.current?.(detection);
      } catch (callbackError) {
        console.error("Live detection callback failed", callbackError);
      }
    },
    [maximumEvents],
  );

  const destroyTransports = useCallback(() => {
    socketRef.current?.disconnect();
    pollerRef.current?.stop();
    socketRef.current = null;
    pollerRef.current = null;
  }, []);

  const stop = useCallback(() => {
    destroyTransports();
    if (!mountedRef.current) return;
    setMonitoring(false);
    setConnectionState("idle");
    setStatus(null);
    setFrameUrl("");
    setFrameMetadata(null);
    setError("");
  }, [destroyTransports]);

  const start = useCallback(() => {
    if (!normalizedGateId) {
      setError("Select an active gate before starting live monitoring.");
      return false;
    }
    if (socketRef.current || pollerRef.current) return false;

    setError("");
    setMonitoring(true);
    setConnectionState("connecting");

    const poller = new GateFramePoller({
      gateId: normalizedGateId,
      fps: 10,
      onFrame: (frame) => {
        if (!mountedRef.current) return;
        setFrameUrl(frame.url);
        setFrameMetadata(frame);
      },
      onState: (frameState) => {
        if (mountedRef.current && frameState.state === "offline") {
          setFrameMetadata(null);
        }
      },
      onError: () => {
        if (mountedRef.current) {
          setError("Live frame connection interrupted. Reconnecting…");
        }
      },
    });

    const socket = new GateLiveSocket({
      gateId: normalizedGateId,
      onConnection: (connection) => {
        if (!mountedRef.current) return;
        setConnectionState(connection.state);
        if (connection.state === "connected") setError("");
      },
      onSnapshot: (snapshot) => {
        if (!mountedRef.current) return;
        applyStatus(snapshot.status);
        const events = Array.isArray(snapshot.recent_events)
          ? snapshot.recent_events
          : [];
        setRecentEvents((current) =>
          mergeRecentEvents(events, current, maximumEvents),
        );
        if (events[0]) setLatestDetection(events[0]);
      },
      onStatus: applyStatus,
      onDetection: applyDetection,
      onError: (socketError) => {
        if (mountedRef.current) {
          setError(socketError?.message || "Live status connection interrupted.");
        }
      },
      onAuthFailure: () => {
        if (!mountedRef.current) return;
        setConnectionState("unauthorized");
        setError("Your session expired. Please sign in again.");
        stopFramePolling();
      },
    });

    pollerRef.current = poller;
    socketRef.current = socket;
    socket.connect();
    return true;
  }, [
    applyDetection,
    applyStatus,
    maximumEvents,
    normalizedGateId,
    stopFramePolling,
  ]);

  const requestStatus = useCallback(
    () => socketRef.current?.requestStatus() || false,
    [],
  );

  const clearError = useCallback(() => setError(""), []);

  useEffect(() => {
    stop();
    setRecentEvents([]);
    setLatestDetection(null);
  }, [normalizedGateId, stop]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      destroyTransports();
    };
  }, [destroyTransports]);

  const metrics = useMemo(
    () => ({
      fps: Number(status?.fps || frameMetadata?.fps || 0),
      targetFps: Number(status?.target_fps || 10),
      vehicleCount: Number(
        status?.vehicle_count ?? frameMetadata?.vehicleCount ?? 0,
      ),
      trackedCount: Number(
        status?.tracked_count ?? frameMetadata?.trackedCount ?? 0,
      ),
      frameQueueSize: Number(status?.frame_queue_size || 0),
      frameQueueCapacity: Number(status?.frame_queue_capacity || 30),
      frameQueueDropped: Number(status?.frame_queue_dropped || 0),
      vehicleQueueSize: Number(status?.vehicle_queue_size || 0),
      vehicleQueueCapacity: Number(status?.vehicle_queue_capacity || 100),
      workerInFlight: Number(status?.worker_in_flight || 0),
      workerCount: Number(status?.worker_count || 0),
      recordsSaved: Number(status?.records_saved || 0),
      lineCrossings: Number(status?.line_crossings || 0),
      reconnects: Number(status?.camera_reconnects || 0),
    }),
    [frameMetadata, status],
  );

  const pipelineState = status?.state || (monitoring ? "OFFLINE" : "IDLE");
  const isLive = monitoring && pipelineState === "RUNNING" && Boolean(frameUrl);

  return {
    monitoring,
    connectionState,
    pipelineState,
    isLive,
    status,
    metrics,
    frameUrl,
    frameMetadata,
    latestDetection,
    recentEvents,
    error,
    start,
    stop,
    requestStatus,
    clearError,
  };
}


export default useLiveAnpr;
