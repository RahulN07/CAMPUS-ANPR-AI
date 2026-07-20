"""Per-camera orchestration for continuous tracking ANPR."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from anpr.line_crossing import LineCrossingDetector, LineCrossingEvent
from anpr.track_buffer import (
    FinalizedVehicleTrack,
    TrackBufferConfig,
    TrackCandidateBuffer,
)
from anpr.vehicle_cache import VehicleCache, get_vehicle_cache
from anpr.vehicle_cache_sync import (
    VehicleCacheRefreshService,
    VehicleCacheSyncConfig,
)
from anpr.vehicle_processor import (
    RecentPlateGuard,
    VehicleProcessingResult,
    VehicleProcessor,
    VehicleProcessorConfig,
)
from anpr.vehicle_tracker import (
    VehicleDetection,
    VehicleTracker,
    VehicleTrackingResult,
)
from anpr.vehicle_worker_pool import (
    VehicleWorkerPool,
    WorkerPoolConfig,
    WorkerPoolStats,
)


ActivityCallback = Callable[
    [FinalizedVehicleTrack, VehicleProcessingResult],
    None,
]
ErrorCallback = Callable[[FinalizedVehicleTrack, BaseException], None]


@dataclass(frozen=True, slots=True)
class TrackingPipelineConfig:
    worker_count: int = 5
    vehicle_queue_size: int = 100
    candidates_per_track: int = 3
    required_unknown_votes: int = 2
    duplicate_seconds: float = 5.0
    cache_refresh_seconds: float = 30.0
    cache_retry_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not 1 <= self.worker_count <= 32:
            raise ValueError("worker_count must be between 1 and 32")
        if not 1 <= self.vehicle_queue_size <= 10000:
            raise ValueError("vehicle_queue_size must be between 1 and 10000")
        if not 1 <= self.candidates_per_track <= 10:
            raise ValueError("candidates_per_track must be between 1 and 10")
        if not 1 <= self.required_unknown_votes <= self.candidates_per_track:
            raise ValueError(
                "required_unknown_votes must be between 1 and "
                "candidates_per_track"
            )
        if self.duplicate_seconds <= 0:
            raise ValueError("duplicate_seconds must be greater than 0")
        if self.cache_refresh_seconds <= 0:
            raise ValueError("cache_refresh_seconds must be greater than 0")
        if self.cache_retry_seconds <= 0:
            raise ValueError("cache_retry_seconds must be greater than 0")


@dataclass(frozen=True, slots=True)
class TrackingPipelineFrameResult:
    frame_index: int
    detections: tuple[VehicleDetection, ...]
    crossings: tuple[LineCrossingEvent, ...]
    submitted_track_ids: tuple[int, ...]
    rejected_track_ids: tuple[int, ...]
    tracker_inference_ms: float
    frame_processing_ms: float
    worker_stats: WorkerPoolStats

    @property
    def vehicle_count(self) -> int:
        return len(self.detections)

    @property
    def tracked_count(self) -> int:
        return sum(detection.is_tracked for detection in self.detections)


@dataclass(frozen=True, slots=True)
class TrackingPipelineStats:
    running: bool
    frames_processed: int
    vehicles_observed: int
    tracked_vehicles_observed: int
    line_crossings: int
    tasks_submitted: int
    tasks_rejected: int
    processing_results: int
    records_saved: int
    duplicate_results: int
    processing_failures: int
    last_result: VehicleProcessingResult | None
    last_error: str


class CameraTrackingPipeline:
    """Own all state for one gate camera and never block on OCR work."""

    def __init__(
        self,
        *,
        gate,
        recorded_by_id: int | None,
        config: TrackingPipelineConfig | None = None,
        cache: VehicleCache | None = None,
        tracker: VehicleTracker | None = None,
        line_detector: LineCrossingDetector | None = None,
        candidate_buffer: TrackCandidateBuffer | None = None,
        cache_service: VehicleCacheRefreshService | None = None,
        worker_pool: VehicleWorkerPool[FinalizedVehicleTrack] | None = None,
        processor_factory: Callable[[], Callable] | None = None,
        on_activity: ActivityCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        if not getattr(gate, "pk", None):
            raise ValueError("gate must be a saved Gate instance")
        if recorded_by_id is not None and recorded_by_id < 1:
            raise ValueError("recorded_by_id must be positive when provided")

        self.gate = gate
        self.recorded_by_id = recorded_by_id
        self.config = config or TrackingPipelineConfig()
        self.cache = cache or get_vehicle_cache()
        self.tracker = tracker or VehicleTracker()
        self.line_detector = line_detector or LineCrossingDetector.from_gate(gate)
        self.candidate_buffer = candidate_buffer or TrackCandidateBuffer(
            TrackBufferConfig(
                candidates_per_track=self.config.candidates_per_track,
            )
        )
        self.cache_service = cache_service or VehicleCacheRefreshService(
            cache=self.cache,
            config=VehicleCacheSyncConfig(
                refresh_interval_seconds=self.config.cache_refresh_seconds,
                retry_interval_seconds=self.config.cache_retry_seconds,
                thread_name=f"anpr-cache-gate-{gate.pk}",
            ),
        )
        self._on_activity = on_activity
        self._on_error = on_error
        self._duplicate_guard = RecentPlateGuard(
            self.config.duplicate_seconds
        )

        vehicle_processor_config = VehicleProcessorConfig(
            gate_id=int(gate.pk),
            direction=str(gate.gate_type),
            recorded_by_id=recorded_by_id,
            required_unknown_votes=self.config.required_unknown_votes,
            maximum_candidates=self.config.candidates_per_track,
            duplicate_seconds=self.config.duplicate_seconds,
        )

        if processor_factory is None:
            def make_processor_callable():
                processor = VehicleProcessor(
                    config=vehicle_processor_config,
                    cache=self.cache,
                    duplicate_guard=self._duplicate_guard,
                )
                return processor.process

            processor_factory = make_processor_callable

        self.worker_pool = worker_pool or VehicleWorkerPool(
            processor_factory=processor_factory,
            config=WorkerPoolConfig(
                worker_count=self.config.worker_count,
                queue_size=self.config.vehicle_queue_size,
                thread_name_prefix=f"anpr-gate-{gate.pk}-worker",
                manage_django_connections=True,
            ),
            on_success=self._handle_worker_success,
            on_error=self._handle_worker_error,
        )

        self._frame_lock = threading.Lock()
        self._stats_lock = threading.RLock()
        self._running = False
        self._frames_processed = 0
        self._vehicles_observed = 0
        self._tracked_vehicles_observed = 0
        self._line_crossings = 0
        self._tasks_submitted = 0
        self._tasks_rejected = 0
        self._processing_results = 0
        self._records_saved = 0
        self._duplicate_results = 0
        self._processing_failures = 0
        self._last_result: VehicleProcessingResult | None = None
        self._last_error = ""
        self._last_frame_index = -1

    def start(self) -> bool:
        with self._stats_lock:
            if self._running:
                return False

        # Warm authorization data before workers or camera frames are accepted.
        self.cache_service.start(warm=True)
        try:
            self.worker_pool.start()
        except Exception:
            self.cache_service.stop()
            raise

        with self._stats_lock:
            self._running = True
        return True

    def process_frame(
        self,
        *,
        frame: np.ndarray,
        frame_index: int,
        captured_at: float | None = None,
    ) -> TrackingPipelineFrameResult:
        """Run sequential tracking and enqueue crossings without waiting."""

        started = time.perf_counter()
        with self._frame_lock:
            with self._stats_lock:
                if not self._running:
                    raise RuntimeError("tracking pipeline is not running")
                if frame_index <= self._last_frame_index:
                    raise ValueError("frame_index must increase monotonically")

            tracking = self.tracker.track(frame)
            crossings: list[LineCrossingEvent] = []
            submitted: list[int] = []
            rejected: list[int] = []
            capture_time = (
                float(captured_at)
                if captured_at is not None
                else time.monotonic()
            )

            for detection in tracking.detections:
                if detection.track_id is None:
                    continue

                self.candidate_buffer.observe(
                    frame=frame,
                    detection=detection,
                    frame_index=frame_index,
                    captured_at=capture_time,
                )
                crossing = self.line_detector.update(
                    track_id=detection.track_id,
                    center=detection.center,
                    frame_width=tracking.frame_width,
                    frame_height=tracking.frame_height,
                    frame_index=frame_index,
                )
                if crossing is None:
                    continue

                crossings.append(crossing)
                task = self.candidate_buffer.finalize(crossing)
                if task is None:
                    rejected.append(detection.track_id)
                    continue
                if self.worker_pool.submit(task):
                    submitted.append(detection.track_id)
                else:
                    rejected.append(detection.track_id)

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            with self._stats_lock:
                self._last_frame_index = frame_index
                self._frames_processed += 1
                self._vehicles_observed += len(tracking.detections)
                self._tracked_vehicles_observed += tracking.tracked_count
                self._line_crossings += len(crossings)
                self._tasks_submitted += len(submitted)
                self._tasks_rejected += len(rejected)

            return TrackingPipelineFrameResult(
                frame_index=frame_index,
                detections=tracking.detections,
                crossings=tuple(crossings),
                submitted_track_ids=tuple(submitted),
                rejected_track_ids=tuple(rejected),
                tracker_inference_ms=tracking.inference_ms,
                frame_processing_ms=elapsed_ms,
                worker_stats=self.worker_pool.stats(),
            )

    def stop(
        self,
        *,
        drain: bool = True,
        timeout: float | None = 30.0,
    ) -> bool:
        # Wait for an in-progress tracking frame to finish before closing the
        # worker queue; otherwise its crossing task could be rejected during
        # shutdown even though the frame was already accepted.
        with self._frame_lock:
            with self._stats_lock:
                if not self._running:
                    self.cache_service.stop(timeout=5.0)
                    return True
                self._running = False

        workers_stopped = self.worker_pool.stop(
            drain=drain,
            timeout=timeout,
        )
        cache_stopped = self.cache_service.stop(timeout=5.0)
        return workers_stopped and cache_stopped

    def stats(self) -> TrackingPipelineStats:
        with self._stats_lock:
            return TrackingPipelineStats(
                running=self._running,
                frames_processed=self._frames_processed,
                vehicles_observed=self._vehicles_observed,
                tracked_vehicles_observed=self._tracked_vehicles_observed,
                line_crossings=self._line_crossings,
                tasks_submitted=self._tasks_submitted,
                tasks_rejected=self._tasks_rejected,
                processing_results=self._processing_results,
                records_saved=self._records_saved,
                duplicate_results=self._duplicate_results,
                processing_failures=self._processing_failures,
                last_result=self._last_result,
                last_error=self._last_error,
            )

    def _handle_worker_success(
        self,
        track: FinalizedVehicleTrack,
        result: VehicleProcessingResult,
    ) -> None:
        with self._stats_lock:
            self._processing_results += 1
            self._last_result = result
            self._last_error = ""
            if result.saved:
                self._records_saved += 1
            if result.reason == "DUPLICATE_IGNORED":
                self._duplicate_results += 1

        if self._on_activity is not None:
            self._on_activity(track, result)

    def _handle_worker_error(
        self,
        track: FinalizedVehicleTrack,
        error: BaseException,
    ) -> None:
        with self._stats_lock:
            self._processing_failures += 1
            self._last_error = f"{type(error).__name__}: {error}"

        if self._on_error is not None:
            self._on_error(track, error)